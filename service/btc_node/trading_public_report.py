"""The public author owns the five public sections. A portfolio report only consumes their frozen wording."""
from __future__ import annotations

from . import trading_decisions, trading_delivery, trading_report
from .codec import Conflict, encode, fingerprint, identity, millis
from .entities import Entities
from .trading_report_inputs import evidence_refs, previous_display, public_inputs


ITEMS = {name: items for name, (_, items) in trading_report.SECTIONS.items() if name != 'allocation'}
PATHS = {name + '.' + key for name, items in ITEMS.items() for key in items}


def _edition_key(target, market, base_id):
    return 'trading.public.copy.' + identity(target['thread_id'], market, base_id)


def read(db, target, market, base):
    copy = db.metadata(_edition_key(target, trading_decisions.binding(market), (base or {}).get('report_id')))
    # Older unsent candidates permitted wholesale rewrites without reviewing the original items.
    if not copy or 'original_items' not in copy:
        return None
    if copy.get('legacy_review_id') != (_legacy_review(db, target, base) or {}).get('review_id'):
        return None
    return copy


def verify_frozen(db, target, document):
    candidate = db.metadata(_edition_key(target, document['public_market'], document['base_report_id']))
    if not candidate or candidate['copy_id'] != document['risk_public_copy']:
        raise Conflict('the source edition of this risk report changed; refresh its public sections')


class LegacyReviewRequired(Conflict):
    """An older display must be reviewed on the portfolio side before the public author can inherit it."""


def _review_key(target, base):
    return 'trading.public.baseline.' + identity(target['thread_id'], base['report_id'])


def _legacy_review(db, target, base):
    if base and base.get('seeded') and not base.get('public_market'):
        return db.metadata(_review_key(target, base))
    return None


def check_legacy_source(db, target, document):
    review = db.metadata(_review_key(target, {'report_id': document['base_report_id']}))
    if not review or review['review_id'] != document['legacy_public_review']:
        raise Conflict('the approved original public passages changed; recompose this risk edition')


def _original_sections(base):
    return {name: {k: base['modules'].get(name, {}).get(k) for k in keys} for name, keys in ITEMS.items()}


def legacy_digests(base):
    if not base or not base.get('seeded') or base.get('public_market'):
        return {}
    return {name + '.' + key: fingerprint(text.encode()) if text else None
            for name, items in _original_sections(base).items() for key, text in items.items()}


def review_baseline(db, target, base_report_id, items, previous_review_id=None):
    base = trading_delivery.report_base(db, target)
    if not base or not base.get('seeded') or base['report_id'] != base_report_id or base.get('public_market'):
        raise Conflict('review the original unclassified display at this destination')
    if not isinstance(items, dict) or items.keys() != PATHS:
        raise ValueError('classify each actual public report item from this original display')
    sections, reviewed = _original_sections(base), {}
    hashes = legacy_digests(base)
    for path, item in items.items():
        name, key = path.split('.')
        if not isinstance(item, dict) or 'digest' not in item or item['digest'] != hashes[path]:
            raise Conflict('review the exact original item ' + path)
        original = sections[name][key]
        if set(item) == {'digest'} and original:
            continue
        if set(item) != {'digest', 'keep', 'reason'} or not isinstance(item['keep'], list):
            raise ValueError('identify the original public passages to retain or why none can be used')
        if not isinstance(item['reason'], str) or not item['reason'].strip():
            raise ValueError('review the actual missing or contaminated material')
        retained, end = [], 0
        for passage in item['keep']:
            if not isinstance(passage, str) or not passage.strip() or not original:
                raise ValueError('retain actual nonempty public passages from the original display')
            start = original.find(passage, end)
            if start < 0:
                raise Conflict('retained public passages must occur in the original order')
            retained.append(passage)
            end = start + len(passage)
        sections[name][key] = '\n\n'.join(retained) or None
        reviewed[path] = fingerprint(item['reason'].encode())
    candidate = {'sections': sections, 'reviewed': reviewed}
    candidate['review_id'] = identity(base_report_id, candidate)
    existing = _legacy_review(db, target, base)
    if candidate != existing:
        if previous_review_id != (existing or {}).get('review_id'):
            raise Conflict('recover the exact prior verification before amending this public baseline')
        db.metadata(_review_key(target, base), candidate, write=True)
    return {'base_report_id': base_report_id, 'review_id': candidate['review_id'], 'rebuild': sorted(
        name + '.' + k for name, items in sections.items() for k, text in items.items() if text is None)}


def legacy_inputs(db, target, base):
    if not base or not base.get('seeded') or base.get('public_market'):
        return {}
    reviewed = _legacy_review(db, target, base)
    return {'legacy_review_required': not bool(reviewed), 'legacy_review_id': (reviewed or {}).get('review_id'),
            'previous_public': reviewed['sections'] if reviewed else None}


def _public_display(base, approved, legacy):
    if not approved:
        return {'modules': legacy['sections'], 'lead': None} if legacy else None
    displayed = previous_display(base)
    modules = {name: displayed['modules'][name] for name in ITEMS}
    lead = displayed['lead']
    risk_paths = {'risk_updates.' + note['event_ref'] for note in base.get('risk_updates', [])}
    if lead and lead['path'] not in PATHS | risk_paths:
        lead = None
    return {'modules': modules, 'lead': lead}


def _public_reviews(db, run, snapshot):
    """Read this run's actual checks, including conditions subsequently retired by its new judgement."""
    objects = Entities(db)
    origins = {}
    reviews = []
    for ref in snapshot.get('independent_condition_reviews', []):
        record = objects.get('REVIEW', ref['object_id'], version=ref['version'])
        if (not record or trading_decisions.binding(record) != ref or record['scope'] != 'PUBLIC'
                or record['source_ref'] != run['ref']):
            raise Conflict('recover the exact independent public condition review for this run')
        if record['known_ms'] > millis(run['known_at']):
            continue
        review = record['payload']
        target = review['subject']
        if target['digest'] not in origins:
            original = objects.get(target['kind'], target['object_id'], version=target['version'])
            if not original or original['scope'] != 'PUBLIC' or trading_decisions.binding(original) != target:
                raise Conflict('public condition review no longer resolves to its original public judgement')
            origins[target['digest']] = {c['id']: c for c in original['payload']['result']['conditions']}
        condition = origins[target['digest']][review['condition_id']]
        evidence = [ref for ref in record['dependencies'] if ref.get('relation') != 'ORIGINAL_JUDGEMENT']
        reviews.append({'origin': target, 'condition_id': condition['id'], 'criterion': condition['criterion'],
                        'review_ref': ref, 'known_ms': record['known_ms'],
                        'outcome': review['outcome'], 'impact': review.get('impact'), 'explanation': review['explanation'],
                        'review_at': condition['review_at'], 'observation_end': condition.get('observation_end'),
                        'next_review_at': review.get('next_review_at'), 'evidence': evidence_refs(evidence)})
    return reviews


def _market_review(snapshot, market, at):
    review = snapshot.get('market_review') or {}
    if review.get('public_market') == trading_decisions.binding(market) and millis(review['assessed_at']) <= millis(at):
        return {**review, 'evidence': evidence_refs(review['evidence'])}
    return None


def context(db, target, market, base, *, run, snapshot):
    approved = bool(base and base.get('public_market'))
    staged = read(db, target, market, base)
    legacy = _legacy_review(db, target, base)
    previous = staged or legacy
    sections = previous['sections'] if previous else (_original_sections(base) if approved else None)
    return {'market': public_inputs(market), 'base_report_id': (base or {}).get('report_id'),
            'previous_public': sections, 'previous_copy_id': staged['copy_id'] if staged else None,
            'previous_display': _public_display(base, approved, legacy),
            'market_review': _market_review(snapshot, market, run['known_at']),
            'condition_reviews': _public_reviews(db, run, snapshot),
            'public_risk_updates': [{k: v for k, v in note.items() if approved or k != 'text'}
                                    for note in (base or {}).get('risk_updates', [])],
            'legacy_review_required': bool(base and base.get('seeded') and not approved and not legacy and not staged),
            'legacy_review_id': (legacy or {}).get('review_id'),
            'market_sources': staged['sources'] if staged else (base or {}).get('market_sources', {}),
            'copy_ready': bool(staged or (approved and base['public_market'] == trading_decisions.binding(market)))}


def _sources(market):
    result = market['payload']['result']
    sources = {k: result[k] for k in ('conclusion', 'primary', 'value')}
    sources.update({'alternative/' + str(i): value for i, value in enumerate(result['alternatives'])})
    sources.update({'assessment/' + name: value for name, value in result['assessments'].items()})
    sources.update({'condition/' + c['id']: c['criterion'] for c in result['conditions']})
    return sources


def _extract(sources, value, *, used=None):
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
        if used is not None:
            used.add(item['source'])
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


def _check_source(sources, evidence, used):
    if not isinstance(evidence, dict) or set(evidence) != {'source', 'quote'}:
        raise ValueError('cite the decisive passage in the exact frozen public source')
    _extract(sources, {'extracts': [evidence]}, used=used)


def _update_item(sources, prior, value, *, independent, changed, review_sources, used):
    if prior is None:
        if not independent or isinstance(value, dict):
            value = _extract(sources, value, used=used)
        return trading_report.edit_item(None, value)
    if not isinstance(value, dict):
        raise ValueError('update the confirmed public wording with exact edits; use reviewed_unchanged to retain it')
    value = dict(value)
    correction = value.pop('correction', None)
    if correction is not None:
        _check_source(sources, correction, used)
    if 'replace_all' in value:
        if not (changed or correction):
            raise Conflict('a changed public basis or actual correction is required to replace the original item')
        _check_source(sources, value.get('source'), used)
        if not independent or isinstance(value['replace_all'], dict):
            value['replace_all'] = _extract(sources, value['replace_all'], used=used)
        return trading_report.edit_item(prior, value, allow_full=True)
    edits = value.get('edits', [value])
    if not isinstance(edits, list):
        raise ValueError('list edits against the original public item')
    changes = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'from', 'to'}:
            raise ValueError('each public edit needs the unique original passage and replacement')
        replacement = edit['to']
        excerpt_sources = set()
        if isinstance(replacement, dict) or (not independent and replacement != ''):
            replacement = _extract(sources, replacement, used=excerpt_sources)
        if not (changed or correction or excerpt_sources.intersection(review_sources)):
            raise Conflict('use an exact new public review excerpt for an update to unchanged market inputs')
        used.update(excerpt_sources)
        changes.append({'from': edit['from'], 'to': replacement})
    if set(value) not in ({'from', 'to'}, {'edits'}):
        raise ValueError('public partial edits and whole-item replacements have distinct scopes')
    return trading_report.edit_item(prior, {'edits': changes})


def prepare(db, run, target, market, base, draft, *, independent, previous=None):
    from .trading import material, report_basis
    if (not isinstance(draft, dict) or 'base_report_id' not in draft
            or set(draft) - {'base_report_id', 'changes', 'market_sources', 'reviewed_unchanged',
                             'legacy_reviewed', 'previous_copy_id'}):
        raise ValueError('identify the public edition baseline, its changes and any reviewed unchanged items')
    if draft['base_report_id'] != (base or {}).get('report_id'):
        raise Conflict('the public author needs the current destination baseline')
    changes = draft.get('changes', {})
    if not isinstance(changes, dict) or changes.keys() - ITEMS.keys():
        raise ValueError('public copy may address only public sections')
    if draft.get('legacy_reviewed'):
        raise ValueError('review and release actual legacy public passages on their authorized portfolio side')
    legacy = _legacy_review(db, target, base)
    existing_edition = bool(base and base.get('seeded'))
    if existing_edition and not base.get('public_market') and not (legacy or previous):
        raise LegacyReviewRequired('LEGACY_PUBLIC_BASELINE_REVIEW_REQUIRED')
    prior_sections = (previous or legacy or {}).get('sections') or (_original_sections(base) if existing_edition else {})
    chosen = dict(previous['sources'] if previous else (base or {}).get('market_sources', {}))
    chosen.update(draft.get('market_sources', {}))
    basis = report_basis(db, run, market, {}, chosen)
    snapshot = material(db, run)
    sources = _sources(market)
    review_sources = {}
    review = _market_review(snapshot, market, run['known_at'])
    if review and review['mode'] == 'REUSE':
        sources['market_review'] = review['reason']
        review_sources['market_review'] = {'run_ref': run['ref'], 'market_review': review}
    for check in _public_reviews(db, run, snapshot):
        key = 'review/' + check['review_ref']['digest']
        sources[key] = check['explanation']
        review_sources[key] = {'run_ref': run['ref'], 'review_ref': check['review_ref']}
    used = set()
    previous_basis = (previous or base or {}).get('basis', {})
    affected = {p for p, digest in basis.items() if (previous or existing_edition) and previous_basis.get(p) != digest}
    pending = (base or {}).get('pending_review', {})
    originally = dict((previous or {}).get('original_items', {}))
    sections, edited, sanitized = {}, set(), set()
    for name, keys in ITEMS.items():
        items = changes.get(name, {})
        if not isinstance(items, dict) or items.keys() - set(keys):
            raise ValueError('unknown public report item in ' + name)
        sections[name] = {}
        for key in keys:
            path = name + '.' + key
            prior = prior_sections.get(name, {}).get(key)
            if key in items:
                text = _update_item(sources, prior, items[key], independent=independent, changed=path in affected,
                                    review_sources=review_sources, used=used)
                edited.add(path)
            elif prior is None:
                raise Conflict('provide the independently sourced public report item ' + path)
            else:
                text = prior
            if legacy and not previous and text != base['modules'].get(name, {}).get(key):
                sanitized.add(path)
            if path in edited or path in sanitized:
                originally.setdefault(path, fingerprint(prior.encode()) if prior else None)
            sections[name][key] = text
    reviewed = set(draft.get('reviewed_unchanged', []))
    if reviewed - ((affected | pending.keys()) - edited) or affected - edited - reviewed:
        raise Conflict('account for each changed public input with a public edit or reviewed_unchanged')
    edited.update(sanitized)
    for path in reviewed & pending.keys():
        name, key = path.split('.')
        edited.add(path)
        originally.setdefault(path, fingerprint(sections[name][key].encode()))
    if previous:
        edited.update(previous['edited'])
        reviewed.update(previous['reviewed'])
    still_changed = {p for p in PATHS if base and base.get('basis', {}).get(p) != basis.get(p)}
    copy = {'market': trading_decisions.binding(market), 'base_report_id': (base or {}).get('report_id'),
            'sections': sections, 'basis': basis, 'sources': chosen, 'edited': sorted(edited), 'original_items': originally,
            'reviewed': sorted((reviewed - edited) & still_changed)}
    if legacy:
        copy['legacy_review_id'] = legacy['review_id']
    bindings = list((previous or {}).get('review_sources', []))
    for name in sorted(used.intersection(review_sources)):
        if review_sources[name] not in bindings:
            bindings.append(review_sources[name])
    if bindings:
        copy['review_sources'] = bindings
    return {**copy, 'copy_id': fingerprint(encode(copy))}


def freeze(db, run, target, market, draft, *, independent):
    base = trading_delivery.report_base(db, target)
    previous = read(db, target, market, base)
    if not isinstance(draft, dict) or draft.get('previous_copy_id') != (previous or {}).get('copy_id'):
        raise Conflict('read the current public copy and apply these edits to its exact previous_copy_id')
    copy = prepare(db, run, target, market, base, draft, independent=independent, previous=previous)
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
                'sources': sources, 'edited': [], 'reviewed': [], 'original_items': {}}
        copy['copy_id'] = fingerprint(encode(copy))
    if not copy:
        raise Conflict('freeze the public report items for this market and destination baseline with trading.market')
    if copy['basis'] != report_basis(db, run, market, {}, copy['sources']):
        raise Conflict('the public report inputs changed; review and freeze the public wording again')
    return copy
