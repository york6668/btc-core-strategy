"""Read the BTC inverse option chain and bracket 25-delta bid/ask IV from quoted contracts."""
from __future__ import annotations

import json
import math
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from statistics import NormalDist
from urllib.parse import parse_qs, urlencode, urlsplit

from btc_node.market_events import get, utc

BASE = 'https://www.deribit.com/api/v2/public/'


def snapshot_id(sources):
    members = sorted((s['url'], utc(s.get('retrieved_at') or s['known_at']).isoformat(),
                      sha256(s['content'].encode()).hexdigest()) for s in sources)
    return sha256(json.dumps(members).encode()).hexdigest()


def fetch(method, params):
    url = BASE + method + '?' + urlencode(params)
    raw, fetched = get(url)
    data = json.loads(raw)
    if 'result' not in data:
        raise ValueError('Deribit did not return a result for ' + method)
    observed = datetime.fromtimestamp(data['usOut'] / 1e6, timezone.utc).isoformat()
    return data['result'], {'source': 'Deribit public ' + method, 'url': url, 'content': raw,
                            'retrieved_at': fetched, 'observed_at': observed}


def interpolate(tickers, option_type):
    target = 0.25
    quoted = []
    for tick in tickers:
        if not tick['instrument_name'].endswith('-' + option_type) or not tick.get('best_bid_price') or not tick.get('best_ask_price'):
            continue
        bid, ask = tick.get('bid_iv'), tick.get('ask_iv')
        delta = tick.get('greeks', {}).get('delta')
        if (delta is None or bid is None or ask is None or not 0 < bid <= ask
                or not (0 < delta < 1 if option_type == 'C' else -1 < delta < 0)):
            continue
        quoted.append((abs(delta), tick))
    lower = max((r for r in quoted if r[0] <= target), key=lambda r: r[0], default=None)
    upper = min((r for r in quoted if r[0] >= target), key=lambda r: r[0], default=None)
    if lower is None or upper is None:
        return None
    weight = (target - lower[0]) / (upper[0] - lower[0]) if lower[0] != upper[0] else 0
    def by(field):
        return round(lower[1][field] * (1 - weight) + upper[1][field] * weight, 5)
    return {'bid_iv': by('bid_iv'), 'ask_iv': by('ask_iv'),
            'legs': [{'instrument': t['instrument_name'], 'timestamp_ms': t['timestamp'],
                      'delta': t['greeks']['delta'], 'bid_iv': t['bid_iv'], 'ask_iv': t['ask_iv']}
                     for t in (lower[1], upper[1])]}


def compare_previous(current, receipts):
    """Compare only prior, frozen observations of these instruments and this maturity."""
    prior_chain, tickers, names = None, [], set()
    for source in receipts:
        url = urlsplit(source['url'])
        if url.scheme != 'https' or url.netloc != 'www.deribit.com' or not source['url'].startswith(BASE):
            raise ValueError('use frozen Deribit originals from the earlier options observation')
        data = json.loads(source['content'])['result']
        method = url.path.rsplit('/', 1)[1]
        if method == 'get_instruments':
            names = {s['instrument_name'] for s in data if s.get('settlement_currency') == 'BTC'
                     and s['expiration_timestamp'] == current['expiry_ms']}
        elif method == 'get_book_summary_by_currency':
            prior_chain = data
        elif method == 'ticker' and parse_qs(url.query).get('instrument_name') == [data['instrument_name']]:
            tickers.append(data)
        else:
            raise ValueError('use the source references from one earlier options snapshot')
    if not names or prior_chain is None or any(t['instrument_name'] not in names for t in tickers):
        raise ValueError('the earlier sources do not cover the requested expiry')
    group = snapshot_id(receipts)
    coherent = all(s['source'].endswith(' snapshot ' + group) for s in receipts)
    put, call = interpolate(tickers, 'P'), interpolate(tickers, 'C')
    change = None
    if coherent and put and call and current['put_minus_call_vol_points']:
        before = [put['bid_iv'] - call['ask_iv'], put['ask_iv'] - call['bid_iv']]
        previous_ticks = [r['timestamp_ms'] for side in (put, call) for r in side['legs']]
        current_ticks = [r['timestamp_ms'] for side in (current['put_25d'], current['call_25d']) for r in side['legs']]
        if max(previous_ticks) < min(current_ticks):
            change = [round(current['put_minus_call_vol_points'][0] - before[1], 5),
                      round(current['put_minus_call_vol_points'][1] - before[0], 5)]
    past = {r['instrument_name']: r for r in prior_chain if r['instrument_name'] in names}
    oi_changes = {}
    for side in current['largest_oi_by_side'].values():
        for row in side:
            older = past.get(row['instrument'])
            if older and older.get('open_interest') is not None:
                old_at = older['creation_timestamp'] - 8 * 3600 * 1000
                if old_at < row['oi_as_of_ms']:
                    oi_changes[row['instrument']] = {'from_ms': old_at, 'to_ms': row['oi_as_of_ms'],
                                                     'change_btc': round(row['oi_btc'] - older['open_interest'], 8)}
    return {'same_prior_collection': coherent, 'change_in_put_minus_call_vol_points': change, 'matched_oi_changes': oi_changes}


def collect(expiry):
    day = date.fromisoformat(expiry)
    params = {'currency': 'BTC', 'kind': 'option'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        instruments = pool.submit(fetch, 'get_instruments', {**params, 'expired': 'false'})
        summaries = pool.submit(fetch, 'get_book_summary_by_currency', params)
        instruments, spec_source = instruments.result()
        summaries, chain_source = summaries.result()
    specs = {s['instrument_name']: s for s in instruments if s.get('settlement_currency') == 'BTC'
             and s['instrument_name'].startswith('BTC-')
             and datetime.fromtimestamp(s['expiration_timestamp'] / 1000, timezone.utc).date() == day}
    chain = [r for r in summaries if r['instrument_name'] in specs]
    if not chain:
        raise ValueError('no active BTC inverse options for this expiry')
    expiry_ms = specs[chain[0]['instrument_name']]['expiration_timestamp']
    as_of = datetime.fromisoformat(chain_source['observed_at']).timestamp()
    years = (expiry_ms / 1000 - as_of) / 31557600
    if years <= 0:
        raise ValueError('this expiry has already passed')
    candidates = {'P': [], 'C': []}
    oi = []
    for row in chain:
        name = row['instrument_name']
        k = specs[name]['strike']
        if row.get('open_interest') is not None:
            oi.append({'instrument': name, 'strike': k, 'oi_btc': row['open_interest'],
                       'oi_as_of_ms': row['creation_timestamp'] - 8 * 3600 * 1000})
        if not all(row.get(f) for f in ('bid_price', 'ask_price', 'mark_iv', 'underlying_price')):
            continue
        sigma = row['mark_iv'] / 100 * math.sqrt(years)
        # Estimate only which tickers to retrieve. The result uses their published deltas and both sides.
        c = NormalDist().cdf(math.log(row['underlying_price'] / k) / sigma + sigma / 2)
        typ = name.rsplit('-', 1)[1]
        candidates[typ].append((abs((c if typ == 'C' else 1-c) - 0.25), name))
    ranked = {typ: [name for _, name in sorted(rows)] for typ, rows in candidates.items()}
    responses, gaps = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            tickers = [t for t, _ in responses]
            names = []
            for typ, remaining in ranked.items():
                if remaining and interpolate(tickers, typ) is None:
                    names.extend(remaining[:4])
                    del remaining[:4]
            if not names:
                break
            requests = {name: pool.submit(fetch, 'ticker', {'instrument_name': name}) for name in names}
            before = len(responses)
            for name, request in requests.items():
                try:
                    responses.append(request.result())
                except (OSError, KeyError, ValueError) as error:
                    gaps.append({'instrument': name, 'reason': str(error)})
            if len(responses) == before:
                break
    tickers = [tick for tick, _ in responses]
    put, call = interpolate(tickers, 'P'), interpolate(tickers, 'C')
    spread = [round(put['bid_iv'] - call['ask_iv'], 5), round(put['ask_iv'] - call['bid_iv'], 5)] if put and call else None
    used = {leg['instrument'] for side in (put, call) if side for leg in side['legs']}
    sources = [spec_source, chain_source, *(src for tick, src in responses if tick['instrument_name'] in used)]
    group = snapshot_id(sources)
    for source in sources:
        source['source'] += ' snapshot ' + group
    return {'expiry': expiry, 'expiry_ms': expiry_ms, 'contracts': len(chain), 'observed_at': chain_source['observed_at'],
            'put_25d': put, 'call_25d': call, 'put_minus_call_vol_points': spread, 'quote_gaps': gaps,
            'largest_oi_by_side': {side: sorted((r for r in oi if r['instrument'].endswith('-'+side)),
                                                key=lambda r: -r['oi_btc'])[:5] for side in ('P', 'C')}}, sources
