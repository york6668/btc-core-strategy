"""Durable command admission and recovery for the unchanged domain validator.

No command sends a report. The host receives its result only after state and
projections are committed; ambiguous interrupted commands are never replayed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .catalog import Catalog
from .codec import Conflict, encode, fingerprint, millis, sync_directory
from .database import Database
from .entities import Entities
from .projection import Projection


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encode(value)); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path); sync_directory(path.parent)


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
    for option, required in (('--project', project), ('--root', project / '.strategy/v4')):
        if argv.count(option) != 1 or Path(argv[argv.index(option) + 1]).resolve() != required.resolve():
            raise ValueError('command belongs to another project')
    operation = request.get('operation_id')
    if not isinstance(operation, str) or not 0 < len(operation) <= 191:
        raise ValueError('stable operation_id required')
    request_digest = fingerprint(encode(params))
    for path, expected in params.get('input_hashes', {}).items():
        if fingerprint(Path(path).read_bytes()) != expected:
            raise Conflict('frozen command input hash mismatch')
    journal = Path(settings['journal']) / (fingerprint(operation.encode()) + '.json')
    db = Database(settings)
    try:
        with db.writer(timeout=600):
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
            mysql_native = 'connect_if_configured' in (release / 'skill/btc-core-strategy/scripts/market_observation.py').read_text()
            original = Path(settings['observation_db'])
            if not mysql_native and not original.exists() and db.one('SELECT 1 FROM mo_evidence LIMIT 1'):
                raise Conflict('restore the compatibility observation schema before resuming an old pinned release')
            with db.transaction():
                db.put_blob(encode(params))
                db.execute('INSERT INTO operations(operation_id,request_digest,status,started_ms,release_id) '
                           'VALUES(%s,%s,%s,%s,%s)', (operation, request_digest, 'EXECUTING', millis(), release.name))
            journal_state = {'operation_id': operation, 'request': params, 'status': 'EXECUTING',
                             'mysql_native': mysql_native, 'sqlite_prepared': False}
            atomic(journal, journal_state)
            if not mysql_native:
                from .market_store import export_sqlite
                if original.exists():
                    export_sqlite(db, original)
                journal_state['sqlite_prepared']=True
                atomic(journal,journal_state)
            environment = dict(os.environ, BTC_IN_CONTAINER='1', BTC_NODE_CONFIG=settings['container_node_config'],
                               BTC_SERVICE_CONFIG=settings['_path'], BTC_RELEASE=str(release), BTC_PROJECT=str(project),
                               BTC_PYTHON=sys.executable, PYTHONPATH='/opt/btc')
            command = [sys.executable, str(release / 'skill/btc-core-strategy/scripts/strategy_v4.py'), *argv]
            process = subprocess.run(command, env=environment, capture_output=True, text=True)
            result = {'returncode': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}
            atomic(journal, {**journal_state, 'status': 'EXECUTED', 'result': result})
            with db.transaction():
                if not mysql_native and Path(settings['observation_db']).exists():
                    from .market_store import import_sqlite
                    import_sqlite(db, settings['observation_db'])
                # Rows in the hourly market store already use MySQL. Remaining
                # immutable files and pointer changes become a recovery manifest.
                catalog = Catalog(db)
                catalog.sync('strategy', project / '.strategy', hardlink=True,
                             protect=settings.get('protected_paths', []))
                result['continuity'] = project_state(db, settings, release, argv)
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
    if directory and (directory / 'run.json').is_file():
        public = full_context(db, 'PUBLIC')
        public_path = directory / 'continuous-public.json'
        atomic(public_path, public)
        result['public_context'] = str(public_path)
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


def full_context(db, scope):
    objects, after, values = Entities(db), None, []
    while True:
        page = objects.list(scope=scope, limit=500, after=after)
        for row in page['objects']:
            value=objects.get(row['kind'],row['object_id'])
            # Every run gets a bounded index, not another full copy of all
            # unchanged analyses. Original typed state stays in immutable CAS.
            values.append({name:value[name] for name in
                ('kind','object_id','scope','product','version','digest','known_ms','observed_ms')})
            values[-1]['content_path']=str(db.blobs.path(value['digest']))
        after = page['next']
        if not after:
            break
    requirements = db.rows('SELECT r.* FROM review_requirements r JOIN entities e USING(entity_key) '
                           'WHERE e.scope=%s AND r.resolved_ms IS NULL ORDER BY r.opened_ms', (scope,))
    return {'scope': scope, 'objects': values, 'requirements': requirements,
            'meaning': 'Complete maintenance index. Read immutable content_path or object.get at the listed version before judging; report visibility does not determine maintenance.'}


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
            Catalog(db).sync('strategy', Path(settings['project']) / '.strategy', hardlink=True)
            continuity = project_state(db, settings, value['request']['release'])
            result = value.get('result') or {'returncode': 75, 'stdout': '', 'stderr':
                'Execution was interrupted. State preserved. Inspect the original run and delivery before a new operation.'}
            result['continuity'] = continuity
            result['recovery'] = 'STATE_PRESERVED_NO_REPLAY'
            digest = db.put_blob(encode(result))
            db.execute("UPDATE operations SET status='COMMITTED',finished_ms=%s,result_digest=%s WHERE operation_id=%s",
                       (millis(), digest, operation))
        journal.unlink(missing_ok=True)
        return result
    finally:
        db.close()
