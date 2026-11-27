"""Prospective, per-contract comparisons using the existing public entity history."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from . import market_events as events
from . import trading_theses as theses
from .codec import Conflict, millis, stamp
from .entities import Entities

PREFIX = 'trading/forecast/'


def note_exposure(db, url, content, at):
    if urlsplit(url).hostname != 'gamma-api.polymarket.com':
        return
    try:
        body = json.loads(content)
    except ValueError:
        return
    if isinstance(body, dict):
        rows = [body] if 'conditionId' in body or 'markets' in body else body.get('events')
    else:
        rows = body
    if not isinstance(rows, list):
        return
    for item in rows:
        if not isinstance(item, dict):
            continue
        for market in item['markets'] if isinstance(item.get('markets'), list) else [item]:
            if not isinstance(market, dict):
                continue
            condition = str(market.get('conditionId', ''))
            if (re.fullmatch(r'0x[0-9a-fA-F]{64}', condition)
                    and any(market.get(k) is not None for k in ('outcomePrices', 'bestBid', 'bestAsk'))):
                parents = market.get('events') or ([item] if isinstance(item.get('markets'), list) else [])
                subjects = [condition, *('event.' + str(e['id']) for e in parents if isinstance(e, dict) and e.get('id'))]
                for subject in subjects:
                    key = 'trading.forecast.seen.' + subject
                    if db.metadata(key) is None:
                        db.metadata(key, {'at': at}, write=True)


def interval(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('probability uses [lower,upper], equal for a point forecast')
    lo, hi = (events.price(v) for v in value)
    if lo is None or hi is None or lo > hi:
        raise ValueError('probabilities must be ordered between zero and one')
    return [str(lo.normalize()), str(hi.normalize())]


def forecast_record(db, ref):
    if not isinstance(ref, dict) or ref.get('kind') != 'HYPOTHESIS' or not ref.get('object_id', '').startswith(PREFIX):
        raise ValueError('cite the registered event forecast')
    row = Entities(db).get('HYPOTHESIS', ref['object_id'], version=ref.get('version'))
    if not row or theses.reference(row) != ref:
        raise Conflict('event forecast version or digest differs')
    return row


def pending(db):
    rows = db.rows("SELECT f.object_id FROM entities f LEFT JOIN entities r ON r.kind='REVIEW' "
                   "AND r.object_id=CONCAT(f.object_id,'/settlement') WHERE f.kind='HYPOTHESIS' "
                   "AND f.object_id LIKE %s AND r.entity_key IS NULL ORDER BY f.valid_until_ms,f.object_id",
                   (PREFIX + '%',))
    items = []
    for item in rows:
        row = Entities(db).get('HYPOTHESIS', item['object_id'])
        data = row['payload']
        items.append({'forecast': theses.reference(row), 'slug': data['terms']['slug'], 'outcome': data['outcome'],
                      'cutoff': data['cutoff'], 'prediction': data['prediction'], 'basis_mode': data['basis_mode']})
    return items


def save_run(db, run, snapshot, row, entry):
    from .trading import material_key
    entries = dict(snapshot.get('event_forecasts', {}))
    entries[row['payload']['terms']['slug']] = entry
    db.metadata(material_key(run['ref']), {**snapshot, 'event_forecasts': entries}, write=True)
    return entry


def register(db, run, snapshot, *, terms, outcome, prediction, basis, invalidation, evidence, seen_odds,
             cutoff=None, probability=None):
    from .trading import plain
    if snapshot.get('market_binding'):
        raise Conflict('record the initial forecast in the public phase before final adoption')
    canonical = events.terms({'slug': terms['slug'], 'conditionId': terms['condition_id'], 'question': terms['question'],
                              'outcomes': terms['outcomes'], 'description': terms['rules'], 'endDate': terms['end_at'],
                              'resolutionSource': terms['resolution_source'], 'groupItemTitle': terms['bracket'],
                              'events': [{'id': i} for i in terms['event_ids']]})
    if canonical != terms or outcome not in terms['outcomes'] or type(seen_odds) is not bool:
        raise ValueError('use the published terms, a named outcome and actual prior odds exposure')
    deadline = stamp(cutoff or terms['end_at'])
    if not millis(run['known_at']) < millis(deadline) <= millis(terms['end_at']):
        raise ValueError('the forecast must precede the effective outcome cutoff')
    theses.source_index(db, evidence, run['known_at'])
    subjects = [terms['condition_id'], *('event.' + i for i in terms['event_ids'])]
    seen_odds = seen_odds or any(db.metadata('trading.forecast.seen.' + k) for k in subjects)
    for ref in evidence:
        if ref['type'] == 'BLOB':
            stored = db.value(ref['digest'].removeprefix('sha256:'))
            host = urlsplit(stored.get('url', '')).hostname if isinstance(stored, dict) else None
            if host and (host == 'polymarket.com' or host.endswith('.polymarket.com')):
                seen_odds = True
    row_id = PREFIX + terms['condition_id']
    existing = Entities(db).get('HYPOTHESIS', row_id)
    data = {'terms': terms, 'outcome': outcome, 'prediction': plain(prediction, 'initial prediction'),
            'basis': plain(basis, 'initial evidence and reasoning'), 'invalidation': plain(invalidation, 'invalidation'),
            'cutoff': deadline, 'probability': interval(probability),
            'basis_mode': 'MARKET_AWARE' if seen_odds else 'PRE_QUOTE'}
    if existing:
        if any(existing['payload'].get(key) != value for key, value in data.items() if key != 'basis_mode'):
            raise Conflict('the original is already recorded; use its exact forecast reference for reassessment')
        row = existing
    else:
        data['registered_at'] = run['known_at']
        spec = {'kind': 'HYPOTHESIS', 'object_id': row_id, 'scope': 'PUBLIC', 'active': False,
                'known_at': run['known_at'], 'observed_at': run['known_at'], 'valid_until': deadline,
                'source_ref': run['ref'], 'method_version': run['method_version'], 'dependencies': evidence,
                'change_reason': 'Forecast registered before the comparison', 'payload': data}
        Entities(db).put(spec, expected_version=0)
        row = Entities(db).get('HYPOTHESIS', row_id)
    ref = theses.reference(row)
    entry = snapshot.get('event_forecasts', {}).get(terms['slug'], {'forecast': ref})
    return save_run(db, run, snapshot, row, entry)


def detail(db, snapshot, ref):
    row = forecast_record(db, ref)
    return {'forecast': ref, 'registered_at': row['known_ms'], **row['payload'],
            'this_run': snapshot.get('event_forecasts', {}).get(row['payload']['terms']['slug'], {}),
            'settlement': result_of(db, row)}


def result_of(db, row):
    objects = Entities(db)
    review = objects.get('REVIEW', row['object_id'] + '/settlement') or objects.get('REVIEW', row['object_id'] + '/dispute')
    return {'review': theses.reference(review), **review['payload']} if review else None


def history(db, *, after='', limit=50):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('use a history page of 1 to 100 closed forecasts')
    rows = db.rows("SELECT f.object_id FROM entities f JOIN entities r ON r.kind='REVIEW' "
                   "AND r.object_id=CONCAT(f.object_id,'/settlement') WHERE f.kind='HYPOTHESIS' "
                   "AND f.object_id LIKE %s AND f.object_id>%s ORDER BY f.object_id LIMIT %s",
                   (PREFIX + '%', after, limit + 1))
    records = []
    for item in rows[:limit]:
        forecast = Entities(db).get('HYPOTHESIS', item['object_id'])
        decision = forecast['payload']
        review = result_of(db, forecast)
        records.append({'forecast': theses.reference(forecast), 'slug': decision['terms']['slug'],
                        'event_ids': decision['terms']['event_ids'], 'cutoff': decision['cutoff'],
                        'basis_mode': decision['basis_mode'], 'review': review['review'],
                        'status': review['status'], 'brier': review.get('brier')})
    return {'records': records, 'next_after': rows[limit-1]['object_id'] if len(rows) > limit else None}


def from_receipt(db, run, row, receipt):
    from .trading import register_evidence
    expected = row['payload']['terms']
    if receipt['url'] != events.GAMMA + '/markets/slug/' + expected['slug']:
        raise ValueError('use the original condition-specific Gamma response')
    raw = json.loads(receipt['content'])
    if (raw['slug'] != expected['slug'] or raw['conditionId'] != expected['condition_id']
            or millis(receipt['observed_at']) != millis(raw['updatedAt'])):
        raise ValueError('wrong contract or source update time')
    if events.utc(receipt['retrieved_at']) <= events.utc(row['payload']['registered_at']):
        raise ValueError('use a retrieval made after forecast registration')
    source = register_evidence(db, run, **receipt)
    if events.terms(raw) != expected:
        return {'status': 'TERMS_CHANGED', 'source': source}
    quote = events.pricing(raw, row['payload']['outcome'], receipt['retrieved_at'])
    return {**quote, 'source': source, 'updated_at': receipt['observed_at'], 'retrieved_at': receipt['retrieved_at']}


def quote(db, run, snapshot, *, forecast, receipt=None, failure=None):
    if snapshot.get('market_binding'):
        raise Conflict('record the comparison quote before final market adoption')
    row = forecast_record(db, forecast)
    entry = snapshot.get('event_forecasts', {}).get(row['payload']['terms']['slug'], {'forecast': forecast})
    if entry.get('quote', {}).get('status') not in (None, 'UNAVAILABLE'):
        return entry
    if failure and receipt is None:
        return save_run(db, run, snapshot, row, {**entry, 'quote': {'status': 'UNAVAILABLE', 'reason': str(failure),
                                                                   'checked_at': run['known_at']}})
    if not isinstance(receipt, dict) or failure:
        raise ValueError('supply the actual quote receipt or its retrieval failure')
    data = from_receipt(db, run, row, receipt)
    if millis(run['known_at']) >= millis(row['payload']['cutoff']) and data['status'] in ('QUOTED', 'WIDE'):
        data['status'] = 'AFTER_CUTOFF'
    return save_run(db, run, snapshot, row, {**entry, 'quote': data})


def adjudicate(db, run, snapshot, submitted):
    from .trading import plain
    entries = snapshot.get('event_forecasts', {})
    if not isinstance(submitted, dict) or submitted.keys() != entries.keys():
        raise ValueError('conclude each event forecast opened in this public assessment')
    comparisons, dependencies = {}, []
    for slug, entry in entries.items():
        answer = submitted[slug]
        if not isinstance(answer, dict) or type(answer.get('same_information')) is not bool:
            raise ValueError('assess whether the two forecasts used a comparable information window')
        row = forecast_record(db, entry['forecast'])
        original = row['payload']
        side = entry.get('quote', {'status': 'UNAVAILABLE'})
        comparable = (answer['same_information'] and side['status'] == 'QUOTED'
                      and millis(run['known_at']) < millis(original['cutoff']))
        comparison = {'forecast': entry['forecast'], 'basis_mode': original['basis_mode'],
                      'quote': side, 'same_information': answer['same_information'],
                      'comparable': comparable, 'assessed_at': run['known_at'], 'probability': interval(answer.get('probability')),
                      'conclusion': plain(answer.get('conclusion'), 'final event forecast'),
                      'reason': plain(answer.get('reason'), 'what the comparison changes or preserves')}
        probability = original['probability']
        if comparable and probability:
            low, high = map(events.price, probability)
            comparison['relative_to_quote'] = ('BELOW' if high < events.price(side['bid']) else
                                               'ABOVE' if low > events.price(side['ask']) else 'OVERLAP')
        comparisons[slug] = comparison
        dependencies.append(theses.dependency(entry['forecast']))
        if side.get('source'):
            dependencies.append(side['source'])
    return comparisons, dependencies


def save_first_decisions(db, run, market, comparisons):
    # A fresh comparison can support retaining the existing market decision.
    for comparison in comparisons.values():
        forecast = comparison['forecast']
        subject = forecast['object_id'] + '/comparison'
        if not Entities(db).get('REVIEW', subject):
            refs = [theses.dependency(forecast), theses.dependency(market)]
            if comparison['quote'].get('source'):
                refs.append(comparison['quote']['source'])
            theses.put(db, 'REVIEW', subject, {'market': market, 'comparison': comparison}, refs,
                       'First public decision after the event forecast', run, 0)


def settle(db, run, snapshot, *, forecast, receipt, evidence=None, observed_value=None, official_outcome=None, reason=None,
           official_receipt=None):
    from .trading import plain, register_evidence
    if snapshot.get('market_binding'):
        raise Conflict('settle the original public question before adopting the next market decision')
    row = forecast_record(db, forecast)
    prior = result_of(db, row)
    if prior and prior['status'] != 'DISPUTED':
        return prior
    state = from_receipt(db, run, row, receipt)
    if state['status'] not in ('RESOLVED', 'TERMS_CHANGED'):
        return state
    terms = row['payload']['terms']
    status = 'VOID' if state['status'] == 'RESOLVED' and state.get('winner') is None else state['status']
    first = Entities(db).get('REVIEW', row['object_id'] + '/comparison')
    result = {'forecast': forecast, 'status': status, 'platform': state, 'first_decision': first and first['payload']}
    refs = [theses.dependency(forecast), state['source']]
    if status == 'RESOLVED':
        if official_receipt is not None:
            observed_value, official_outcome = events.binance_result(terms, official_receipt)
            evidence = register_evidence(db, run, **official_receipt)
            reason = 'Binance BTCUSDT terminal minute close and the published higher-bracket boundary rule.'
        if evidence is None:
            return {**result, 'status': 'AWAITING_OFFICIAL_SOURCE'}
        if evidence.get('type') != 'BLOB' or official_outcome not in terms['outcomes']:
            raise ValueError('cite the observed settlement source and its corresponding outcome label')
        theses.source_index(db, [evidence], run['known_at'])
        source = db.value(evidence['digest'].removeprefix('sha256:'))
        if events.binance_close_url(terms) and events.binance_result(terms, source) != (observed_value, official_outcome):
            raise ValueError('settlement differs from the exact terminal candle and bracket')
        host = urlsplit(source.get('url', '')).hostname
        hosts = {urlsplit(url).hostname for url in [terms['resolution_source'], *re.findall(r'https?://[^\s)]+', terms['rules'])]}
        hosts.discard(None)
        if hosts & {'www.binance.com', 'binance.com', 'api.binance.com'}:
            hosts.update({'data-api.binance.vision', 'api.binance.com'})
        if (not host or host not in hosts or not source.get('observed_at') or millis(source['observed_at']) < row['known_ms']
                or plain(observed_value, 'actual settlement observation') not in source.get('content', '')):
            raise ValueError('use a later original observation from the resolution source named in the contract')
        result.update(official_source=evidence, observed_value=observed_value, official_outcome=official_outcome,
                      reason=plain(reason, 'how the terms apply to this observation'),
                      status='CONFIRMED' if official_outcome == state['winner'] else 'DISPUTED')
        refs.append(evidence)
        decision = result['first_decision']
        if decision and result['status'] == 'CONFIRMED' and row['payload']['basis_mode'] == 'PRE_QUOTE':
            c = decision['comparison']
            initial, final = row['payload']['probability'], c['probability']
            if (c['comparable'] and millis(source['observed_at']) >= millis(c['assessed_at'])
                    and initial and initial[0] == initial[1] and final and final[0] == final[1]):
                actual = int(official_outcome == row['payload']['outcome'])
                mid = (events.price(c['quote']['bid']) + events.price(c['quote']['ask'])) / 2
                result['brier'] = {k: str((p - actual) ** 2) for k, p in (
                    ('initial', events.price(initial[0])), ('market_midpoint', mid), ('final', events.price(final[0])))}
    if result['first_decision']:
        refs.append(theses.dependency(result['first_decision']['market']))
    disputed = result['status'] == 'DISPUTED'
    review = theses.put(db, 'REVIEW', row['object_id'] + ('/dispute' if disputed else '/settlement'), result, refs,
                        'Event settlement review', run, prior['review']['version'] if prior and disputed else 0)
    return {'review': theses.reference(review), **result}
