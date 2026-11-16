"""Bind official durable author events without inventing unexposed runtime fields."""
import json
from pathlib import Path
import re
import shlex
import hashlib
import os

from .common import digest, exact, require, text


SCHEMA = 'BTC_DURABLE_AUTHOR_PROOF_V1'
MAIN_SCHEMA = 'BTC_DURABLE_MAIN_PROOF_V1'
SCOPE = 'DURABLE_AUTHOR_FINAL_AND_MAIN_INPUT_REVIEW'
MAIN_SCOPE = 'DURABLE_MAIN_THREAD_TURN_AND_RUN_ENTRY'
PREVIEW_SCHEMA = 'BTC_PREVIEW_EXECUTION_PROOF_V1'
PREVIEW_SCOPE = 'ISOLATED_PREVIEW_EXECUTION'
NATIVE_SCHEMA = 'BTC_DURABLE_NATIVE_CONTINUATION_PROOF_V1'


def location_key(path):
    """Keep the origin's lexical path; /tmp aliases differ across hosts."""
    path = Path(path)
    require(path.is_absolute(), 'native proof origin must be absolute')
    original = os.path.normpath(str(path))
    return original, hashlib.sha256(original.encode('utf-8')).hexdigest()


def resolve_path(path):
    """Find transported original bytes, without rewriting immutable records.

    Existing originals (including damaged files/links) are never hidden by an
    archive. The configured location only supplies a missing durable manifest;
    the caller still verifies every source, author, event and expected hash.
    """
    path = Path(path)
    if path.exists() or path.is_symlink() or not path.is_absolute():
        return path
    import node_config
    config = node_config.load()
    directory = config.get('native_proof_dir') or config.get('docker_service', {}).get('native_proof_dir')
    if not directory:
        return path
    root = Path(directory)
    require(root.is_absolute(), 'native proof archive must be absolute')
    original, key = location_key(path)
    parent = root / 'locations' / key
    require(not root.is_symlink() and not (root / 'locations').is_symlink() and not parent.is_symlink(),
            'native proof location must not be linked')
    marker = parent / 'location.json'
    if not marker.exists() and not marker.is_symlink():
        return path
    require(marker.is_file() and not marker.is_symlink() and marker.stat().st_size <= 4096,
            'native proof location invalid')
    location = json.loads(marker.read_bytes())
    exact(location, 'schema original_path proof_name proof_hash', 'native proof location')
    require(location['schema'] == 'BTC_NATIVE_PROOF_LOCATION_V1'
            and location['original_path'] == original, 'native proof origin mismatch')
    name = Path(location['proof_name'])
    require(len(name.parts) == 1 and name.name not in ('', '.', '..', 'location.json')
            and not name.is_absolute(), 'native proof archive name invalid')
    archived = parent / name
    require(archived.is_file() and not archived.is_symlink()
            and 0 < archived.stat().st_size <= 16384, 'archived native proof missing or invalid')
    require(digest(archived.read_bytes()) == location['proof_hash'], 'archived native proof bytes changed')
    return archived


def is_manifest(path):
    # Native JSONL is classified from its first record. Only a small standalone
    # manifest may be read to the end, without imposing JSON property order.
    path = resolve_path(path)
    with path.open('rb') as stream:
        raw = stream.readline(16385)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            if Path(path).stat().st_size > 16384:
                return False
            while len(raw) <= 16384:
                line = stream.readline(16385 - len(raw))
                if not line:
                    break
                raw += line
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                return False
    return isinstance(value, dict) and value.get('schema') in (SCHEMA, MAIN_SCHEMA, PREVIEW_SCHEMA, NATIVE_SCHEMA)


def _reference_file(directory, reference, *, maximum=32 * 1024 * 1024):
    exact(reference, 'path sha256', 'durable source reference')
    require(isinstance(reference['sha256'], str) and re.fullmatch(r'sha256:[a-f0-9]{64}', reference['sha256']),
            'durable source byte digest required')
    name = Path(reference['path'])
    require(not name.is_absolute() and len(name.parts) == 1
            and name.name == reference['sha256'][7:] + '.json', 'durable source must be a content-addressed sibling file')
    path = directory / name
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= maximum,
            'durable source file missing or invalid')
    raw = path.read_bytes()
    require(digest(raw) == reference['sha256'], 'durable source bytes changed')
    return path, raw


def _document(directory, reference, *, expected_host='durable'):
    _, raw = _reference_file(directory, reference)
    value = json.loads(raw)
    require(value.get('schemaVersion') == 1 and isinstance(value.get('turns'), list),
            'official durable thread response required')
    thread = value.get('thread')
    require(isinstance(thread, dict) and thread.get('kind') == 'codex'
            and isinstance(thread.get('hostId'), str) and bool(thread['hostId'])
            and (expected_host is None or thread['hostId'] == expected_host)
            and isinstance(thread.get('id'), str),
            'durable source thread identity required')
    return value


def source_files(path):
    """Validate and expose only the two immutable sources for the existing bridge."""
    path = Path(path)
    manifest = json.loads(path.read_bytes())
    if manifest.get('schema') == PREVIEW_SCHEMA:
        preview_identity(path, manifest['claimed_agent_id'], manifest['run_ref'])
        return [path.parent / manifest[key]['path'] for key in ('receipt', 'node_config')]
    if manifest.get('schema') == MAIN_SCHEMA:
        _main_source(path, manifest)
        return [path.parent / manifest['thread']['path']]
    if manifest.get('schema') == NATIVE_SCHEMA:
        from . import native_author_proof
        return native_author_proof.source_files(path, manifest)
    exact(manifest, 'schema provider parent child parent_turn_id child_turn_id', 'durable proof manifest')
    require(manifest['schema'] == SCHEMA and manifest['provider'] == 'mcp__codex_app__read_thread',
            'unsupported durable source provider')
    for key in ('parent', 'child'):
        _document(path.parent, manifest[key])
    return [path.parent / manifest[key]['path'] for key in ('parent', 'child')]


def _one(values, label):
    require(len(values) == 1, 'durable proof needs unique ' + label)
    return values[0]


def _main_source(path, manifest, *, historical=False):
    exact(manifest, 'schema provider thread thread_id turn_id entry_item_id run_ref', 'durable MAIN proof')
    require(manifest['schema'] == MAIN_SCHEMA and manifest['provider'] == 'mcp__codex_app__read_thread',
            'unsupported MAIN source provider')
    source = _document(path.parent, manifest['thread'])
    require(source['thread']['id'] == manifest['thread_id'], 'MAIN source thread mismatch')
    _, turn = _one([(i, t) for i, t in enumerate(source['turns'])
                    if t.get('id') == manifest['turn_id']], 'MAIN turn')
    require(turn.get('status') in ('inProgress', 'completed') and turn.get('error') is None,
            'MAIN turn must be active or successfully completed')
    # Missing fields remain missing. A source that exposes configuration must
    # use the ordinary known-configuration branch, never hide a conflict here.
    for value in (source['thread'], turn):
        require(all(value.get(k) is None for k in
                    ('model', 'modelName', 'effort', 'reasoningEffort', 'reasoning_effort')),
                'known runtime configuration cannot be recorded as unknown')
    items = turn.get('items')
    require(isinstance(items, list), 'MAIN source items missing')
    if not any(item.get('type') == 'commandExecution' for item in items):
        # In-progress projections may lag execution. Absence in one response
        # cannot distinguish delayed visibility, clipping and unsupported data.
        require(False, 'MAIN_SOURCE_RECORD_NOT_VISIBLE: this official thread snapshot has no command execution records; '
                'delegation delivery, standalone command output and an ACTIVE node lease cannot establish '
                'same-thread/turn execution. Missing records do not prove permanent host incapability; '
                'use a supported bounded read of an available later or completed source, '
                'without fabricating an entry id, polling indefinitely or repeating business execution')
    item = _one([i for i in items if i.get('id') == manifest['entry_item_id']],
                'successful MAIN run entry')
    require(item.get('type') == 'commandExecution' and item.get('status') == 'completed'
            and item.get('exitCode') == 0 and not item.get('truncated'), 'MAIN run entry did not succeed')
    command = shlex.split(item.get('command', ''))
    if len(command) == 3 and command[1] in ('-c', '-lc'):
        require(len(command[2].splitlines()) == 1, 'MAIN entry must be one standalone command')
        command = shlex.split(command[2])
    # Bind the node-owned wrapper, not any same-named Python script. Its
    # successful JSON result binds the immutable release selected by enter.
    output = item.get('output')
    require(isinstance(output, dict) and output.get('truncated') is False
            and isinstance(output.get('text'), str), 'MAIN entry needs its complete actual output')
    result = json.loads(output['text'])
    if historical:
        # A frozen completion belongs to the node where its real command ran,
        # not the node currently reading it. Keep command/output/run/author
        # bindings; this branch cannot admit a new current-node author.
        release = Path(result.get('release', ''))
        require(release.is_absolute(), 'historical MAIN release must be absolute')
        runtime = release.parent.parent
    else:
        import node_config
        config = node_config.load()
        require(isinstance(config.get('release_root'), str), 'MAIN source needs the configured node runtime')
        runtime = Path(config['release_root'])
    binding = result.get('node_path_binding')
    if binding is not None:
        exact(binding, 'release_root resolved_release_root', 'original node path binding')
        require(binding['release_root'] == str(runtime)
                and Path(binding['resolved_release_root']).is_absolute(),
                'original node path binding differs from its release')
        if not historical:
            require(binding['resolved_release_root'] == str(runtime.resolve()),
                    'current node path binding changed')
    # Old non-alias sources retain their literal origin relationship. An old
    # alias source without a producer-recorded binding cannot be proven on a
    # different host; never reconstruct that relationship from this host.
    expected_node = (str(Path(binding['resolved_release_root']) / 'node.json')
                     if historical and binding is not None else
                     str(runtime / 'node.json') if historical else
                     str((runtime / 'node.json').resolve()))
    require(len(command) in (4, 6) and command[:4] ==
            [str(runtime / 'bin/btc-core'), 'enter', '--run-id', manifest['run_ref']]
            and (len(command) == 4 or (command[4] == '--resume-context'
                 and re.fullmatch(r'sha256:[a-f0-9]{64}', command[5]))),
            'MAIN source does not bind the declared normal run entry')
    require(result.get('run_id') == manifest['run_ref'] and result.get('status') == 'ACTIVE'
            and result.get('entry_action') in ('START_NEW_RUN', 'RESUME_ORIGINAL_RUN')
            and re.fullmatch(r'[a-f0-9]{40}', result.get('commit', ''))
            and result.get('release') == str(runtime / 'releases' / result['commit'])
            and result.get('node_config') == expected_node,
            'MAIN entry result does not bind this node, active run and fixed release')
    return source, turn


def main_identity(path, agent_id, run_ref, *, historical=False):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384,
            'durable MAIN proof manifest invalid')
    manifest = json.loads(path.read_bytes())
    _main_source(path, manifest, historical=historical)
    require(manifest['run_ref'] == run_ref, 'durable MAIN proof belongs to another run')
    require(manifest['thread_id'] == agent_id, 'durable MAIN agent identity must be its actual thread id')
    return {'thread_id': manifest['thread_id'], 'turn_id': manifest['turn_id'],
            'runtime_configuration_verified': False}


def code_fingerprint(directory):
    """Identify actual validation code, separately from the pinned node lease."""
    root = Path(directory).resolve()
    names = json.loads((root / 'export-manifest.json').read_bytes())['files']
    require(isinstance(names, list) and names and len(names) == len(set(names)), 'invalid validation code manifest')
    hashes = {}
    for name in ['export-manifest.json', *names]:
        relative = Path(name)
        source = root / relative
        require(not relative.is_absolute() and '..' not in relative.parts and source.is_file()
                and not source.is_symlink() and root in source.resolve().parents, 'unsafe validation code source')
        hashes[name] = digest(source.read_bytes())
    return digest(hashes)


def preview_identity(path, agent_id, run_ref, *, project=None):
    """Check execution/lease/code bindings without asserting platform attribution.

    This result is usable by non-adopting validation consumers only. The tool
    request and caller name are recorded claims, not an authenticated call ID.
    Hashes protect saved bytes; they do not turn those claims into attestation.
    """
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384, 'preview execution proof missing')
    manifest = json.loads(path.read_bytes())
    exact(manifest, 'schema provider scope receipt node_config command claimed_agent_id run_ref validation_code attribution runtime_configuration', 'preview execution proof')
    require(manifest['schema'] == PREVIEW_SCHEMA and manifest['scope'] == PREVIEW_SCOPE
            and manifest['provider'] == 'functions.exec_command', 'unsupported preview execution source')
    require(manifest['claimed_agent_id'] == agent_id and manifest['run_ref'] == run_ref, 'preview execution identity mismatch')
    exact(manifest['attribution'], 'verified thread_id turn_id call_id limitation', 'preview attribution')
    a = manifest['attribution']
    require(a['verified'] is False and all(a[k] is None for k in ('thread_id', 'turn_id', 'call_id')),
            'preview execution cannot assert platform caller attribution')
    text(a['limitation'], 'unverified caller limitation')
    require(manifest['runtime_configuration'] == {'model': None, 'effort': None, 'verified': False},
            'preview execution cannot assert runtime model configuration')
    def saved(key):
        ref = manifest[key]
        exact(ref, 'path sha256', 'preview ' + key)
        require(isinstance(ref['sha256'], str) and re.fullmatch(r'sha256:[a-f0-9]{64}', ref['sha256'])
                and ref['path'] == ref['sha256'][7:] + '.json', 'preview source must be content-addressed sibling')
        source = path.parent / ref['path']
        require(source.is_file() and not source.is_symlink() and source.stat().st_size <= 65536, 'preview source missing or invalid')
        raw = source.read_bytes()
        require(digest(raw) == ref['sha256'], 'preview source bytes changed')
        return json.loads(raw)
    receipt, config = saved('receipt'), saved('node_config')
    require(type(receipt.get('exit_code')) is int and receipt['exit_code'] == 0 and isinstance(receipt.get('output'), str)
            and 'session_id' not in receipt, 'preview entry must actually complete successfully')
    text(receipt.get('chunk_id'), 'actual execution receipt chunk')
    result = json.loads(receipt['output'])
    import node_config
    current = node_config.load()
    require(current and config == current, 'preview node configuration changed')
    runtime = Path(config['release_root'])
    require(manifest['command'] == [str(runtime / 'bin/btc-core'), 'enter', '--run-id', run_ref],
            'preview recorded request must use the normal node entry')
    require(result.get('run_id') == run_ref and result.get('status') == 'ACTIVE'
            and result.get('entry_action') in ('START_NEW_RUN', 'RESUME_ORIGINAL_RUN')
            and isinstance(result.get('commit'), str) and re.fullmatch(r'[a-f0-9]{40}', result['commit'])
            and result.get('release') == str(runtime / 'releases' / result['commit'])
            and result.get('node_config') == str((runtime / 'node.json').resolve())
            and result.get('project_dir') == config['project_dir'], 'preview entry node/run/release mismatch')
    require(result.get('node_path_binding') == {'release_root': str(runtime), 'resolved_release_root': str(runtime.resolve())},
            'preview node path binding changed')
    if project is not None:
        require(Path(project).resolve() == Path(config['project_dir']).resolve(), 'preview belongs to another project')
    import hashlib
    lease = json.loads((runtime / 'runs' / (hashlib.sha256(run_ref.encode()).hexdigest() + '.json')).read_bytes())
    require(all(lease.get(k) == result.get(k) for k in ('run_id', 'commit', 'release', 'node_config', 'project_dir', 'status')),
            'preview run lease changed')
    code = manifest['validation_code']
    exact(code, 'root fingerprint', 'preview validation code')
    actual = Path(__file__).resolve().parents[4]
    require(Path(code['root']).resolve() == actual and code_fingerprint(actual) == code['fingerprint'],
            'preview validation code changed')
    return {'scope': PREVIEW_SCOPE, 'execution': 'SUCCESSFUL_TOOL_RECEIPT', 'run_ref': run_ref,
            'node_commit': result['commit'], 'validation_code': code,
            'platform_attribution': a, 'runtime_configuration_verified': False,
            'adoption_eligible': False}


def turn(path, dispatch_id, return_id, method, agent_id=None):
    path = resolve_path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384,
            'durable proof manifest invalid')
    manifest = json.loads(path.read_bytes())
    if manifest.get('schema') == NATIVE_SCHEMA:
        from . import native_author_proof
        return native_author_proof.turn(path, manifest, dispatch_id, return_id, method, agent_id)
    exact(manifest, 'schema provider parent child parent_turn_id child_turn_id', 'durable proof manifest')
    require(manifest['schema'] == SCHEMA and manifest['provider'] == 'mcp__codex_app__read_thread',
            'unsupported durable source provider')
    parent = _document(path.parent, manifest['parent'])
    child = _document(path.parent, manifest['child'])
    require(parent['thread']['id'] != child['thread']['id'], 'durable author must be a separate thread')
    parent_index, pt = _one([(n, t) for n, t in enumerate(parent['turns'])
                            if t.get('id') == manifest['parent_turn_id']], 'parent turn')
    child_index, ct = _one([(n, t) for n, t in enumerate(child['turns'])
                           if t.get('id') == manifest['child_turn_id']], 'author turn')
    require(isinstance(pt.get('items'), list) and isinstance(ct.get('items'), list),
            'durable source items missing')
    start_index, start = _one([(n, i) for n, i in enumerate(pt['items'])
                              if i.get('id') == dispatch_id], 'dispatch activity')
    require(method in ('spawn_agent', 'followup_task'), 'unsupported durable phase method')
    require(start.get('type') == 'subAgentActivity'
            and start.get('kind') == ('started' if method == 'spawn_agent' else 'interacted')
            and start.get('agentThreadId') == child['thread']['id'],
            'durable activity does not identify this author phase')
    author = start.get('agentPath')
    require(isinstance(author, str) and author.startswith('/'), 'durable author path missing')
    require(method == 'spawn_agent' or agent_id == author, 'durable follow-up must use the same author')
    end_index, ended = _one([(n, i) for n, i in enumerate(pt['items'])
                            if i.get('id') == 'subagent-completed-' + ct['id']], 'author completion activity')
    require(ended.get('type') == 'subAgentActivity' and ended.get('kind') == 'completed'
            and ended.get('agentThreadId') == child['thread']['id'] and ended.get('agentPath') == author
            and start_index < end_index, 'durable phase completion does not match dispatch')
    require(not any(i.get('type') == 'subAgentActivity' and i.get('kind') == 'completed'
                    and i.get('agentThreadId') == child['thread']['id']
                    for i in pt['items'][start_index + 1:end_index]),
            'earlier completed durable phase must be registered first')
    require(ct.get('status') == 'completed' and ct.get('error') is None,
            'durable author turn did not complete successfully')
    began, completed = ct.get('startedAt'), ct.get('completedAt')
    require(type(began) in (int, float) and type(completed) in (int, float)
            and 0 < began <= completed, 'durable author clocks missing or reversed')
    final_index, final = _one([(n, i) for n, i in enumerate(ct['items'])
                              if i.get('type') == 'agentMessage' and i.get('phase') == 'final_answer'],
                             'actual final answer')
    require(final.get('id') == return_id and final.get('truncated') is not True,
            'durable final identity invalid or truncated')
    analysis = final.get('text')
    text(analysis, 'actual durable final analysis')
    continuations = [{'call_id': i['id'], 'hash': digest(i)} for i in pt['items'][start_index + 1:end_index]
                     if i.get('type') == 'subAgentActivity' and i.get('kind') == 'interacted'
                     and i.get('agentThreadId') == child['thread']['id']]
    # The reader exposes author identity and exact output, but not the original
    # inbound messages, model configuration or context inheritance. Keep those
    # limitations explicit; the guard claim is admission, not runtime telemetry.
    verification = {'provider': manifest['provider'], 'host_id': 'durable',
                    'parent_thread_id': parent['thread']['id'], 'author_thread_id': child['thread']['id'],
                    'author_turn_id': ct['id'], 'runtime_model': None, 'runtime_effort': None,
                    'runtime_fork_turns': None, 'runtime_configuration_verified': False,
                    'inbound_messages_verified': False,
                    'dispatch_pointer': f'/turns/{parent_index}/items/{start_index}',
                    'return_pointer': f'/turns/{child_index}/items/{final_index}',
                    'input_verification': 'MAIN_INPUT_REVIEW_WITH_AVAILABLE_SOURCE_READS'}
    return {'agent_id': author, 'dispatch_call_id': dispatch_id, 'return_message_id': return_id,
            'started_at': began, 'completed_at': completed, 'analysis': analysis,
            'dispatch_line': None, 'return_line': None,
            'dispatch_hash': digest({'source': manifest['parent'], 'turn_id': pt['id'], 'item': start}),
            'return_hash': digest({'source': manifest['child'], 'turn_id': ct['id'], 'item': final,
                                   'started_at': began, 'completed_at': completed}),
            'continuations': continuations, 'verification_scope': SCOPE,
            'source_verification': verification}
