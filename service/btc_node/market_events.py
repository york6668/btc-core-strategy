"""Anonymous prediction-market data. Discovery exposes settlement terms only."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

GAMMA = 'https://gamma-api.polymarket.com'
FINAL_SETTLEMENTS = {'CONFIRMED', 'VOID', 'TERMS_CHANGED'}


def utc(value):
    # Python 3.9 needs three or six fractional digits; Gamma emits variable precision.
    normalized = re.sub(
        r'(\d{2}:\d{2}:\d{2}\.)(\d+)',
        lambda match: match[1] + match[2].ljust(6, '0')[:6],
        value.replace('Z', '+00:00'),
    )
    instant = datetime.fromisoformat(normalized)
    if instant.tzinfo is None:
        raise ValueError('source time needs a timezone')
    return instant.astimezone(timezone.utc)


def get(url):
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Public-Market-Research/1.0'})
    with urlopen(request, timeout=10) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('public source exceeds the evidence limit')
    return raw.decode('utf-8'), datetime.now(timezone.utc).isoformat()


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,399}', value):
        raise ValueError('use the exact published market or event slug')
    return value


def items(value):
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        raise ValueError('missing ordered outcome array')
    return parsed


def terms(market, parents=()):
    labels = items(market['outcomes'])
    if (len(labels) != 2 or len(set(labels)) != 2 or not all(isinstance(s, str) and s.strip() for s in labels)
            or not re.fullmatch(r'0x[0-9a-fA-F]{64}', market['conditionId'])):
        raise ValueError('a named binary market with an exact condition identity is required')
    if not market.get('question') or not market.get('description'):
        raise ValueError('the original question and resolution rules are required')
    parents = {str(e['id']) for e in market.get('events') or parents if isinstance(e, dict) and e.get('id')}
    return {'slug': slug(market['slug']), 'condition_id': market['conditionId'], 'event_ids': sorted(parents),
            'question': market['question'],
            'outcomes': labels, 'rules': market['description'], 'end_at': utc(market['endDate']).isoformat(),
            'resolution_source': market.get('resolutionSource') or '', 'bracket': market.get('groupItemTitle') or ''}


def binance_close_url(contract):
    rules = contract['rules']
    end = utc(contract['end_at'])
    noon = end.astimezone(ZoneInfo('America/New_York'))
    if (contract['outcomes'] != ['Yes', 'No'] or 'higher range bracket' not in rules
            or 'BTC/USDT closing at 12:00' not in rules or 'Binance 1 minute candle' not in rules
            or (noon.hour, noon.minute, noon.second) != (12, 0, 0)
            or not re.fullmatch(r'(?:[<>]\s*[\d,]+|[\d,]+\s*[-–]\s*[\d,]+)', contract['bracket'])):
        return None
    start = int(end.timestamp()) * 1000 - 60_000
    return 'https://data-api.binance.vision/api/v3/klines?' + urlencode(
        {'symbol': 'BTCUSDT', 'interval': '1m', 'startTime': start, 'limit': 1})


def binance_result(contract, source):
    url = binance_close_url(contract)
    if not url or source['url'] != url or utc(source['observed_at']) != utc(contract['end_at']):
        raise ValueError('use the exact Binance terminal minute required by the event')
    rows = json.loads(source['content'])
    end = int(utc(contract['end_at']).timestamp()) * 1000
    if len(rows) != 1 or rows[0][0] != end - 60_000 or rows[0][6] != end - 1:
        raise ValueError('the Binance candle does not end at the specified settlement time')
    value = str(rows[0][4])
    closing = Decimal(value)
    if not closing.is_finite() or closing <= 0:
        raise ValueError('invalid closing price')
    bracket = contract['bracket'].replace(',', '').replace(' ', '').replace('–', '-')
    if bracket.startswith('<'):
        inside = closing < Decimal(bracket[1:])
    elif bracket.startswith('>'):
        inside = closing >= Decimal(bracket[1:])
    else:
        low, high = map(Decimal, bracket.split('-'))
        inside = low <= closing < high
    return value, 'Yes' if inside else 'No'


def discover(*, search=None, event=None):
    if (search is None) == (event is None):
        raise ValueError('supply a search topic or an exact event slug')
    if search is not None:
        query = urlencode({'q': search, 'limit_per_type': 12, 'events_status': 'active'})
        raw, _ = get(GAMMA + '/public-search?' + query)
        rows = json.loads(raw).get('events') or []
        now = datetime.now(timezone.utc)
        return sorted(({'slug': r['slug'], 'title': r['title'], 'end_at': r['endDate']} for r in rows
                       if not r.get('closed') and r.get('endDate') and utc(r['endDate']) > now),
                      key=lambda r: (r['end_at'], r['slug']))
    raw, _ = get(GAMMA + '/events/slug/' + slug(event))
    data = json.loads(raw)
    if data['slug'] != event:
        raise ValueError('provider returned a different event')
    markets = sorted((terms(m, [data]) for m in data['markets']), key=lambda m: m['slug'])
    view = {'event': event, 'title': data['title'], 'markets': markets}
    rules = {m['rules'] for m in markets}
    if len(markets) > 1 and len(rules) == 1:
        view['common_rules'] = rules.pop()
        for m in markets:
            del m['rules']
    return view


def receipt(market_slug):
    url = GAMMA + '/markets/slug/' + slug(market_slug)
    content, retrieved = get(url)
    market = json.loads(content)
    if market['slug'] != market_slug:
        raise ValueError('provider returned a different market')
    updated = utc(market['updatedAt'])
    if updated > utc(retrieved):
        raise ValueError('provider update is later than retrieval')
    return {'url': url, 'source': 'Polymarket Gamma', 'content': content,
            'observed_at': updated.isoformat(), 'retrieved_at': retrieved}


def price(value):
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation):
        return None
    return result if result.is_finite() and 0 <= result <= 1 else None


def pricing(market, label, at):
    labels = items(market['outcomes'])
    index = labels.index(label)
    if market.get('closed') and market.get('umaResolutionStatus') == 'resolved':
        payouts = [price(p) for p in items(market.get('outcomePrices') or [])]
        if len(payouts) == 2 and set(payouts) == {Decimal(0), Decimal(1)}:
            return {'status': 'RESOLVED', 'winner': labels[payouts.index(Decimal(1))]}
        if payouts == [Decimal('0.5'), Decimal('0.5')]:
            return {'status': 'RESOLVED', 'winner': None}
        return {'status': 'PENDING'}
    if (market.get('closed') or not market.get('active') or not market.get('acceptingOrders')
            or utc(at) >= utc(market['endDate'])):
        return {'status': 'PENDING'}
    bid, ask = price(market.get('bestBid')), price(market.get('bestAsk'))
    if ([s.casefold() for s in labels] not in (['yes', 'no'], ['up', 'down'])
            or bid is None or ask is None or not 0 < bid <= ask < 1):
        return {'status': 'NO_TWO_SIDES'}
    if index:
        bid, ask = 1 - ask, 1 - bid
    return {'status': 'WIDE' if ask - bid > Decimal('0.10') else 'QUOTED',
            'bid': str(bid), 'ask': str(ask)}
