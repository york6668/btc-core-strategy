"""Bind official durable author events without inventing unexposed runtime fields."""
import json
from pathlib import Path
import re
import shlex

from .common import digest, exact, require, text


SCHEMA = 'BTC_DURABLE_AUTHOR_PROOF_V1'
MAIN_SCHEMA = 'BTC_DURABLE_MAIN_PROOF_V1'
SCOPE = 'DURABLE_AUTHOR_FINAL_AND_MAIN_INPUT_REVIEW'
MAIN_SCOPE = 'DURABLE_MAIN_THREAD_TURN_AND_RUN_ENTRY'


def is_manifest(path):
    # Native JSONL is classified from its first record. Only a small standalone
    # manifest may be read to the end, without imposing JSON property order.
    with Path(path).open('rb') as stream:
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
    return isinstance(value, dict) and value.get('schema') in (SCHEMA, MAIN_SCHEMA)


def _document(directory, reference):
    exact(reference, 'path sha256', 'durable source reference')
    require(isinstance(reference['sha256'], str) and re.fullmatch(r'sha256:[a-f0-9]{64}', reference['sha256']),
            'durable source byte digest required')
    name = Path(reference['path'])
    require(not name.is_absolute() and len(name.parts) == 1
            and name.name == reference['sha256'][7:] + '.json', 'durable source must be a content-addressed sibling file')
    path = directory / name
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 32 * 1024 * 1024,
            'durable source file missing or invalid')
    raw = path.read_bytes()
    require(digest(raw) == reference['sha256'], 'durable source bytes changed')
    value = json.loads(raw)
    require(value.get('schemaVersion') == 1 and isinstance(value.get('turns'), list),
            'official durable thread response required')
    thread = value.get('thread')
    require(isinstance(thread, dict) and thread.get('kind') == 'codex'
            and thread.get('hostId') == 'durable' and isinstance(thread.get('id'), str),
            'durable source thread identity required')
    return value


def source_files(path):
    """Validate and expose only the two immutable sources for the existing bridge."""
    path = Path(path)
    manifest = json.loads(path.read_bytes())
    if manifest.get('schema') == MAIN_SCHEMA:
        _main_source(path, manifest)
        return [path.parent / manifest['thread']['path']]
    exact(manifest, 'schema provider parent child parent_turn_id child_turn_id', 'durable proof manifest')
    require(manifest['schema'] == SCHEMA and manifest['provider'] == 'mcp__codex_app__read_thread',
            'unsupported durable source provider')
    for key in ('parent', 'child'):
        _document(path.parent, manifest[key])
    return [path.parent / manifest[key]['path'] for key in ('parent', 'child')]


def _one(values, label):
    require(len(values) == 1, 'durable proof needs unique ' + label)
    return values[0]


def _main_source(path, manifest):
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
    item = _one([i for i in turn.get('items', []) if i.get('id') == manifest['entry_item_id']],
                'successful MAIN run entry')
    require(item.get('type') == 'commandExecution' and item.get('status') == 'completed'
            and item.get('exitCode') == 0 and not item.get('truncated'), 'MAIN run entry did not succeed')
    command = shlex.split(item.get('command', ''))
    if len(command) == 3 and command[1] in ('-c', '-lc'):
        require(len(command[2].splitlines()) == 1, 'MAIN entry must be one standalone command')
        command = shlex.split(command[2])
    # Bind the node-owned wrapper, not any same-named Python script. Its
    # successful JSON result binds the immutable release selected by enter.
    import node_config
    config = node_config.load()
    require(isinstance(config.get('release_root'), str), 'MAIN source needs the configured node runtime')
    runtime = Path(config['release_root'])
    require(len(command) in (4, 6) and command[:4] ==
            [str(runtime / 'bin/btc-core'), 'enter', '--run-id', manifest['run_ref']]
            and (len(command) == 4 or (command[4] == '--resume-context'
                 and re.fullmatch(r'sha256:[a-f0-9]{64}', command[5]))),
            'MAIN source does not bind the declared normal run entry')
    output = item.get('output')
    require(isinstance(output, dict) and output.get('truncated') is False
            and isinstance(output.get('text'), str), 'MAIN entry needs its complete actual output')
    result = json.loads(output['text'])
    require(result.get('run_id') == manifest['run_ref'] and result.get('status') == 'ACTIVE'
            and result.get('entry_action') in ('START_NEW_RUN', 'RESUME_ORIGINAL_RUN')
            and re.fullmatch(r'[a-f0-9]{40}', result.get('commit', ''))
            and result.get('release') == str(runtime / 'releases' / result['commit'])
            and result.get('node_config') == str(runtime / 'node.json'),
            'MAIN entry result does not bind this node, active run and fixed release')
    return source, turn


def main_identity(path, agent_id, run_ref):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384,
            'durable MAIN proof manifest invalid')
    manifest = json.loads(path.read_bytes())
    _main_source(path, manifest)
    require(manifest['run_ref'] == run_ref, 'durable MAIN proof belongs to another run')
    require(manifest['thread_id'] == agent_id, 'durable MAIN agent identity must be its actual thread id')
    return {'thread_id': manifest['thread_id'], 'turn_id': manifest['turn_id'],
            'runtime_configuration_verified': False}


def turn(path, dispatch_id, return_id, method, agent_id=None):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384,
            'durable proof manifest invalid')
    manifest = json.loads(path.read_bytes())
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
