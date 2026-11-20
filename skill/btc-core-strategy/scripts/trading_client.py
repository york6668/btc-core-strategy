"""Native host adapter for the direct trading-system service."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

from native_usage import require_admission
from node_bridge import decode_node_response, sync_facts


def read_native_result(config, root, outcome):
    """Bind the completed answer to an actual child of the originating native run."""
    import run_usage
    from v4.research import _completed_turn

    origin_id = outcome['origin_run']
    key = hashlib.sha256(origin_id.encode()).hexdigest()
    origin = json.loads((root / 'runs' / (key + '.json')).read_bytes())
    allowed = {row['thread_id'] for row in origin.get('native_contexts', [])}
    agent = outcome['agent_id']
    database = run_usage.usage_database(configured=config.get('codex_state_db'))
    threads, parents = run_usage.native_threads(database)
    if origin.get('run_id') != origin_id or not allowed or agent not in threads or agent in allowed:
        raise ValueError('a completed independent native research identity is required')
    seen, parent = {agent}, parents.get(agent)
    while parent and parent not in allowed and parent not in seen:
        seen.add(parent)
        parent = parents.get(parent)
    if parent not in allowed:
        raise ValueError('research belongs to another native execution tree')
    result = _completed_turn(threads[agent]['rollout_path'], agent, outcome['turn_id'])
    body = result['last_agent_message']
    if 'sha256:' + hashlib.sha256(body.encode()).hexdigest() != outcome['result_ref']:
        raise ValueError('the admitted result must reference the actual completed output')
    return {**outcome, 'body': body, 'native_parent': parent,
            **{key: datetime.fromtimestamp(result[key], timezone.utc).isoformat()
               for key in ('started_at', 'completed_at')}}


def request(config, run_id, arguments, stdin):
    key = hashlib.sha256(run_id.encode()).hexdigest()
    root = Path(config['release_root']).resolve()
    run = json.loads((root / 'runs' / (key + '.json')).read_bytes())
    release = Path(__file__).resolve().parents[3]
    if run.get('status') != 'ACTIVE' or run.get('run_id') != run_id or run.get('commit') != release.name:
        raise ValueError('use the installed release selected for the original active native run')
    native = os.environ.get('CODEX_THREAD_ID')
    if config.get('codex_automations_db') and not any(
            item['thread_id'] == native for item in run.get('native_contexts', [])):
        raise ValueError('enter this run using the real native execution identity first')

    value = json.load(stdin)
    method = value['method']
    if not isinstance(method, str) or not method.startswith('trading.'):
        raise ValueError('the trading entry only accepts closed-loop operations')
    params = value.get('params', {})
    if not isinstance(params, dict) or 'run_ref' in params or set(value) - {'method', 'params', 'operation_id'}:
        raise ValueError('supply only the method, its parameters and the stable write identity')

    def call(name, supplied, operation=None):
        envelope = {'method': name, 'params': {**supplied, 'run_ref': run_id}}
        if operation is not None:
            envelope['operation_id'] = operation
        response = subprocess.run(arguments, input=json.dumps(envelope).encode(), capture_output=True)
        return decode_node_response(response, operation)

    if method == 'trading.open':
        if params:
            raise ValueError('the native collecting entry takes its inputs from the original sources')
        previous = call('trading.public', {})
        if previous['ok']:
            return previous
        if previous.get('message') != 'prepare the same run with its original public evidence first':
            return previous
        require_admission(config)
        import market_observation
        market = market_observation.validate_evidence(market_observation.collect())
        prepared = call(method, {'market': market}, 'trading-open:' + key)
        return call('trading.public', {}) if prepared['ok'] else prepared

    public_risk = method == 'trading.report' and params.get('purpose') == 'RISK' and params.get('decisions') == []
    if method in {'trading.personal', 'trading.personal_decision', 'trading.policy', 'trading.seed_policy',
                  'trading.report', 'trading.begin_delivery'} and not public_risk:
        binding = call('trading.personal_ready', {})
        if not binding['ok'] and method != 'trading.begin_delivery':
            return binding
        if binding['ok']:
            sync_facts(Path(config['project_dir']), Path(config['docker_service']['fact_bridge_dir']), allow_missing=True)
    if method == 'trading.personal':
        seeded = call('trading.seed_policy', {}, 'trading-policy:' + key)
        if not seeded['ok']:
            return seeded
    if method == 'trading.begin_delivery':
        params = {**params, 'expected_destination': config['report_destination']}
    if method == 'trading.research' and params.get('outcome') is not None:
        params = {**params, 'outcome': read_native_result(config, root, params['outcome'])}
    return call(method, params, value.get('operation_id'))
