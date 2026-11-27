"""Native host adapter for the direct trading-system service."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

from native_usage import require_admission
from node_bridge import decode_node_response, sync_facts


def event_request(release, method, params, operation, call, run_key):
    # The host owns public HTTP. The service records clocks, identities and market decisions.
    service = str(release / 'service')
    if service not in sys.path:
        sys.path.insert(0, service)
    from btc_node import market_events as events
    if method == 'trading.public':
        if len(params) != 1 and set(params) != {'options_expiry', 'previous_options'}:
            raise ValueError('request one source at a time')
        ready = call('trading.public', {})
        if not ready['ok']:
            return ready
        if 'options_expiry' in params:
            import deribit_options
            try:
                result, sources = deribit_options.collect(params['options_expiry'])
            except (OSError, ValueError, KeyError) as error:
                return {'ok': True, 'result': {'status': 'SOURCE_UNAVAILABLE', 'source': 'Deribit',
                                                'expiry': params['options_expiry'], 'reason': str(error)}}
            result['evidence'] = []
            for source in sources:
                identity = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
                saved = call('trading.evidence', source, 'option:' + run_key + ':' + identity)
                if not saved['ok']:
                    return saved
                result['evidence'].append({'url': source['url'], 'source': saved['result']})
            if 'previous_options' in params:
                earlier = []
                for ref in params['previous_options']:
                    original = call('trading.source', {'digest': ref['source']['digest']})
                    if not original['ok']:
                        return original
                    if ref['url'] != original['result'].get('url'):
                        raise ValueError('the earlier reference names a different source')
                    earlier.append(original['result'])
                result['previous'] = {**deribit_options.compare_previous(result, earlier),
                                      'evidence': params['previous_options']}
        else:
            try:
                result = events.discover(search=params.get('event_search'), event=params.get('event_slug'))
            except (OSError, ValueError, KeyError) as error:
                return {'ok': True, 'result': {'status': 'SOURCE_UNAVAILABLE', 'source': 'Polymarket Gamma',
                                                'reason': str(error)}}
        return {'ok': True, 'result': result}
    if not operation:
        raise ValueError('supply the original operation identity')
    if method == 'trading.forecast':
        if 'terms' in params:
            raise ValueError('the host reads the published terms using market_slug')
        market_slug = params['market_slug']
        raw = json.loads(events.receipt(market_slug)['content'])
        if raw.get('closed') or not raw.get('active') or not raw.get('acceptingOrders'):
            raise ValueError('the market is not accepting a prospective comparison')
        return call(method, {**{k: v for k, v in params.items() if k != 'market_slug'}, 'terms': events.terms(raw)}, operation)
    if 'receipt' in params or 'official_receipt' in params:
        raise ValueError('the host reads the original Gamma response')
    detail = call('trading.public', {'forecast': params['forecast']})
    if not detail['ok']:
        return detail
    data = detail['result']
    if method == 'trading.forecast_quote' and data['this_run'].get('quote', {}).get('status') not in (None, 'UNAVAILABLE'):
        return {'ok': True, 'result': data['this_run']}
    if method == 'trading.forecast_settle' and data['settlement'] and data['settlement']['status'] != 'DISPUTED':
        return {'ok': True, 'result': data['settlement']}
    try:
        source = events.receipt(data['terms']['slug'])
    except (OSError, ValueError, KeyError) as error:
        if method == 'trading.forecast_quote':
            return call(method, {**params, 'failure': str(error)}, operation)
        return {'ok': True, 'result': {'status': 'SOURCE_UNAVAILABLE', 'source': 'Polymarket Gamma', 'reason': str(error)}}
    if method == 'trading.forecast_settle' and 'evidence' not in params:
        official_url = events.binance_close_url(data['terms'])
        raw = json.loads(source['content'])
        if official_url and raw.get('umaResolutionStatus') == 'resolved' and raw.get('closed'):
            try:
                content, retrieved = events.get(official_url)
            except OSError:
                return call(method, {**params, 'receipt': source}, operation)
            params = {**params, 'official_receipt': {'source': 'Binance BTCUSDT final 1m candle', 'url': official_url,
                      'content': content, 'observed_at': data['terms']['end_at'], 'retrieved_at': retrieved}}
    return call(method, {**params, 'receipt': source}, operation)


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
    if method == 'trading.set_entry_reference':
        raise ValueError('confirmed opening-price updates use the explicit host data operation')
    params = value.get('params', {})
    if not isinstance(params, dict) or 'run_ref' in params or set(value) - {'method', 'params', 'operation_id'}:
        raise ValueError('supply only the method, its parameters and the stable write identity')

    def call(name, supplied, operation=None):
        envelope = {'method': name, 'params': {**supplied, 'run_ref': run_id}}
        if operation is not None:
            envelope['operation_id'] = operation
        response = subprocess.run(arguments, input=json.dumps(envelope).encode(), capture_output=True)
        return decode_node_response(response, operation)

    if (method in {'trading.forecast', 'trading.forecast_quote', 'trading.forecast_settle'} or
            method == 'trading.public' and params.keys() & {'event_search', 'event_slug', 'options_expiry'}):
        return event_request(release, method, params, value.get('operation_id'), call, key)

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

    if method == 'trading.personal' and ('condition' in params or 'followup' in params):
        # Exact historical reads use the service's phase check and need no policy import.
        return call(method, params)
    public_risk = method == 'trading.report' and params.get('purpose') == 'RISK' and params.get('decisions') == []
    silent_review = method == 'trading.assess_feedback' and params.get('outcome') == 'SILENT'
    if (silent_review or method in {'trading.personal', 'trading.personal_decision', 'trading.portfolio_decision',
                                    'trading.policy', 'trading.seed_policy', 'trading.report',
                                    'trading.begin_delivery'}) and not public_risk:
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
