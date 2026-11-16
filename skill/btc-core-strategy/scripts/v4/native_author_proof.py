"""Read-only native continuation evidence; never sends, claims or adopts.

Matches actual official send arguments to typed userMessage.codexDelegation
inside the receiving turn. The send receipt need not contain a turnId. Textual
labels, standalone caller-written receipts and time matching are not identity.
Delivery, original authorship, FINAL and runtime configuration remain separate.
"""
import json
import math

from .common import digest, exact, require, text


KEYS = ('schema provider parent child parent_turn_id child_turn_id original '
        'dispatch_call_id return_message_id')


def _original(path, manifest):
    from . import durable_proof as d
    exact(manifest, KEYS, 'native continuation proof')
    require(manifest['schema'] == d.NATIVE_SCHEMA
            and manifest['provider'] == 'mcp__codex_app__read_thread', 'unsupported native source provider')
    old = manifest['original']
    exact(old, 'path sha256 dispatch_call_id return_message_id', 'original PUBLIC proof reference')
    original_path, raw = d._reference_file(path.parent, {k: old[k] for k in ('path', 'sha256')}, maximum=16384)
    # Anchor directly to the actual original collaboration PUBLIC. Do not
    # recurse through self-declared aliases or manufacture a fresh author.
    require(json.loads(raw).get('schema') == d.SCHEMA, 'original collaboration PUBLIC proof required')
    proof = d.turn(original_path, old['dispatch_call_id'], old['return_message_id'], 'spawn_agent')
    return original_path, proof


def _result_consistency(call, child, turn_id):
    """An omitted result does not erase a proven native receiver message.

    Available explicit failures or conflicting identifiers still reject. A
    result is never used to invent a turn ID or as the sole delivery proof.
    """
    if 'result' not in call:
        return
    result = call['result']
    require(isinstance(result, dict), 'malformed official native result')
    for value in (result, result.get('structuredContent', {})):
        require(isinstance(value, dict) and value.get('isError') is not True
                and value.get('error') is None and value.get('truncated') is not True
                and value.get('success') is not False and value.get('accepted') is not False
                and value.get('status', 'completed') in ('accepted', 'queued', 'started', 'completed'),
                'official native result explicitly failed or is incomplete')
        for key, expected in (('threadId', child['thread']['id']), ('hostId', child['thread']['hostId']),
                              ('turnId', turn_id), ('callId', call['id'])):
            require(key not in value or value[key] == expected, 'official native result contradicts reception')


def delivery(parent, child, parent_turn_id, child_turn_id, dispatch_id):
    """Bind an actual send to typed native ingress, independently of FINAL.

    This function also supports read-only inspection of an active coordination
    turn. It does not verify original research authorship, completion, model,
    authority or guard admission. Those remain distinct checks in turn/callers.
    Plain text XML/JSON labels and legacy tool-output wrappers are not identity.
    """
    from . import durable_proof as d
    for source in (parent, child):
        require(source.get('schemaVersion') == 1 and isinstance(source.get('turns'), list)
                and isinstance(source.get('thread'), dict) and source['thread'].get('kind') == 'codex'
                and source.get('truncated') is not True and source['thread'].get('truncated') is not True,
                'complete official native source required')
        for key in ('id', 'hostId'):
            text(source['thread'].get(key), 'official source thread ' + key)
    require(parent['thread']['id'] != child['thread']['id'], 'native self-send is not author continuation')
    pi, pt = d._one([(n, t) for n, t in enumerate(parent['turns']) if t.get('id') == parent_turn_id], 'native parent turn')
    ci, ct = d._one([(n, t) for n, t in enumerate(child['turns']) if t.get('id') == child_turn_id], 'native receiver turn')
    require(isinstance(pt.get('items'), list) and isinstance(ct.get('items'), list)
            and pt.get('truncated') is not True and ct.get('truncated') is not True,
            'native source items unavailable or truncated')
    ii, call = d._one([(n, i) for n, i in enumerate(pt['items']) if i.get('id') == dispatch_id], 'native dispatch call')
    require(call.get('type') == 'mcpToolCall' and call.get('server') == 'codex_app'
            and call.get('tool') == 'send_message_to_thread' and call.get('status') == 'completed'
            and call.get('error') is None and call.get('truncated') is not True
            and call.get('argumentsTruncated') is not True, 'native dispatch failed or is not an official send call')
    args = call.get('arguments')
    require(isinstance(args, dict) and set(args) <= {'hostId','threadId','prompt','model','thinking'}
            and args.get('threadId') == child['thread']['id']
            and args.get('hostId', parent['thread']['hostId']) == child['thread']['hostId'],
            'native dispatch targets a different receiver or host')
    text(args.get('prompt'), 'actual native dispatch prompt')
    _result_consistency(call, child, ct['id'])
    def candidates(item):
        if item.get('type') != 'userMessage':
            return []
        content = item.get('content')
        if not isinstance(content, list):
            return []
        return [part for part in content if isinstance(part, dict) and part.get('type') == 'text'
                and isinstance(part.get('codexDelegation'), dict)
                and part['codexDelegation'].get('sourceThreadId') == parent['thread']['id']
                and part['codexDelegation'].get('input') == args['prompt']]
    mi, message, part = d._one([(n, i, p) for n, i in enumerate(ct['items']) for p in candidates(i)],
                               'structured native received message')
    require(len(message['content']) == 1 and message.get('truncated') is not True
            and part.get('truncated') is not True and set(part['codexDelegation']) == {'sourceThreadId','input'},
            'native received message incomplete or truncated')
    text(message.get('id'), 'native received message id')
    # Uniqueness is scoped to the captured official source window. Missing
    # historical input visibility is never promoted to whole-history proof.
    require(sum(len(candidates(i)) for t in child['turns'] for i in t.get('items', [])) == 1,
            'native input has ambiguous receiver-turn association')
    related = [i for t in parent['turns'] for i in t.get('items', [])
               if i.get('type') == 'mcpToolCall' and i.get('server') == 'codex_app'
               and i.get('tool') == 'send_message_to_thread' and isinstance(i.get('arguments'), dict)
               and i['arguments'].get('threadId') == child['thread']['id']
               and i['arguments'].get('hostId', parent['thread']['hostId']) == child['thread']['hostId']
               and i['arguments'].get('prompt') == args['prompt']]
    require(len(related) == 1, 'native input has ambiguous send-call association')
    return {'parent_index': pi, 'parent_turn': pt, 'call_index': ii, 'call': call,
            'child_index': ci, 'child_turn': ct, 'received_index': mi, 'received_message': message,
            'association': 'OFFICIAL_SEND_AND_STRUCTURED_NATIVE_INGRESS',
            'association_scope': 'CAPTURED_OFFICIAL_SOURCE_WINDOW', 'received_input_hash': digest(args['prompt'])}


def turn(path, manifest, dispatch_id, return_id, method, agent_id=None):
    from . import durable_proof as d
    require(method == 'followup_task', 'native proof is continuation only, not independent PUBLIC')
    original_path, original = _original(path, manifest)
    require(dispatch_id == manifest['dispatch_call_id'] and return_id == manifest['return_message_id'],
            'native proof call/final binding differs')
    require(return_id != original['return_message_id'], 'native continuation cannot replay the original PUBLIC FINAL')
    require(agent_id is None or agent_id == original['agent_id'], 'native continuation original author differs')
    parent = d._document(path.parent, manifest['parent'], expected_host=None)
    child = d._document(path.parent, manifest['child'])
    author_thread = original['source_verification']['author_thread_id']
    require(child['thread']['id'] == author_thread, 'native continuation must target the exact original author thread')
    received = delivery(parent, child, manifest['parent_turn_id'], manifest['child_turn_id'], dispatch_id)
    pi, pt = received['parent_index'], received['parent_turn']
    ci, ct = received['child_index'], received['child_turn']
    ii, call = received['call_index'], received['call']
    args = call['arguments']
    require(args.get('model') in (None, 'gpt-6-astra') and args.get('thinking') in (None, 'max'),
            'native author configuration conflicts with research contract')
    require(ct['id'] != original['source_verification']['author_turn_id']
            and ct.get('status') == 'completed' and ct.get('error') is None,
            'native continuation needs a new successfully completed author turn')
    began, completed, parent_began = ct.get('startedAt'), ct.get('completedAt'), pt.get('startedAt')
    require(all(type(v) in (int, float) and math.isfinite(v) for v in (began, completed, parent_began))
            and 0 < parent_began <= began <= completed and began >= original['completed_at'],
            'native continuation clocks missing, reversed or replayed')
    # This source route currently verifies identity/turn/final, not model
    # telemetry. Exposed settings cannot be silently downgraded to unknown.
    require(all(v.get(k) is None for v in (child['thread'], ct)
                for k in ('model', 'modelName', 'effort', 'reasoningEffort', 'reasoning_effort')),
            'known native runtime configuration needs an explicit verifier')
    fi, final = d._one([(n, i) for n, i in enumerate(ct['items'])
                       if i.get('type') == 'agentMessage' and i.get('phase') == 'final_answer'], 'native actual FINAL')
    require(final.get('id') == return_id and final.get('truncated') is not True,
            'native FINAL differs or is truncated')
    require(received['received_index'] < fi, 'native FINAL precedes the received input')
    text(final.get('text'), 'native completed author analysis')
    return {'agent_id': original['agent_id'], 'dispatch_call_id': dispatch_id, 'return_message_id': return_id,
            'started_at': began, 'completed_at': completed, 'analysis': final['text'],
            'dispatch_line': None, 'return_line': None,
            'dispatch_hash': digest({'source': manifest['parent'], 'turn_id': pt['id'], 'item': call}),
            'return_hash': digest({'source': manifest['child'], 'turn_id': ct['id'], 'item': final,
                                   'started_at': began, 'completed_at': completed}),
            'continuations': [], 'verification_scope': d.SCOPE,
            'source_verification': {'provider': manifest['provider'], 'host_id': 'durable',
                'parent_thread_id': parent['thread']['id'], 'author_thread_id': author_thread,
                'author_turn_id': ct['id'], 'dispatch_method': 'mcp__codex_app__send_message_to_thread',
                'association': received['association'], 'association_scope': received['association_scope'],
                'received_message_id': received['received_message']['id'],
                'received_input_hash': received['received_input_hash'],
                'received_input_verified': True, 'parent_host_id': parent['thread']['hostId'],
                'received_pointer': f"/turns/{ci}/items/{received['received_index']}",
                'original_proof': manifest['original'], 'author_path_source': 'VERIFIED_ORIGINAL_PUBLIC_PROOF',
                'original_public_trace': {k: original[k] for k in ('dispatch_hash', 'return_hash')},
                'runtime_model': None, 'runtime_effort': None, 'runtime_fork_turns': None,
                'runtime_configuration_verified': False, 'inbound_messages_verified': False,
                'dispatch_pointer': f'/turns/{pi}/items/{ii}', 'return_pointer': f'/turns/{ci}/items/{fi}',
                'input_verification': 'MAIN_INPUT_REVIEW_WITH_AVAILABLE_SOURCE_READS'}}


def source_files(path, manifest):
    from . import durable_proof as d
    original_path, original = _original(path, manifest)
    turn(path, manifest, manifest['dispatch_call_id'], manifest['return_message_id'],
         'followup_task', original['agent_id'])
    return list(dict.fromkeys([original_path, *d.source_files(original_path),
                              *(path.parent / manifest[k]['path'] for k in ('parent', 'child'))]))
