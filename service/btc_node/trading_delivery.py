"""Record report obligations and native delivery under the node's writer transaction."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import json
import re
from zoneinfo import ZoneInfo

from .codec import Conflict, encode, fingerprint, identity, millis, stamp, utc
from .entities import Entities, key as entity_key
from . import trading_decisions, trading_policy, trading_report


REPORT = 'trading/report/'
OBLIGATION = 'trading/feedback/'
PERSONAL_FEEDBACK = 'trading-personal-feedback'
DAY = ZoneInfo('Asia/Tokyo')
HASH = re.compile(r'[a-f0-9]{64}')
URL_TOKEN = re.compile(r'\ue200url\ue202([^\ue200\ue201\ue202]+)\ue202(https?://[^\ue200\ue201\ue202]+)\ue201')
SECRET = re.compile(r'(?i)authorization\s*:|cookie\s*:|(?:api[_-]?key|password|access[_-]?token)\s*[:=]|钱包助记词')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' is required')
    return value


def _exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError(label + ' has invalid fields')


def validate_destination(value):
    fields = 'thread_id project_id' if isinstance(value, dict) and 'project_id' in value else 'thread_id'
    _exact(value, fields, 'configured report destination')
    for key, val in value.items():
        _text(val, key)
    return value


def _channel(destination):
    return identity('trading-report-destination', validate_destination(destination)['thread_id'])


def _identifier(value, prefix):
    value = _text(value, 'business record identity')
    if not value.startswith(prefix) or not HASH.fullmatch(value[len(prefix):]):
        raise ValueError('invalid report or feedback identity')
    return value


def _by_id(db, value, prefix):
    row = Entities(db).get('DELIVERY', _identifier(value, prefix))
    if row is None:
        raise Conflict('report or feedback record does not exist')
    return row


def _put(db, key, original, payload, reason, *, active=True, dependencies=()):
    moment = stamp()
    return Entities(db).put({
        'kind': 'DELIVERY', 'object_id': key, 'scope': 'PERSONAL',
        'product': _channel(payload['destination']) if 'destination' in payload else PERSONAL_FEEDBACK,
        'active': active,
        'known_at': moment, 'observed_at': moment, 'payload': payload,
        'change_reason': reason, 'source_ref': payload.get('trigger_ref', key),
        'method_version': db.settings.get('release_id', 'transactional-delivery'),
        'dependencies': list(dependencies),
    }, expected_version=original['version'] if original else 0)


def _body_dependency(value):
    evidence = value.get('feedback_assessment', {}).get('evidence', [])
    return [{'type': 'BLOB', 'digest': value['body_hash'], 'relation': 'FROZEN_REPORT'},
            *({**ref, 'relation': 'FEEDBACK_BASIS'} for ref in evidence)]


def _body(db, value):
    return db.blobs.get(value['body_hash']).decode('utf-8')


def _records(db, destination):
    channel = _channel(destination)
    rows = db.rows('SELECT object_id FROM entities WHERE kind=%s AND scope=%s AND product IN (%s,%s) AND active=1',
                   ('DELIVERY', 'PERSONAL', channel, PERSONAL_FEEDBACK))
    for row in rows:
        item = Entities(db).get('DELIVERY', row['object_id'])
        if item is not None:
            yield item['payload']


def _last_key(destination):
    return 'trading.reported.' + _channel(destination)


def _edition_key(destination):
    return 'trading.report.edition.' + _channel(destination)


def report_base(db, destination, *, identity_only=False, stored=False):
    """The last modular edition actually displayed at this destination."""
    displayed = db.metadata(_edition_key(destination))
    if displayed is None:
        return None
    if identity_only:
        return {'report_id': displayed['report_id']}
    record = _by_id(db, displayed['report_id'], REPORT)['payload']
    base = {**record['document'], **displayed, 'decisions': record['decisions']}
    if not stored and base.get('presentation', {}).get('shorten'):
        base['modules'] = trading_report.displayed_modules(base)
        base['_excerpted_items'] = list(base['presentation']['shorten'])
        base['presentation'] = {k: v for k, v in base['presentation'].items() if k != 'shorten'}
    if 'feedback_assessment' in record:
        base['feedback_assessment'] = record['feedback_assessment']
    return base


def _check_report_base(db, destination, expected):
    latest = report_base(db, destination, identity_only=True)
    if (latest and latest['report_id']) != expected:
        raise Conflict('a different complete edition was displayed; rebase the changes before dispatch')


def missing_monthly_comparison(db, base, portfolio, market):
    """Find a first substantive tranche comparison that never reached the last confirmed edition."""
    if not base or not portfolio or portfolio['payload']['market'] != market:
        return None
    objects = Entities(db)
    displayed = {r['object_id']: r for r in base.get('decisions', ())}
    affected = []
    for subject, ref in portfolio['payload']['decisions'].items():
        terms = base.get('allocation_terms', {}).get(subject, {})
        before = displayed.get(ref['object_id'])
        if terms.get('kind') != 'MONTHLY' or not before:
            continue
        current = objects.get(ref['kind'], ref['object_id'], version=ref['version'])
        if not current or current['digest'] != ref['digest']:
            continue
        choice = current['payload']['result']
        comparison = choice.get('comparison_at_price')
        if not comparison or terms.get('month') != choice.get('month'):
            continue
        prior = objects.get(before['kind'], before['object_id'], version=before['version'])
        if not prior or prior['digest'] != before['digest']:
            continue
        leads = [lead['quote'] for lead in trading_report.lead_excerpts(base.get('presentation') or {})
                 if lead['path'] == 'allocation.' + subject]
        shown = '\n'.join([*leads, base['modules']['allocation'].get(subject, '')])
        for decision in (prior['payload']['result'], choice):
            pivot = decision.get('comparison_at_price', {}).get('switch_when')
            close = decision.get('month_close_reason') if decision['decision'] == 'SKIP' else None
            if (pivot and pivot in shown and decision['opportunity_cost'] in shown
                    and (decision['decision'] != 'SKIP' or (close and close in shown))):
                break
        else:
            affected.append(subject)
    if affected:
        return {'report_id': base['report_id'], 'subjects': affected,
                'reason': '上份实收的月度段落缺少已核定的分批与留存取舍或本月安排，需要向原目的地交付更正。'}


def _later_display(displayed, previous):
    return not previous or (utc(displayed['completed_at']), displayed['report_id']) > (
        utc(previous['completed_at']), previous['report_id'])


def _day_key(destination, day):
    return 'trading.report.day.' + _channel(destination) + '.' + day


def _inheritance_key(destination):
    return 'trading.feedback.archive.' + _channel(destination)


def _archive_confirmation_key(report_id, attempt_id):
    return 'trading.archive.confirmation.' + identity(report_id, attempt_id)


def _message_text(value):
    return value[:-1] if value.endswith('\n') and not value.endswith(('\n\n', '\r\n')) else value


def position_action(record):
    result = record['payload']['result']
    return result.get('conditional_actions', {}).get('PRESENT', result)['decision']


def position_line(record, reason=None):
    payload = record['payload']
    choice = position_action(record)
    labels = {'REDUCE': '建议减持空单', 'KEEP': '建议不变（维持现有空单规模）', 'INCREASE': '建议增持空单',
              'EXIT': '建议平掉剩余空单', 'NO_POSITION': '原单已平', 'UNRESOLVED': '调整方向待定'}
    if choice not in labels:
        raise Conflict('reassess the original short before displaying its direction')
    policy = payload.get('policy', {})
    conditional = ('PRESENT' in payload['result'].get('conditional_actions', {}) or
                   (policy.get('position_status') == 'UNKNOWN' and policy.get('entry_reference') is not None))
    premise = '（若原单仍在）' if conditional else ''
    line = '原 BTCUSDT 保护性空单' + premise + '：' + labels[choice] + '。'
    return line if reason is None else line + ' ' + _text(reason, 'reviewed position explanation').strip()


def explained_position(record, explanation):
    """Render the current reason together with the comparison adopted for this edition."""
    if not explanation:
        return position_line(record)
    text = ' '.join(explanation[key] for key in ('reason', 'comparison') if key in explanation)
    return position_line(record, text)


def _request_body(body):
    return ('VERBATIM：请将下方正文逐字作为唯一回复，保留所有 Markdown、换行、空格和顺序。'
            '不要整理、润色、压缩、补充解释、加前后缀或代码围栏；不要调用工具或重新分析。'
            '正文从下一空行开始直至消息结束。\n\n' + _message_text(body))


def _restore_links(text):
    return URL_TOKEN.sub(lambda match: '[' + match[1] + '](' + match[2] + ')', text)


def _display_match(body, displayed):
    if body == displayed:
        return 'EXACT'
    if _message_text(body) == _message_text(displayed):
        return 'TERMINAL_LF_ONLY'
    restored = _restore_links(displayed)
    if restored != displayed and _message_text(restored) == _message_text(body):
        return 'PLATFORM_URL_MARKUP'
    return 'MISMATCH'


def _archive(db, path):
    item = db.one('SELECT digest FROM documents WHERE document_key=%s AND kind=%s',
                  (identity('strategy', path), 'FILE'))
    return db.value(item['digest']) if item else None


def _archived_receipt(db, original_id, name):
    if not isinstance(name, str) or PurePosixPath(name).name != name or name.startswith('.'):
        return None
    return _archive(db, 'v4/outbox/' + original_id + '/' + name)


def _historical_feedback(db, destination, at):
    """Look up the original projected receipt while the new baseline is empty."""
    pointer = _archive(db, 'v4/last-display-current.json') or {}
    original_id = pointer.get('delivery_id')
    if not isinstance(original_id, str) or not HASH.fullmatch(original_id):
        return None
    record = Entities(db).get('DELIVERY', original_id)
    if record is None:
        return None
    original = record['payload']
    status = original.get('status') or {}
    if status.get('state') != 'DELIVERED':
        return None
    artifact = db.value(original['artifact_digest'])
    if artifact.get('destination', {}).get('thread_id') != destination['thread_id']:
        return None
    receipt = _archived_receipt(db, original_id, status.get('receipt'))
    if not receipt or receipt.get('destination') != artifact['destination']:
        return None
    assistant = receipt.get('assistant') or {}
    attempt = status.get('attempt') or {}
    completed_at = assistant.get('completed_at')
    if (not completed_at or assistant.get('role') != 'assistant' or assistant.get('completed') is not True
            or receipt.get('attempt_id') != attempt.get('attempt_id')
            or not attempt.get('request_ref') or receipt.get('request_ref') != attempt['request_ref']
            or assistant.get('parent_request_ref') != attempt['request_ref']
            or assistant.get('record_ref') not in (receipt.get('record_refs') or [])
            or not isinstance(assistant.get('body'), str) or utc(completed_at) > at):
        return None
    return {'report_id': original_id, 'completed_at': completed_at, 'receipt_ref': assistant['record_ref'],
            'body': assistant['body'], 'source': 'ARCHIVED_NATIVE_READBACK',
            'credited_day': _archive_credited_day(artifact, completed_at)}


def _archive_credited_day(artifact, completed_at):
    review = artifact.get('source', {}).get('report_review', {})
    maintenance = review.get('maintenance') or {}
    results = review.get('result_binding') or {}
    quality = (review.get('layout') or {}).get('quality_review') or {}
    complete_report = (review.get('action') == 'REPORT' and results.get('market_result_hash')
                       and results.get('investment_decision_hash') and quality.get('upstream_completeness')
                       and quality.get('decision_delivery'))
    day = utc(completed_at).astimezone(DAY).date().isoformat()
    daily_valid = (complete_report and maintenance.get('kind') not in {None, 'EXPRESSION_ONLY'} and review.get('created_at')
                   and utc(review['created_at']).astimezone(DAY).date().isoformat() == day
                   and maintenance.get('recheck_at') and millis(completed_at) < millis(maintenance['recheck_at']))
    return day if daily_valid else None


def _archived_attempts(db, destination):
    rows = db.rows('SELECT object_id FROM entities WHERE kind=%s AND scope=%s AND product IS NULL AND active=1',
                   ('DELIVERY', 'PERSONAL'))
    result = []
    for row in rows:
        original_id = row['object_id']
        if not HASH.fullmatch(original_id):
            continue
        record = Entities(db).get('DELIVERY', original_id)
        original = record['payload']
        status = original.get('status') or {}
        if status.get('state') not in {'UNKNOWN', 'SENDING', 'CORRECTION_REQUIRED'}:
            continue
        artifact = db.value(original['artifact_digest'])
        if artifact.get('destination', {}).get('thread_id') != destination['thread_id']:
            continue
        attempt = status.get('attempt') or {}
        if db.metadata(_archive_confirmation_key(original_id, attempt.get('attempt_id'))):
            continue
        result.append({'report_id': original_id, 'destination': artifact['destination'], 'status': status['state'],
                       'attempt': status.get('attempt'), 'recovery': original.get('recovery'),
                       'original_binding': _archived_receipt(db, original_id, status.get('request_binding')),
                       'projected_by_release': record['method_version']})
    return result


def _closed_reports(db, destination):
    query = ('SELECT e.object_id,e.last_checked_ms,v.digest FROM entities e JOIN entity_versions v '
             'ON e.entity_key=v.entity_key AND e.current_version=v.version '
             'WHERE e.kind=%s AND e.scope=%s AND e.product=%s AND e.active=0 AND e.object_id LIKE %s ')
    params = ('DELIVERY', 'PERSONAL', _channel(destination), REPORT + '%')
    cursor = ()
    while True:
        continuation = 'AND (e.last_checked_ms,e.object_id)<(%s,%s) ' if cursor else ''
        rows = db.rows(query + continuation + 'ORDER BY e.last_checked_ms DESC,e.object_id DESC LIMIT 64',
                       params + cursor)
        yield from rows
        if len(rows) < 64:
            return
        cursor = (rows[-1]['last_checked_ms'], rows[-1]['object_id'])


def _report_position_line(report):
    document = report.get('document') or {}
    if 'modules' in document:
        return trading_report.displayed_modules(document).get('allocation', {}).get(trading_policy.POSITION_SUBJECT)
    return report.get('position_line')


def _displayed_member(db, report, decision_id):
    ref = next((r for r in report['decisions'] if r['object_id'] == decision_id), None)
    if decision_id != trading_decisions.PERSONAL_PREFIX + trading_policy.POSITION_SUBJECT:
        return {'decision': ref} if ref else None

    line = _report_position_line(report)
    if not line and ref is None:
        return None
    body = _restore_links(report['receipt']['assistant']['body']).strip('\n')
    if not line or f'\n\n{line}\n\n' not in f'\n\n{body}\n\n':
        return {'decision': None}
    document = report.get('document') or {}
    explanation = document.get('position_explanation') or {}
    source = explanation.get('decision') or {}
    # A limited risk report may show an inherited position without adopting personal decisions.
    if (ref is None and document.get('limited') and source.get('kind') == 'PLAN'
            and source.get('object_id') == decision_id and type(source.get('version')) is int
            and explanation.get('reason')):
        origin = Entities(db).get('PLAN', decision_id, version=source['version'])
        if origin and trading_decisions.binding(origin) == source and explained_position(origin, explanation) == line:
            ref = source
    shown = {'decision': ref, 'position_line': line}
    if ref and source == ref and explanation.get('reason'):
        shown['position_explanation'] = explanation
    if 'allocation.' + trading_policy.POSITION_SUBJECT in document.get('pending_review', {}):
        shown['position_pending_review'] = True
    return shown


def previous_display(db, *, destination, decision_id, at, before_report_id=None):
    """Find a confirmed display of this decision, using display time rather than acknowledgement order."""
    _text(decision_id, 'decision object identity')
    before = None
    if before_report_id is not None:
        anchor = _by_id(db, before_report_id, REPORT)['payload']
        if (anchor['status'] != 'DELIVERED' or _channel(anchor['destination']) != _channel(destination)
                or not (_displayed_member(db, anchor, decision_id) or {}).get('decision')):
            raise Conflict('the comparison cursor must be a delivered report citing this decision at this destination')
        before = (utc(anchor['receipt']['assistant']['completed_at']), before_report_id)

    cutoff = utc(at)
    best = None
    found = None
    for row in _closed_reports(db, destination):
        # A receipt cannot confirm a display later than the recording time.
        if best and row['last_checked_ms'] < millis(best[0]):
            break
        report = db.value(row['digest'])['payload']
        if report['status'] != 'DELIVERED':
            continue
        assistant = report['receipt']['assistant']
        order = (utc(assistant['completed_at']), report['report_id'])
        if order[0] > cutoff or (before and order >= before) or (best and order <= best):
            continue
        member = _displayed_member(db, report, decision_id)
        if member is None:
            continue
        best = order
        # A more recent visible position with no verifiable binding breaks the comparison.
        found = ({'report_id': report['report_id'], 'completed_at': assistant['completed_at'],
                  'receipt_ref': assistant['record_ref'], 'body': assistant['body'], 'source': 'NATIVE_READBACK',
                  **member} if member['decision'] else None)
    return found


def position_already_displayed(db, destination, record, at):
    decision_id = record['object_id']
    ref = trading_decisions.binding(record)
    previous = previous_display(db, destination=destination, decision_id=decision_id, at=at)
    if not previous or previous['decision'] != ref or previous.get('position_pending_review'):
        return False
    explanation = previous.get('position_explanation') or {}
    confirmed_closed = (position_action(record) == 'NO_POSITION'
                        and record['payload'].get('policy', {}).get('position_status') == 'ABSENT')
    if not explanation.get('reason') and not confirmed_closed:
        return False
    line = explained_position(record, explanation)
    if previous['position_line'] != line:
        return False
    # An unresolved send may show an older decision or its older explanation, including in a risk edition.
    for report in _records(db, destination):
        if report.get('status') not in {'SENDING', 'UNKNOWN'}:
            continue
        visible = _report_position_line(report)
        binding = next((r for r in report.get('decisions', []) if r['object_id'] == decision_id), None)
        if visible is None and binding is None:
            continue
        document = report.get('document') or {}
        inherited = binding is None and document.get('limited') and explanation.get('decision') == ref
        if (visible != line or (document.get('position_explanation') or {}) != explanation
                or (binding != ref and not inherited)
                or 'allocation.' + trading_policy.POSITION_SUBJECT in document.get('pending_review', {})):
            return False
    return True


def context(db, *, destination, at=None):
    at = utc(at)
    day = at.astimezone(DAY).date().isoformat()
    last = db.metadata(_last_key(destination))
    credited = db.metadata(_day_key(destination, day))
    archive = db.metadata(_inheritance_key(destination))
    historical = _historical_feedback(db, destination, at) if not last or not credited else None
    if last is None:
        last = historical
    elif millis(last['completed_at']) <= millis(at):
        if last.get('archived_confirmation'):
            report = db.metadata(last['archived_confirmation'])
            last = {**last, 'body': report['receipt']['assistant']['body'], 'source': 'ARCHIVED_NATIVE_READBACK'}
        else:
            report = _by_id(db, last['report_id'], REPORT)['payload']
            last = {**last, 'body': report['receipt']['assistant']['body'], 'source': 'NATIVE_READBACK'}
    else:
        last = None
    obligations, pending, reconcile = [], [], []
    for record in _records(db, destination):
        if 'obligation_id' in record:
            obligations.append(record)
        elif record.get('status') in {'PENDING', 'NOT_SENT_CONFIRMED'}:
            pending.append({**record, 'body': _body(db, record)})
        else:
            reconcile.append(record)
    delivered_today = bool((credited and millis(credited['completed_at']) <= millis(at)) or
                           (historical and historical.get('credited_day') == day))
    return {'day': day, 'daily_due': not delivered_today, 'last_delivered': last,
            'obligations': obligations, 'pending': pending, 'reconcile': reconcile,
            'due_obligation_ids': [item['obligation_id'] for item in obligations if item['status'] == 'OPEN'
                                   and (item['due_at'] is None or utc(item['due_at']) <= at)],
            'archived_attempts': _archived_attempts(db, destination),
            'legacy_feedback_review': {'captured_at': archive['captured_at'], 'candidates': len(archive['candidates'])}
            if archive is not None else None}


def inherit_open(db, *, destination, legacy_root, retain=()):
    """Capture original unsatisfied reports once, and carry only reviewed meanings."""
    destination = validate_destination(destination)
    legacy_root = Path(legacy_root)
    key = _inheritance_key(destination)
    archived = db.metadata(key)
    if archived is None:
        from v4 import reporting

        candidates = []
        for item in reporting.obligations(legacy_root):
            reread = reporting.review(legacy_root, item['review_id'])
            deliveries = []
            for event in item['deliveries']:
                original = legacy_root / 'outbox' / event['delivery_id'] / 'artifact.json'
                artifact = json.loads(original.read_bytes())
                if (_channel(artifact['destination']) == _channel(destination)
                        and artifact.get('source', {}).get('report_review', {}).get('review_id') == item['review_id']):
                    deliveries.append(event)
            if item['deliveries'] and not deliveries:
                continue
            daily = (reread.get('maintenance') or {}).get('daily') or {}
            candidates.append({**item, 'deliveries': deliveries, 'original_due_at': daily.get('report_by'),
                               'original_source': 'v4/report-reviews/' + item['review_id'].removeprefix('sha256:') + '.json'})
        archived = {'captured_at': stamp(), 'candidates': candidates}
        db.metadata(key, archived, write=True)
    selected = {item['review_id']: item for item in archived['candidates']}
    inherited = []
    for review in retain:
        _exact(review, 'review_id reason', 'current relevance of archived feedback')
        original = selected.get(review['review_id'])
        if original is None:
            raise Conflict('the requested original review has no captured outstanding obligation')
        reason = _text(review['reason'], 'actual continuing impact of the archived feedback')
        value = _require_obligation(db, kind='ARCHIVED', subject='retained feedback',
                                    trigger_ref=original['review_id'], reason=reason,
                                    due_at=original['original_due_at'], legacy=original,
                                    legacy_destination=destination)
        if value['status'] == 'OPEN':
            inherited.append(value['obligation_id'])
    return {**archived, 'inherited': inherited}


def require_report(db, *, subject, trigger_ref, reason, kind='UPDATE', due_at=None, legacy_destination=None,
                   decision=None, scope=None, change=None):
    _text(subject, 'feedback subject')
    _text(trigger_ref, 'original feedback trigger')
    _text(reason, 'why feedback is needed')
    if kind not in {'UPDATE', 'DAILY', 'RISK', 'CORRECTION'}:
        raise ValueError('invalid feedback purpose')
    due_at = stamp(due_at) if due_at is not None else None
    return _require_obligation(db, kind=kind, subject=subject, trigger_ref=trigger_ref,
                               reason=reason, due_at=due_at, legacy_destination=legacy_destination, decision=decision,
                               scope=scope, change=change)


def _obligation_id(value):
    change = value.get('change')
    if change:
        if change['impact'] == 'PLAN_READY':
            return OBLIGATION + identity(_channel(value['destination']), value['kind'], value['subject'],
                                         change['impact'], value['trigger_ref'])
        # Another market version or portfolio binding can adopt exactly the same unsent change.
        parts = [_channel(value['destination']), value['kind'], value['subject'], change['base_report_id'],
                 change['path'], change['impact'], change['before'], change['after']]
        return OBLIGATION + identity(*parts)
    parts = [value['kind'], value['subject'], value['trigger_ref']]
    if 'scope' in value:
        parts.extend((value['scope'], value['decision']['version']))
    return OBLIGATION + identity(*parts)


def pending_public_risks(db, destination):
    return [{'obligation_id': item['obligation_id'], **item['public_risk']} for item in _records(db, destination)
            if item.get('public_risk') and item.get('destination') == destination
            and item['status'] == 'OPEN' and not item['report_id']]


def risk_note(db, destination, event_ref):
    key = _obligation_id({'kind': 'RISK', 'subject': 'material-feedback', 'trigger_ref': identity([], event_ref)})
    record = Entities(db).get('DELIVERY', key)
    if record and record['payload'].get('public_risk'):
        if record['payload'].get('destination') != destination:
            raise Conflict('recover the original risk feedback at its bound destination')
        return record['payload']['public_risk']


def defer_public_risk(db, destination, support, params, missing):
    event = support['event_ref']
    note = {'event_ref': event, 'valid_until': support['valid_until'], 'assessed_at': params['assessed_at'],
            'text': params['changes']['risks']['conditions'], 'pending_review': params.get('pending_review', [])}
    if not isinstance(note['text'], str) or not note['text'].strip() or SECRET.search(note['text']):
        raise ValueError('provide the verified public risk text to retain it until a complete edition is available')
    if not isinstance(note['pending_review'], list) or not all(isinstance(p, str) and p for p in note['pending_review']):
        raise ValueError('pending risk review items must be report paths')
    value = require_report(db, subject='material-feedback', trigger_ref=identity([], event), reason=params['reason'],
                           kind='RISK', legacy_destination=destination)
    original = risk_note(db, destination, event)
    if original and (original['valid_until'] != note['valid_until'] or original['text'] != note['text']):
        raise Conflict('the original public risk wording and its validity are already retained')
    if not original and value['status'] == 'OPEN' and not value['report_id']:
        row = _by_id(db, value['obligation_id'], OBLIGATION)
        _put(db, value['obligation_id'], row, {**value, 'destination': destination, 'public_risk': note},
             'Keep the verified public event until its complete source edition is ready')
    return {'status': 'AWAITING_REPORT_BASIS' if not value['report_id'] else value['status'],
            'obligation_id': value['obligation_id'], 'report_id': value['report_id'], 'missing': missing}


def _existing_obligation(db, value, legacy_destination):
    objects = Entities(db)
    prior = objects.get('DELIVERY', _obligation_id(value))
    if prior is None and 'change' in value:
        original = {k: v for k, v in value.items() if k not in {'change', 'destination'}}
        prior = objects.get('DELIVERY', _obligation_id(original))
        if prior and prior['payload'].get('destination') not in (None, legacy_destination):
            prior = None
    if prior is not None or legacy_destination is None or 'scope' in value:
        return prior
    channel = _channel(legacy_destination)
    kind, subject, trigger_ref = (value[field] for field in ('kind', 'subject', 'trigger_ref'))
    legacy_identity = (channel, kind, trigger_ref) if kind == 'ARCHIVED' else (channel, kind, subject, trigger_ref)
    return objects.get('DELIVERY', OBLIGATION + identity(*legacy_identity))


def _require_obligation(db, *, kind, subject, trigger_ref, reason, due_at, legacy=None, legacy_destination=None,
                        decision=None, scope=None, change=None):
    payload = {'subject': subject, 'trigger_ref': trigger_ref, 'reason': reason, 'kind': kind, 'due_at': due_at,
               'created_at': stamp(), 'report_id': None, 'status': 'OPEN'}
    if decision is not None:
        payload['decision'] = decision
    if scope is not None:
        payload['scope'] = scope
    if change is not None:
        payload.update(change=change, destination=validate_destination(legacy_destination))
    prior = _existing_obligation(db, payload, legacy_destination)
    if prior:
        return prior['payload']
    identifier = _obligation_id(payload)
    payload['obligation_id'] = identifier
    if legacy is not None:
        payload['legacy'] = legacy
    _put(db, identifier, None, payload, 'Record required feedback')
    return payload


def get(db, *, report_id):
    value = _by_id(db, report_id, REPORT)['payload']
    return {**value, 'body': _body(db, value)}


def check_displayed_correction(db, destination, original, event):
    report = _by_id(db, event, REPORT)['payload']
    if (report['status'] != 'DELIVERED' or _channel(report['destination']) != _channel(destination)
            or _confirmed_display(db, report, report['receipt']) == 'MISMATCH'):
        raise Conflict('the correction must refer to a confirmed display at this destination')
    if original is not None:
        _exact(original, 'report_id reason', 'report replacement')
        if original['report_id'] != event:
            draft = _by_id(db, original['report_id'], REPORT)['payload']
            if draft.get('document', {}).get('corrects') != event:
                raise Conflict('replace the outstanding draft of this correction')


def _check_decision_coverage(db, obligation, decisions, body, coverage, position_line=None, document=None):
    # Event and daily notifications also have trigger references. Only an actual
    # decision version under the original subject establishes a decision binding.
    event = obligation.get('public_risk')
    if event:
        expired = millis(event['valid_until']) <= millis()
        if not expired and coverage is None and event['text'] in body:
            return
        _exact(coverage, 'excerpt', 'review of the retained public risk')
        excerpt = _text(coverage['excerpt'], 'current impact of the original public risk')
        if excerpt not in body or (expired and excerpt == event['text']):
            raise Conflict('explain the reviewed effect of the retained public risk in this edition')
        return
    subject = obligation['subject']
    origin = obligation.get('decision')
    if origin is None and HASH.fullmatch(obligation['trigger_ref']) and len(subject) <= 240:
        possible = (entity_key('DECISION', trading_decisions.MARKET_PREFIX + subject),
                    entity_key('PLAN', trading_decisions.PERSONAL_PREFIX + subject))
        origin = db.one('SELECT e.kind,e.object_id,v.version,v.digest FROM entities e JOIN entity_versions v '
                        'ON e.entity_key=v.entity_key WHERE v.entity_key IN (%s,%s) AND v.digest=%s '
                        'AND v.known_ms<=%s ORDER BY v.version DESC LIMIT 1',
                        (*possible, obligation['trigger_ref'], millis(obligation['created_at'])))
    ref = next((r for r in decisions if origin and r['kind'] == origin['kind']
                and r['object_id'] == origin['object_id']), None)
    change = obligation.get('change')
    if change and change['impact'] == 'PLAN_READY' and subject in document.get('member_order', []):
        updates = document.get('execution_updates', [])
        if not any(update['obligation_id'] == obligation['obligation_id'] and update['decision'] == origin
                   and update['subject'] == subject and update['judgement'] == change['after'] for update in updates):
            raise Conflict('show the original user-accepted plan and its verified execution condition')
        return
    if change and ref and not (coverage or {}).get('policy'):
        if ref['version'] < origin['version'] or (ref['version'] == origin['version'] and ref != origin):
            raise Conflict('cite the original material decision or its reviewed successor')
        section, item = change['path'].split('.', 1)
        visible = [trading_report.displayed_modules(document)[section].get(item, '')]
        visible.extend(lead['quote'] for lead in trading_report.lead_excerpts(document.get('presentation', {}))
                       if lead['path'] == change['path'])
        excerpt = change['after']
        if coverage is not None:
            _exact(coverage, 'excerpt', 'displayed investment change')
            excerpt = _text(coverage['excerpt'], 'displayed investment change')
        if change['after'] not in excerpt or not any(excerpt in text for text in visible):
            raise Conflict('show the material judgement or its reviewed excerpt in the affected report item')
        return
    if subject == trading_policy.POSITION_SUBJECT and origin and position_line and ref:
        if ref['version'] < origin['version'] or (ref['version'] == origin['version'] and ref != origin):
            raise Conflict('cite the adopted original position decision or its actual successor')
        if coverage is not None:
            _exact(coverage, 'excerpt', 'position direction coverage')
            if coverage['excerpt'] != position_line:
                raise Conflict('the original position obligation is displayed only through its adopted direction')
        elif ref != origin:
            raise Conflict('identify the successor direction displayed in the original position allocation item')
        return
    if coverage is None:
        if origin and ref != origin:
            raise Conflict('cite the original decision or explain its successor in the report: ' + subject)
        return
    if not origin:
        raise Conflict('decision coverage needs an identifiable original decision')
    if 'policy' not in coverage:
        _exact(coverage, 'excerpt', 'successor decision coverage')
        excerpt = _text(coverage['excerpt'], 'displayed explanation of the decision')
        shown = body
        if document and document.get('modules'):
            modules = trading_report.displayed_modules(document)
            if origin['kind'] == 'PLAN':
                shown = modules['allocation'].get(subject, '')
            else:
                shown = '\n\n'.join(text for section, items in modules.items() if section != 'allocation'
                                      for text in items.values())
        if not ref or ref['version'] < origin['version'] or (ref['version'] == origin['version'] and ref != origin) or excerpt not in shown:
            raise Conflict('the cited decision and its owed explanation must appear in the appropriate report item')
        return
    _exact(coverage, 'policy excerpt', 'retired decision coverage')
    excerpt = _text(coverage['excerpt'], 'displayed explanation of the retired decision')
    owner = trading_policy.confirmed_scope_end(db, subject) if origin['kind'] == 'PLAN' else None
    if not owner or trading_decisions.binding(owner) != coverage['policy'] or excerpt not in body:
        raise Conflict('retired decision coverage needs the confirmed scope ending and its displayed explanation')


def prepare(db, *, destination, body, document, decisions, obligation_ids, reason, run_ref, complete,
            supersedes=None, support=None, legacy_coverage=None, decision_coverage=None, position_line=None,
            feedback_assessment=None):
    destination = validate_destination(destination)
    for value, name in ((body, 'frozen report body'), (reason, 'report reason'), (run_ref, 'original run')):
        _text(value, name)
    if type(complete) is not bool:
        raise ValueError('report coverage must state whether it is complete')
    if SECRET.search(body):
        raise ValueError('report contains private credential material')
    if body != trading_report.render(document):
        raise Conflict('the frozen body must be the rendered complete report edition')
    body_bytes = body.encode('utf-8')
    body_hash = fingerprint(body_bytes)
    if support is not None:
        _exact(support, 'event_ref valid_until', 'limited public report evidence')
        if (complete or decisions
                or not HASH.fullmatch(_text(support['event_ref'], 'verified material event reference').removeprefix('sha256:'))
                or millis(support['valid_until']) <= millis()):
            raise Conflict('the verified public evidence needs a live report window')
    if (not isinstance(decisions, list) or (not decisions and (complete or support is None))
            or len({encode(v) for v in decisions}) != len(decisions)):
        raise ValueError('report requires distinct approved decision references')
    if not isinstance(obligation_ids, list) or not obligation_ids or len(set(obligation_ids)) != len(obligation_ids):
        raise ValueError('report requires distinct open feedback obligations')
    decisions = sorted(decisions, key=encode)
    if supersedes is not None:
        _exact(supersedes, 'report_id reason', 'report replacement')
        _text(supersedes['reason'], 'material reason for replacement')
    if legacy_coverage is None:
        legacy_coverage = {}
    if not isinstance(legacy_coverage, dict) or not set(legacy_coverage) <= set(obligation_ids):
        raise ValueError('coverage must name the actual original outstanding feedback')
    if decision_coverage is None:
        decision_coverage = {}
    if (not isinstance(decision_coverage, dict) or not decision_coverage.keys() <= set(obligation_ids)
            or any(not isinstance(value, dict) for value in decision_coverage.values())):
        raise ValueError('decision coverage requires an original obligation and its exact displayed explanation')
    identity_parts = [destination, body_hash, document, sorted(obligation_ids), decisions, complete, support,
                      supersedes['report_id'] if supersedes else None]
    if decision_coverage:
        identity_parts.append(decision_coverage)
    if position_line:
        identity_parts.append(position_line)
    key = REPORT + identity(*identity_parts)
    prior = Entities(db).get('DELIVERY', key)
    if prior:
        return {**prior['payload'], 'body': body}
    _check_report_base(db, destination, document['base_report_id'])
    obligations = [_by_id(db, item, OBLIGATION) for item in obligation_ids]
    old = _by_id(db, supersedes['report_id'], REPORT) if supersedes else None
    for row in obligations:
        entry = row['payload']
        _check_decision_coverage(db, entry, decisions, body, decision_coverage.get(entry['obligation_id']),
                                 position_line, document)
        bound = entry.get('destination')
        if not row['active'] or (bound is not None and _channel(bound) != _channel(destination)):
            raise Conflict('only open obligations for the original approved destination may be covered')
        if entry['kind'] == 'DAILY' and not complete:
            raise Conflict('a limited update cannot satisfy the complete daily report')
        if entry['kind'] == 'ARCHIVED':
            _text(legacy_coverage.get(entry['obligation_id']), 'how this new report covers the original meaning')
        elif entry['obligation_id'] in legacy_coverage:
            raise ValueError('legacy coverage must address a retained original review')
        if entry['report_id'] is not None and (old is None or entry['report_id'] != old['object_id']):
            raise Conflict('an active report already owns this feedback; review its replacement explicitly')
    if old:
        prior_report = old['payload']
        if (_channel(prior_report['destination']) != _channel(destination)
                or (prior_report.get('superseded_by') and prior_report['status'] != 'DELIVERED')):
            raise Conflict('replacement requires the active original report and destination')
        if prior_report['status'] == 'BLOCKED':
            raise Conflict('the rejected original delivery needs recovery in its original authorization scope')
        corrected_display = (prior_report['status'] == 'DELIVERED' and document['purpose'] == 'CORRECTION'
                             and document.get('corrects') == prior_report['report_id'])
        if prior_report['status'] == 'DELIVERED' and not corrected_display:
            raise Conflict('use a correction to address a report already displayed')
        if not corrected_display and not set(prior_report['obligation_ids']) <= set(obligation_ids):
            raise Conflict('replacement must explicitly cover the prior feedback obligations')
        old_document = prior_report.get('document')
        needs_recomposition = (prior_report['status'] in {'PENDING', 'NOT_SENT_CONFIRMED'}
                               and (old_document is None or not old_document.get('seeded')
                                    or not old_document.get('public_market')
                                    or old_document.get('public_copy_id') != document.get('public_copy_id')
                                    or old_document.get('risk_public_copy') != document.get('risk_public_copy')
                                    or old_document.get('legacy_public_review') != document.get('legacy_public_review')
                                    or old_document.get('seed_sources') != document.get('seed_sources')
                                    or old_document['base_report_id'] != document['base_report_id']
                                    or _risk_window_changed(old_document, document['assessed_at'])))
        if (not needs_recomposition and prior_report['decisions'] == decisions
                and set(prior_report['obligation_ids']) == set(obligation_ids)
                and prior_report['status'] != 'CORRECTION_REQUIRED'):
            raise Conflict('replacement requires changed related decisions or a documented communication correction')
    if db.put_blob(body_bytes) != body_hash:
        raise Conflict('stored report bytes differ from the submitted body')
    payload = {'report_id': key, 'destination': destination, 'body_hash': body_hash,
               'document': document,
               'decisions': decisions, 'obligation_ids': obligation_ids, 'reason': reason,
               'run_ref': run_ref, 'complete': complete, 'status': 'PENDING', 'created_at': stamp(), 'attempt': None,
               'attempt_count': 0, 'supersedes': supersedes}
    if feedback_assessment is not None:
        payload['feedback_assessment'] = {**feedback_assessment, 'report_id': key}
    if support is not None:
        payload['support'] = support
    if legacy_coverage:
        payload['legacy_coverage'] = legacy_coverage
    payload['decision_coverage'] = decision_coverage
    if position_line:
        payload['position_line'] = position_line
    _put(db, key, None, payload, 'Freeze reviewed report', dependencies=_body_dependency(payload))
    if old and old['payload']['status'] != 'DELIVERED':
        # Preserve the old request for readback if it may already have reached the target.
        prior_report = {**old['payload'], 'superseded_by': key}
        if prior_report['status'] in {'PENDING', 'NOT_SENT_CONFIRMED'}:
            prior_report['status'] = 'SUPERSEDED'
        _put(db, old['object_id'], old, prior_report, supersedes['reason'],
             active=prior_report['status'] in {'SENDING', 'UNKNOWN'}, dependencies=_body_dependency(prior_report))
    for row in obligations:
        _put(db, row['object_id'], row, {**row['payload'], 'report_id': key}, 'Attach reviewed report')
    return {**payload, 'body': body}


def _risk_window_changed(document, at):
    return (trading_report.expired_risks(document, document['assessed_at'])
            != trading_report.expired_risks(document, at))


def _check_inflight_editions(db, destination, report):
    # Once either external request has been dispatched their display order is
    # outside the writer transaction. Different full editions can undo one
    # another even if their authors changed disjoint items of the same base.
    for other in _records(db, destination):
        if (other.get('status') not in {'SENDING', 'UNKNOWN'} or 'document' not in other
                or other['report_id'] == report['report_id']):
            continue
        if report['document'] != other['document'] or report['decisions'] != other['decisions']:
            raise Conflict('resolve the outstanding display before sending a different report edition: '
                           + other['report_id'])


def begin(db, *, destination, report_id, anchor_ref, run_ref):
    row = _by_id(db, report_id, REPORT)
    value = row['payload']
    if _channel(value['destination']) != _channel(destination):
        raise Conflict('the configured target differs from the report’s original destination')
    if value['status'] not in {'PENDING', 'NOT_SENT_CONFIRMED'} or value.get('superseded_by'):
        return {'report_id': report_id, 'ready_to_send': False, 'status': value['status'],
                'attempt_id': (value.get('attempt') or {}).get('attempt_id')}
    if not value.get('document', {}).get('public_market'):
        raise Conflict('recompose this unsent draft with independently frozen public sections and its original obligations')
    _check_report_base(db, destination, value['document']['base_report_id'])
    if _risk_window_changed(value['document'], None):
        raise Conflict('an earlier risk note reached its review boundary; recompose before sending')
    _check_inflight_editions(db, destination, value)
    if value.get('support') and millis(value['support']['valid_until']) <= millis():
        raise Conflict('review the now-expired public report before sending')
    for identifier in value['obligation_ids']:
        obligation = _by_id(db, identifier, OBLIGATION)
        if not obligation['active'] or obligation['payload']['report_id'] != report_id:
            raise Conflict('this report no longer owns its outstanding feedback')
        if 'decision_coverage' in value:
            coverage = value['decision_coverage'].get(identifier)
            _check_decision_coverage(db, obligation['payload'], value['decisions'],
                                     _body(db, value) if coverage or obligation['payload'].get('public_risk') else '',
                                     coverage, value.get('position_line'), value.get('document'))
    _text(anchor_ref, 'actual target readback cursor')
    _text(run_ref, 'actual sending run')
    number = value['attempt_count'] + 1
    if number > 1:
        recovery_key = 'trading.send.recovery.' + identity(_channel(destination), run_ref)
        if db.metadata(recovery_key):
            raise Conflict('this run already began one recovery send for the original destination')
        db.metadata(recovery_key, report_id, write=True)
    # Earlier attempts and their readbacks remain in entity history. This
    # attempt's state only contains evidence actually observed for this attempt.
    value = {key: item for key, item in value.items() if key not in {'transport', 'unconfirmed', 'receipt', 'text_match'}}
    attempt_id = 'sha256:' + identity(report_id, number)
    attempt = {'attempt_id': attempt_id, 'destination': value['destination'], 'anchor_ref': anchor_ref,
               'run_ref': run_ref, 'started_at': stamp()}
    value = {**value, 'attempt': attempt, 'attempt_count': number, 'status': 'SENDING'}
    _put(db, report_id, row, value, 'Reserve one native send attempt', dependencies=_body_dependency(value))
    return {'report_id': report_id, 'attempt_id': attempt_id, 'ready_to_send': True, 'status': 'SENDING',
            'destination': value['destination'], 'request_body': _request_body(_body(db, value)),
            'started_at': attempt['started_at']}


def _readback_references(value):
    if not isinstance(value, list) or not value or any(not isinstance(ref, str) or not ref.strip() for ref in value):
        raise ValueError('actual native record references are required')
    return value


def _attempt_receipt(receipt, value, *, checked):
    attempt = value['attempt']
    if attempt is None or receipt.get('attempt_id') != attempt['attempt_id']:
        raise Conflict('readback belongs to another send attempt')
    if 'destination' in receipt and receipt['destination'] != value['destination']:
        raise Conflict('readback belongs to another destination')
    if not millis(attempt['started_at']) <= millis(receipt[checked]) <= millis():
        raise Conflict('readback time is outside its original attempt')
    return _readback_references(receipt['record_refs'])


def _request_binding(db, value, receipt, *, body=None):
    _exact(receipt, 'schema attempt_id destination request_ref record_refs request_body anchor_ref request_started_at',
           'native sent request')
    refs = _attempt_receipt(receipt, value, checked='request_started_at')
    attempt = value['attempt']
    request_ref = _text(receipt['request_ref'], 'actual native request')
    if body is None:
        body = _body(db, value)
    if (receipt['schema'] != 'BTC_SENT_REQUEST_V1' or request_ref not in refs
            or receipt['anchor_ref'] != attempt['anchor_ref'] or request_ref == attempt['anchor_ref']
            or _message_text(_text(receipt['request_body'], 'actual native request body')) != _message_text(_request_body(body))):
        raise Conflict('native request does not match the original frozen send')
    owner_key = 'trading.request.' + identity(_channel(value['destination']), request_ref)
    existing = db.metadata(owner_key)
    if existing and existing != attempt['attempt_id']:
        raise Conflict('native request is already bound to a different send attempt')
    previous = attempt.get('request')
    if previous and previous != receipt:
        raise Conflict('the actual native request binding is immutable')
    if not existing:
        db.metadata(owner_key, attempt['attempt_id'], write=True)
    return {**attempt, 'request': receipt}


def _confirmed_display(db, value, receipt, *, body=None):
    _exact(receipt, 'schema destination checked_at attempt_id request_ref assistant record_refs absence_confirmed review',
           'actual native display')
    refs = _attempt_receipt(receipt, value, checked='checked_at')
    binding = value['attempt'].get('request')
    if (receipt['schema'] != 'BTC_CLOUD_READBACK_V5' or receipt['absence_confirmed'] is not False
            or not binding or receipt['request_ref'] != binding['request_ref'] or receipt['request_ref'] not in refs):
        raise Conflict('native display does not identify the actual sent request')
    assistant = receipt['assistant']
    _exact(assistant, 'record_ref parent_request_ref role body completed tool_calls completed_at', 'actual assistant message')
    if (assistant['role'] != 'assistant' or assistant['completed'] is not True
            or assistant['record_ref'] not in refs or assistant['record_ref'] == receipt['request_ref']
            or assistant['parent_request_ref'] != receipt['request_ref']
            or not millis(binding['request_started_at']) <= millis(assistant['completed_at']) <= millis(receipt['checked_at'])):
        raise Conflict('display is unfinished or unrelated to the original request')
    review = receipt['review']
    _exact(review, 'reviewer_role agent_id source_hash display_hash bindings checks unresolved_issues', 'readback verification')
    _text(review['agent_id'], 'actual readback verifier')
    displayed = _text(assistant['body'], 'actual displayed body')
    if (review['reviewer_role'] != 'MAIN' or not isinstance(review['source_hash'], str)
            or not isinstance(review['display_hash'], str)
            or review['source_hash'].removeprefix('sha256:') != value['body_hash']
            or review['display_hash'].removeprefix('sha256:') != fingerprint(displayed.encode('utf-8'))
            or review['bindings'] != [] or review['checks'] != {} or not isinstance(review['unresolved_issues'], list)):
        raise Conflict('readback verification does not match the actual source and display')
    match = _display_match(_body(db, value) if body is None else body, displayed)
    if assistant['tool_calls'] != [] or review['unresolved_issues'] or SECRET.search(displayed):
        return 'MISMATCH'
    return match


def _acknowledge(value, receipt):
    _exact(receipt, 'schema attempt_id recorded_at status record_refs detail', 'native transport receipt')
    if receipt['schema'] != 'BTC_TRANSPORT_RECEIPT_V1' or receipt['status'] not in {'ACCEPTED', 'PROCESSING', 'FAILED'}:
        raise ValueError('unsupported transport status')
    _attempt_receipt(receipt, value, checked='recorded_at')
    _text(receipt['detail'], 'actual transport result')
    return 'UNKNOWN' if receipt['status'] == 'FAILED' else 'SENDING'


def _non_dispatch(value, receipt):
    _exact(receipt, 'schema destination checked_at attempt_id request_ref assistant record_refs absence_confirmed review dispatch_evidence',
           'authoritative non-dispatch record')
    refs = _attempt_receipt(receipt, value, checked='checked_at')
    proof = receipt['dispatch_evidence']
    _exact(proof, 'outcome record_ref detail', 'non-dispatch evidence')
    _text(proof['detail'], 'actual non-dispatch explanation')
    if (receipt['schema'] != 'BTC_CLOUD_READBACK_V5' or receipt['absence_confirmed'] is not True
            or any(receipt[key] is not None for key in ('request_ref', 'assistant', 'review'))
            or proof['outcome'] != 'NOT_DISPATCHED' or proof['record_ref'] not in refs
            or value['attempt'].get('request')):
        raise Conflict('empty readback or a bound sent request does not establish non-dispatch')


def _finalize_feedback(db, value, receipt):
    report_id = value['report_id']
    assistant = receipt['assistant']
    destination = value['destination']
    delivered = {'report_id': report_id, 'completed_at': assistant['completed_at'],
                 'receipt_ref': assistant['record_ref']}
    previous = db.metadata(_last_key(destination))
    if _later_display(delivered, previous):
        db.metadata(_last_key(destination), delivered, write=True)
    if 'document' in value and _later_display(delivered, db.metadata(_edition_key(destination))):
        db.metadata(_edition_key(destination), delivered, write=True)
    day = utc(assistant['completed_at']).astimezone(DAY).date().isoformat()
    daily_key = _day_key(destination, day)
    credited = db.metadata(daily_key)
    if (value['complete'] and utc(value['created_at']).astimezone(DAY).date().isoformat() == day
            and (not credited or millis(delivered['completed_at']) < millis(credited['completed_at']))):
        db.metadata(daily_key, delivered, write=True)
    for identifier in value['obligation_ids']:
        obligation = _by_id(db, identifier, OBLIGATION)
        payload = obligation['payload']
        if obligation['active'] and payload['report_id'] == report_id:
            _close_obligation(db, obligation, assistant['completed_at'], report_id)


def _close_obligation(db, obligation, completed_at, report_id):
    payload = obligation['payload']
    late = payload['due_at'] is not None and millis(completed_at) > millis(payload['due_at'])
    _put(db, obligation['object_id'], obligation,
         {**payload, 'report_id': report_id, 'status': 'DELIVERED', 'completed_at': completed_at,
          'delivered_after_due': late}, 'Close feedback from actual displayed report', active=False)


def _verified_coverage(db, destination, original, obligation, proof):
    fields = 'obligation_digest report_id receipt_ref reason'
    exact = isinstance(proof, dict) and 'covered_by' in proof
    _exact(proof, fields + (' covered_by' if exact else ' excerpts'), 'review of actually displayed coverage')
    _text(proof['reason'], 'why the actual display covers the original obligation')
    if proof['obligation_digest'] != obligation['digest']:
        raise Conflict('the original feedback changed since its review')
    delivery = _by_id(db, proof['report_id'], REPORT)['payload']
    receipt = delivery.get('receipt') or {}
    assistant = receipt.get('assistant') or {}
    if (delivery['status'] != 'DELIVERED' or _channel(delivery['destination']) != _channel(destination)
            or not proof['receipt_ref'] or assistant.get('record_ref') != proof['receipt_ref']
            or _confirmed_display(db, delivery, receipt) == 'MISMATCH'):
        raise Conflict('the original obligation needs a real, completed display in the authorized destination')
    value = obligation['payload']
    if exact:
        covered = _by_id(db, proof['covered_by'], OBLIGATION)['payload']
        if (covered['status'] != 'DELIVERED' or covered['report_id'] != delivery['report_id']
                or covered['obligation_id'] not in delivery['obligation_ids']
                or _obligation_id(covered) != _obligation_id(value)):
            raise Conflict('the delivered obligation does not represent the original feedback')
    else:
        # A report can cover an earlier public market notification when its
        # later decision and actual displayed wording are both pinned.
        sources = db.rows('SELECT e.object_id,v.version FROM entity_versions v JOIN entities e '
                          'ON e.entity_key=v.entity_key WHERE e.kind=%s AND e.scope=%s AND v.digest=%s',
                          ('DECISION', 'PUBLIC', value['trigger_ref']))
        has_successor = any(ref['kind'] == 'DECISION' and ref['object_id'] == row['object_id']
                            and ref['version'] > row['version']
                            for row in sources for ref in delivery['decisions'])
        excerpts = proof['excerpts']
        if (value['kind'] != 'UPDATE' or delivery['run_ref'] != original['run_ref'] or not has_successor
                or not isinstance(excerpts, list) or not excerpts
                or any(_text(part, 'reviewed original display excerpt') not in assistant['body'] for part in excerpts)):
            raise Conflict('the coverage needs the actual successor public decision and displayed passages')
    return delivery['report_id']


def void_misbound(db, *, destination, report_id, expected_digest, evidence_ref, coverage):
    """Retire a proven wrong-destination draft that never began an attempt."""
    _text(expected_digest, 'original frozen report record digest')
    _text(evidence_ref, 'original wrong-binding evidence')
    if not isinstance(coverage, dict):
        raise ValueError('coverage must name the exact original feedback reviewed')
    row = _by_id(db, report_id, REPORT)
    original = row['payload']
    decision = {'original_digest': expected_digest, 'evidence_ref': evidence_ref, 'coverage': coverage}
    previous = original.get('retirement')
    if original['status'] == 'VOID_MISBOUND' and previous and all(previous.get(key) == value
                                                                for key, value in decision.items()):
        return {'report_id': report_id, 'status': original['status'], 'continuations': previous['continuations']}
    if (row['digest'] != expected_digest or not row['active'] or original['status'] != 'PENDING'
            or original['attempt_count'] != 0 or original.get('attempt') is not None or original.get('receipt')
            or original.get('superseded_by') or _channel(original['destination']) == _channel(destination)):
        raise Conflict('only the original unattempted wrong-destination report can be retired')
    if not set(coverage) <= set(original['obligation_ids']):
        raise Conflict('coverage includes unrelated feedback')
    continuations = {}
    for identifier in original['obligation_ids']:
        obligation = _by_id(db, identifier, OBLIGATION)
        value = obligation['payload']
        if (not obligation['active'] or value['status'] != 'OPEN' or value['report_id'] != report_id
                or value.get('destination') != original['destination']):
            raise Conflict('the wrong-destination draft no longer owns the original obligation')
        if identifier in coverage:
            matched_report = _verified_coverage(db, destination, original, obligation, coverage[identifier])
            result = {'outcome': 'COVERED_IN_AUTHORIZED_DESTINATION', 'report_id': matched_report}
        else:
            kept = {key: item for key, item in value.items() if key != 'destination'}
            replacement = _obligation_id(kept)
            if Entities(db).get('DELIVERY', replacement):
                raise Conflict('review the existing equivalent obligation before retiring the wrong destination')
            kept.update(obligation_id=replacement, report_id=None, original_obligation=identifier)
            _put(db, replacement, None, kept, 'Carry outstanding feedback from a wrong-destination draft')
            result = {'outcome': 'RETAINED', 'obligation_id': replacement}
        continuations[identifier] = result
        _put(db, identifier, obligation, {**value, 'status': 'VOID_MISBOUND', 'continuation': result},
             'Retire feedback bound to the unattempted wrong destination', active=False)
    retired = {**original, 'status': 'VOID_MISBOUND',
               'retirement': {**decision, 'continuations': continuations}}
    _put(db, report_id, row, retired, 'Retire the unattempted wrong-destination report',
         active=False, dependencies=_body_dependency(retired))
    return {'report_id': report_id, 'status': retired['status'], 'continuations': continuations}


def reconcile_archived(db, *, destination, report_id, receipt):
    """Attach actual readback to the original archived send without restarting it."""
    if not isinstance(report_id, str) or not HASH.fullmatch(report_id):
        raise ValueError('an original archived delivery identity is required')
    row = Entities(db).get('DELIVERY', report_id)
    if not row or not row['active']:
        raise Conflict('original archived attempt is not available for reconciliation')
    original = row['payload']
    status = original.get('status') or {}
    attempt = status.get('attempt') or {}
    if status.get('state') not in {'SENDING', 'UNKNOWN'} or not attempt.get('request_ref'):
        raise Conflict('archived delivery has no unresolved original bound request')
    artifact_digest = original.get('artifact_digest')
    if not isinstance(artifact_digest, str) or not HASH.fullmatch(artifact_digest):
        raise Conflict('archived delivery lacks its original frozen artifact')
    artifact = db.value(artifact_digest)
    if (fingerprint(encode(artifact)) != report_id or attempt.get('delivery_id') != report_id
            or artifact.get('presentation_mode') != 'VERBATIM'
            or (artifact.get('source') or {}).get('layout', {}).get('format') != 'AUTHORED_REPORT_V1'):
        raise Conflict('archived attempt does not match its original verbatim report')
    target = artifact.get('destination')
    if _channel(target) != _channel(destination) or target != attempt.get('destination'):
        raise Conflict('archived request was addressed to another destination')
    body = artifact.get('body')
    body_digest = fingerprint(_text(body, 'original archived report body').encode('utf-8'))
    if artifact.get('body_hash') != 'sha256:' + body_digest:
        raise Conflict('archived report body differs from its original digest')
    request = _archived_receipt(db, report_id, status.get('request_binding'))
    if not request or request.get('request_ref') != attempt['request_ref']:
        raise Conflict('original native request binding is missing from the archive')

    value = {'attempt': attempt, 'destination': target, 'body_hash': body_digest}
    value['attempt'] = _request_binding(db, value, request, body=body)
    match = _confirmed_display(db, value, receipt, body=body)
    if match == 'MISMATCH':
        raise Conflict('readback does not exactly present the original archived report')
    key = _archive_confirmation_key(report_id, attempt['attempt_id'])
    prior = db.metadata(key)
    if prior:
        if prior['receipt']['assistant'] != receipt['assistant']:
            raise Conflict('the confirmed original archived display is immutable')
        return prior
    result = {'report_id': report_id, 'attempt_id': attempt['attempt_id'], 'status': 'DELIVERED',
              'artifact_digest': artifact_digest, 'receipt': receipt, 'text_match': match}
    db.metadata(key, result, write=True)
    completed_at = receipt['assistant']['completed_at']
    displayed = {'report_id': report_id, 'completed_at': completed_at,
                 'receipt_ref': receipt['assistant']['record_ref'], 'archived_confirmation': key}
    last = db.metadata(_last_key(target)) or _historical_feedback(db, target, utc())
    if _later_display(displayed, last):
        db.metadata(_last_key(target), displayed, write=True)
    day = _archive_credited_day(artifact, completed_at)
    if day:
        credited = db.metadata(_day_key(target, day))
        if not credited or millis(completed_at) < millis(credited['completed_at']):
            db.metadata(_day_key(target, day), displayed, write=True)
    review_id = artifact['source'].get('report_review', {}).get('review_id')
    if review_id:
        obligation = _existing_obligation(db, {'kind': 'ARCHIVED', 'subject': 'retained feedback',
                                               'trigger_ref': review_id}, target)
        if obligation and obligation['active'] and obligation['payload']['report_id'] is None:
            originals = obligation['payload']['legacy']['deliveries']
            if any(previous['delivery_id'] == report_id for previous in originals):
                _close_obligation(db, obligation, completed_at, report_id)
    return result


def record(db, *, report_id, action, receipt):
    row = _by_id(db, report_id, REPORT)
    value = row['payload']
    state = value['status']
    if state == 'DELIVERED' and action == 'CONFIRM':
        if _confirmed_display(db, value, receipt) != 'MISMATCH' and receipt['assistant'] == value['receipt']['assistant']:
            return value
        raise Conflict('confirmed native display is immutable')
    if state not in {'SENDING', 'UNKNOWN', 'CORRECTION_REQUIRED'}:
        raise Conflict('this native attempt is not awaiting evidence')
    if state == 'CORRECTION_REQUIRED' and action != 'CONFIRM':
        raise Conflict('actual mismatched display is already recorded')
    if action == 'BIND_REQUEST':
        added = {'attempt': _request_binding(db, value, receipt)}
    elif action == 'CONFIRM':
        match = _confirmed_display(db, value, receipt)
        if state == 'CORRECTION_REQUIRED' and receipt['assistant'] != value['receipt']['assistant']:
            raise Conflict('correction requires the original actual display or a new report')
        added = {'status': 'CORRECTION_REQUIRED' if match == 'MISMATCH' else 'DELIVERED',
                 'receipt': receipt, 'text_match': match}
        if added['status'] == 'DELIVERED':
            _finalize_feedback(db, value, receipt)
    elif action == 'ACKNOWLEDGE':
        new_status = _acknowledge(value, receipt)
        if value.get('transport') and millis(receipt['recorded_at']) < millis(value['transport']['recorded_at']):
            return value
        if value.get('unconfirmed') and millis(receipt['recorded_at']) < millis(value['unconfirmed']['checked_at']):
            return value
        added = {'status': new_status, 'transport': receipt}
    elif action == 'UNKNOWN':
        _exact(receipt, 'schema attempt_id checked_at record_refs detail', 'uncertain native attempt')
        if receipt['schema'] != 'BTC_UNCONFIRMED_READBACK_V1':
            raise ValueError('unsupported uncertain readback')
        _text(receipt['detail'], 'uncertain delivery detail')
        _attempt_receipt(receipt, value, checked='checked_at')
        if value.get('transport') and millis(receipt['checked_at']) < millis(value['transport']['recorded_at']):
            return value
        if value.get('unconfirmed') and millis(receipt['checked_at']) < millis(value['unconfirmed']['checked_at']):
            return value
        added = {'status': 'UNKNOWN', 'unconfirmed': receipt}
    elif action in {'NOT_SENT', 'BLOCKED'}:
        _non_dispatch(value, receipt)
        added = {'status': 'BLOCKED' if action == 'BLOCKED' else 'NOT_SENT_CONFIRMED', 'receipt': receipt}
    else:
        raise ValueError('unsupported native delivery action')
    result = {**value, **added}
    _put(db, report_id, row, result, 'Record native delivery evidence',
         active=(row['active'] and result['status'] != 'DELIVERED' and
                 (not result.get('superseded_by') or result['status'] in {'SENDING', 'UNKNOWN'})),
         dependencies=_body_dependency(result))
    return result
