"""Source-bound views of the confirmed policy, resolved by investment subject.

Views reflect confirmed local policy without changing files or confirming an
execution. The existing short's opening-price reference is held by this projection.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .codec import Conflict, encode, fingerprint, identity, millis, stamp, utc
from .entities import Entities
from .fact_views import NAMES


PREFIX = 'trading/policy/'
SEED = 'trading.policy.bootstrap'
POSITION_SUBJECT = 'existing-position'
POSITION_OWNER = PREFIX + POSITION_SUBJECT
KINDS = {'MONTHLY', 'OPPORTUNITY', 'POSITION'}
FIELDS = {
    'MONTHLY': {'weights', 'budget', 'currency', 'calendar', 'purpose', 'constraints'},
    'OPPORTUNITY': {'asset', 'product', 'budget', 'minimum', 'currency', 'calendar', 'limit', 'purpose', 'constraints'},
    'POSITION': {'position', 'baseline', 'purpose', 'constraints'},
}
NUM = r'(?:[0-9]+(?:,[0-9]{3})*)(?:\.[0-9]+)?'
OPENING_PRICE = re.compile(
    r'(?:[-*]\s*)?(?:用户告知\s*)?[「“"]?\s*(?:空单\s*)?'
    r'(?:开(?:仓|单)(?:参考)?价(?:格)?|开仓参考|opening price|entry price)\s*(?:为|是|[:：=])?\s*'
    + NUM + r'(?:\s*[A-Z]+)?\s*[」”"]?', re.IGNORECASE,
)
PRICE_CACHE_SCOPE = re.compile(r'按已有\s*\w+\s*分析范围[，,]\s*以\s*[A-Z]+\s*缓存该价格条件')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' is required')
    return value.strip()


def _number(value, label):
    if type(value) not in (str, int, float):
        raise ValueError(label + ' requires a positive number')
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(label + ' requires a positive number') from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(label + ' requires a positive number')
    return number


def _normalized(number):
    if abs(number.adjusted()) > 30:
        return str(number)
    text = format(number, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _calendar(value):
    answer = _text(value, 'confirmed planning calendar')
    try:
        ZoneInfo(answer)
    except ZoneInfoNotFoundError as exc:
        raise ValueError('unknown planning calendar') from exc
    return answer


def _policy(kind, fields):
    if kind not in KINDS or not isinstance(fields, dict):
        raise ValueError('confirmed investment purpose and fields are required')
    if not fields.keys() <= FIELDS[kind]:
        raise ValueError('policy fields must describe this investment subject')
    values = dict(fields)
    view = {'kind': kind}
    if kind == 'MONTHLY':
        weights = values.get('weights')
        if not isinstance(weights, dict) or not weights:
            raise ValueError('the confirmed basket needs constituent weights')
        weights = {_text(product, 'product'): _number(weight, 'weight') for product, weight in weights.items()}
        if len(weights) != len(values['weights']) or sum(weights.values()) != 100:
            raise ValueError('weights must total 100 percent within the confirmed basket')
        view.update(budget=_normalized(_number(values.get('budget'), 'basket budget')),
                    currency=_text(values.get('currency'), 'planning currency'),
                    calendar=_calendar(values.get('calendar')))
        values.update({k: view[k] for k in ('budget', 'currency', 'calendar')},
                      weights={symbol: _normalized(weight) for symbol, weight in weights.items()})
    elif kind == 'OPPORTUNITY' and 'budget' in values:
        budget = _number(values['budget'], 'opportunity maximum')
        minimum = _number(values['minimum'], 'opportunity minimum') if 'minimum' in values else None
        if minimum is not None and minimum > budget:
            raise ValueError('opportunity amount range is inverted')
        view.update(budget=_normalized(budget), currency=_text(values.get('currency'), 'opportunity currency'))
        if minimum is not None:
            view['minimum'] = _normalized(minimum)
        if values.get('calendar') is not None:
            view['calendar'] = _calendar(values['calendar'])
        values.update({k: v for k, v in view.items() if k != 'kind'})
    elif kind == 'POSITION':
        position = values.get('position') or {}
        if not isinstance(position, dict) or position.get('status', 'UNKNOWN') not in {'PRESENT', 'ABSENT', 'UNKNOWN'}:
            raise ValueError('invalid confirmed position state')
        view['position_status'] = position.get('status', 'UNKNOWN')
        if view['position_status'] != 'UNKNOWN' and not position.get('observed_at'):
            raise ValueError('a confirmed position state needs its confirmation time')
    view['basis'] = fingerprint(encode({'kind': kind, 'inputs': values}))
    return {**view, 'inputs': values}


def _files(db):
    project = Path(db.settings['project'])
    result = {}
    for name in NAMES:
        try:
            result[name] = (project / name).read_bytes()
        except FileNotFoundError:
            continue
    return result


def _live_refs(refs, files):
    if not isinstance(refs, list) or not refs:
        raise ValueError('cite the literal confirmed source of this policy')
    result = []
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {'file', 'line_start', 'line_end', 'quote'}:
            raise ValueError('literal source references need file, line range and quote')
        quote = _text(ref['quote'], 'literal confirmed quote')
        if ref['file'] not in NAMES or ref['file'] not in files:
            raise Conflict('confirmed source file is unavailable')
        raw = files[ref['file']].decode('utf-8')
        lines = raw.splitlines()
        first, last = ref['line_start'], ref['line_end']
        if type(first) is not int or type(last) is not int or not 1 <= first <= last:
            raise ValueError('invalid literal source line range')
        if last <= len(lines) and quote in '\n'.join(lines[first-1:last]):
            result.append({**ref, 'quote': quote})
        elif raw.count(quote) == 1:
            # A unique literal may move with surrounding edits; its meaning is
            # the same and its location has no bearing on applicability.
            first = raw[:raw.index(quote)].count('\n') + 1
            result.append({**ref, 'quote': quote, 'line_start': first, 'line_end': first + quote.count('\n')})
        else:
            raise Conflict('confirmed policy clause changed or became ambiguous')
    return result


def _plain(value):
    return value.translate(str.maketrans({'*': '', '`': '', '％': '%'}))


def _number_tokens(value):
    return {Decimal(token.replace(',', '')) for token in re.findall(r'(?<![\d.,])' + NUM + r'(?![\d.,])', _plain(value))}


def _has_unit(text, unit):
    return re.search(r'(?<![A-Za-z])' + re.escape(unit) + r'(?![A-Za-z0-9])', _plain(text)) is not None


def _basket_in(quote, fields):
    text = _plain(quote)
    budget = Decimal(fields['budget'])
    unit = re.escape(fields['currency'])
    units = re.findall(r'(?<![\d.,])(' + NUM + r')\s*' + unit + r'(?![A-Za-z0-9])', text)
    if budget not in {Decimal(number.replace(',', '')) for number in units}:
        return False
    for product, weight in fields['weights'].items():
        name = re.escape(product)
        values = re.findall(r'(?<![A-Za-z0-9_.])' + name + r'(?![A-Za-z0-9_.])'
                            r'[^\w%\n。；;]*(?:(?:占比?|权重|为|Weight)[^\w%\n。；;]*)*('
                            + NUM + r')\s*%', text, re.IGNORECASE)
        if Decimal(weight) not in {Decimal(number.replace(',', '')) for number in values}:
            return False
    return True


def _verify_fields(policy, refs, *, verify_price=True):
    source = '\n'.join(ref['quote'] for ref in refs)
    if policy['kind'] == 'MONTHLY' and not _basket_in(source, policy['inputs']):
        raise Conflict('basket budget and product weights need their actual confirmed wording')
    if policy['kind'] == 'OPPORTUNITY' and 'budget' in policy:
        amounts = {Decimal(policy[key]) for key in ('budget', 'minimum') if key in policy}
        if not amounts <= _number_tokens(source) or not _has_unit(source, policy['currency']):
            raise Conflict('independent contribution range needs its actual confirmed wording')
    if verify_price and policy['kind'] == 'POSITION':
        position = policy['inputs'].get('position') or {}
        entry = position.get('entry_reference')
        if entry is not None and (_number(entry.get('price'), 'opening price') not in _number_tokens(source)
                                  or not _has_unit(source, _text(entry.get('currency'), 'opening price currency'))):
            raise Conflict('opening price needs its own confirmed wording')


def _local_source_ref(ref):
    suffix = str(ref['line_start'])
    if ref['line_end'] != ref['line_start']:
        suffix += '-' + str(ref['line_end'])
    return ref['file'] + '#L' + suffix


def _price_fields(owner):
    payload = owner['payload']
    policy = payload.get('policy') or {}
    position = policy.get('inputs', {}).get('position') or {}
    entry = position.get('entry_reference')
    if entry is None:
        return None
    price = _number(entry.get('price'), 'opening price')
    source_ref = entry.get('source_ref')
    if source_ref is None:
        quotes = {_local_source_ref(ref) for ref in _clauses(payload['refs'])
                  if OPENING_PRICE.fullmatch(_plain(ref['quote'])) and price in _number_tokens(ref['quote'])}
        source_ref = next(iter(quotes)) if len(quotes) == 1 else owner['source_ref']
    return {'price': _normalized(price), 'currency': _text(entry.get('currency'), 'opening price currency'),
            'observed_at': stamp(_text(entry.get('observed_at'), 'opening price confirmation time')),
            'source_ref': _text(source_ref, 'opening price confirmation source'),
            'instrument': _text(entry.get('instrument', position.get('instrument')), 'opening price instrument'),
            'product': entry.get('product', position.get('product'))}


def _price_projection(fields):
    basis = {**fields, 'direction': 'SHORT'}
    return {**basis, 'revision': fingerprint(encode(basis))}


def entry_reference(db):
    """Read the confirmed opening price independently of its legacy files."""
    owner = Entities(db).get('PROFILE', POSITION_OWNER)
    entry = _price_fields(owner) if owner else None
    return _price_projection(entry) if entry is not None else None


def set_entry_reference(db, *, price, confirmed_at, confirmation_ref):
    """Replace only the opening-price condition in the caller's writer transaction."""
    price = _normalized(_number(price, 'opening price'))
    confirmed = utc(_text(confirmed_at, 'opening price confirmation time'))
    source = _text(confirmation_ref, 'opening price confirmation source')
    now = utc()
    if confirmed > now:
        raise ValueError('opening price confirmation cannot be in the future')
    objects = Entities(db)
    owner = objects.get('PROFILE', POSITION_OWNER)
    if not owner or owner['payload']['kind'] != 'POSITION' or not owner['payload'].get('policy'):
        raise Conflict('the existing short policy needs to be established before entering its opening price')
    previous = _price_fields(owner)
    if previous:
        observed = utc(previous['observed_at'])
        if previous['source_ref'] == source:
            if previous['price'] == price and observed == confirmed:
                return _price_projection(previous)
            raise Conflict('the opening price confirmation source was already used with other details')
        if confirmed <= observed:
            raise Conflict('the opening price confirmation must be newer than the stored confirmation')

    payload = owner['payload']
    policy = payload['policy']
    inputs = policy['inputs']
    position = inputs.get('position') or {}
    scope = previous if previous is not None else position
    entry = {'price': price, 'currency': _text(scope.get('currency', 'USDT'), 'opening price currency'),
             'observed_at': stamp(confirmed),
             'source_ref': source, 'instrument': _text(scope.get('instrument'), 'opening price instrument'),
             'product': scope.get('product')}
    updated_inputs = {**inputs, 'position': {**position, 'entry_reference': entry}}
    updated = {**policy, 'inputs': updated_inputs,
               'basis': fingerprint(encode({'kind': policy['kind'], 'inputs': updated_inputs}))}
    objects.put({'kind': 'PROFILE', 'object_id': POSITION_OWNER, 'scope': owner['scope'],
                 'product': owner['product'], 'active': owner['active'], 'review_at': owner['review_at'],
                 'valid_until': owner['valid_until'], 'dependencies': owner['dependencies'],
                 'payload': {**payload, 'policy': updated},
                 'change_reason': 'record the user-confirmed opening price for the existing short',
                 'source_ref': source, 'method_version': db.settings.get('release_id', 'confirmed-entry-reference'),
                 'observed_at': now, 'known_at': now},
                expected_version=owner['version'], trusted_projection=True)
    return entry_reference(db)


def _store(db, *, subject, kind, policy, refs, files, expected_version, run, active, ended_reason=None):
    objects = Entities(db)
    object_id = PREFIX + subject
    previous = objects.get('PROFILE', object_id)
    if previous and previous['payload']['kind'] != kind:
        raise Conflict('a confirmed subject cannot change investment purpose by relabeling')
    held = previous['version'] if previous else 0
    if type(expected_version) is not int or held != expected_version:
        raise Conflict('the source view changed before submission')
    payload = {'subject': subject, 'kind': kind, 'policy': policy, 'refs': refs, 'applicable': active}
    if ended_reason:
        payload['ended_reason'] = ended_reason
    objects.put({'kind': 'PROFILE', 'object_id': object_id, 'scope': 'PERSONAL', 'payload': payload,
                 'change_reason': 'capture confirmed policy wording for one investment subject',
                 'source_ref': run['ref'], 'observed_at': run['known_at'], 'known_at': run['known_at'],
                 'method_version': run['method_version'],
                 'dependencies': [{'type': 'BLOB', 'digest': db.put_blob(files[name]), 'relation': 'LOCAL_POLICY_SOURCE'}
                                  for name in sorted({r['file'] for r in refs})]},
                expected_version=expected_version, trusted_projection=True)
    return objects.get('PROFILE', object_id)


def _verify_unchanged(db, original, refs):
    project = Path(db.settings['project'])
    try:
        changed = any((project / name).read_bytes() != original[name] for name in {r['file'] for r in refs})
    except FileNotFoundError as exc:
        raise Conflict('a cited source disappeared during policy capture') from exc
    if changed:
        raise Conflict('a cited source changed during policy capture')


def record(db, *, subject, kind, fields, refs, expected_version, run, active=True):
    subject = _text(subject, 'stable policy subject')
    if len(subject) > 240 or kind not in KINDS or type(active) is not bool or not isinstance(fields, dict):
        raise ValueError('invalid policy subject, purpose or scope status')
    ended_reason = _text(fields.get('reason'), 'why the confirmed scope ended') if not active else None
    existing_short = subject == POSITION_SUBJECT and kind == 'POSITION'
    owner = Entities(db).get('PROFILE', POSITION_OWNER) if existing_short else None
    saved_entry = _price_fields(owner) if owner else None
    if existing_short and active:
        supplied = fields.get('position') or {}
        if not isinstance(supplied, dict):
            raise ValueError('invalid confirmed position state')
        previous_status = (owner['payload'].get('policy') or {}).get('position_status') if owner else None
        if previous_status in {'PRESENT', 'ABSENT'} and 'status' not in supplied:
            raise Conflict('a policy update must retain or explicitly reassess the established position state')
        position = {key: value for key, value in supplied.items() if key != 'entry_reference'}
        if saved_entry is not None:
            position['entry_reference'] = saved_entry
        if position or 'position' in fields:
            fields = {**fields, 'position': position}
    policy = _policy(kind, fields) if active else None
    files = _files(db)
    if existing_short:
        refs = _position_refs(refs)
    refs = _live_refs(refs, files)
    if policy:
        _verify_fields(policy, refs, verify_price=not existing_short)
        if kind == 'POSITION' and policy['position_status'] != 'UNKNOWN':
            if policy['position_status'] == 'ABSENT':
                state_words = r'已平仓|已清仓|未持有|不持有|无(?:实际)?(?:持仓|仓位|现仓|空单)|仓位已归零|无仓'
            else:
                state_words = r'已开仓|仍持有|目前持有|当前持有|实际持有|在仓|尚未平仓'
            unconfirmed = r'若|如果|假设|假定|是否|未知|待确认|未确认|未核实|无法|不能确认|不证明|不确认|历史'
            if not any(re.search(state_words, ref['quote']) and not re.search(unconfirmed, ref['quote'])
                       for ref in _clauses(refs)):
                raise Conflict('confirmed position state needs its own explicit source wording')
        position = fields.get('position') or {}
        local_entry = {} if existing_short else position.get('entry_reference') or {}
        for source in (position, local_entry, fields.get('baseline') or {}):
            if source.get('observed_at') and millis(source['observed_at']) > millis(run['known_at']):
                raise ValueError('confirmed fact observation cannot postdate the source view')
    elif saved_entry is not None:
        previous = owner['payload']['policy']
        position = previous['inputs']['position']
        policy = _policy(kind, {**previous['inputs'], 'position': {**position, 'entry_reference': saved_entry}})
    result = _store(db, subject=subject, kind=kind, policy=policy, refs=refs, files=files,
                    expected_version=expected_version, run=run, active=active, ended_reason=ended_reason)
    _verify_unchanged(db, files, refs)
    return result


def _clauses(references):
    return [{**ref, 'quote': part.strip()} for ref in references
            for part in re.split(r'[。；;\n]', ref['quote']) if part.strip()]


def _position_refs(refs):
    """Leave standalone legacy price wording to the stored price's audit."""
    if not isinstance(refs, list):
        return refs
    result = []
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {'file', 'line_start', 'line_end', 'quote'}
                or not isinstance(ref['quote'], str)):
            result.append(ref)
            continue
        clauses = _clauses([ref])
        nonprice = [part for part in clauses if not
                    (OPENING_PRICE.fullmatch(_plain(part['quote'])) or PRICE_CACHE_SCOPE.fullmatch(_plain(part['quote'])))]
        result.extend(nonprice if len(nonprice) < len(clauses) else [ref])
    return result


def _legacy_views(db, release, files, *, seed_entry=False):
    from .execution import load_methods
    load_methods(release)
    from v4.position_analysis import analysis_refs, economic_projection, latest, validate_spec

    project = Path(db.settings['project'])
    owner = latest(project / '.strategy/v4')
    if not owner:
        return {}, {}
    raw = owner['position_analysis']
    validate_spec(raw)
    economic = economic_projection(raw)
    plan = economic.get('monthly_plan')
    clauses = _clauses(owner['source_refs'])
    scopes, gaps = {}, {}

    def verify(subject, kind, fields, refs):
        try:
            policy = _policy(kind, fields)
            sources = _live_refs(refs, files)
            _verify_fields(policy, sources)
        except ValueError:
            gaps[subject] = 'the confirmed wording of this scope needs review'
        else:
            scopes[subject] = (policy, sources)

    if not plan:
        if economic.get('baseline'):
            verify('existing-position', 'POSITION', {'baseline': economic['baseline']}, analysis_refs(raw))
        return scopes, gaps

    for basket in plan.get('regular_baskets', []):
        weights = basket['weights']
        subject = basket.get('id') or 'basket-' + identity(sorted(weights))[:20]
        scoped = {'weights': weights, 'budget': basket['amount'], 'currency': plan['currency'],
                  'calendar': plan['month_timezone']}
        fields = _policy('MONTHLY', scoped)['inputs']
        references = [ref for ref in clauses if _basket_in(ref['quote'], fields)]
        verify(subject, 'MONTHLY', scoped, references)

    extra = {'budget': plan['extra_max'], 'minimum': plan['extra_min'], 'limit': plan['extra_limit'],
             'calendar': plan['month_timezone'], 'currency': plan.get('extra_currency', plan['currency'])}
    bounds = {Decimal(str(extra['budget'])), Decimal(str(extra['minimum']))}
    verify('independent-opportunity', 'OPPORTUNITY', extra,
           [ref for ref in clauses if bounds <= _number_tokens(ref['quote']) and _has_unit(ref['quote'], extra['currency'])])

    short = economic.get('short_position')
    if short:
        # The archived record distinguishes purpose, presence and a price-only
        # reference. Losing the latter cannot erase the actual position scope.
        source = raw['short_position']
        refs = [*source['source_refs'], *[ref for ref in clauses if '空单' in ref['quote']
                                         and any(term in ref['quote'] for term in ('补偿', '动态条件'))]]
        entry = source.get('entry_reference')
        if entry is not None and seed_entry:
            try:
                price_refs = _live_refs([entry['source_ref']], files)
            except ValueError:
                short['entry_reference'] = None
            else:
                short['entry_reference']['source_ref'] = _local_source_ref(price_refs[0])
                refs = [*refs, *price_refs]
        else:
            short['entry_reference'] = None
        verify(POSITION_SUBJECT, 'POSITION', {'position': short, 'purpose': plan['short_objective']}, refs)
    return scopes, gaps


def seed(db, release, run):
    """Bring forward source-verified scopes once, after the public freeze."""
    if db.metadata(SEED) is not None:
        return
    files = _files(db)
    try:
        scopes, gaps = _legacy_views(db, release, files, seed_entry=True)
    except (ValueError, OSError):
        scopes, gaps = {}, {'existing-policy': 'the previous confirmed policy requires source review'}
    objects = Entities(db)
    for subject, (policy, refs) in scopes.items():
        if objects.get('PROFILE', PREFIX + subject) is None:
            _store(db, subject=subject, kind=policy['kind'], policy=policy, refs=refs, files=files,
                   expected_version=0, run=run, active=True)
            _verify_unchanged(db, files, refs)
    db.metadata(SEED, {'unavailable': gaps}, write=True)


def inputs(db, release):
    """Applicable policies and source gaps, without requiring whole-file equality."""
    files = _files(db)
    marker = db.metadata(SEED)
    if marker is None:
        try:
            old, gaps = _legacy_views(db, release, files)
        except (ValueError, OSError):
            old, gaps = {}, {'existing-policy': 'the previous confirmed policy requires source review'}
        current = {subject: policy for subject, (policy, _) in old.items()}
    else:
        current, gaps = {}, dict(marker['unavailable'])
    objects = Entities(db)
    views = db.rows("SELECT object_id FROM entities WHERE kind='PROFILE' AND scope='PERSONAL' "
                    'AND LEFT(object_id,%s)=%s ORDER BY object_id', (len(PREFIX), PREFIX))
    for row in views:
        view = objects.get('PROFILE', row['object_id'])['payload']
        subject = view['subject']
        current.pop(subject, None)
        gaps.pop(subject, None)
        try:
            refs = _position_refs(view['refs']) if subject == POSITION_SUBJECT else view['refs']
            if refs or subject != POSITION_SUBJECT or not view['refs']:
                _live_refs(refs, files)
        except ValueError:
            gaps[subject] = 'the confirmed wording of this scope needs review'
        else:
            if view['applicable']:
                current[subject] = view['policy']
    known_applicability = bool(current or (marker is not None and views and not gaps))
    status = 'PARTIAL' if current and gaps else 'CURRENT' if known_applicability else (
        'CONFIRMED_POLICY_REQUIRES_RECHECK' if gaps else 'NO_CONFIRMED_POLICY')
    return current, {'state': status, 'unavailable': gaps}
