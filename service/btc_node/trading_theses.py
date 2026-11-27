"""Public cycle theses, their hourly tests and retrospective results on the node's entity history."""
from __future__ import annotations

from .codec import Conflict, encode, fingerprint, millis
from .entities import Entities, key as entity_key

PREFIX = 'trading/cycle/'
THESIS_FIELDS = ('claim', 'driver', 'expected_response', 'alternative', 'priced_in', 'invalidation')
OUTCOMES = {'SUPPORTED', 'WEAKENED', 'REFUTED', 'UNRESOLVED'}


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' is required')
    return value


def subject_id(key):
    text(key, 'stable cycle thesis key')
    if '/' in key or len(key) > 160 or key.strip() != key:
        raise ValueError('invalid cycle thesis key')
    return PREFIX + key


def bindings(market):
    return market['payload']['result'].get('theses', {}) if market else {}


def reference(record):
    return {key: record[key] for key in ('kind', 'object_id', 'version', 'digest')}


def dependency(ref):
    return {'type': 'ENTITY', **ref}


def bound(db, ref, at):
    if not isinstance(ref, dict) or ref.get('kind') != 'HYPOTHESIS' or not str(ref.get('object_id', '')).startswith(PREFIX):
        raise ValueError('an exact public cycle thesis is required')
    row = Entities(db).get('HYPOTHESIS', ref['object_id'], version=ref.get('version'))
    if not row or row['scope'] != 'PUBLIC' or reference(row) != ref or row['known_ms'] > millis(at):
        raise Conflict('cycle thesis is unknown at this time or binding has changed')
    return row


def put(db, kind, object_id, payload, refs, reason, run, version):
    objects = Entities(db)
    spec = {'kind': kind, 'object_id': object_id, 'scope': 'PUBLIC', 'payload': payload,
            'dependencies': refs, 'observed_at': run['known_at'], 'known_at': run['known_at'],
            'source_ref': run['ref'], 'method_version': run['method_version'], 'change_reason': reason}
    saved = objects.put(spec, expected_version=version)
    return objects.get(kind, object_id, version=saved['version'])


def packet_observation(db, ref, meta):
    selector = ref.get('sample')
    if (meta['provider'] != 'ANONYMOUS_MARKET_COLLECTION' or not isinstance(selector, dict)
            or set(selector) != {'dataset', 'time'}):
        raise ValueError('a packet observation needs its frozen dataset and original row time')
    packet = db.value(meta['digest'])
    dataset = packet['datasets'].get(selector['dataset'])
    start = millis(selector['time'])
    row = next((item for item in dataset['rows'] if millis(item['time']) == start), None) if dataset else None
    if not row:
        raise ValueError('the named observation is missing from the frozen public packet')
    receipt = packet['retrievals'][selector['dataset']]
    observed = millis(row.get('end', row['time']))
    known = millis(receipt['retrieved_at'])
    if receipt['status'] != 'RECEIVED' or not observed <= known <= millis(meta['known_at']):
        raise ValueError('the named packet observation lacks a dated completed public retrieval')
    unit = (dataset['source'], dataset['instrument'], dataset['kind'], dataset['interval_seconds'], start)
    return unit, row, {'observed_ms': observed, 'known_ms': known}


def packet_sample(db, ref, meta):
    unit, row, timing = packet_observation(db, ref, meta)
    return unit, fingerprint(encode(row)), timing


def source_index(db, refs, at, *, individual=False):
    """One underlying observation can appear in more than one retrieval or reference."""
    from .trading import public_evidence

    index = {}
    for ref in public_evidence(db, refs):
        if ref['type'] == 'BLOB':
            digest = ref['digest'].removeprefix('sha256:')
            meta = db.metadata('trading.public.' + digest)
            if millis(meta['known_at']) > millis(at):
                raise ValueError('cycle evidence was unavailable at assessment time')
            revision = digest
            if 'sample' in ref:
                key, revision, _ = packet_sample(db, ref, meta)
            elif meta['provider'] == 'PUBLIC_SOURCE_RETRIEVAL':
                original = db.value(digest)
                revision = fingerprint(original['content'].encode())
                key = (original['url'], millis(original['observed_at']))
            elif meta['provider'] == 'ANONYMOUS_MARKET_COLLECTION':
                if individual:
                    raise ValueError('cite a particular frozen sample for an individual counterevidence event')
                revision = db.value(digest)['evidence_id']
                key = (meta['provider'], revision)
            else:
                key = ('BLOB', digest)
        elif ref['type'] == 'OBSERVATION':
            row = db.one('SELECT known_ms,digest FROM observations WHERE product=%s AND series=%s AND item_key=%s '
                         'AND observation_key=%s', tuple(ref[k] for k in ('product', 'series', 'item_key', 'observation_key')))
            if not row or row['known_ms'] > millis(at):
                raise ValueError('cycle evidence needs an observation revision known at assessment')
            key = ('OBSERVATION', ref['product'], ref['series'], ref['item_key'])
            revision = row['digest']
        else:
            if type(ref.get('version')) is not int:
                raise ValueError('cycle evidence needs an exact public entity version')
            record = Entities(db).get(ref['kind'], ref['object_id'], version=ref['version'])
            if not record or record['known_ms'] > millis(at) or record['digest'] != ref.get('digest', record['digest']):
                raise ValueError('cycle evidence needs a matching known public entity')
            key = ('ENTITY', ref['kind'], ref['object_id'])
            revision = record['digest']
        index.setdefault((key, revision), ref)
    return index


def revisions(db, current, changes, reviews, refs, run):
    previous = bindings(current)
    next_bindings = dict(previous)
    if not isinstance(changes, dict) or not isinstance(reviews, dict):
        raise ValueError('cycle theses and hourly reviews must be keyed by their stable subjects')
    if changes:
        source_index(db, refs, run['known_at'])
    for key, definition in changes.items():
        object_id = subject_id(key)
        prior = bound(db, previous[key], run['known_at']) if key in previous else None
        if prior and definition == previous[key]:
            continue
        if not isinstance(definition, dict) or definition.get('status') not in {'ESTABLISHED', 'UNRESOLVED'}:
            raise ValueError('a cycle thesis needs an established or unresolved assessment')
        fields = {field: text(definition.get(field), 'cycle thesis ' + field) for field in THESIS_FIELDS
                  if field == 'claim' or definition['status'] == 'ESTABLISHED' or definition.get(field) is not None}
        fields['status'] = definition['status']
        why = text(definition.get('reason'), 'why this cycle thesis is adopted')
        if prior and {k: v for k, v in prior['payload'].items() if k != 'replaces'} == fields:
            continue
        if prior and key not in reviews:
            raise ValueError('revising a cycle thesis requires assessing its original expectation')
        dependencies = refs
        if prior:
            fields['replaces'] = previous[key]
            dependencies = [*refs, dependency(previous[key])]
        next_bindings[key] = reference(put(db, 'HYPOTHESIS', object_id, fields, dependencies, why, run,
                                           prior['version'] if prior else 0))
    return next_bindings


def reviewed_evidence(db, latest, checked, original, at):
    from .trading_decisions import _later_evidence

    additions = checked.get('counterevidence', [])
    resolved = checked.get('resolved', {})
    if not isinstance(additions, list) or not isinstance(resolved, dict):
        raise ValueError('provide observed counterevidence and reasons for resolving previous items')
    pending = dict(latest['pending'])
    # Keep only identity and source references for closed items. Findings and resolutions
    # can be recovered in full from the immutable hourly version that settled them.
    settled = dict(latest.get('settled_sources', {}))
    for key, why in resolved.items():
        if key not in pending:
            raise Conflict('resolve the original outstanding counterevidence id')
        text(why, 'why this counterevidence is resolved')
        prior = pending.pop(key)
        settled[key] = {field: prior[field] for field in ('sources', 'evidence', 'first_known_at')}
    used = {key: {tuple(item) for item in row['sources']} for key, row in {**settled, **pending}.items()}
    new_ids = set()
    for item in additions:
        key = text(item.get('id'), 'independent event id')
        if key in resolved or key in new_ids:
            raise ValueError('submit a counterevidence item once per assessment')
        new_ids.add(key)
        finding = text(item.get('finding'), 'actual counterevidence')
        refs = item['evidence']
        cited = source_index(db, refs, at, individual=True)
        sources = {unit for unit, _ in cited}
        if not _later_evidence(db, Entities(db), refs, original=original, cutoff=millis(at), observation_end=None):
            raise ValueError('new counterevidence requires a dated market observation after the original expectation')
        if any(key != other and sources <= known for other, known in used.items()):
            raise ValueError('carry repeated evidence under its existing independent event id')
        previous = pending.get(key) or settled.get(key) or {}
        originals = source_index(db, previous['evidence'], at) if previous else {}
        if key in settled and cited.keys() <= originals.keys():
            raise ValueError('reopening settled counterevidence requires a new observation or a source revision')
        settled.pop(key, None)
        for signature, ref in cited.items():
            originals.setdefault(signature, ref)
        evidence = list(originals.values())
        used[key] = used.get(key, set()) | sources
        pending[key] = {'finding': finding, 'sources': sorted(used[key]), 'evidence': evidence,
                        'first_known_at': previous.get('first_known_at', at)}
    return pending, settled


def hourly(db, previous, following, reviews, refs, run, *, opened_at):
    from .trading_decisions import _later_evidence

    objects = Entities(db)
    if reviews:
        source_index(db, refs, run['known_at'])
    for key, checked in reviews.items():
        if key not in previous or not isinstance(checked, dict) or checked.get('target') != previous[key]:
            raise Conflict('review the exact cycle thesis in effect before this market assessment')
        outcome = checked.get('outcome')
        if outcome not in OUTCOMES:
            raise ValueError('invalid cycle review outcome')
        reason = text(checked.get('reason'), 'meaning of the observed response for the cycle thesis')
        observed = text(checked.get('observed_response'), 'observed market response')
        review_id = subject_id(key) + '/hourly'
        latest = objects.get('REVIEW', review_id)
        prior_ref = checked.get('prior_review')
        if prior_ref is not None and (not latest or prior_ref != reference(latest)):
            raise Conflict('the independent review head changed; read its latest public evidence')
        if latest and latest['known_ms'] > millis(opened_at) and prior_ref != reference(latest):
            raise Conflict('another hourly assessment arrived after open; read thesis_history at the actual current '
                           'as_of and cite the latest prior_review after assessing it')
        original = bound(db, previous[key], run['known_at'])
        if outcome == 'REFUTED' and previous[key] == following[key]:
            raise ValueError('a refuted cycle thesis must be revised or marked unresolved')
        if (outcome != 'UNRESOLVED' and not _later_evidence(
                db, objects, refs, original=original['known_ms'], cutoff=millis(run['known_at']), observation_end=None)):
            raise ValueError('testing a cycle expectation needs a later dated public observation')
        pending, settled = reviewed_evidence(db, latest['payload'] if latest else {'pending': {}},
                                            checked, original['known_ms'], run['known_at'])
        dependencies = [dependency(previous[key]), *refs]
        if following[key] != previous[key]:
            dependencies.append(dependency(following[key]))
        for entry in pending.values():
            for ref in entry['evidence']:
                if ref not in dependencies:
                    dependencies.append(ref)
        payload = {'target': previous[key], 'resulting_thesis': following[key], 'outcome': outcome,
                   'observed_response': observed, 'reason': reason, 'pending': pending,
                   'resolved': checked.get('resolved', {}), 'settled_sources': settled, 'opened_at': opened_at,
                   'assessed_at': run['known_at']}
        put(db, 'REVIEW', review_id, payload, dependencies, reason, run, latest['version'] if latest else 0)


def context(db, market, at):
    objects = Entities(db)
    result = {}
    for key, ref in bindings(market).items():
        name = subject_id(key)
        # A retrospective can be completed after its thesis is superseded. Select by
        # actual knowledge time across that key's versions, including an older thesis.
        prefix = name.replace('=', '==').replace('%', '=%').replace('_', '=_') + '/recap/%'
        latest = db.one("SELECT e.object_id,v.version FROM entities e JOIN entity_versions v ON e.entity_key=v.entity_key "
                        "WHERE e.kind='REVIEW' AND e.scope='PUBLIC' AND e.object_id LIKE %s ESCAPE '=' AND v.known_ms<=%s "
                        "ORDER BY v.known_ms DESC,v.recorded_ms DESC,e.object_id DESC,v.version DESC LIMIT 1",
                        (prefix, millis(at)))
        result[key] = {'thesis': bound(db, ref, at), 'review': objects.get('REVIEW', name + '/hourly', as_of=at),
                       'recap': objects.get('REVIEW', latest['object_id'], version=latest['version']) if latest else None}
    return result


def recap_id(row):
    return row['object_id'] + '/recap/' + str(row['version'])


def recap(db, run, *, target, expected_version, direction, mechanism, timing, lesson, evidence):
    from .trading_decisions import _later_evidence

    original = bound(db, target, run['known_at'])
    assessments = {'direction': direction, 'mechanism': mechanism, 'timing': timing}
    for item in assessments.values():
        if not isinstance(item, dict) or item.get('outcome') not in {'SUPPORTED', 'REFUTED', 'UNRESOLVED'}:
            raise ValueError('review the original direction, mechanism and timing separately')
        text(item.get('reason'), 'basis for the retrospective outcome')
    source_index(db, evidence, run['known_at'])
    if (any(item['outcome'] != 'UNRESOLVED' for item in assessments.values())
            and not _later_evidence(db, Entities(db), evidence, original=original['known_ms'],
                                    cutoff=millis(run['known_at']), observation_end=None)):
        raise ValueError('a realized or refuted expectation needs a later dated public observation')
    lesson = text(lesson, 'conditions under which this comparison applies again')
    return put(db, 'REVIEW', recap_id(original), {'target': target, **assessments, 'lesson': lesson},
               [dependency(target), *evidence], lesson, run, expected_version)


def history(db, at, *, key, version=None, stream='REVIEWS', after=0, limit=20):
    object_id = subject_id(key)
    objects = Entities(db)
    if version is not None and (type(version) is not int or version < 1):
        raise ValueError('positive original cycle thesis version required')
    original = objects.get('HYPOTHESIS', object_id, version=version, as_of=at)
    if not original or original['scope'] != 'PUBLIC' or original['known_ms'] > millis(at):
        raise Conflict('no such public cycle thesis at the requested knowledge time')
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('bounded cycle history range required')
    names = {'REVIEWS': ('REVIEW', object_id + '/hourly'), 'THESES': ('HYPOTHESIS', object_id),
             'RECAPS': ('REVIEW', recap_id(original))}
    if stream not in names:
        raise ValueError('unknown cycle history stream')
    kind, name = names[stream]
    lower, upper = 0, millis(at)
    if stream == 'REVIEWS' and version is not None:
        lower = original['known_ms']
        successor = objects.get('HYPOTHESIS', object_id, version=version + 1)
        if successor:
            upper = min(upper, successor['known_ms'])
    rows, cursor = [], after
    while len(rows) <= limit:
        page = db.rows('SELECT version FROM entity_versions WHERE entity_key=%s AND version>%s '
                       'AND known_ms BETWEEN %s AND %s ORDER BY version LIMIT %s',
                       (entity_key(kind, name), cursor, lower, upper, limit + 1))
        for item in page:
            row = objects.get(kind, name, version=item['version'])
            payload = row['payload']
            if (stream != 'REVIEWS' or version is None
                    or reference(original) in (payload['target'], payload['resulting_thesis'])):
                rows.append(row)
        if len(page) <= limit:
            break
        cursor = page[-1]['version']
    from .trading import MARKET
    from .trading_decisions import current_market

    market = current_market(db, MARKET, as_of=at)
    return {'as_of': at, 'market': reference(market) if market else None, 'thesis': original,
            'latest_review': objects.get('REVIEW', object_id + '/hourly', as_of=at),
            'recap': objects.get('REVIEW', recap_id(original), as_of=at), 'stream': stream,
            'history': rows[:limit], 'next': rows[limit - 1]['version'] if len(rows) > limit else None}
