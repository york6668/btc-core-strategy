#!/usr/bin/env python3
"""One-shot fixed-input maintenance evidence; never business admission.

The existing node remains the fault/usage authority. The original run's sibling
journal records this diagnostic's intent and result; it cannot settle a research
claim, clear a fault, launch a model, or execute a supplied command.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import sys

import model_daily
import node_config
import native_usage
from native_usage import require_admission
from v4 import common, durable_proof
from v4.common import digest, encoded, exact, now, require


ACTION = 'READ_FROZEN_REPORT_CONTEXT_SHA256_V1'
APPROVAL_ACTION = 'READ_FAILED_APPROVAL_INPUT_SHA256_V1'
PORTFOLIO_ACTION = 'READ_FAILED_PORTFOLIO_INPUT_SHA256_V1'
SCHEMA = 'BTC_REPORT_MAINTENANCE_DIAGNOSTIC_V1'
MAX_INPUT = 2 * 1024 * 1024


def _directory(root, diagnostic_id):
    path = root
    for part in ('runs', 'diagnostics', diagnostic_id):
        path = path / part
        require(not path.is_symlink(), 'diagnostic journal symlink rejected')
    return path


@contextmanager
def _journal(directory, *, make=False):
    """Bind every journal component to directory handles, not checked paths."""
    root = Path(node_config.load()['release_root']).resolve()
    parts = Path(directory).relative_to(root).parts
    require(len(parts) == 3 and parts[:2] == ('runs', 'diagnostics')
            and len(parts[2]) == 64 and all(c in '0123456789abcdef' for c in parts[2]),
            'original run diagnostic directory required')
    handles = [os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    try:
        for name in parts:
            if make:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=handles[-1]); os.fsync(handles[-1])
                except FileExistsError:pass
            handles.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handles[-1]))
        yield handles[-1]
        actual_root = os.stat(root, follow_symlinks=False); held_root = os.fstat(handles[0])
        require((actual_root.st_dev, actual_root.st_ino) == (held_root.st_dev, held_root.st_ino),
                'runtime directory changed during journal operation')
        for parent, child, name in zip(handles, handles[1:], parts):
            actual = os.stat(name, dir_fd=parent, follow_symlinks=False)
            held = os.fstat(child)
            require((actual.st_dev, actual.st_ino) == (held.st_dev, held.st_ino),
                    'diagnostic journal directory changed during operation')
    finally:
        for fd in reversed(handles):os.close(fd)


def _journal_create(directory, name, value):
    require(name in ('intent.json', 'execution-intent.json', 'result.json', 'error.json', 'verified.json'),
            'fixed diagnostic journal record required')
    with _journal(directory, make=True) as fd:
        output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(output, 'wb') as stream:
            stream.write(encoded(value));stream.flush();os.fsync(stream.fileno())
        os.fsync(fd)


def _journal_json(directory, name):
    require(name in ('intent.json', 'result.json'), 'fixed diagnostic read record required')
    with _journal(directory) as fd:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, 'rb') as stream:
            require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'ordinary diagnostic journal file required')
            raw = stream.read(MAX_INPUT + 1)
        require(0 < len(raw) <= MAX_INPUT, 'diagnostic journal size limit')
        return json.loads(raw)


def _argv(config, script, diagnostic_id):
    return [config['python'], str(script), '--node-config', str(Path(os.environ['BTC_NODE_CONFIG']).resolve()),
            'read', '--diagnostic-id', diagnostic_id]


def _code_sources(directory=None):
    files = [Path(module.__file__).resolve()
             for module in (model_daily, node_config, native_usage, common, durable_proof)]
    if directory is not None:
        # Historical verification reads these fixed source files; never import
        # or execute caller-selected code from an old intent.
        current = Path(__file__).resolve().parent
        files = [Path(directory) / p.relative_to(current) for p in files]
    return {str(p): digest(_bytes(p)) for p in files}


def _bytes(path, maximum=MAX_INPUT):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'ordinary file required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'ordinary diagnostic input required')
        value = stream.read(maximum + 1)
    require(0 < len(value) <= maximum, 'diagnostic read size limit')
    return value


def _json(path):
    return json.loads(_bytes(path))


def _run(config, run_ref):
    require(isinstance(run_ref, str) and 0 < len(run_ref) <= 191, 'original run required')
    root = Path(config['release_root']).resolve()
    path = root / 'runs' / (hashlib.sha256(run_ref.encode()).hexdigest() + '.json')
    run = _json(path)
    require(run.get('run_id') == run_ref and run.get('status') == 'ACTIVE', 'original ACTIVE run required')
    require(Path(run['node_config']).resolve() == Path(os.environ['BTC_NODE_CONFIG']).resolve(),
            'diagnostic must stay on original node')
    return path, root


def _authority(fault_id, original_claim_id, action=ACTION):
    state = model_daily.status()
    claim = state.get('claims', {}).get(original_claim_id)
    fault = state.get('faults', {}).get(fault_id)
    require(isinstance(claim, dict) and isinstance(fault, dict), 'original claim and fault required')
    require(action in (ACTION, APPROVAL_ACTION, PORTFOLIO_ACTION), 'fixed diagnostic action required')
    phase, kind = {ACTION: ('REPORT', 'NETWORK'), APPROVAL_ACTION: ('APPROVAL', 'UNKNOWN'),
                   PORTFOLIO_ACTION: ('PORTFOLIO', 'NETWORK')}[action]
    require(claim.get('state') == 'FAILED' and claim.get('phase') == phase
            and claim.get('failure_kind') == kind, 'original failed phase/kind is not diagnostic eligible')
    if action == PORTFOLIO_ACTION:
        require(claim.get('model') == 'gpt-6-astra', 'original portfolio model admission required')
    require(fault.get('state') == 'OPEN' and fault.get('kind') == kind
            and fault.get('fault_digest') == claim.get('input_digest')
            and fault.get('scope') == claim.get('scope'), 'original open fault/input/scope mismatch')
    if action in (APPROVAL_ACTION, PORTFOLIO_ACTION):
        signature = digest((kind + '\0' + claim['input_digest']).encode())
        require(fault.get('signature') == signature, 'original failed claim signature required')
        related = [c for c in state['claims'].values()
                   if c.get('state') in ('FAILED', 'UNKNOWN')
                   and c.get('failure_kind') == kind and c.get('scope') == fault['scope']
                   and c.get('input_digest') == fault['fault_digest']]
        origin = fault.get('originating_claim_id')
        if origin is not None:
            require(origin == original_claim_id, 'fault belongs to another original claim')
        else:
            require(len(related) == 1 and related[0]['claim_id'] == original_claim_id,
                    'legacy fault needs a unique original failed claim')
        require(claim.get('failure_ref') in (None, fault['evidence_ref']),
                'original failure reference mismatch')
    require(not any(c.get('state') in ('CLAIMED', 'UNKNOWN')
                    for c in state.get('claims', {}).values()), 'in-flight call requires reconciliation')
    scope = claim['scope']
    affected = lambda s: s == scope or scope.startswith(s + '/') or s.startswith(scope + '/')
    require(not any(k != fault_id and f.get('state') == 'OPEN' and affected(f['scope'])
                    for k, f in state.get('faults', {}).items()), 'another affected fault blocks this diagnostic')
    return claim, fault


def _original_author(fault, claim_id):
    failure_path = Path(fault['evidence_ref'])
    failure = _json(failure_path)
    require(failure.get('claim_id') == claim_id and failure.get('business_result') == 'FAILED_NO_REPORT_ARTIFACT',
            'original failed-report evidence required')
    name = Path(failure['proof'])
    require(not name.is_absolute() and name.name == str(name), 'original proof must be sibling')
    proof_path = failure_path.parent / name
    manifest = _json(proof_path)
    require(manifest.get('schema') == durable_proof.SCHEMA, 'supported original durable proof required')
    sources = durable_proof.source_files(proof_path)
    parent, child = [_json(p) for p in sources]
    ct = next(t for t in child['turns'] if t['id'] == manifest['child_turn_id'])
    pt = next(t for t in parent['turns'] if t['id'] == manifest['parent_turn_id'])
    starts = [i for i in pt['items'] if i.get('type') == 'subAgentActivity'
              and i.get('kind') == 'interacted' and i.get('agentThreadId') == child['thread']['id']]
    finals = [i for i in ct['items'] if i.get('type') == 'agentMessage' and i.get('phase') == 'final_answer']
    require(len(starts) == len(finals) == 1, 'unique original dispatch/final required')
    proof = durable_proof.turn(proof_path, starts[0]['id'], finals[0]['id'], 'followup_task', starts[0]['agentPath'])
    require(any(i.get('type') == 'commandExecution' and i.get('status') == 'failed'
                and i.get('exitCode') == -1 for i in ct['items']), 'actual original execution failure required')
    files = [failure_path, proof_path, *sources]
    return {'thread_id': child['thread']['id'], 'agent_path': proof['agent_id'],
            'failed_turn_id': ct['id']}, {str(p.resolve()): digest(_bytes(p)) for p in files}


def _context(path, expected, run_ref):
    raw = _bytes(path)
    require(digest(raw) == expected, 'frozen input digest mismatch')
    value = json.loads(raw)
    require(value.get('schema') == 'BTC_REPORT_PREVIEW_CONTEXT_V1' and value.get('preview_only') is True
            and value.get('execution_evidence', {}).get('run_ref') == run_ref,
            'original isolated report context/run required')
    return {'sha256': digest(raw), 'bytes': len(raw), 'json_parse': True}


def _approval_context(path, expected, run_ref):
    """Read only the already failed packet and its original frozen file list."""
    raw = _bytes(path)
    require(digest(raw) == expected, 'failed approval input digest mismatch')
    packet = json.loads(raw)
    require(packet.get('schema') == 'BTC_FORMAL_FACT_REASSESSMENT_REVIEW_V1'
            and packet.get('phase') == 'APPROVAL', 'original approval packet required')
    files = packet.get('files')
    require(isinstance(files, dict) and 0 < len(files) <= 16, 'bounded original input list required')
    checked = {}
    total = len(raw)
    for name, entry in files.items():
        exact(entry, 'path bytes raw_sha256', 'frozen approval file')
        require(type(entry['bytes']) is int and 0 < entry['bytes'] <= MAX_INPUT,
                'bounded frozen approval file required')
        total += entry['bytes']
        require(total <= MAX_INPUT, 'total frozen approval input exceeds diagnostic bound')
        content = _bytes(entry['path'])
        require(len(content) == entry['bytes'] and digest(content) == entry['raw_sha256'],
                'frozen approval dependency changed')
        if name == 'cycle-candidate-unapproved.json':
            require(digest(json.loads(content)) == packet['current_unapproved_candidate'],
                    'failed approval candidate mismatch')
        checked[name] = {'bytes': len(content), 'sha256': digest(content)}
    require('cycle-candidate-unapproved.json' in checked, 'failed candidate must be fully read')
    return {'sha256': digest(raw), 'bytes': len(raw), 'json_parse': True, 'files': checked}


def _approval_author(fault, claim, packet_path, original_result_id):
    """Use the immutable prior result/proof, never a caller-selected new author."""
    packet_raw = _bytes(packet_path)
    require(digest(packet_raw) == claim['input_digest'],
            'packet must bind the original claim input/issue/scope/phase')
    packet = json.loads(packet_raw)
    require(packet.get('issue_id') == claim['issue_id']
            and packet.get('scope') == claim['scope'] and packet.get('phase') == claim['phase'],
            'packet must bind the original claim input/issue/scope/phase')
    failure_path = Path(fault['evidence_ref'])
    failure_raw = _bytes(failure_path)
    failure = json.loads(failure_raw)
    require(failure.get('reported_first_tool_error') == 'No usable execution environment is available.'
            and failure.get('native_status') == 'completed' and failure.get('native_turn_error') is None
            and failure.get('actual_candidate_read') is False
            and failure.get('content_approved') is False and failure.get('content_rejected') is False,
            'recorded pre-review environment failure required')
    require(isinstance(original_result_id, str) and len(original_result_id) == 71
            and original_result_id.startswith('sha256:')
            and all(c in '0123456789abcdef' for c in original_result_id[7:]),
            'original immutable result identity required')
    root = Path(node_config.load()['project_dir']) / '.strategy/v4'
    def artifact(kind, identity, key):
        require(isinstance(identity, str) and len(identity) == 71 and identity.startswith('sha256:')
                and all(c in '0123456789abcdef' for c in identity[7:]),
                'original artifact identity required')
        path = root / 'research' / kind / (identity[7:] + '.json')
        value = _json(path)
        require(value.get(key) == identity == digest({k:v for k,v in value.items() if k != key}),
                'original research artifact damaged')
        return path, value
    result_path, result = artifact('results', original_result_id, 'result_id')
    candidate_path, disclosed = artifact('candidates', result['candidate_id'], 'candidate_id')
    require(result['request_id'] == packet['request_id'] == disclosed['request_id']
            and disclosed['candidate_hash'] == packet['prior_approved_candidate'],
            'prior result must belong to the original packet and approved base')
    require(result.get('cycle_review', {}).get('approved') is True
            and result['cycle_review']['candidate_hash'] == packet['prior_approved_candidate'],
            'actual prior approved result required')
    proof_path = durable_proof.resolve_path(Path(result['rollout_path']))
    proof = durable_proof.turn(proof_path, result['dispatch_call_id'], result['return_message_id'],
                              'followup_task', result['agent_id'])
    require(proof['analysis'] == result['analysis'], 'original result FINAL changed')
    manifest = _json(proof_path)
    require(manifest.get('schema') == durable_proof.SCHEMA, 'original durable source required')
    sources = durable_proof.source_files(proof_path)
    child = durable_proof._document(proof_path.parent, manifest['child'])
    thread = child['thread']['id']
    require(thread == packet['original_author_thread'] == failure['thread_id']
            and proof['agent_id'] == packet['original_author_canonical'],
            'same original author required')
    files = [result_path, candidate_path, proof_path, *sources]
    source_hashes = {str(p.resolve()): digest(_bytes(p, maximum=32*1024*1024)) for p in files}
    # Bind the exact bytes parsed above, never a second view of mutable inputs.
    source_hashes.update({str(failure_path.resolve()): digest(failure_raw),
                          str(Path(packet_path).resolve()): digest(packet_raw)})
    return {'thread_id': thread, 'agent_path': proof['agent_id'],
            'failed_turn_id': failure['turn_id']}, source_hashes


PORTFOLIO_FILES = ('run', 'normal_main_context', 'normal_main_template', 'normal_main_read_set',
                   'frozen_market', 'frozen_market_result', 'bounded_delta',
                   'observe_answers', 'observe_receipt')
PORTFOLIO_PRIOR_FILES = ('own_prior_proposal', 'own_prior_brief', 'own_prior_manifest')


def _portfolio_bytes(path, root):
    """Read a fixed producer file below the trusted root without following links."""
    parts = Path(path).relative_to(root).parts
    require(parts and all(p not in ('', '.', '..') for p in parts), 'fixed producer file required')
    handles = []
    try:
        handles.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for part in parts[:-1]:
            handles.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=handles[-1]))
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=handles[-1])
        with os.fdopen(fd, 'rb') as stream:
            require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'ordinary producer file required')
            raw = stream.read(MAX_INPUT + 1)
        require(0 < len(raw) <= MAX_INPUT, 'diagnostic read size limit')
        return raw
    finally:
        for fd in reversed(handles): os.close(fd)


def _portfolio_packet(path, expected, run_ref):
    """Only the original run producer's packet, never a caller-supplied file list."""
    require(isinstance(run_ref, str) and Path(run_ref).name == run_ref
            and run_ref not in ('', '.', '..'), 'original portfolio run name required')
    root = (Path(node_config.load()['project_dir']) / '.strategy/v4').resolve()
    directory = root / 'runs' / run_ref
    owner = directory / 'personal-expiry-author'
    require(Path(path).resolve() == owner / 'personal-input.json',
            'original portfolio packet location required')
    raw = _portfolio_bytes(owner / 'personal-input.json', root)
    require(digest(raw) == expected, 'failed portfolio input digest mismatch')
    packet = json.loads(raw)
    require(isinstance(packet, dict)
            and packet.get('schema') == 'BTC_BOUNDED_PERSONAL_EXPIRY_REASSESSMENT_INPUT_V1'
            and packet.get('phase') == 'PORTFOLIO', 'original portfolio producer required')
    sources = packet.get('sources')
    require(isinstance(sources, dict) and set(sources) == set(PORTFOLIO_FILES + PORTFOLIO_PRIOR_FILES),
            'fixed portfolio source roles required')
    for entry in sources.values():
        exact(entry, 'path raw_sha256 bytes', 'frozen portfolio source')
        require(isinstance(entry['path'], str) and Path(entry['path']).is_absolute()
                and type(entry['bytes']) is int and 0 < entry['bytes'] <= MAX_INPUT
                and isinstance(entry['raw_sha256'], str) and len(entry['raw_sha256']) == 71
                and entry['raw_sha256'].startswith('sha256:')
                and all(c in '0123456789abcdef' for c in entry['raw_sha256'][7:]),
                'bounded portfolio source identity required')
    # Read current phase material only. Prior proposal paths are references,
    # not permission to read arbitrary old files during a maintenance command.
    for role in PORTFOLIO_PRIOR_FILES:
        entry = sources[role]; p = Path(entry['path']).resolve()
        require(p.is_relative_to(root / 'runs') and p.parent.name == 'immutable'
                and p.parent.parent.name == 'author'
                and p.name.startswith(entry['raw_sha256'][7:] + '-'),
                'content-addressed prior portfolio reference required')
    context = sources['normal_main_context']
    context_name = 'refresh-main-' + context['raw_sha256'][7:19]
    expected_paths = {'run': directory / 'run.json',
        'normal_main_context': directory / (context_name + '-context.json'),
        'normal_main_template': directory / (context_name + '-main-template.json'),
        'frozen_market': directory / 'market.json', 'frozen_market_result': directory / 'market-result.json',
        'bounded_delta': owner / 'personal-author-delta.frozen.md',
        'observe_answers': owner / 'observe-answers.frozen.json',
        'observe_receipt': directory / 'observe-stage-receipt.json'}
    for role, target in expected_paths.items():
        require(Path(sources[role]['path']).resolve() == target,
                'portfolio source is outside its producer role')
    read_set = Path(sources['normal_main_read_set']['path']).resolve()
    require(read_set.is_relative_to(directory / 'reading') and read_set.name == 'read-set.json'
            and read_set.parent.name.startswith('main-'), 'normal main read-set required')
    expected_paths['normal_main_read_set'] = read_set
    require(len(raw) + sum(sources[k]['bytes'] for k in PORTFOLIO_FILES) <= MAX_INPUT,
            'total frozen portfolio input exceeds diagnostic bound')
    return raw, packet, expected_paths


def _portfolio_context(path, expected, run_ref):
    raw, packet, paths = _portfolio_packet(path, expected, run_ref)
    root = (Path(node_config.load()['project_dir']) / '.strategy/v4').resolve()
    values, checked = {}, {}
    for role in PORTFOLIO_FILES:
        entry = packet['sources'][role]; content = _portfolio_bytes(paths[role], root)
        require(len(content) == entry['bytes'] and digest(content) == entry['raw_sha256'],
                'frozen portfolio dependency changed')
        values[role] = (content.decode('utf-8') if role == 'bounded_delta' else json.loads(content))
        require(role == 'bounded_delta' or isinstance(values[role], dict),
                'normal portfolio object required')
        checked[role] = {'bytes': len(content), 'sha256': digest(content)}
    run, context = values['run'], values['normal_main_context']
    context_ref = packet['sources']['normal_main_context']
    require(run.get('schema') == 'BTC_RUN_MATERIAL_V1' and run.get('run_ref') == run_ref
            and Path(run['project']).resolve() == Path(node_config.load()['project_dir']).resolve(),
            'original normal portfolio run required')
    require(context.get('schema') == 'BTC_SENSING_CONTEXT_V4'
            and context.get('identity') == packet.get('normal_context_identity')
            and context['identity']['decision_id'] == run['anchors']['decision_id']
            and context['identity']['fact_version'] == packet.get('fact_version') == run['anchors']['fact_version'],
            'original portfolio context identity changed')
    binding = values['normal_main_template'].get('_binding', {})
    read_set = values['normal_main_read_set']
    require(Path(binding.get('context_file', '')).resolve() == Path(context_ref['path']).resolve()
            and binding.get('context_hash') == context_ref['raw_sha256']
            and read_set.get('schema') == 'BTC_STAGE_READ_SET_V1' and read_set.get('stage') == 'main'
            and Path(read_set.get('material', '')).resolve() == Path(context_ref['path']).resolve()
            and read_set.get('material_hash') == context_ref['raw_sha256'],
            'normal portfolio producer binding changed')
    market = values['frozen_market_result']; answers = values['observe_answers']
    require(run['market_hash'] == checked['frozen_market']['sha256']
            and market.get('result_hash') == packet.get('new_observation_result_hash') == digest(market['payload'])
            and market.get('lifecycle_frozen_at') == packet.get('new_observation_frozen_at')
            and values['frozen_market'].get('cutoff') == packet.get('new_market_cutoff')
            and answers.get('allocation_review', {}).get('market_result_hash') == market['result_hash']
            and answers['allocation_review'].get('fact_version') == packet['fact_version']
            and answers.get('main_evaluation', {}).get('required') is True
            and values['observe_receipt'].get('observation_id') == context['identity']['observation_id'],
            'frozen portfolio observation binding changed')
    return {'sha256': digest(raw), 'bytes': len(raw), 'json_parse': True, 'files': checked}


def _portfolio_author(fault, claim, packet_path, run_ref):
    packet_raw, packet, _ = _portfolio_packet(packet_path, claim['input_digest'], run_ref)
    require(packet.get('issue_id') == claim['issue_id'] and packet.get('scope') == claim['scope'],
            'portfolio input/issue/scope/phase mismatch')
    root = (Path(node_config.load()['project_dir']) / '.strategy/v4').resolve()
    def artifact(kind, identity, key):
        require(isinstance(identity, str) and len(identity) == 71 and identity.startswith('sha256:')
                and all(c in '0123456789abcdef' for c in identity[7:]), 'original research identity required')
        path = root / 'research' / kind / (identity[7:] + '.json'); value = _json(path)
        require(isinstance(value, dict)
                and value.get(key) == identity == digest({k:v for k,v in value.items() if k != key}),
                'original research artifact damaged')
        return path, value
    request_path, request = artifact('requests', packet.get('request_id'), 'request_id')
    public_path, public = artifact('independent', packet.get('frozen_public_owner'), 'independent_id')
    require(request.get('schema') == 'BTC_RESEARCH_REQUEST_V4'
            and public.get('schema') == 'BTC_RESEARCH_INDEPENDENT_V4'
            and request.get('issue_id') == claim['issue_id'] and public.get('request_id') == request['request_id'],
            'portfolio request/public authority mismatch')
    require(isinstance(public.get('market_result'), dict)
            and all(k in public['market_result'] for k in ('outlook', 'factor_assessment'))
            and digest({k: public['market_result'][k] for k in ('outlook', 'factor_assessment')})
            == packet.get('frozen_public_semantic_hash'), 'original public meaning changed')
    failure_path = Path(fault['evidence_ref']); failure_raw = _bytes(failure_path); failure = json.loads(failure_raw)
    require(isinstance(failure, dict)
            and failure_path.resolve().is_relative_to(root / 'runs' / run_ref / 'personal-expiry-author')
            and failure.get('schema') == 'BTC_ACTUAL_PORTFOLIO_EXECUTION_FAILURE_OBSERVATION_V1'
            and failure.get('claim_id') == claim['claim_id'] and failure.get('phase') == 'PORTFOLIO'
            and failure.get('business_result') == 'FAILED_NO_PERSONAL_DECISION_ARTIFACT',
            'original portfolio failure producer required')
    reference = failure.get('proof'); exact(reference, 'path raw_sha256 bytes', 'portfolio failure proof')
    require(isinstance(reference['path'], str) and Path(reference['path']).is_absolute(),
            'original portfolio proof path required')
    proof_path = Path(reference['path']); proof_raw = _bytes(proof_path)
    require(proof_path.resolve().parent == failure_path.resolve().parent / 'proof'
            and len(proof_raw) == reference['bytes'] and digest(proof_raw) == reference['raw_sha256'],
            'original portfolio proof changed')
    manifest = json.loads(proof_raw)
    require(isinstance(manifest, dict) and manifest.get('schema') == durable_proof.SCHEMA,
            'official portfolio source required')
    proof = durable_proof.turn(proof_path, failure.get('dispatch_call_id'), failure.get('return_message_id'),
                              'followup_task', packet.get('author_canonical'))
    claimed, finished, opened = (claim.get('claimed_at_ms'), claim.get('finished_at_ms'),
                                  fault.get('opened_at_ms'))
    require(all(type(v) is int and v > 0 for v in (claimed, finished, opened))
            and claimed <= finished
            and proof['started_at'] * 1000 >= claimed - 1000
            and proof['completed_at'] * 1000 <= min(finished, opened) + 1000,
            'native failure must belong to the original reserved call interval')
    child = durable_proof._document(proof_path.parent, manifest['child'])
    ct = next(t for t in child['turns'] if t['id'] == manifest['child_turn_id'])
    actual = json.loads(proof['analysis'])
    require(isinstance(actual, dict) and actual == failure.get('actual_final')
            and actual.get('status') == 'BLOCKED_EXECUTION_ENVIRONMENT_UNAVAILABLE'
            and actual.get('phase') == 'PORTFOLIO' and actual.get('input_digest') == claim['input_digest']
            and actual.get('new_decided_at') is None and actual.get('new_valid_until') is None,
            'actual failed portfolio FINAL required')
    require(any(i.get('type') == 'commandExecution' and i.get('status') == 'failed'
                and i.get('exitCode') == -1 and i.get('commandTruncated') is not True
                and isinstance(i.get('output'), dict) and i['output'].get('truncated') is False
                and i['output'].get('text') ==
                    'Failed to create unified exec process: exec-server transport disconnected'
                for i in ct['items']),
            'known process-creation network failure required')
    original_path = durable_proof.resolve_path(Path(public['rollout_path']))
    original = durable_proof.turn(original_path, public['dispatch_call_id'], public['return_message_id'], 'spawn_agent')
    require(all(original[k] == public['trace_proof'][k] for k in ('dispatch_hash', 'return_hash'))
            and original['agent_id'] == public['agent_id'] == proof['agent_id'] == packet['author_canonical']
            and original['source_verification']['author_thread_id'] == child['thread']['id'] == packet['author_thread'],
            'same original public author required')
    files = [request_path, public_path, proof_path, *durable_proof.source_files(proof_path),
             original_path, *durable_proof.source_files(original_path)]
    sources = {str(p.resolve()): digest(_bytes(p, maximum=32*1024*1024)) for p in files}
    sources.update({str(failure_path.resolve()): digest(failure_raw),
                    str(Path(packet_path).resolve()): digest(packet_raw)})
    return {'thread_id': child['thread']['id'], 'agent_path': proof['agent_id'],
            'failed_turn_id': ct['id']}, sources


def _binding(action, fault, claim, context_path, run_ref, original_result_id, *, validate_input=True):
    if action == ACTION:
        require(original_result_id is None, 'report diagnostic does not take a research result')
        author, sources = _original_author(fault, claim['claim_id'])
        if validate_input:
            _context(context_path, claim['input_digest'], run_ref)
    elif action == APPROVAL_ACTION:
        if validate_input:
            _approval_context(context_path, claim['input_digest'], run_ref)
        author, sources = _approval_author(fault, claim, context_path, original_result_id)
    else:
        require(action == PORTFOLIO_ACTION and original_result_id is None,
                'portfolio diagnostic uses its original public owner, not a selected result')
        author, sources = _portfolio_author(fault, claim, context_path, run_ref)
        if validate_input:
            _portfolio_context(context_path, claim['input_digest'], run_ref)
    return author, sources


def admit(*, fault_id, original_claim_id, context_path, run_ref, action=ACTION, original_result_id=None):
    """Reserve one maintenance turn in the original run journal, not a deep ticket."""
    config = node_config.load()
    require(config.get('usage_guard_required') is True, 'diagnostic needs enforced original usage guard')
    usage = require_admission(config)
    require(usage.get('enforced') is True and usage.get('allowed') is True, 'actual usage admission required')
    claim, fault = _authority(fault_id, original_claim_id, action)
    run_path, root = _run(config, run_ref)
    context_path = Path(context_path)
    require(not context_path.is_symlink(), 'frozen context symlink rejected')
    context_path = context_path.resolve()
    author, sources = _binding(action, fault, claim, context_path, run_ref, original_result_id)
    identity = {'action': action, 'fault_id': fault_id, 'original_claim_id': original_claim_id,
                'input_digest': claim['input_digest'], 'author': author, 'run_ref': run_ref}
    if action == APPROVAL_ACTION:
        identity['original_result_id'] = original_result_id
    # One diagnostic per original failed approval/action, even if a caller
    # changes run/result arguments. The full binding is still verified below.
    slot = {k: identity[k] for k in ('action', 'fault_id', 'original_claim_id')}
    diagnostic_id = digest(slot if action in (APPROVAL_ACTION, PORTFOLIO_ACTION) else identity)[7:]
    directory = _directory(root, diagnostic_id)
    script = Path(__file__).resolve()
    record = {'schema': SCHEMA, **identity, 'diagnostic_id': diagnostic_id, 'admitted_at': now(),
              'run_path': str(run_path), 'run_digest': digest(_bytes(run_path)),
              'node_config': str(Path(os.environ['BTC_NODE_CONFIG']).resolve()),
              'node_digest': digest(_bytes(os.environ['BTC_NODE_CONFIG'])),
              'context_path': str(context_path), 'script': str(script), 'script_digest': digest(_bytes(script)),
              'code_sources': _code_sources(),
              'sources': sources, 'business_allowed': False,
              'usage': {'enforced': True, 'allowed': True, 'measurement': usage.get('measurement')},
              'argv': _argv(config, script, diagnostic_id)}
    if action in (APPROVAL_ACTION, PORTFOLIO_ACTION):
        record['binding_digest'] = digest(identity)
    # O_EXCL + fsync survives lost responses/restarts. Never auto-reissue an
    # existing intent, even if there is no execution/result file yet.
    _journal_create(directory, 'intent.json', record)
    return record


def _intent(diagnostic_id, *, verify_sources=True, original_release=None):
    require(isinstance(diagnostic_id, str) and len(diagnostic_id) == 64
            and all(c in '0123456789abcdef' for c in diagnostic_id), 'diagnostic identity required')
    root = Path(node_config.load()['release_root']).resolve()
    path = _directory(root, diagnostic_id) / 'intent.json'
    record = _journal_json(path.parent, 'intent.json')
    exact(record, 'schema action fault_id original_claim_id input_digest author run_ref diagnostic_id admitted_at '
          'run_path run_digest node_config node_digest context_path script script_digest code_sources sources business_allowed usage argv'
          + (' original_result_id binding_digest' if record.get('action') == APPROVAL_ACTION else
             ' binding_digest' if record.get('action') == PORTFOLIO_ACTION else ''),
          'maintenance intent')
    require(record.get('schema') == SCHEMA and record.get('diagnostic_id') == diagnostic_id
            and record.get('action') in (ACTION, APPROVAL_ACTION, PORTFOLIO_ACTION)
            and record.get('business_allowed') is False,
            'fixed diagnostic intent required')
    identity = {k: record[k] for k in ('action', 'fault_id', 'original_claim_id', 'input_digest', 'author', 'run_ref')}
    if record['action'] == APPROVAL_ACTION:
        identity['original_result_id'] = record['original_result_id']
    if record['action'] in (APPROVAL_ACTION, PORTFOLIO_ACTION):
        require(digest(identity) == record['binding_digest'], 'diagnostic binding changed')
        identity = {k: identity[k] for k in ('action', 'fault_id', 'original_claim_id')}
    require(digest(identity)[7:] == diagnostic_id, 'diagnostic binding changed')
    script = Path(__file__).resolve()
    if original_release is not None:
        require(record['action'] == PORTFOLIO_ACTION and isinstance(original_release, str)
                and len(original_release) == 40
                and all(c in '0123456789abcdef' for c in original_release),
                'original installed portfolio diagnostic release required')
        release = root / 'releases' / original_release
        require(not release.is_symlink(), 'original installed release identity required')
        metadata = _json(release / 'RELEASE.json')
        require(isinstance(metadata, dict) and metadata.get('commit') == original_release
                and metadata.get('data_schema') == 'BTC_V4_UNCHANGED',
                'original installed release identity required')
        script = release / 'skill/btc-core-strategy/scripts/model_diagnostic.py'
    require(record['script'] == str(script)
            and record['script_digest'] == digest(_bytes(script)), 'diagnostic code changed')
    require(record['code_sources'] == _code_sources(script.parent if original_release is not None else None),
            'diagnostic dependency code changed')
    require(record['argv'] == _argv(node_config.load(), script, diagnostic_id),
            'fixed diagnostic argv changed')
    require(record['node_config'] == str(Path(os.environ['BTC_NODE_CONFIG']).resolve())
            and record['node_digest'] == digest(_bytes(record['node_config'])), 'original node changed')
    require(record['run_digest'] == digest(_bytes(record['run_path'])), 'original run changed')
    _run(node_config.load(), record['run_ref'])
    if verify_sources:
        _verify_sources(record)
    return path.parent, record


def _verify_sources(record):
    for source, expected in record['sources'].items():
        require(digest(_bytes(source, maximum=32*1024*1024)) == expected,
                'original failure evidence changed')


def execute(diagnostic_id):
    """Only read the bound JSON bytes; no subprocess/callback/command argument."""
    directory, record = _intent(diagnostic_id, verify_sources=False)
    require(0 <= (datetime.now(timezone.utc) - datetime.fromisoformat(record['admitted_at'])).total_seconds() <= 900,
            'diagnostic admission expired; do not auto-reissue')
    usage = require_admission(node_config.load())
    require(usage.get('enforced') is True and usage.get('allowed') is True, 'actual usage admission required')
    claim, fault = _authority(record['fault_id'], record['original_claim_id'], record['action'])
    _journal_create(directory, 'execution-intent.json', {'diagnostic_id': diagnostic_id, 'started_at': now()})
    try:
        _verify_sources(record)
        author, sources = _binding(record['action'], fault, claim, record['context_path'],
                                   record['run_ref'], record.get('original_result_id'), validate_input=False)
        require(author == record['author'] and sources == record['sources']
                and claim['input_digest'] == record['input_digest'], 'original failure binding changed')
        read_input = {ACTION: _context, APPROVAL_ACTION: _approval_context,
                      PORTFOLIO_ACTION: _portfolio_context}[record['action']]
        actual = read_input(record['context_path'], record['input_digest'], record['run_ref'])
        result = {'schema': SCHEMA, 'diagnostic_id': diagnostic_id, 'action': record['action'],
                  'result': 'READ_MATCH', **actual, 'completed_at': now(), 'business_allowed': False,
                  'author_attribution': 'REQUIRES_OFFICIAL_COMPLETED_TURN_EVIDENCE'}
        _journal_create(directory, 'result.json', result)
        return result
    except BaseException as exc:
        _journal_create(directory, 'error.json', {'diagnostic_id': diagnostic_id, 'failed_at': now(),
                                               'error_type': type(exc).__name__, 'business_allowed': False})
        raise


def verify(diagnostic_id, proof_path, dispatch_id, return_id, original_release=None):
    """Consume one actual original-author turn; never infer it from local output."""
    directory, record = _intent(diagnostic_id, verify_sources=original_release is None,
                                original_release=original_release)
    actual = None
    if original_release is not None:
        # Compatibility consumes a completed read only; it cannot reserve or
        # execute a diagnostic with another release's intent.
        actual = _journal_json(directory, 'result.json')
        require(isinstance(actual, dict) and actual.get('result') == 'READ_MATCH'
                and actual.get('diagnostic_id') == diagnostic_id
                and actual.get('action') == record['action'] and actual.get('business_allowed') is False
                and actual.get('sha256') == record['input_digest'], 'completed original read required')
        _verify_sources(record)
    proof = durable_proof.turn(proof_path, dispatch_id, return_id, 'followup_task', record['author']['agent_path'])
    manifest = _json(proof_path)
    child = _json(Path(proof_path).parent / manifest['child']['path'])
    require(child['thread']['id'] == record['author']['thread_id'], 'same original author required')
    turn = next(t for t in child['turns'] if t['id'] == manifest['child_turn_id'])
    require(turn['id'] != record['author']['failed_turn_id'], 'new actual maintenance turn required')
    require(proof['started_at'] >= datetime.fromisoformat(record['admitted_at']).timestamp() - 1,
            'maintenance turn must follow original admission')
    commands = [i for i in turn['items'] if i.get('type') == 'commandExecution']
    def passive(item):
        return (item.get('type') in ('commandExecution', 'reasoning', 'agentMessage', 'userMessage')
                or (set(item) == {'type', 'id'} and item.get('type') == 'contextCompaction'
                    and isinstance(item['id'], str) and 0 < len(item['id']) <= 191))
    require(len(commands) == 1 and all(passive(i) for i in turn['items']),
            'only one fixed diagnostic command and passive native messages permitted')
    item = commands[0]
    command = shlex.split(item.get('command', ''))
    if len(command) == 3 and command[1] in ('-c', '-lc'):
        require(command[0] in ('/bin/zsh', '/bin/bash', '/bin/sh'), 'unsupported diagnostic shell wrapper')
        require(len(command[2].splitlines()) == 1, 'one standalone diagnostic command required')
        command = shlex.split(command[2])
    require(command == record['argv'], 'non-diagnostic command rejected')
    output = item.get('output')
    require(item.get('status') == 'completed' and item.get('exitCode') == 0
            and item.get('truncated') is not True and isinstance(output, dict)
            and output.get('truncated') is False, 'actual complete command output required')
    if actual is None:
        actual = _journal_json(directory, 'result.json')
    require(json.loads(output['text']) == actual and actual['result'] == 'READ_MATCH'
            and actual['sha256'] == record['input_digest'], 'actual frozen-input readback required')
    # The raw native sources stay at their original paths. Never copy the
    # author's arbitrary FINAL/analysis into a metadata-only diagnostic result.
    metadata = {key: value for key, value in proof.items() if key != 'analysis'}
    completed = {'schema': SCHEMA, 'diagnostic_id': diagnostic_id, 'verified_at': now(),
                 'result': 'VERIFIED_DIAGNOSTIC_ONLY', 'command_item_id': item['id'],
                 'proof': metadata, 'business_allowed': False, 'fault_resolved': False}
    if original_release is not None:
        completed['verification_code'] = {
            'script': str(Path(__file__).resolve()), 'script_digest': digest(_bytes(__file__)),
            'code_sources': _code_sources(), 'original_execution_release': original_release,
            'original_script': record['script'], 'original_script_digest': record['script_digest']}
    _journal_create(directory, 'verified.json', completed)
    return completed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-config', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('admit')
    for key in ('fault-id', 'original-claim-id', 'context-path', 'run-ref'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--action', choices=(ACTION, APPROVAL_ACTION, PORTFOLIO_ACTION), default=ACTION)
    p.add_argument('--original-result-id')
    p = sub.add_parser('read'); p.add_argument('--diagnostic-id', required=True)
    p = sub.add_parser('verify')
    for key in ('diagnostic-id', 'proof-path', 'dispatch-id', 'return-id'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--original-release', help='verify an existing portfolio READ_MATCH from its immutable installed code')
    args = vars(parser.parse_args(argv)); os.environ['BTC_NODE_CONFIG'] = args.pop('node_config')
    command = args.pop('command')
    try:
        result = {'admit': admit, 'read': lambda **kw: execute(**kw), 'verify': verify}[command](**args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0
    except (OSError, ValueError, KeyError, StopIteration, model_daily.Blocked) as exc:
        print(json.dumps({'blocked': type(exc).__name__, 'reason': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
