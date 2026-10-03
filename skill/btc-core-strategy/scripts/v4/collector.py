"""Frozen multi-asset auxiliary measurements; no collector conclusion authority."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import sys

import collector_observation
import node_config
from .common import create, digest, instant, now, read, require

SCHEMA = 'MULTI_ASSET_AUXILIARY_V1'
ASSETS = ('BTC', 'QQQ', 'VOO')
INSTRUCTIONS = (
    'BTC、QQQ、VOO各自核验最新辅助测量；采集来源独立核验数为0，不能直接支持已确认事实、价格突破或交易。'
    'QQQ股票为EQ_QQQ，QQQUSDT是直接衍生品参考；VOO股票为EQ_VOO，SPYUSDT仅为代理参考。'
    '股数成交量不等于主动买卖资金，Regular Funding与Special股息调整分开；股票按交易日历和复权口径比较。'
    '按数据域和窗口保留局部有效值，来源时间未知不以抓取时间替代；盈利、估值等缺口继续独立补证。'
    'V2日周数据保留各自来源与覆盖；股票复权UNKNOWN及analysis_eligible=false只允许原始观察，不能确认趋势或关键位。'
    '场内爆仓估算不代表全市场，未覆盖时段及零记录不能推成零事件；普通资金费不包含股息调整。'
    '新增采集覆盖与数据刷新不自动触发深评或报告；实际判断是否改变原论点、风险或必要反馈。'
    'BTC卖竭日值按回踩等实际场景使用；核对回踩前后数据日期、组成项、来源口径与现货承接，'
    '日值未覆盖回踩只能作此前背景，低值不证明底部，回升不证明需求恢复；见references/seller-exhaustion.md。'
)


def provenance(item):
    """Retain report/transport identity, never file credentials or collector prose."""
    value = {key: item[key] for key in ('bundle_id', 'report_id')
             if isinstance(item.get(key), str) and 0 < len(item[key]) <= 512}
    transport = item.get('transport')
    if isinstance(transport, dict):
        value['transport'] = {key: transport[key] for key in (
            'text_message_id', 'document_message_id', 'received_at',
            'download_status', 'download_attempts', 'download_error') if key in transport}
    elif type(item.get('text_message_id')) is int:
        value['transport'] = {'text_message_id': item['text_message_id']}
    return value


def capture(project, export=None):
    """Only the local reader/export is read. No Telegram connection or sync."""
    reader_root = node_config.path('reader_code_dir', Path(project) / 'telegram_reader')
    if export is None and not (reader_root / 'reader.py').is_file():
        return None
    failure = None
    if export is not None:
        items = collector_observation.load_export(str(export))
    else:
        try:
            if str(reader_root) not in sys.path:
                sys.path.insert(0, str(reader_root))
            spec = importlib.util.spec_from_file_location('local_multi_asset_reader', reader_root / 'reader.py')
            reader = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reader)
            data_root = Path(os.environ.get('BTC_READER_DATA_DIR', str(
                node_config.path('reader_data_dir', reader_root / 'data')))).expanduser()
            items = reader.collector_observations(data_root / 'messages.sqlite3', 'all')
        except (OSError, ValueError, ImportError, RuntimeError):
            items, failure = [], 'LOCAL_COLLECTOR_UNAVAILABLE'
    review = collector_observation.review_exports(items)
    if 'assets' not in review:
        review = {'assets': {'BTC': ({'status': 'AUXILIARY', 'review': review}
                                    if review['accepted_observations'] else
                                    {'status': 'UNAVAILABLE', 'reasons': [failure or 'NO_USABLE_AUXILIARY']})}}
    assets = {}
    for asset in ASSETS:
        value = review['assets'].get(asset, {})
        item = next((r for r in items if isinstance(r, dict) and r.get('asset_id', 'BTC') == asset), {})
        trace = provenance(item)
        if value.get('status') != 'AUXILIARY':
            assets[asset] = {'status': 'UNAVAILABLE', 'reasons': [failure] if failure else
                             value.get('reasons', ['LATEST_SNAPSHOT_UNAVAILABLE']), **trace}
            continue
        generated = item.get('generated_at')
        if generated is None or instant(generated) > instant(now()):
            assets[asset] = {'status': 'UNAVAILABLE', 'reasons': ['INVALID_GENERATION_TIME']}
            continue
        if asset == 'BTC' and 'observation' not in value:
            accepted = value['review'].get('accepted_observations', [])
            datasets = {r['dataset']: r['measurements'] for r in accepted}
            sources = {sid: s for r in accepted for sid, s in r['sources'].items()}
            reasons = [{'dataset': r.get('dataset'), 'reasons': r['reasons']} for r in value['review'].get('rejected_observations', [])]
            observation = {'asset_id': asset, 'generated_at': generated, 'datasets': datasets,
                           'sources': sources, 'dataset_reasons': reasons}
        else:
            observation = value['observation']
        assets[asset] = {'status': 'AUXILIARY', **observation, **trace}
    return {'schema': SCHEMA, 'role': 'auxiliary_observation', 'captured_at': now(),
            'independent_verified_sources': 0, 'requires_independent_verification': True,
            'requires_current_source_check': True, 'assets': assets}


def validate(value):
    require(isinstance(value, dict) and value.get('schema') == SCHEMA, 'invalid collector snapshot')
    require(value.get('role') == 'auxiliary_observation' and type(value.get('independent_verified_sources')) is int
            and value['independent_verified_sources'] == 0 and value.get('requires_independent_verification') is True
            and value.get('requires_current_source_check') is True, 'collector trust boundary changed')
    require(set(value.get('assets', {})) == set(ASSETS), 'collector assets are incomplete')
    instant(value['captured_at'])
    for asset, row in value['assets'].items():
        require(row.get('status') in ('AUXILIARY', 'UNAVAILABLE'), 'invalid collector asset status')
        if row['status'] == 'AUXILIARY':
            require(row.get('asset_id') == asset and isinstance(row.get('datasets'), dict), 'collector asset differs')
            require(instant(row['generated_at']) <= instant(value['captured_at']), 'collector data is from the future')
    return value


def add_source(spec, files, snapshot_file):
    payload = validate(read(snapshot_file))
    raw_hash = digest(Path(snapshot_file).read_bytes())
    sid = 'collector_' + raw_hash[7:27]
    times = [r['generated_at'] for r in payload['assets'].values() if r['status'] == 'AUXILIARY']
    spec['sources'].append({'id': sid, 'origin': 'UNESTABLISHED:t.me', 'url': 'https://t.me/',
        'trust': 'UNTRUSTED_LEAD', 'published_at': max(times, key=instant) if times else payload['captured_at'],
        'retrieved_at': payload['captured_at'], 'start': min(times, key=instant) if times else payload['captured_at'],
        'end': max(times, key=instant) if times else payload['captured_at'], 'raw_hash': raw_hash})
    files[sid] = str(snapshot_file)
    spec['collector'] = {'source_id': sid, 'snapshot_id': digest(payload)}


def context(bundle, directory):
    binding = (bundle or {}).get('collector')
    if not binding:
        return None
    source = next(s for s in bundle['sources'] if s['id'] == binding['source_id'])
    require(source['trust'] == 'UNTRUSTED_LEAD', 'collector cannot be promoted to public raw')
    value = validate(read(Path(directory) / 'raw' / (source['raw_hash'][7:] + '.json')))
    require(digest(value) == binding['snapshot_id'], 'collector snapshot changed')
    assets = {}
    for asset, row in value['assets'].items():
        out = {k: deepcopy(v) for k, v in row.items() if k != 'datasets'}
        out['datasets'] = {}
        for name, ds in row.get('datasets', {}).items():
            pointer = '/assets/' + asset + '/datasets/' + name
            brief = {k: deepcopy(v) for k, v in ds.items() if k not in ('rows', 'calendar_periods', 'curve')}
            if isinstance(ds.get('curve'), list):
                brief['curve_count'] = len(ds['curve'])
            rows = ds.get('rows') or []
            brief.update(row_count=len(rows), latest=deepcopy(rows[-1]) if rows else None,
                         raw_ref={'source_id': source['id'], 'pointer': pointer, 'value_hash': digest(ds)})
            if name == 'seller_exhaustion_daily':
                measured = (ds.get('values') or {}).get('as_of_ms')
                last = next((r for r in rows if r.get('observed_ms') == measured), None)
                def utc_ms(value):
                    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
                brief['temporal_coverage'] = {
                    'data_date_utc': utc_ms(last['period_start_ms'])[:10] if last else None,
                    'period_start_utc': utc_ms(last['period_start_ms']) if last else None,
                    'period_end_exclusive_utc': utc_ms(last['period_end_ms']) if last else None,
                    'observed_at_utc': utc_ms(measured) if last else None,
                    'first_received_at': (row.get('transport') or {}).get('received_at'),
                    'publication_time_unknown': ds.get('publication_time_unknown', True),
                    'meaning': 'UTC measurement bucket, not publication time or proof of event coverage; compare the actual event and independently verify source timing.'}
            if name == 'macro':
                brief['latest'] = None  # Whole macro series remain at raw_ref.
            out['datasets'][name] = brief
        assets[asset] = out
    return {'snapshot_id': binding['snapshot_id'], 'captured_at': value['captured_at'], 'assets': assets,
            'independent_verified_sources': 0, 'instructions': INSTRUCTIONS}
