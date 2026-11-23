"""The public author owns the five public sections. A portfolio report only consumes their frozen wording."""
from __future__ import annotations

from . import trading_decisions, trading_delivery, trading_report
from .codec import Conflict, encode, fingerprint, identity
from .trading_report_inputs import public_inputs


ITEMS = {name: items for name, (_, items) in trading_report.SECTIONS.items() if name != 'allocation'}
PATHS = {name + '.' + key for name, items in ITEMS.items() for key in items}


def _edition_key(target, market, base_id):
    return 'trading.public.copy.' + identity(target['thread_id'], market, base_id)


def read(db, target, market, base):
    return db.metadata(_edition_key(target, trading_decisions.binding(market), (base or {}).get('report_id')))


def verify_frozen(db, target, document):
    candidate = db.metadata(_edition_key(target, document['public_market'], document['base_report_id']))
    if not candidate or candidate['copy_id'] != document['risk_public_copy']:
        raise Conflict('the source edition of this risk report changed; refresh its public sections')


def context(db, target, market, base):
    approved = bool(base and base.get('public_market'))
    staged = read(db, target, market, base)
    previous = staged['sections'] if staged else ({name: base['modules'][name] for name in ITEMS} if approved else None)
    return {'market': public_inputs(market), 'base_report_id': (base or {}).get('report_id'),
            'previous_public': previous, 'previous_copy_id': staged['copy_id'] if staged else None,
            'public_risk_updates': [{k: v for k, v in note.items() if approved or k != 'text'}
                                    for note in (base or {}).get('risk_updates', [])],
            'legacy_review_required': bool(base and not approved and not staged),
            'market_sources': staged['sources'] if staged else (base or {}).get('market_sources', {}),
            'copy_ready': bool(staged or
                               (approved and base['public_market'] == trading_decisions.binding(market)))}


def legacy_digests(base):
    # Returned only alongside the original edition on the authorized portfolio side.
    if not base or base.get('public_market'):
        return {}
    return {name + '.' + key: fingerprint(base['modules'][name][key].encode())
            for name, items in ITEMS.items() for key in items}


def _sources(market):
    result = market['payload']['result']
    sources = {k: result[k] for k in ('conclusion', 'primary', 'value')}
    sources.update({'alternative/' + str(i): value for i, value in enumerate(result['alternatives'])})
    sources.update({'assessment/' + name: value for name, value in result['assessments'].items()})
    sources.update({'condition/' + c['id']: c['criterion'] for c in result['conditions']})
    return sources


def _extract(sources, value):
    if not isinstance(value, dict) or set(value) != {'extracts'} or not isinstance(value['extracts'], list):
        raise ValueError('after the market freeze use extracts from actual public sources')
    parts = []
    for item in value['extracts']:
        if not isinstance(item, dict) or set(item) - {'source', 'quote'} or item.get('source') not in sources:
            raise ValueError('each extract needs its actual public source')
        source = sources[item['source']]
        quote = item.get('quote', source)
        if not isinstance(quote, str) or not quote.strip() or quote not in source:
            raise Conflict('the cited public source does not contain this passage')
        parts.append(quote)
    if not parts:
        raise ValueError('select the public evidence to display')
    return '\n\n'.join(parts)


def risk_text(db, run, target, support, value):
    from .trading import MARKET, material, plain
    event = support['event_ref']
    original = trading_delivery.risk_note(db, target, event)
    if original and value == original['text']:
        return value
    base = trading_delivery.report_base(db, target)
    if base and base.get('public_market') and any(
            row['event_ref'] == event and row['text'] == value for row in base['risk_updates']):
        return value
    if not material(db, run).get('market_binding') and isinstance(value, str):
        return plain(value, 'independently assessed public risk')
    event_text = db.value(event.removeprefix('sha256:'))['content']
    if isinstance(value, str) and value.strip() and value in event_text:
        return value
    market = trading_decisions.current_market(db, MARKET)
    sources = _sources(market) if market else {}
    text = _extract({**sources, 'event': event_text}, value)
    if not any(item['source'] == 'event' for item in value['extracts']):
        raise ValueError('the limited risk update must reference its verified public event')
    return text


def prepare(db, run, market, base, draft, *, independent, previous=None):
    from .trading import report_basis
    if (not isinstance(draft, dict) or 'base_report_id' not in draft
            or set(draft) - {'base_report_id', 'changes', 'market_sources', 'reviewed_unchanged',
                             'legacy_reviewed', 'previous_copy_id'}):
        raise ValueError('identify the public edition baseline, its changes and any reviewed unchanged items')
    if draft['base_report_id'] != (base or {}).get('report_id'):
        raise Conflict('the public author needs the current destination baseline')
    changes = draft.get('changes', {})
    if not isinstance(changes, dict) or changes.keys() - ITEMS.keys():
        raise ValueError('public copy may address only public sections')
    verified = draft.get('legacy_reviewed', {})
    old_hashes = {} if previous else legacy_digests(base)
    if (not isinstance(verified, dict) or any(path not in old_hashes or old_hashes[path] != digest
                                               for path, digest in verified.items())):
        raise Conflict('reuse only the exact legacy passages reviewed as public from the original display')
    chosen = dict(previous['sources'] if previous else (base or {}).get('market_sources', {}))
    chosen.update(draft.get('market_sources', {}))
    basis = report_basis(db, run, market, {}, chosen)
    sections, edited = {}, set()
    for name, keys in ITEMS.items():
        items = changes.get(name, {})
        if not isinstance(items, dict) or items.keys() - set(keys):
            raise ValueError('unknown public report item in ' + name)
        sections[name] = {}
        for key in keys:
            path = name + '.' + key
            prior = previous['sections'][name][key] if previous else (base['modules'][name][key] if base else None)
            if key in items:
                value = items[key]
                if not independent or (isinstance(value, dict) and 'extracts' in value):
                    text = _extract(_sources(market), value)
                else:
                    if old_hashes and not isinstance(value, str):
                        raise ValueError('replace the complete unverified legacy item with public wording')
                    text = trading_report.replace_passage(prior, value)
                edited.add(path)
            elif prior is None or (old_hashes and (path == 'risks.conditions' or path not in verified)):
                raise Conflict('provide the independently sourced public report item ' + path)
            else:
                text = prior
            sections[name][key] = text
    previous_basis = (previous or base or {}).get('basis', {})
    affected = {p for p, digest in basis.items() if (previous or base) and previous_basis.get(p) != digest}
    reviewed = set(draft.get('reviewed_unchanged', []))
    if reviewed - (affected - edited) or affected - edited - reviewed:
        raise Conflict('account for each changed public input with a public edit or reviewed_unchanged')
    if previous:
        edited.update(previous['edited'])
        reviewed.update(previous['reviewed'])
    still_changed = {p for p in PATHS if base and base.get('basis', {}).get(p) != basis.get(p)}
    copy = {'market': trading_decisions.binding(market), 'base_report_id': (base or {}).get('report_id'),
            'sections': sections, 'basis': basis, 'sources': chosen, 'edited': sorted(edited),
            'reviewed': sorted((reviewed - edited) & still_changed)}
    return {**copy, 'copy_id': fingerprint(encode(copy))}


def freeze(db, run, target, market, draft, *, independent):
    base = trading_delivery.report_base(db, target)
    previous = read(db, target, market, base)
    if not isinstance(draft, dict) or draft.get('previous_copy_id') != (previous or {}).get('copy_id'):
        raise Conflict('read the current public copy and apply these edits to its exact previous_copy_id')
    copy = prepare(db, run, market, base, draft, independent=independent, previous=previous)
    db.metadata(_edition_key(target, copy['market'], copy['base_report_id']), copy, write=True)
    return copy['copy_id']


def for_report(db, run, target, market, base):
    from .trading import report_basis
    copy = read(db, target, market, base)
    if not copy and base and base.get('public_market') == trading_decisions.binding(market):
        sources = base.get('market_sources', {})
        copy = {'market': base['public_market'], 'base_report_id': base['report_id'],
                'sections': {name: base['modules'][name] for name in ITEMS},
                'basis': {p: v for p, v in base.get('basis', {}).items() if p in PATHS},
                'sources': sources, 'edited': [], 'reviewed': []}
        copy['copy_id'] = fingerprint(encode(copy))
    if not copy:
        raise Conflict('freeze the public report items for this market and destination baseline with trading.market')
    if copy['basis'] != report_basis(db, run, market, {}, copy['sources']):
        raise Conflict('the public report inputs changed; review and freeze the public wording again')
    return copy
