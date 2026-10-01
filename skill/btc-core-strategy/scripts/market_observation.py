#!/usr/bin/env python3
"""Anonymous public BTC evidence and an append-only, model-reviewed observation log.

This module measures and validates; it does not classify market patterns, execute
trades, edit strategy owners, or publish reports. See references/market-observation.md.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
HOUR = 3600
import node_config
DEFAULT_DB = node_config.path('observation_db', node_config.project() / '.strategy/market_observation.sqlite3')
EVIDENCE_SCHEMA = 'BTC_MARKET_EVIDENCE_V1'
REVIEW_SCHEMA = 'BTC_HOURLY_REVIEW_V1'
CONTEXT_SCHEMA = 'BTC_MARKET_CONTEXT_V1'
PATTERN_KINDS = {'ABSORPTION', 'POSSIBLE_DISTRIBUTION', 'ACTIVE_SELLING', 'DELEVERAGING', 'LEVERAGED_RISE_OR_SHORT_COVER'}
STAGES = {'UNRESOLVED', 'FORMING', 'ACCUMULATING', 'STRENGTHENING', 'WEAKENING', 'REVERSED', 'FALSIFIED'}
CONFIDENCES = {'UNRESOLVED', 'LOW', 'MEDIUM', 'HIGH'}
CURRENT_HOURLY_MODEL = {'name': 'gpt-6.1-sol', 'reasoning_effort': 'high'}
# Historical bindings remain readable, including the original isolated comparisons.
MODELS = {('gpt-6.1-sol', 'high'), ('gpt-6-sol', 'high'), ('gpt-5.6-sol', 'high'), ('gpt-5.6-sol', 'medium'), ('gpt-5.6-terra', 'high')}
ISSUES = {'OUTSIDE_REQUESTED_WINDOW', 'UNCLOSED_OR_FUTURE', 'INVALID_SAMPLE', 'DUPLICATE_SAMPLE', 'CONFLICTING_DUPLICATE', 'SOURCE_UNAVAILABLE'}
LIMITATIONS = ['Market patterns require model review; measurements do not establish intent or participant identity.', 'Binance spot, perpetuals, OI and funding share a source group.', 'Coinbase/Kraken price and volume do not independently confirm Binance taker flow.', 'USD and USDT quotes are separate units; no FX equivalence is assumed.', 'Missing measurements remain null; sums cover observed rows only.', 'Funding settlement schedule is not assumed; historical events are sparse, not hourly bars.']
SOURCE_INFO = {
    'binance_spot': {'group': 'BINANCE', 'market': 'SPOT', 'instrument': 'BTCUSDT', 'quote_unit': 'USDT'},
    'binance_futures': {'group': 'BINANCE', 'market': 'PERPETUAL', 'instrument': 'BTCUSDT', 'quote_unit': 'USDT'},
    'coinbase_spot': {'group': 'COINBASE', 'market': 'SPOT', 'instrument': 'BTC-USD', 'quote_unit': 'USD'},
    'kraken_spot': {'group': 'KRAKEN', 'market': 'SPOT', 'instrument': 'XBTUSD', 'quote_unit': 'USD'},
}
ALLOWED_ENDPOINTS = {
    'https://data-api.binance.vision/api/v3/klines',
    'https://api.binance.com/api/v3/klines',
    'https://fapi.binance.com/fapi/v1/klines',
    'https://fapi.binance.com/futures/data/openInterestHist',
    'https://fapi.binance.com/fapi/v1/fundingRate',
    'https://fapi.binance.com/fapi/v1/markPriceKlines',
    'https://fapi.binance.com/fapi/v1/indexPriceKlines',
    'https://api.exchange.coinbase.com/products/BTC-USD/candles',
    'https://api.kraken.com/0/public/OHLC',
}


class ObservationError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return 'sha256:' + hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def iso(epoch):
    precision = 'seconds' if float(epoch).is_integer() else 'milliseconds'
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec=precision).replace('+00:00', 'Z')


def timestamp(value):
    if not isinstance(value, str):
        raise ObservationError('time must be an ISO 8601 string with timezone')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.timestamp()
    except (ValueError, OverflowError):
        raise ObservationError('invalid or timezone-free timestamp') from None


def number(value, nonnegative=False, positive=False):
    if isinstance(value, bool):
        raise ObservationError('boolean is not a measurement')
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ObservationError('measurement is not numeric') from None
    if not math.isfinite(n) or (nonnegative and n < 0) or (positive and n <= 0):
        raise ObservationError('invalid measurement range')
    return n


def integer(value):
    n = number(value)
    if not n.is_integer():
        raise ObservationError('fractional epoch or integer field is forbidden')
    return int(n)


def millis_epoch(value):
    return integer(value) / 1000


def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ObservationError(f'{label}: invalid fields')


def utc_cutoff(as_of=None):
    now = time.time()
    wanted = timestamp(as_of) if as_of else now
    if wanted > now + 1:
        raise ObservationError('future as-of is forbidden')
    return int(wanted // HOUR * HOUR)


class PublicClient:
    """Fixed GET-only anonymous market endpoints; no credentials or redirects."""
    def __init__(self, timeout=12):
        self.timeout = min(max(number(timeout, positive=True), 1), 20)

    def get(self, endpoint, params):
        if endpoint not in ALLOWED_ENDPOINTS:
            raise ObservationError('endpoint is not an anonymous market-data allowlist member')
        if any(k.lower() in {'signature', 'apikey', 'api_key', 'token', 'timestamp'} for k in params):
            raise ObservationError('authentication parameters are forbidden')
        url = endpoint + '?' + urllib.parse.urlencode(params)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise ObservationError('market endpoint redirected; explicit review required')
        req = urllib.request.Request(url, headers={'User-Agent': 'BTC-Public-Observation/1.0', 'Accept': 'application/json'}, method='GET')
        with urllib.request.build_opener(NoRedirect).open(req, timeout=self.timeout) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ObservationError('public response exceeds bounded size')
        return json.loads(raw)


def spec(dataset_id, source, kind='CANDLES', interval=HOUR, count=240):
    return {'dataset_id': dataset_id, 'source': source, 'kind': kind, 'interval_seconds': interval, 'count': count}


def requested_bounds(cutoff, interval, count):
    # Binance weeks start Monday 00:00 UTC; the Unix epoch was Thursday.
    offset = 4 * 86400 if interval == 7 * 86400 else 0
    end = ((cutoff - offset) // interval) * interval + offset
    return end - count * interval, end


def _request_plan(s, cutoff):
    start, end = requested_bounds(cutoff, s['interval_seconds'], s['count'])
    start_ms, end_ms = start * 1000, end * 1000 - 1
    identifier, source, kind = s['dataset_id'], s['source'], s['kind']
    if source == 'coinbase_spot':
        endpoint = 'https://api.exchange.coinbase.com/products/BTC-USD/candles'
        params = {'granularity': 3600, 'start': iso(start), 'end': iso(end)}
    elif source == 'kraken_spot':
        endpoint = 'https://api.kraken.com/0/public/OHLC'
        params = {'pair': 'XBTUSD', 'interval': 60, 'since': start}
    elif kind == 'OI':
        endpoint = 'https://fapi.binance.com/futures/data/openInterestHist'
        # Include the boundary snapshot to measure quantity change for a full window.
        params = {'symbol': 'BTCUSDT', 'period': '1h', 'startTime': start_ms, 'endTime': end * 1000, 'limit': 500}
    elif kind == 'FUNDING':
        endpoint = 'https://fapi.binance.com/fapi/v1/fundingRate'
        params = {'symbol': 'BTCUSDT', 'startTime': start_ms, 'endTime': end * 1000, 'limit': 1000}
    else:
        interval = {3600: '1h', 86400: '1d', 604800: '1w'}[s['interval_seconds']]
        if kind == 'MARK':
            endpoint = 'https://fapi.binance.com/fapi/v1/markPriceKlines'
        elif kind == 'INDEX':
            endpoint = 'https://fapi.binance.com/fapi/v1/indexPriceKlines'
        elif source == 'binance_futures':
            endpoint = 'https://fapi.binance.com/fapi/v1/klines'
        else:
            endpoint = 'https://data-api.binance.vision/api/v3/klines'
        params = {'pair' if kind == 'INDEX' else 'symbol': 'BTCUSDT', 'interval': interval, 'startTime': start_ms, 'endTime': end_ms, 'limit': s['count']}
    return {'endpoint': endpoint, 'parameters': params}


def _raw_request(s, cutoff, client):
    request = _request_plan(s, cutoff)
    return client.get(request['endpoint'], request['parameters']), request


def normalize(s, raw, cutoff):
    """Normalize real provider rows, reject partial/future/bad/duplicate samples."""
    start, end = requested_bounds(cutoff, s['interval_seconds'], s['count'])
    source, kind, interval = s['source'], s['kind'], s['interval_seconds']
    issues, records = [], {}
    if source == 'kraken_spot':
        if not isinstance(raw, dict) or raw.get('error'):
            raise ObservationError('Kraken returned an error or invalid object')
        result = raw.get('result')
        if not isinstance(result, dict):
            raise ObservationError('Kraken result missing')
        keys = [key for key in result if key != 'last']
        if len(keys) != 1 or keys[0] not in {'XXBTZUSD', 'XBTUSD'}:
            raise ObservationError('Kraken pair mismatch')
        raw = result[keys[0]]
    if not isinstance(raw, list) or len(raw) > 1500:
        raise ObservationError('invalid or oversized public row array')
    for item in raw:
        try:
            if kind == 'OI':
                if not isinstance(item, dict) or item.get('symbol') != 'BTCUSDT':
                    raise ObservationError('OI symbol mismatch')
                at = millis_epoch(item['timestamp'])
                if at < start or at > end:
                    issues.append('OUTSIDE_REQUESTED_WINDOW')
                    continue
                if at % HOUR:
                    raise ObservationError('OI timestamp is not hourly aligned')
                row = {'time': iso(at), 'quantity_btc': number(item['sumOpenInterest'], nonnegative=True), 'notional_usdt': number(item['sumOpenInterestValue'], nonnegative=True)}
            elif kind == 'FUNDING':
                if not isinstance(item, dict) or item.get('symbol') != 'BTCUSDT':
                    raise ObservationError('funding symbol mismatch')
                at = millis_epoch(item['fundingTime'])
                if not start <= at <= end:
                    issues.append('OUTSIDE_REQUESTED_WINDOW')
                    continue
                row = {'time': iso(at), 'rate': number(item['fundingRate'])}
            else:
                if not isinstance(item, list):
                    raise ObservationError('candle must be an array')
                if source == 'coinbase_spot':
                    if len(item) != 6:
                        raise ObservationError('Coinbase candle shape mismatch')
                    at = integer(item[0])
                    op, hi, lo, close, vol = item[3], item[2], item[1], item[4], item[5]
                    quote_volume = buy_base = buy_quote = None
                elif source == 'kraken_spot':
                    if len(item) < 8:
                        raise ObservationError('Kraken candle shape mismatch')
                    at = integer(item[0])
                    op, hi, lo, close, vol = item[1], item[2], item[3], item[4], item[6]
                    quote_volume = buy_base = buy_quote = None
                else:
                    if len(item) < 11:
                        raise ObservationError('Binance candle shape mismatch')
                    at = millis_epoch(item[0])
                    if integer(item[6]) != (at + interval) * 1000 - 1:
                        raise ObservationError('candle close timestamp does not match interval')
                    op, hi, lo, close, vol = item[1], item[2], item[3], item[4], item[5]
                    quote_volume = number(item[7], nonnegative=True) if kind == 'CANDLES' else None
                    buy_base = number(item[9], nonnegative=True) if kind == 'CANDLES' else None
                    buy_quote = number(item[10], nonnegative=True) if kind == 'CANDLES' else None
                alignment = 4 * 86400 if interval == 7 * 86400 else 0
                if (at - alignment) % interval:
                    raise ObservationError('candle open timestamp not interval aligned')
                if at >= end or at + interval > cutoff:
                    issues.append('UNCLOSED_OR_FUTURE')
                    continue
                if at < start:
                    issues.append('OUTSIDE_REQUESTED_WINDOW')
                    continue
                row = {'time': iso(at), 'end': iso(at + interval), 'open': number(op, positive=True), 'high': number(hi, positive=True), 'low': number(lo, positive=True), 'close': number(close, positive=True)}
                if not row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']:
                    raise ObservationError('OHLC relationship invalid')
                if kind == 'CANDLES':
                    volume = number(vol, nonnegative=True)
                    if buy_base is not None and (buy_base > volume + 1e-8 or buy_quote > quote_volume + 1e-6):
                        raise ObservationError('taker volume exceeds total volume')
                    row.update({'volume_btc': volume, 'quote_volume': quote_volume, 'taker_buy_btc': buy_base, 'taker_buy_quote': buy_quote})
            if at in records:
                if row != records[at]:
                    issues.append('CONFLICTING_DUPLICATE')
                    records[at] = None
                else:
                    issues.append('DUPLICATE_SAMPLE')
            else:
                records[at] = row
        except (ValueError, KeyError, IndexError, TypeError, OverflowError):
            issues.append('INVALID_SAMPLE')
    rows = [v for _, v in sorted(records.items()) if v is not None]
    return build_dataset(s, rows, cutoff, issues)


def build_dataset(s, rows, cutoff, issues=None):
    start, end = requested_bounds(cutoff, s['interval_seconds'], s['count'])
    interval, kind = s['interval_seconds'], s['kind']
    if kind == 'FUNDING':
        expected = None  # Settlement schedules can change; do not invent missing zeros.
        missing = []
        complete = None
    else:
        expected = s['count'] + (1 if kind == 'OI' else 0)
        times = {timestamp(r['time']) for r in rows}
        missing = [iso(t) for t in range(start, end + (interval if kind == 'OI' else 0), interval) if t not in times]
        complete = len(rows) == expected and not missing
    info = SOURCE_INFO[s['source']]
    return {**s, **info, 'requested_start': iso(start), 'requested_end': iso(end), 'rows': rows,
            'coverage': {'expected_count': expected, 'actual_count': len(rows), 'complete': complete, 'first_time': rows[0]['time'] if rows else None, 'last_time': rows[-1]['time'] if rows else None, 'missing_times': missing},
            'issues': sorted(set(issues or []))}


def summarize_candles(rows, start, end, interval):
    selected = [r for r in rows if start <= timestamp(r['time']) < end]
    complete = len(selected) == (end - start) // interval
    base = {'start': iso(start), 'end': iso(end), 'expected_count': (end - start) // interval, 'actual_count': len(selected), 'complete': complete}
    if not selected:
        return {**base, 'measurements': None}
    first, last = selected[0], selected[-1]
    high, low = max(r['high'] for r in selected), min(r['low'] for r in selected)
    measurements = {'first_open': first['open'], 'last_close': last['close'], 'high': high, 'low': low, 'observed_return_ratio': last['close'] / first['open'] - 1,
                    'close_location_ratio': (last['close'] - low) / (high - low) if high > low else None}
    if 'volume_btc' in first:
        volume = sum(r['volume_btc'] for r in selected)
        measurements['volume_btc'] = volume
        # These are sums over observed bars, not a claimed continuous-window CVD.
        for suffix, total_key, buy_key in [('btc', 'volume_btc', 'taker_buy_btc'), ('quote', 'quote_volume', 'taker_buy_quote')]:
            known = all(r[buy_key] is not None and r[total_key] is not None for r in selected)
            total = sum(r[total_key] for r in selected) if known else None
            buy = sum(r[buy_key] for r in selected) if known else None
            net = 2 * buy - total if known else None
            measurements[f'net_taker_{suffix}'] = net
            measurements[f'net_taker_{suffix}_ratio'] = net / total if known and total else None
        measurements['quote_volume'] = sum(r['quote_volume'] for r in selected) if all(r['quote_volume'] is not None for r in selected) else None
    return {**base, 'measurements': measurements}


def summarize_point(rows, start, end, kind):
    selected = [r for r in rows if start <= timestamp(r['time']) <= end]
    if kind == 'OI':
        expected = int((end - start) // HOUR + 1)
        complete = len(selected) == expected
        measurements = None
        if selected:
            measurements = {'first_quantity_btc': selected[0]['quantity_btc'], 'last_quantity_btc': selected[-1]['quantity_btc'], 'first_notional_usdt': selected[0]['notional_usdt'], 'last_notional_usdt': selected[-1]['notional_usdt'], 'quantity_change_btc': selected[-1]['quantity_btc'] - selected[0]['quantity_btc'], 'notional_change_usdt': selected[-1]['notional_usdt'] - selected[0]['notional_usdt']}
        return {'start': iso(start), 'end': iso(end), 'expected_count': expected, 'actual_count': len(selected), 'complete': complete, 'measurements': measurements}
    selected = [r for r in selected if start < timestamp(r['time']) <= end]
    return {'start': iso(start), 'end': iso(end), 'expected_count': None, 'actual_count': len(selected), 'complete': None, 'measurements': {'rates': selected, 'sum_observed_rates': sum(r['rate'] for r in selected)} if selected else None}


def compute_windows(datasets, cutoff):
    windows = {}
    for hours in [4, 24, 72, 240]:
        start = cutoff - hours * HOUR
        w = {}
        for name, dataset in datasets.items():
            if dataset['interval_seconds'] != HOUR:
                continue
            if dataset['kind'] in {'CANDLES', 'MARK', 'INDEX'}:
                w[name] = summarize_candles(dataset['rows'], start, cutoff, HOUR)
            elif dataset['kind'] in {'OI', 'FUNDING'}:
                w[name] = summarize_point(dataset['rows'], start, cutoff, dataset['kind'])
        mark = {r['time']: r for r in datasets.get('binance_mark_1h', {}).get('rows', []) if start <= timestamp(r['time']) < cutoff}
        index = {r['time']: r for r in datasets.get('binance_index_1h', {}).get('rows', []) if start <= timestamp(r['time']) < cutoff}
        aligned = sorted(set(mark) & set(index))
        basis = [{'time': t, 'end': mark[t]['end'], 'mark_close_usdt': mark[t]['close'], 'index_close_usdt': index[t]['close'], 'basis_ratio': mark[t]['close'] / index[t]['close'] - 1} for t in aligned]
        w['binance_basis_1h'] = {'start': iso(start), 'end': iso(cutoff), 'expected_count': hours, 'actual_count': len(basis), 'complete': len(basis) == hours, 'measurements': {'rows': basis, 'last_basis_ratio': basis[-1]['basis_ratio'], 'mean_observed_basis_ratio': sum(b['basis_ratio'] for b in basis) / len(basis)} if basis else None}
        windows[f'{hours}h'] = w
    return windows


def evidence_identity(evidence):
    # Retrieval time/status are provenance, not evidence revisions. Only normalized
    # measurement content and requested coverage identify a market-data revision.
    body = {k: evidence[k] for k in ['schema', 'observed_hour', 'cutoff', 'datasets', 'windows']}
    return digest(body)


def collect(as_of=None, deep=False, timeout=12, client=None, cycle_cache=None):
    cutoff = utc_cutoff(as_of)
    client = client or PublicClient(timeout)
    specs = [spec('binance_spot_1h', 'binance_spot'), spec('binance_futures_1h', 'binance_futures'), spec('coinbase_spot_1h', 'coinbase_spot'), spec('kraken_spot_1h', 'kraken_spot'), spec('binance_oi_1h', 'binance_futures', 'OI'), spec('binance_funding', 'binance_futures', 'FUNDING'), spec('binance_mark_1h', 'binance_futures', 'MARK'), spec('binance_index_1h', 'binance_futures', 'INDEX')]
    specs += [spec('binance_spot_1d', 'binance_spot', interval=86400, count=90), spec('binance_spot_1w', 'binance_spot', interval=604800, count=52)]
    if cycle_cache is not None:
        validate_evidence(cycle_cache)
    datasets, retrievals, gaps = {}, {}, {}
    def fetch(s):
        started = time.monotonic()
        request = _request_plan(s, cutoff)
        if cycle_cache and not deep and s['interval_seconds'] > HOUR:
            old = cycle_cache['datasets'].get(s['dataset_id'])
            receipt = cycle_cache['retrievals'].get(s['dataset_id'])
            if old and receipt and old['coverage']['complete'] and not old['issues'] and receipt['status'] == 'RECEIVED':
                if all(receipt[k] == request[k] for k in ('endpoint', 'parameters')):
                    # Same completed daily/weekly window: retain its real retrieval time.
                    return s['dataset_id'], old, receipt
        try:
            raw, request = _raw_request(s, cutoff, client)
            dataset = normalize(s, raw, cutoff)
            retrieval = {**request, 'retrieved_at': iso(time.time()), 'status': 'RECEIVED', 'elapsed_seconds': round(time.monotonic() - started, 3)}
        except Exception as exc:
            # Do not persist response bodies, request headers, or arbitrary exceptions.
            if isinstance(exc, urllib.error.HTTPError):
                failure = f'HTTP_{exc.code}'
            elif isinstance(exc, (TimeoutError, urllib.error.URLError)):
                failure = 'NETWORK_OR_TIMEOUT'
            else:
                failure = 'INVALID_PUBLIC_RESPONSE'
            dataset = build_dataset(s, [], cutoff, ['SOURCE_UNAVAILABLE'])
            retrieval = {**request, 'retrieved_at': iso(time.time()), 'status': failure, 'elapsed_seconds': round(time.monotonic() - started, 3)}
        return s['dataset_id'], dataset, retrieval
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for identifier, dataset, retrieval in pool.map(fetch, specs):
            datasets[identifier] = dataset
            retrievals[identifier] = retrieval
            if not dataset['rows'] or dataset['coverage']['complete'] is False or dataset['issues']:
                gaps[identifier] = {'coverage': dataset['coverage'], 'issues': dataset['issues'], 'status': retrieval['status']}
    evidence = {'schema': EVIDENCE_SCHEMA, 'observed_hour': iso(cutoff - HOUR), 'cutoff': iso(cutoff), 'datasets': datasets, 'windows': compute_windows(datasets, cutoff), 'gaps': gaps, 'retrievals': retrievals,
                'limitations': list(LIMITATIONS)}
    evidence['evidence_id'] = evidence_identity(evidence)
    evidence['retrieval_id'] = digest({'evidence_id': evidence['evidence_id'], 'retrievals': retrievals})
    return evidence


def validate_evidence(evidence):
    exact_keys(evidence, {'schema', 'observed_hour', 'cutoff', 'datasets', 'windows', 'gaps', 'retrievals', 'limitations', 'evidence_id', 'retrieval_id'}, 'evidence')
    if evidence['limitations'] != LIMITATIONS:
        raise ObservationError('evidence limitations must be the fixed public-data contract')
    if evidence['schema'] != EVIDENCE_SCHEMA:
        raise ObservationError('unsupported evidence schema')
    cutoff = timestamp(evidence['cutoff'])
    if cutoff % HOUR or evidence['cutoff'] != iso(cutoff) or evidence['observed_hour'] != iso(cutoff - HOUR) or cutoff > time.time():
        raise ObservationError('invalid evidence cutoff/hour')
    if not isinstance(evidence['datasets'], dict) or not 8 <= len(evidence['datasets']) <= 10:
        raise ObservationError('all requested data dimensions must exist, including gaps')
    expected = {'binance_spot_1h', 'binance_futures_1h', 'coinbase_spot_1h', 'kraken_spot_1h', 'binance_oi_1h', 'binance_funding', 'binance_mark_1h', 'binance_index_1h'}
    if not expected <= set(evidence['datasets']) or set(evidence['datasets']) - expected - {'binance_spot_1d', 'binance_spot_1w'}:
        raise ObservationError('unexpected/missing market dataset')
    registry = {s['dataset_id']: s for s in [spec('binance_spot_1h', 'binance_spot'), spec('binance_futures_1h', 'binance_futures'), spec('coinbase_spot_1h', 'coinbase_spot'), spec('kraken_spot_1h', 'kraken_spot'), spec('binance_oi_1h', 'binance_futures', 'OI'), spec('binance_funding', 'binance_futures', 'FUNDING'), spec('binance_mark_1h', 'binance_futures', 'MARK'), spec('binance_index_1h', 'binance_futures', 'INDEX'), spec('binance_spot_1d', 'binance_spot', interval=86400, count=90), spec('binance_spot_1w', 'binance_spot', interval=604800, count=52)]}
    for identifier, ds in evidence['datasets'].items():
        exact_keys(ds, set(registry[identifier]) | {'group', 'market', 'instrument', 'quote_unit', 'requested_start', 'requested_end', 'rows', 'coverage', 'issues'}, identifier)
        if any(ds[k] != v for k, v in registry[identifier].items()) or any(ds[k] != v for k, v in SOURCE_INFO[ds['source']].items()):
            raise ObservationError('source/market/unit/interval identity mismatch')
        if not isinstance(ds['issues'], list) or any(issue not in ISSUES for issue in ds['issues']):
            raise ObservationError('unknown data-quality issue')
        if not isinstance(ds['rows'], list) or len(ds['rows']) > 1000:
            raise ObservationError('invalid normalized rows')
        prev = -math.inf
        start, end = requested_bounds(int(cutoff), ds['interval_seconds'], ds['count'])
        for row in ds['rows']:
            at = timestamp(row.get('time'))
            if row['time'] != iso(at) or at <= prev or at < start or at > end or (ds['kind'] not in {'OI', 'FUNDING'} and at == end):
                raise ObservationError('out-of-window, duplicate or unordered row')
            prev = at
            if ds['kind'] == 'OI':
                exact_keys(row, {'time', 'quantity_btc', 'notional_usdt'}, 'OI row')
                if at % HOUR:
                    raise ObservationError('OI time not aligned')
                number(row['quantity_btc'], nonnegative=True)
                number(row['notional_usdt'], nonnegative=True)
            elif ds['kind'] == 'FUNDING':
                exact_keys(row, {'time', 'rate'}, 'funding row')
                number(row['rate'])
            else:
                keys = {'time', 'end', 'open', 'high', 'low', 'close'}
                if ds['kind'] == 'CANDLES':
                    keys |= {'volume_btc', 'quote_volume', 'taker_buy_btc', 'taker_buy_quote'}
                exact_keys(row, keys, 'candle row')
                offset = 4 * 86400 if ds['interval_seconds'] == 604800 else 0
                if (at - offset) % ds['interval_seconds'] or timestamp(row['end']) != at + ds['interval_seconds'] or timestamp(row['end']) > end:
                    raise ObservationError('invalid candle interval')
                for key in ['open', 'high', 'low', 'close']:
                    number(row[key], positive=True)
                if not row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']:
                    raise ObservationError('invalid OHLC')
                if ds['kind'] == 'CANDLES':
                    number(row['volume_btc'], nonnegative=True)
                    is_binance = ds['source'].startswith('binance')
                    for key in ['quote_volume', 'taker_buy_btc', 'taker_buy_quote']:
                        if is_binance:
                            number(row[key], nonnegative=True)
                        elif row[key] is not None:
                            raise ObservationError('provider does not supply this taker/quote measurement')
                    if is_binance and (row['taker_buy_btc'] > row['volume_btc'] + 1e-8 or row['taker_buy_quote'] > row['quote_volume'] + 1e-6):
                        raise ObservationError('invalid taker relation')
        if ds != build_dataset(registry[identifier], ds['rows'], int(cutoff), ds['issues']):
            raise ObservationError('dataset coverage is not derived from its rows')
    if evidence['windows'] != compute_windows(evidence['datasets'], int(cutoff)):
        raise ObservationError('window measurement mismatch')
    if evidence['evidence_id'] != evidence_identity(evidence):
        raise ObservationError('evidence fingerprint mismatch')
    if set(evidence['retrievals']) != set(evidence['datasets']):
        raise ObservationError('retrieval provenance missing')
    for source, retrieval in evidence['retrievals'].items():
        if not isinstance(retrieval, dict) or set(retrieval) != {'endpoint', 'parameters', 'retrieved_at', 'status', 'elapsed_seconds'}:
            raise ObservationError('invalid retrieval metadata')
        expected_request = _request_plan(registry[source], int(cutoff))
        if retrieval['endpoint'] != expected_request['endpoint'] or retrieval['parameters'] != expected_request['parameters']:
            raise ObservationError('public source request provenance mismatch')
        import re
        if not isinstance(retrieval['status'], str) or not re.fullmatch(r'RECEIVED|NETWORK_OR_TIMEOUT|INVALID_PUBLIC_RESPONSE|HTTP_[1-5][0-9]{2}', retrieval['status']):
            raise ObservationError('invalid public retrieval status')
        fetched_at = timestamp(retrieval.get('retrieved_at'))
        earliest = cutoff if registry[source]['interval_seconds'] == HOUR else timestamp(evidence['datasets'][source]['requested_end'])
        if fetched_at > time.time() + 1 or fetched_at < earliest:
            raise ObservationError('retrieval time is future or precedes evidence cutoff')
        number(retrieval.get('elapsed_seconds'), nonnegative=True)
    if evidence['retrieval_id'] != digest({'evidence_id': evidence['evidence_id'], 'retrievals': evidence['retrievals']}):
        raise ObservationError('retrieval fingerprint mismatch')
    expected_gaps = {name: {'coverage': ds['coverage'], 'issues': ds['issues'], 'status': evidence['retrievals'][name]['status']} for name, ds in evidence['datasets'].items() if not ds['rows'] or ds['coverage']['complete'] is False or ds['issues']}
    if evidence['gaps'] != expected_gaps:
        raise ObservationError('gap records do not match coverage')
    return evidence


def reference_ids(evidence):
    refs = {f'datasets.{name}' for name in evidence['datasets']}
    refs |= {f'windows.{window}.{name}' for window, sources in evidence['windows'].items() for name in sources}
    refs |= {f'gaps.{name}' for name in evidence['gaps']}
    return refs


def _text(value, label, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ObservationError(f'{label}: nonempty bounded text required')
    # Reject common credential payloads rather than silently copying them into SQLite.
    import re
    if re.search(r'(?i)(authorization\s*:|bearer\s+[a-z0-9._-]{12,}|cookie\s*:|-----BEGIN [A-Z ]*PRIVATE KEY|sk-[a-z0-9_-]{16,}|api[_ -]?key\s*[:=])', value):
        raise ObservationError(f'{label}: credential-like content forbidden')
    return value


def _texts(value, label, maximum=20):
    if not isinstance(value, list) or len(value) > maximum:
        raise ObservationError(f'{label}: bounded text list required')
    for entry in value:
        _text(entry, label)


def _has_new_rows(evidence, old):
    if old is None:
        return any(ds['rows'] for ds in evidence['datasets'].values())
    for name, ds in evidence['datasets'].items():
        previous = {r['time']: r for r in old['datasets'].get(name, {}).get('rows', [])}
        if any(previous.get(row['time']) != row for row in ds['rows']):
            return True
    return False


def validate_review(evidence, review, context, *, model_market=False):
    validate_evidence(evidence)
    exact_keys(review, {'schema', 'evidence_id', 'observed_hour', 'context_id', 'model', 'assessment_summary', 'patterns', 'changes_since_hour', 'changes_since_deep', 'deep_review'}, 'review')
    if review['schema'] != REVIEW_SCHEMA or review['evidence_id'] != evidence['evidence_id'] or review['observed_hour'] != evidence['observed_hour']:
        raise ObservationError('review evidence binding mismatch')
    if review['context_id'] != context['context_id']:
        raise ObservationError('review does not bind the current three-baseline context')
    exact_keys(review['model'], {'name', 'reasoning_effort'}, 'model')
    if (review['model']['name'], review['model']['reasoning_effort']) not in MODELS:
        raise ObservationError('unsupported hourly model route')
    _text(review['assessment_summary'], 'assessment summary', 4000)
    if model_market:
        patterns = review['patterns']
        kinds = [p.get('kind') for p in patterns if isinstance(p, dict)] if isinstance(patterns, list) else []
        if not isinstance(patterns, list) or len(kinds) != len(patterns) or len(set(kinds)) != len(kinds) or not set(kinds) <= PATTERN_KINDS:
            raise ObservationError('optional legacy patterns need distinct supported kinds; open market analysis belongs in revision 5 fields')
    elif not isinstance(review['patterns'], list) or len(review['patterns']) != 5 or {p.get('kind') for p in review['patterns'] if isinstance(p, dict)} != PATTERN_KINDS:
        raise ObservationError('review must examine all five distinct competing patterns')
    refs = reference_ids(evidence)
    for p in review['patterns']:
        exact_keys(p, {'kind', 'stage', 'confidence', 'support_refs', 'counter_refs', 'interpretation', 'next_evidence'}, 'pattern')
        if p['stage'] not in STAGES or p['confidence'] not in CONFIDENCES:
            raise ObservationError('unknown pattern stage/confidence')
        for name in ['support_refs', 'counter_refs']:
            _texts(p[name], name, 30)
            if len(set(p[name])) != len(p[name]) or any(ref not in refs for ref in p[name]):
                raise ObservationError('invalid or duplicate evidence reference')
        if p['stage'] != 'UNRESOLVED':
            supported = False
            for ref in p['support_refs']:
                parts = ref.split('.')
                if parts[0] == 'datasets' and evidence['datasets'][parts[1]]['rows']:
                    supported = True
                if parts[0] == 'windows' and evidence['windows'][parts[1]][parts[2]]['measurements'] is not None:
                    supported = True
            if not supported:
                raise ObservationError('resolved pattern stage requires actual supporting measurements, not only gaps')
        _text(p['interpretation'], 'pattern interpretation')
        _texts(p['next_evidence'], 'next evidence', 10)
        if not p['next_evidence']:
            raise ObservationError('each pattern needs discriminating next evidence')
        if p['stage'] == 'REVERSED' and not context['baselines'].get('HOUR') and not context['baselines'].get('DEEP'):
            raise ObservationError('first observation cannot claim reversal from a missing baseline')
    for field, baseline in [('changes_since_hour', 'HOUR'), ('changes_since_deep', 'DEEP')]:
        _texts(review[field], field, 20)
        if review[field] and not context['baselines'].get(baseline):
            raise ObservationError(f'{field}: absent baseline cannot have relative changes')
    exact_keys(review['deep_review'], {'required', 'reasons'}, 'deep review')
    if type(review['deep_review']['required']) is not bool:
        raise ObservationError('deep_review.required must be boolean')
    _texts(review['deep_review']['reasons'], 'deep review reasons', 10)
    if review['deep_review']['required'] and not review['deep_review']['reasons']:
        raise ObservationError('deep review needs explicit discriminating reasons')
    # The tool checks traceability/novel input; the model assesses significance.
    old = context.get('successful_deep', {}).get('evidence') if context.get('successful_deep') else None
    novel = _has_new_rows(evidence, old)
    review_time = max(timestamp(r['retrieved_at']) for r in evidence['retrievals'].values())
    due = bool(context['baselines'].get('DEEP') and review_time - timestamp(context['baselines']['DEEP']['completed_at']) >= 24 * HOUR and novel)
    initialization_due = not bool(context['baselines'].get('DEEP')) or bool(context.get('successful_deep') and context['successful_deep'].get('accepted_assessment_status') == 'LEGACY_COMPLETION_PAYLOAD_MISSING')
    return {'valid': True, 'review_id': digest(review), 'binding': {'evidence_id': evidence['evidence_id'], 'review_id': digest(review), 'observed_hour': evidence['observed_hour'], 'context_id': context['context_id']}, 'has_new_valid_evidence': novel, 'periodic_review_due': due, 'initialization_review_due': initialization_due, 'deep_eligible': bool(review['deep_review']['required'] or due or initialization_due)}


_SCHEMA_SQL = '''
CREATE TABLE IF NOT EXISTS evidence (
 evidence_id TEXT PRIMARY KEY, observed_hour TEXT NOT NULL, revision INTEGER NOT NULL,
 payload TEXT NOT NULL, first_seen TEXT NOT NULL, UNIQUE(observed_hour,revision)
);
CREATE TABLE IF NOT EXISTS retrieval (
 retrieval_id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
 provenance TEXT NOT NULL, retrieved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review (
 review_id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
 observed_hour TEXT NOT NULL, context_id TEXT NOT NULL, payload TEXT NOT NULL,
 context_snapshot TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS review_hour_idx ON review(observed_hour,created_at);
CREATE TABLE IF NOT EXISTS baseline (
 kind TEXT PRIMARY KEY CHECK(kind IN ('HOUR','DEEP','REPORT')),
 review_id TEXT NOT NULL REFERENCES review(review_id), completed_at TEXT NOT NULL,
 proof_identity TEXT
);
CREATE TABLE IF NOT EXISTS completion_attempt (
 kind TEXT NOT NULL, review_id TEXT NOT NULL REFERENCES review(review_id),
 proof_identity TEXT NOT NULL, completed_at TEXT NOT NULL, outcome TEXT NOT NULL,
 accepted_market_assessment TEXT, report_fingerprint TEXT,
 PRIMARY KEY(kind,review_id,proof_identity,outcome)
);
'''


def _connect(db_path, readonly=False):
    path = Path(db_path).expanduser().resolve()
    if readonly:
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    if not readonly:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version not in {0, 1}:
            conn.close()
            raise ObservationError('unsupported observation database version')
        conn.executescript(_SCHEMA_SQL)
        columns = {r[1] for r in conn.execute('PRAGMA table_info(completion_attempt)')}
        for column in ('accepted_market_assessment', 'report_fingerprint'):
            if column not in columns:
                conn.execute(f'ALTER TABLE completion_attempt ADD COLUMN {column} TEXT')
        conn.execute('PRAGMA user_version=1')
        conn.commit()
    return conn


def _read_review(conn, review_id, full=True):
    row = conn.execute('SELECT r.*, e.payload AS evidence_payload FROM review r JOIN evidence e USING(evidence_id) WHERE r.review_id=?', (review_id,)).fetchone()
    if row is None:
        raise ObservationError('review does not exist')
    result = {'binding': {'evidence_id': row['evidence_id'], 'review_id': row['review_id'], 'observed_hour': row['observed_hour'], 'context_id': row['context_id']}, 'review': json.loads(row['payload'])}
    if full:
        result['evidence'] = json.loads(row['evidence_payload'])
    return result


def _context(conn=None, limit=240):
    baselines = {}
    if conn:
        for row in conn.execute('SELECT b.*,r.evidence_id,r.observed_hour,r.context_id FROM baseline b JOIN review r USING(review_id)'):
            baselines[row['kind']] = {k: row[k] for k in ['review_id', 'evidence_id', 'observed_hour', 'context_id', 'completed_at', 'proof_identity']}
    context = {'schema': CONTEXT_SCHEMA, 'context_id': digest(baselines), 'baselines': baselines, 'previous_hour': None, 'successful_deep': None, 'reported_conclusion': None, 'since_deep': [], 'since_deep_total': 0, 'since_deep_truncated': False}
    if conn:
        for kind, key in [('HOUR', 'previous_hour'), ('DEEP', 'successful_deep'), ('REPORT', 'reported_conclusion')]:
            if kind in baselines:
                result = _read_review(conn, baselines[kind]['review_id'])
                if kind in {'DEEP', 'REPORT'}:
                    columns = {r[1] for r in conn.execute('PRAGMA table_info(completion_attempt)')}
                    accepted = None
                    if 'accepted_market_assessment' in columns:
                        accepted = conn.execute("SELECT accepted_market_assessment,report_fingerprint FROM completion_attempt WHERE kind=? AND review_id=? AND proof_identity=? AND outcome='SUCCEEDED'", (kind, baselines[kind]['review_id'], baselines[kind]['proof_identity'])).fetchone()
                    result['original_hourly_candidate'] = result.pop('review')
                    result['accepted_market_assessment'] = json.loads(accepted['accepted_market_assessment']) if accepted and accepted['accepted_market_assessment'] else None
                    result['report_fingerprint'] = accepted['report_fingerprint'] if accepted else None
                    result['accepted_assessment_status'] = 'AVAILABLE' if result['accepted_market_assessment'] is not None else 'LEGACY_COMPLETION_PAYLOAD_MISSING'
                context[key] = result
        since = baselines.get('DEEP', {}).get('observed_hour', '0000')
        count = conn.execute('SELECT COUNT(*) FROM review WHERE observed_hour>=?', (since,)).fetchone()[0]
        # Pagination is explicit: the caller must fetch the remaining hours before
        # describing the entire accumulation when the bounded response truncates.
        context['since_deep_total'] = count
        context['since_deep_truncated'] = count > limit
        for row in conn.execute('SELECT review_id,payload,observed_hour FROM review WHERE observed_hour>=? ORDER BY observed_hour DESC,created_at DESC LIMIT ?', (since, limit)):
            review = json.loads(row['payload'])
            context['since_deep'].append({'review_id': row['review_id'], 'observed_hour': row['observed_hour'], 'assessment_summary': review['assessment_summary'], 'patterns': [{'kind': p['kind'], 'stage': p['stage'], 'confidence': p['confidence']} for p in review['patterns']], 'changes_since_deep': review['changes_since_deep'], 'deep_review': review['deep_review']})
        context['since_deep'].reverse()
    return context


def read_context(db_path=DEFAULT_DB, limit=240):
    if not 1 <= int(limit) <= 10000:
        raise ObservationError('context limit must be between 1 and 10000')
    if not Path(db_path).exists():
        return _context()
    with _connect(db_path, readonly=True) as conn:
        return _context(conn, int(limit))


def _store_retrieval(conn, evidence):
    latest_time = max(r['retrieved_at'] for r in evidence['retrievals'].values())
    conn.execute('INSERT OR IGNORE INTO retrieval VALUES(?,?,?,?)', (evidence['retrieval_id'], evidence['evidence_id'], canonical(evidence['retrievals']), latest_time))


def record(evidence, review, db_path=DEFAULT_DB):
    validate_evidence(evidence)
    with _connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = _context(conn)
        latest = current.get('previous_hour')
        if latest and latest['binding']['evidence_id'] == evidence['evidence_id']:
            if canonical(review) != canonical(latest['review']):
                validate_review(evidence, review, current)
                if review['model'] != CURRENT_HOURLY_MODEL:
                    raise ObservationError('new hourly review requires gpt-6.1-sol/high')
            # Same complete-hour market input is one canonical review even if a
            # model's prose varies on a rerun. Never manufacture a new revision.
            _store_retrieval(conn, evidence)
            return {'status': 'UNCHANGED', 'binding': latest['binding']}
        validation = validate_review(evidence, review, current)
        if review['model'] != CURRENT_HOURLY_MODEL:
            raise ObservationError('new hourly review requires gpt-6.1-sol/high')
        if latest and timestamp(review['observed_hour']) < timestamp(latest['binding']['observed_hour']):
            raise ObservationError('late older-hour review cannot overwrite latest-hour baseline')
        review_id = validation['review_id']
        existing = conn.execute('SELECT 1 FROM review WHERE review_id=?', (review_id,)).fetchone()
        if existing:
            _store_retrieval(conn, evidence)
            return {'status': 'UNCHANGED', 'binding': validation['binding']}
        created = iso(time.time())
        if conn.execute('SELECT 1 FROM evidence WHERE evidence_id=?', (evidence['evidence_id'],)).fetchone() is None:
            revision = conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM evidence WHERE observed_hour=?', (evidence['observed_hour'],)).fetchone()[0]
            conn.execute('INSERT INTO evidence VALUES(?,?,?,?,?)', (evidence['evidence_id'], evidence['observed_hour'], revision, canonical(evidence), created))
        _store_retrieval(conn, evidence)
        conn.execute('INSERT INTO review VALUES(?,?,?,?,?,?,?)', (review_id, evidence['evidence_id'], review['observed_hour'], review['context_id'], canonical(review), canonical(current), created))
        conn.execute('INSERT OR REPLACE INTO baseline VALUES(?,?,?,NULL)', ('HOUR', review_id, created))
        return {'status': 'RECORDED', **validation}


def load_review_binding(binding, db_path=DEFAULT_DB):
    exact_keys(binding, {'evidence_id', 'review_id', 'observed_hour', 'context_id'}, 'hourly review binding')
    if not Path(db_path).exists():
        raise ObservationError('hourly observation database missing')
    with _connect(db_path, readonly=True) as conn:
        result = _read_review(conn, binding['review_id'])
        if result['binding'] != binding:
            raise ObservationError('hourly review binding does not match stored immutable review')
        row = conn.execute('SELECT context_snapshot FROM review WHERE review_id=?', (binding['review_id'],)).fetchone()
        validation = validate_review(result['evidence'], result['review'], json.loads(row['context_snapshot']))
        return {**result, **validation}


def record_completion(db_path, kind, binding, proof_identity, completed_at, outcome='SUCCEEDED', *, accepted_market_assessment=None, report_fingerprint=None):
    """Internal adapter for strategy_contract after validating actual bundle/receipt.

    No CLI exposes this operation. A model's success flag is not a completion proof.
    The integration owner must hash validated physical artifacts into proof_identity.
    """
    import re
    if kind not in {'DEEP', 'REPORT'} or outcome not in {'SUCCEEDED', 'FAILED'}:
        raise ObservationError('invalid completion kind/outcome')
    if not isinstance(proof_identity, str) or not re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', proof_identity):
        raise ObservationError('completion needs the SHA256 of validated contract artifacts')
    if outcome == 'SUCCEEDED':
        from pattern_semantics import validate_accepted_market_assessment
        accepted_errors = validate_accepted_market_assessment(accepted_market_assessment)
        if accepted_errors:
            raise ObservationError('; '.join(accepted_errors))
        if kind == 'REPORT' and (not isinstance(report_fingerprint, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', report_fingerprint)):
            raise ObservationError('successful REPORT needs the validated report fingerprint')
        if kind == 'DEEP' and report_fingerprint is not None:
            raise ObservationError('DEEP completion cannot claim a report fingerprint')
    elif accepted_market_assessment is not None or report_fingerprint is not None:
        raise ObservationError('failed completion cannot contain an accepted assessment')
    completed = timestamp(completed_at)
    if completed > time.time() + 1 or completed < timestamp(binding['observed_hour']) + HOUR:
        raise ObservationError('completion time is future or before evidence cutoff')
    loaded = load_review_binding(binding, db_path)
    if kind == 'DEEP' and not loaded['deep_eligible']:
        raise ObservationError('review has no validated deep candidate or periodic review')
    with _connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        # Validated retries and pending-to-delivered replay cannot reset the
        # successful-deep clock. First successful completion for a review wins.
        first_success = conn.execute("SELECT completed_at,proof_identity,accepted_market_assessment,report_fingerprint FROM completion_attempt WHERE kind=? AND review_id=? AND outcome='SUCCEEDED' ORDER BY completed_at LIMIT 1", (kind, binding['review_id'])).fetchone()
        if first_success and outcome == 'SUCCEEDED':
            if first_success['accepted_market_assessment'] != canonical(accepted_market_assessment) or first_success['report_fingerprint'] != report_fingerprint:
                raise ObservationError('same review completion cannot replace its accepted assessment/report')
            return {'kind': kind, 'outcome': outcome, 'binding': binding, 'status': 'UNCHANGED', 'completed_at': first_success['completed_at']}
        # A later completed deep/report cannot be replaced by late older output.
        current = conn.execute('SELECT b.*,r.observed_hour,e.revision FROM baseline b JOIN review r USING(review_id) JOIN evidence e USING(evidence_id) WHERE kind=?', (kind,)).fetchone()
        target_revision = conn.execute('SELECT revision FROM evidence WHERE evidence_id=?', (binding['evidence_id'],)).fetchone()[0]
        if current and ((timestamp(current['observed_hour']), current['revision']) > (timestamp(binding['observed_hour']), target_revision) or timestamp(current['completed_at']) > completed):
            raise ObservationError('late completion cannot move a successful baseline backward')
        if kind == 'REPORT' and outcome == 'SUCCEEDED':
            completed_deep = conn.execute("SELECT accepted_market_assessment FROM completion_attempt WHERE kind='DEEP' AND review_id=? AND outcome='SUCCEEDED' ORDER BY completed_at LIMIT 1", (binding['review_id'],)).fetchone()
            if not completed_deep:
                raise ObservationError('reported baseline requires a successful deep for the same review')
            if completed_deep['accepted_market_assessment'] != canonical(accepted_market_assessment):
                raise ObservationError('reported accepted assessment differs from the completed deep assessment')
        conn.execute('INSERT OR IGNORE INTO completion_attempt VALUES(?,?,?,?,?,?,?)', (kind, binding['review_id'], proof_identity, iso(completed), outcome, canonical(accepted_market_assessment) if accepted_market_assessment is not None else None, report_fingerprint))
        if outcome == 'SUCCEEDED':
            conn.execute('INSERT OR REPLACE INTO baseline VALUES(?,?,?,?)', (kind, binding['review_id'], iso(completed), proof_identity))
    return {'kind': kind, 'outcome': outcome, 'binding': binding}


def prune(db_path=DEFAULT_DB, now=None):
    now_time = timestamp(now) if now else time.time()
    if now_time > time.time() + 1:
        raise ObservationError('future retention time forbidden')
    cutoff = iso(now_time - 90 * 86400)
    if not Path(db_path).exists():
        return {'deleted_reviews': 0, 'deleted_evidence': 0}
    with _connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        # Keep all pending deep candidates and successful/current report/deep
        # baseline evidence, including the predecessor snapshots needed to audit.
        pinned = {r[0] for r in conn.execute('SELECT review_id FROM baseline')}
        latest_deep = conn.execute("SELECT r.observed_hour,e.revision FROM baseline b JOIN review r USING(review_id) JOIN evidence e USING(evidence_id) WHERE b.kind='DEEP'").fetchone()
        for row in conn.execute('SELECT r.review_id,r.payload,r.context_snapshot,r.observed_hour,e.revision FROM review r JOIN evidence e USING(evidence_id)'):
            review = json.loads(row['payload'])
            stored_context = json.loads(row['context_snapshot'])
            deep_before = stored_context['baselines'].get('DEEP')
            periodic_pending = bool(deep_before and timestamp(row['observed_hour']) + HOUR - timestamp(deep_before['completed_at']) >= 24 * HOUR)
            candidate = review['deep_review']['required'] or not deep_before or periodic_pending
            covered = latest_deep and (timestamp(latest_deep['observed_hour']), latest_deep['revision']) >= (timestamp(row['observed_hour']), row['revision'])
            if candidate and not covered:
                pinned.add(row['review_id'])
        # Context snapshots include the full predecessor payloads; they remain
        # auditable without pinning every historical hour transitively.
        candidates = [r[0] for r in conn.execute('SELECT review_id FROM review WHERE observed_hour<?', (cutoff,)) if r[0] not in pinned]
        for review_id in candidates:
            conn.execute('DELETE FROM completion_attempt WHERE review_id=?', (review_id,))
            conn.execute('DELETE FROM review WHERE review_id=?', (review_id,))
        orphaned = [r[0] for r in conn.execute('SELECT evidence_id FROM evidence WHERE observed_hour<? AND evidence_id NOT IN (SELECT evidence_id FROM review)', (cutoff,))]
        for evidence_id in orphaned:
            conn.execute('DELETE FROM retrieval WHERE evidence_id=?', (evidence_id,))
            conn.execute('DELETE FROM evidence WHERE evidence_id=?', (evidence_id,))
        # Repeated retrieval provenance is bounded independently of pinned evidence.
        conn.execute('DELETE FROM retrieval WHERE retrieved_at<? AND retrieval_id NOT IN (SELECT MIN(retrieval_id) FROM retrieval GROUP BY evidence_id)', (cutoff,))
        return {'deleted_reviews': len(candidates), 'deleted_evidence': len(orphaned)}


def _read_json(path):
    p = Path(path)
    if p.stat().st_size > 30_000_000:
        raise ObservationError('JSON file exceeds bounded input size')
    return json.loads(p.read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    collect_parser = sub.add_parser('collect', help='fetch only anonymous public evidence; does not touch the observation database')
    collect_parser.add_argument('--as-of')
    collect_parser.add_argument('--deep', action='store_true')
    collect_parser.add_argument('--timeout', type=float, default=12)
    collect_parser.add_argument('--output', help='JSON evidence path; default stdout')
    context_parser = sub.add_parser('context', help='read three baselines and accumulation; never creates a database')
    context_parser.add_argument('--db', default=str(DEFAULT_DB))
    context_parser.add_argument('--limit', type=int, default=240, help='up to 10000; increase when since_deep_truncated=true')
    validate_parser = sub.add_parser('validate-evidence')
    validate_parser.add_argument('--evidence', required=True)
    for name in ['validate-review', 'record']:
        p = sub.add_parser(name)
        p.add_argument('--evidence', required=True)
        p.add_argument('--review', required=True)
        p.add_argument('--db', default=str(DEFAULT_DB))
    prune_parser = sub.add_parser('prune')
    prune_parser.add_argument('--db', default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    try:
        if args.command == 'collect':
            result = collect(args.as_of, args.deep, args.timeout)
            validate_evidence(result)
            if args.output:
                # Exclusive creation protects previously frozen evidence packs.
                with Path(args.output).open('x', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
                    f.write('\n')
                print(canonical({'output': str(Path(args.output).resolve()), 'evidence_id': result['evidence_id'], 'datasets': {k: v['coverage'] for k, v in result['datasets'].items()}}))
                return 0
        elif args.command == 'context':
            result = read_context(args.db, args.limit)
        elif args.command == 'validate-evidence':
            evidence = validate_evidence(_read_json(args.evidence))
            result = {'valid': True, 'evidence_id': evidence['evidence_id']}
        elif args.command == 'validate-review':
            result = validate_review(_read_json(args.evidence), _read_json(args.review), read_context(args.db))
        elif args.command == 'record':
            result = record(_read_json(args.evidence), _read_json(args.review), args.db)
        else:
            result = prune(args.db)
        print(canonical(result))
        return 0
    except (ObservationError, sqlite3.Error, OSError, ValueError) as exc:
        # Library errors contain only bounded structural diagnostics or local paths.
        print(canonical({'error': type(exc).__name__, 'message': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
