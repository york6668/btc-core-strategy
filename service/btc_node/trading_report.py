"""Compose the displayed investment report from the destination's confirmed edition."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from .codec import Conflict, utc
from .trading_policy import POSITION_SUBJECT


SECTIONS = {
    'market': ('核心判断与当前定价', ('BTC', 'SPX', 'NDX')),
    'outlook': ('中期展望与情景', ('primary', 'alternative')),
    'opportunities': ('投资机会与风险收益', ('comparison',)),
    'allocation': ('资产配置与持仓纪律', ()),
    'crosschecks': ('交叉验证与分歧', ('evidence',)),
    'risks': ('关键风险与失效条件', ('conditions',)),
}
TOKYO = ZoneInfo('Asia/Tokyo')
MARKET_LABELS = {'BTC': 'BTC', 'SPX': '标普 500（SPX）', 'NDX': '纳指 100（NDX）'}


def _display_time(value):
    return utc(value).astimezone(TOKYO).strftime('%Y-%m-%d %H:%M JST')


def expired_risks(document, at):
    moment = utc(at)
    return {note['event_ref'] for note in document['risk_updates']
            if note.get('valid_until') and utc(note['valid_until']) <= moment}


def _replace(prior, change):
    if isinstance(change, str) and change.strip():
        return change
    if not isinstance(change, dict) or set(change) != {'from', 'to'}:
        raise ValueError('supply new report text or one exact {from,to} passage replacement')
    old, new = change['from'], change['to']
    if not isinstance(old, str) or not old.strip() or not isinstance(new, str):
        raise ValueError('a passage edit needs the original text and its replacement')
    start = prior.find(old) if prior is not None else -1
    if start < 0 or prior.find(old, start + 1) >= 0:
        raise Conflict('the original passage must occur exactly once in the confirmed report item')
    result = prior.replace(old, new, 1)
    if not result.strip():
        raise ValueError('a report item cannot be emptied')
    return result


def render(document):
    sections = document['modules']
    moment = _display_time(document['assessed_at'])
    if document['limited']:
        heading = '公开风险更新于 ' + moment
        last_review = document['reviewed_at']
        if last_review:
            heading += '。其余判断沿用 ' + _display_time(last_review) + ' 的组合评估。'
    else:
        heading = '评估时间 ' + moment
    parts = [heading]
    for name, (title, items) in SECTIONS.items():
        parts.append('## ' + title)
        order = document['member_order'] if name == 'allocation' else items
        for item in order:
            paragraph = sections[name][item]
            if name == 'market':
                paragraph = '**' + MARKET_LABELS[item] + '**　' + paragraph
            elif name == 'outlook':
                paragraph = ('**主情景**　' if item == 'primary' else '**竞争情景**　') + paragraph
            marked = document['pending_review'].get(name + '.' + item)
            if marked:
                if 'judgement_times' in document:
                    source_at = document['judgement_times'].get(name + '.' + item)
                    origin = ('原判断采纳于 ' + _display_time(source_at) + '。'
                              if source_at else '该项原判断的采纳时点待核。')
                    paragraph += '\n\n' + origin + _display_time(marked) + ' 标记为待复评。'
                else:
                    paragraph += '\n\n截至 ' + _display_time(marked) + '，此项待复评。'
            parts.append(paragraph)
        if name == 'risks':
            expired = expired_risks(document, document['assessed_at'])
            for update in document['risk_updates']:
                formed = _display_time(update['at'])
                until = update.get('valid_until')
                window = ('有效至 ' + _display_time(until)) if until else '原有效期未核实'
                if not until or update['event_ref'] in expired:
                    label = '此前风险提示已到复核时点。' if until else '此前风险提示的有效期待核。'
                    original = '\n'.join('> ' + line for line in update['text'].split('\n'))
                    parts.append('**' + label + '** ' + formed + ' 的原提示（' + window + '）\n\n' + original)
                else:
                    parts.append('**' + formed + ' 风险更新（' + window + '）。** ' + update['text'])
    return '\n\n'.join(parts)


def compose(base, changes, assessed_at, position_line=None, limited=False, *, members=None,
            pending_review=(), event_ref=None, position_reviewed=False, risk_valid_until=None,
            source_times=None, reviewed_unchanged=(), risk_event_at=None):
    """Return exact rendered text and the complete edition that produced it."""
    if not isinstance(assessed_at, str) or not assessed_at:
        raise ValueError('report assessment time is required')
    moment = utc(assessed_at)
    if base and moment < utc(base['assessed_at']):
        raise Conflict('the destination has a later assessment; review its edition before updating')
    if not isinstance(changes, dict) or changes.keys() - SECTIONS.keys():
        raise ValueError('report changes must address the defined investment sections')
    if limited:
        if (set(changes) != {'risks'} or not isinstance(changes['risks'], dict)
                or set(changes['risks']) != {'conditions'} or not isinstance(event_ref, str) or not event_ref):
            raise ValueError('limited feedback needs its verified event and the public risk update')
        if not isinstance(risk_valid_until, str) or utc(risk_valid_until) <= moment:
            raise ValueError('the public risk update needs its own future validity boundary')
        if not base or not base.get('seeded'):
            raise Conflict('a public risk report needs the complete edition of its adopted decisions')
        if risk_event_at is not None and utc(risk_event_at) > moment:
            raise ValueError('risk wording cannot be assessed after its reporting time')
        order = list(base['member_order'])
    else:
        if (not isinstance(members, (list, tuple)) or not all(isinstance(s, str) and s for s in members)
                or len(set(members)) != len(members) or 'cash' not in members):
            raise ValueError('specify the adopted allocation members in display order, including cash')
        order = list(members)
    if not isinstance(pending_review, (list, tuple)):
        raise ValueError('identify the specific report items affected by unresolved evidence')
    if not limited and pending_review:
        raise ValueError('new risk review markers belong to a verified public risk update')
    seeded = bool(base and base['seeded'])
    prior = base['modules'] if base else {}
    pending = dict(base['pending_review']) if base else {}
    prior_times = (base or {}).get('judgement_times', {})
    judgement_times = {}
    updates = list(base['risk_updates']) if base else []
    modules = {}
    paths = set()
    for name, (_, default_items) in SECTIONS.items():
        items = order if name == 'allocation' else default_items
        edits = changes.get(name, {})
        if not isinstance(edits, dict) or edits.keys() - set(items):
            raise ValueError('unknown report item in ' + name)
        section = {}
        for item in items:
            path = name + '.' + item
            paths.add(path)
            old = prior.get(name, {}).get(item)
            if name == 'allocation' and item == POSITION_SUBJECT and not limited:
                if item in edits:
                    raise ValueError('the original protective short is displayed from its adopted direction')
                section[item] = position_line or '原 BTCUSDT 保护性空单：当前方向待复评。'
                if position_line and position_reviewed:
                    pending.pop(path, None)
            elif item in edits and not limited:
                section[item] = _replace(old, edits[item])
                pending.pop(path, None)
            elif old is not None and (limited or seeded):
                section[item] = old
            else:
                raise Conflict('supply the current adopted assessment for report item ' + path)
            source_at = prior_times.get(path)
            if not limited and (item in edits or (path in reviewed_unchanged and path not in pending)
                                or (name == 'allocation' and item == POSITION_SUBJECT)):
                source_at = (source_times or {}).get(path)
            if source_at is not None:
                judgement_times[path] = source_at
        modules[name] = section
    if any(not isinstance(path, str) or path not in paths for path in pending_review):
        raise ValueError('pending review must identify an applicable report item')
    pending = {path: time for path, time in pending.items() if path in paths}
    pending.update({path: assessed_at for path in pending_review})
    if limited:
        previous = next((u for u in updates if u['event_ref'] == event_ref), None)
        risk = _replace(previous['text'] if previous else None, changes['risks']['conditions'])
        prior_until = previous.get('valid_until') if previous else None
        if prior_until and utc(risk_valid_until) > utc(prior_until):
            raise Conflict('a continuing public risk needs new evidence to extend its original window')
        if previous is None or previous['text'] != risk or not prior_until or utc(prior_until) != utc(risk_valid_until):
            updates = [u for u in updates if u['event_ref'] != event_ref]
            updates.append({'event_ref': event_ref, 'at': risk_event_at or assessed_at,
                            'valid_until': risk_valid_until, 'text': risk})
    elif 'conditions' in changes.get('risks', {}):
        # An explicit regular risk assessment reconciles outstanding public events.
        updates = []
    document = {'base_report_id': base['report_id'] if base else None, 'modules': modules,
                'assessed_at': assessed_at, 'reviewed_at': (base or {}).get('reviewed_at') if limited else assessed_at,
                'limited': limited, 'seeded': seeded or not limited, 'member_order': order,
                'pending_review': pending, 'risk_updates': updates, 'judgement_times': judgement_times}
    body = render(document)
    if '{{existing_position}}' in body or (position_line and body.count(position_line) != 1):
        raise ValueError('the adopted original position belongs only in its allocation item')
    return body, document
