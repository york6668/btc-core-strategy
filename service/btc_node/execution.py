"""Durable command admission and recovery for the unchanged domain validator.

No command sends a report. The host receives its result only after state and
projections are committed; ambiguous interrupted commands are never replayed.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
from time import monotonic

from .catalog import Catalog
from .codec import Conflict, encode, fingerprint, millis, sync_directory
from .database import Database
from .entities import Entities
from .projection import Projection


RESEARCH_REGISTRATION_COMMANDS = {'research-independent', 'research-orchestrated-independent',
                                  'research-result', 'research-orchestrated-result',
                                  'research-market-amendment'}


def validate_deep_result_admission(db, argv, release):
    """Keep an older pinned CLI from bypassing the node-owned result gate."""
    index = 0
    while index < len(argv):
        if argv[index] in ('--project', '--root', '--runtime-schedule'):
            index += 2
        elif argv[index] == '--full':
            index += 1
        else:
            break
    if index >= len(argv) or argv[index] not in RESEARCH_REGISTRATION_COMMANDS:
        return
    if not (Path(release) / 'skill/btc-core-strategy/scripts/model_daily.py').is_file():
        raise Conflict('pinned release lacks deep-result admission; repair the original context first')
    args = argv[index + 1:]
    if args.count('--guard-attempt') != 1 or args.index('--guard-attempt') + 1 >= len(args):
        raise Conflict('original completed deep claim required for research registration')
    claim_id = args[args.index('--guard-attempt') + 1]
    from . import deep_gate
    state = deep_gate._state(db)
    claim = deep_gate.records(state).get(claim_id)
    if not claim or claim['state'] != 'COMPLETE' or claim['model'] != 'gpt-6-astra':
        raise Conflict('original completed Astra claim required for research registration')
    if any(v['state'] == 'OPEN' and deep_gate._affected(v['scope'], 'btc/research')
           for v in state['faults'].values()):
        raise Conflict('known defect blocks affected research registration')


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encode(value)); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path); sync_directory(path.parent)


def value_reference_only(argv):
    """Recognize the actual subcommand, never a flag value or substring.

    Only this command reads an already frozen run and returns its scalar hash.
    Unknown argument layouts retain the complete mutation/projection path.
    """
    index = 0
    while index < len(argv):
        if argv[index] in ('--project', '--root'):
            if index + 1 >= len(argv):
                return False
            index += 2
        elif argv[index] == '--full':
            index += 1
        else:
            return argv[index] == 'run-value-ref'
    return False


def help_only(argv):
    """An exact command help request cannot execute a business mutation."""
    index = 0
    while index < len(argv):
        if argv[index] in ('--project', '--root') and index + 1 < len(argv):
            index += 2
        elif argv[index] == '--full':
            index += 1
        else:
            return (len(argv) - index == 2 and not argv[index].startswith('-')
                    and argv[index + 1] in ('--help', '-h'))
    return False


def component_binding(release):
    directory = Path(__file__).parent
    names = ('execution.py', 'projection.py', 'entities.py', 'series.py', 'history.py')
    return {'schema': 'BTC_OPERATION_COMPONENT_BINDING_V1', 'core_release': Path(release).name,
            'service_sources': {name: fingerprint((directory / name).read_bytes()) for name in names},
            'meaning': 'Actual executing method and service bytes; data/reader identity remains in frozen run inputs, not inferred from this pin.'}


def execute(settings, request):
    params = request['params']
    release = Path(params['release']).resolve()
    allowed = Path(settings['release_root']).resolve()
    if release.parent != allowed or not (release / 'RELEASE.json').is_file():
        raise ValueError('command requires an installed, pinned release')
    project = Path(settings['project']).resolve()
    argv = params['argv']
    if not isinstance(argv, list) or not all(isinstance(v, str) for v in argv):
        raise ValueError('argument vector required')
    reference_only = value_reference_only(argv)
    readonly = reference_only or help_only(argv)
    def project_path(value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else project / path).resolve()
    for option, required in (('--project', project), ('--root', project / '.strategy/v4')):
        if argv.count(option) != 1 or project_path(argv[argv.index(option) + 1]) != required.resolve():
            raise ValueError('command belongs to another project')
    operation = request.get('operation_id')
    if not isinstance(operation, str) or not 0 < len(operation) <= 191:
        raise ValueError('stable operation_id required')
    request_digest = fingerprint(encode(params))
    for path, expected in params.get('input_hashes', {}).items():
        if fingerprint(project_path(path).read_bytes()) != expected:
            raise Conflict('frozen command input hash mismatch')
    journal = Path(settings['journal']) / (fingerprint(operation.encode()) + '.json')
    db = Database(settings)
    try:
        with db.writer(timeout=600):
            admitted = monotonic()
            pending = db.rows("SELECT operation_id FROM operations WHERE status IN ('EXECUTING','RECOVERY_REQUIRED')")
            previous = db.one('SELECT * FROM operations WHERE operation_id=%s', (operation,))
            if previous:
                if previous['request_digest'] != request_digest:
                    raise Conflict('operation identity reused with different inputs')
                if previous['status'] == 'COMMITTED':
                    return db.value(previous['result_digest'])
                raise Conflict('interrupted command requires reconciliation; do not replay or resend')
            if pending:
                raise Conflict('recover the interrupted writer before accepting another command')
            if (db.metadata('writer') or {}).get('owner') != 'DOCKER':
                raise Conflict('comparison mode does not execute legacy commands')
            validate_deep_result_admission(db, argv, release)
            mysql_native = 'connect_if_configured' in (release / 'skill/btc-core-strategy/scripts/market_observation.py').read_text()
            original = Path(settings['observation_db'])
            if not readonly and not mysql_native and not original.exists() and db.one('SELECT 1 FROM mo_evidence LIMIT 1'):
                raise Conflict('restore the compatibility observation schema before resuming an old pinned release')
            with db.transaction():
                db.put_blob(encode(params))
                db.execute('INSERT INTO operations(operation_id,request_digest,status,started_ms,release_id) '
                           'VALUES(%s,%s,%s,%s,%s)', (operation, request_digest, 'EXECUTING', millis(), release.name))
            journal_state = {'operation_id': operation, 'request': params, 'status': 'EXECUTING',
                             'mysql_native': mysql_native, 'sqlite_prepared': False,
                             'value_reference_only': reference_only, 'help_only': help_only(argv)}
            atomic(journal, journal_state)
            if not readonly and not mysql_native:
                from .market_store import export_sqlite
                if original.exists():
                    export_sqlite(db, original)
                journal_state['sqlite_prepared']=True
                atomic(journal,journal_state)
            environment = dict(os.environ, BTC_IN_CONTAINER='1', BTC_NODE_CONFIG=settings['container_node_config'],
                               BTC_SERVICE_CONFIG=settings['_path'], BTC_RELEASE=str(release), BTC_PROJECT=str(project),
                               BTC_PYTHON=sys.executable, PYTHONPATH='/opt/btc')
            command = [sys.executable, str(release / 'skill/btc-core-strategy/scripts/strategy_v4.py'), *argv]
            command_started = monotonic()
            from .request_trace import run_child
            process = run_child(command, cwd=project, env=environment)
            result = {'returncode': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr,
                      'component_binding': component_binding(release)}
            timings = {'command_ms': round((monotonic()-command_started)*1000)}
            atomic(journal, {**journal_state, 'status': 'EXECUTED', 'result': result})
            with db.transaction():
                if not readonly and not mysql_native and Path(settings['observation_db']).exists():
                    from .market_store import import_sqlite
                    import_sqlite(db, settings['observation_db'])
                # Rows in the hourly market store already use MySQL. Remaining
                # immutable files and pointer changes become a recovery manifest.
                # Scalar references and exact help create no strategy state. Re-archiving
                # every run and rebuilding all projections here kept the writer
                # busy for longer than the reader's checkpoint admission.
                # Admission, immutable inputs, journal and idempotent result
                # commit still use the same writer and operation identity.
                if not readonly:
                    catalog = Catalog(db)
                    began = monotonic()
                    imported = catalog.sync('strategy', project / '.strategy', hardlink=True,
                                            protect=settings.get('protected_paths', []))
                    timings.update(catalog_ms=round((monotonic()-began)*1000),
                                   immutable_files_reused=imported['immutable_files_reused'])
                    began = monotonic()
                    result['continuity'] = project_state(db, settings, release, argv)
                    timings['projection_ms'] = round((monotonic()-began)*1000)
                if not readonly:
                    timings['writer_ms_before_commit'] = round((monotonic()-admitted)*1000)
                    result['service_timings'] = timings
                result_digest = db.put_blob(encode(result))
                db.execute("UPDATE operations SET status='COMMITTED',finished_ms=%s,result_digest=%s WHERE operation_id=%s",
                           (millis(), result_digest, operation))
            journal.unlink()
            return result
    finally:
        db.close()


def project_state(db, settings, release, argv=()):
    scripts = str(Path(release) / 'skill/btc-core-strategy/scripts')
    # Each service process is tied to an image/release. A different requested
    # method can execute in a subprocess, but projection uses this installed set.
    prior = getattr(project_state, '_scripts', None)
    if prior and prior != scripts:
        for name, module in list(sys.modules.items()):
            filename = getattr(module, '__file__', '') or ''
            if filename.startswith(prior + '/'):
                sys.modules.pop(name, None)
        if prior in sys.path: sys.path.remove(prior)
    if scripts not in sys.path: sys.path.insert(0, scripts)
    project_state._scripts = scripts
    projection = Projection(db, Path(release).name)
    project = Path(settings['project'])
    result = projection.refresh(project, project / '.strategy/v4')
    result['formal_market_binding'] = result.get('market_binding')
    result['market_binding'] = None
    directory = None
    if '--directory' in argv:
        directory = Path(argv[argv.index('--directory') + 1])
    elif '--run' in argv:
        directory = Path(argv[argv.index('--run') + 1]).parent
    if directory and not directory.is_absolute():
        directory = project / directory
    if directory and (directory / 'run.json').is_file():
        run = json.loads((directory / 'run.json').read_bytes())
        cutoff = run['created_at']
        public_path = directory / 'continuous-public.json'
        binding_path = directory / 'continuous-public-binding.json'
        if binding_path.exists():
            snapshot = json.loads(binding_path.read_bytes())
            public_path = directory / snapshot['file']
            if snapshot['as_of'] != cutoff or fingerprint(public_path.read_bytes()) != snapshot['digest']:
                raise Conflict('frozen continuous public context changed; preserve the original run')
        else:
            legacy = {'file': public_path.name, 'digest': fingerprint(public_path.read_bytes())} if public_path.exists() else None
            if legacy is not None:
                # An unbound dynamic index can omit an object retired later,
                # even when its remaining rows contain no future timestamps.
                # Preserve those original bytes and disclose reconstruction.
                public_path = directory / 'continuous-public-as-of.json'
            atomic(public_path, full_context(db, 'PUBLIC', as_of=cutoff))
            snapshot = {'schema': 'BTC_CONTINUOUS_PUBLIC_SNAPSHOT_V1', 'as_of': cutoff,
                        'file': public_path.name, 'digest': fingerprint(public_path.read_bytes()),
                        'legacy_context': legacy,
                        'origin': 'LEGACY_RECONSTRUCTED_AS_KNOWN' if legacy else 'RUN_AS_KNOWN_SNAPSHOT',
                        'component_binding': component_binding(release),
                        'role': 'HISTORICAL_JUDGEMENT_CONTEXT_NOT_NEW_EXTERNAL_EVIDENCE'}
            atomic(binding_path, snapshot)
        result['public_context'] = str(public_path)
        result['public_context_binding'] = snapshot
        frozen_path = directory / 'market-result.json'
        if frozen_path.exists():
            frozen = json.loads(frozen_path.read_bytes())
            known = frozen.get('created_at') or frozen.get('frozen_at')
            if known is None:
                from .codec import stamp
                from datetime import datetime, timezone
                known = stamp(datetime.fromtimestamp(frozen_path.stat().st_mtime, timezone.utc))
            entity = projection.put('DECISION', frozen['result_hash'], frozen, frozen_path, known, active=False)
            personal = full_context(db, 'PERSONAL')
            personal['market_binding'] = {k: entity[k] for k in ('object_id', 'version', 'digest')}
            result['market_binding'] = personal['market_binding']
            personal_path = directory / 'continuous-personal.json'
            atomic(personal_path, personal)
            result['personal_context'] = str(personal_path)
    return result


def full_context(db, scope, *, as_of=None):
    objects, after, values = Entities(db), None, []
    while True:
        page = objects.list(scope=scope, active=None if as_of is not None else True, limit=500, after=after)
        for row in page['objects']:
            value = objects.get(row['kind'], row['object_id'], as_of=as_of)
            if not value or not value['active'] or row['kind'] == 'REVIEW':
                continue
            recovery = value['payload'].get('recovery') or {} if row['kind'] == 'DELIVERY' else {}
            attempts = recovery.get('attempts', [])
            if attempts and not recovery.get('actions') and all(v.get('next_action') == 'WAIT_FOR_NEW_EVIDENCE' for v in attempts):
                continue
            values.append({name:value[name] for name in
                ('kind','object_id','scope','product','version','digest','known_ms','observed_ms')})
            values[-1]['content_path'] = str(db.blobs.path(value['digest']))
        after = page['next']
        if not after:
            break
    limit = millis(as_of) if as_of is not None else millis()
    requirements = db.rows('SELECT r.* FROM review_requirements r JOIN entities e USING(entity_key) '
                           "WHERE e.scope=%s AND e.kind NOT IN ('REVIEW','DECISION') AND r.opened_ms<=%s "
                           'AND (r.resolved_ms IS NULL OR r.resolved_ms>%s) ORDER BY r.opened_ms', (scope, limit, limit))
    if as_of is not None:
        # A later review does not close the obligation in an earlier view.
        # Keep the stored resolution; mask only what was not yet known.
        for row in requirements:
            if row['resolved_ms'] is not None and row['resolved_ms'] > limit:
                row['resolved_ms'] = None
                row['resolution_ref'] = None
    return {'scope': scope, 'as_of_ms': limit, 'objects': values, 'requirements': requirements,
            'role': 'HISTORICAL_JUDGEMENT_CONTEXT_NOT_NEW_EXTERNAL_EVIDENCE',
            'meaning': 'Version-pinned maintenance index as known at the stated time. Read immutable judgement contents and recheck against external evidence; a prior opinion is not a new fact.'}


def recover(settings, operation):
    db = Database(settings)
    journal = Path(settings['journal']) / (fingerprint(operation.encode()) + '.json')
    try:
        with db.writer(), db.transaction():
            record = db.one('SELECT * FROM operations WHERE operation_id=%s', (operation,))
            if not record:
                raise ValueError('unknown operation')
            if record['status'] == 'COMMITTED':
                return db.value(record['result_digest'])
            if not journal.is_file():
                raise Conflict('missing execution journal; inspect compatibility state before reconciliation')
            value = json.loads(journal.read_bytes())
            if value.get('mysql_native') is False and value.get('sqlite_prepared') and Path(settings['observation_db']).exists():
                from .market_store import import_sqlite
                import_sqlite(db, settings['observation_db'])
            readonly = ((value.get('value_reference_only') is True and
                         value_reference_only(value['request']['argv'])) or
                        (value.get('help_only') is True and help_only(value['request']['argv'])))
            if not readonly:
                Catalog(db).sync('strategy', Path(settings['project']) / '.strategy', hardlink=True)
                continuity = project_state(db, settings, value['request']['release'], value['request']['argv'])
            result = value.get('result') or {'returncode': 75, 'stdout': '', 'stderr':
                'Execution was interrupted. State preserved. Inspect the original run and delivery before a new operation.'}
            if not readonly:
                result['continuity'] = continuity
            result['recovery'] = 'STATE_PRESERVED_NO_REPLAY'
            result['recovery_component_binding'] = component_binding(value['request']['release'])
            digest = db.put_blob(encode(result))
            db.execute("UPDATE operations SET status='COMMITTED',finished_ms=%s,result_digest=%s WHERE operation_id=%s",
                       (millis(), digest, operation))
        journal.unlink(missing_ok=True)
        return result
    finally:
        db.close()
