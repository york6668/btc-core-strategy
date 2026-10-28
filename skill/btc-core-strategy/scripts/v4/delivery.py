"""Durable source/presentation journal. This module never sends a cloud message."""
from collections import Counter, deque
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

from .common import Invalid, create, digest, exact, instant, locked, now, read, replace, require, text
from .storage import current
from .report import INTERNAL, freeze_body
from .authored_report import FORMAT as AUTHORED_FORMAT

SOURCE_SCHEMA = 'BTC_REPORT_SOURCE_V3'
ARTIFACT_SCHEMA = 'BTC_REPORT_V5'
EDITED_SCHEMAS = (ARTIFACT_SCHEMA, 'BTC_REPORT_V6')
RECEIPT_SCHEMA = 'BTC_CLOUD_READBACK_V5'
KINDS = {'direction', 'position', 'amount', 'unit', 'condition', 'deadline', 'negation', 'uncertainty', 'limitation', 'judgement', 'dispute_zone'}
CHECKS = {'numbers', 'directions', 'positions', 'units', 'conditions', 'deadlines', 'negation', 'uncertainty', 'limitations', 'no_new_judgement', 'coverage'}
NUMBER = re.compile(r'(?<![A-Za-z0-9_])[-+]?\d+(?:,\d{3})*(?:\.\d+)?')
UNIT = re.compile(r'(?<![A-Za-z])(?:USDT|USD|BTC|ETH|XAUT|SOL|BNB|UTC|JST|U)(?![A-Za-z])|%|保证金|名义(?:金额|额|价值|仓位)?|投入(?:金额|额)?')


def verbatim(artifact):
    return artifact.get('source', {}).get('layout', {}).get('format') == AUTHORED_FORMAT


def message_text(body):
    """A file's final LF is not part of its chat text; retain all other bytes."""
    return body[:-1] if body.endswith('\n') and not body.endswith(('\n\n', '\r\n')) else body


def same_display_text(source, displayed):
    return source == displayed or message_text(source) == message_text(displayed)


def _request_contains_source(artifact, request):
    body = message_text(artifact['body']) if verbatim(artifact) else artifact['body']
    return body in request


def request_body(artifact):
    """Ready-to-send instruction; the source is already the final report."""
    require(verbatim(artifact), 'request body helper is for authored reports')
    return ('VERBATIM：请将下方正文逐字作为唯一回复，保留所有 Markdown、换行、空格和顺序。'
            '不要整理、润色、压缩、补充解释、加前后缀或代码围栏；不要调用工具或重新分析。'
            '正文从下一空行开始直至消息结束。\n\n' + message_text(artifact['body']))


def _explanation(value, label):
    text(value, label)
    require(len(value.strip()) >= 12 and value.strip().lower() not in ('true', 'passed', 'all checks passed'), label + ' needs a concrete review explanation')


def _decision(root, identity):
    require(isinstance(identity, str) and re.fullmatch('[a-f0-9]{64}', identity), 'decision id invalid')
    value = read(Path(root) / 'versions' / identity / 'decision.json')
    require(value['decision_id'] == identity == digest({k: v for k, v in value.items() if k != 'decision_id'})[7:], 'damaged decision')
    return value


def _semantic(value):
    from .semantics import stable
    return digest(stable(value['assessment']))


def _report_due(root, decision):
    return _report_comparison(root, decision)['report_required']


def _report_comparison(root, decision):
    from .main_flow import report_comparison
    return report_comparison(root, decision['assessment'], decision,
                             decision.get('review', {}).get('material_new_outlook', False))


def validate_source(source, decision, verify_render=True):
    from . import reporting
    market_source = source.get('schema') == reporting.SOURCE_SCHEMA
    exact(source, 'schema decision_id body body_hash semantic_hash constraints author_review layout' + (' report_review' if market_source else ''), 'report source')
    require(source['schema'] in (SOURCE_SCHEMA, reporting.SOURCE_SCHEMA) and source['decision_id'] == decision['decision_id'], 'source belongs to another conclusion')
    if market_source:
        value = source['report_review']
        require(value['review_id'] == digest({k:v for k,v in value.items() if k!='review_id'}), 'embedded report review damaged')
        require(value['layout']==source['layout'] and value['action']=='REPORT', 'source differs from reviewed report')
    text(source['body'], 'report source body')
    require(source['body_hash'] == digest(source['body'].encode()) and source['semantic_hash'] == _semantic(decision), 'source is not bound to current conclusion')
    require(isinstance(source['layout'], dict) and source['layout'].get('format') in (('MARKET_CONCISE_V1','MARKET_MOBILE_V1','MARKET_MOBILE_V2',AUTHORED_FORMAT) if market_source else ('CONCISE_V1',)), 'new report source requires reviewed layout')
    if source['layout'].get('format') == AUTHORED_FORMAT:
        require(source['body'] == source['layout']['body'], 'authored source differs from final body')
    if verify_render and market_source:
        require(reporting.render(source['report_review'], decision) == source['body'], 'source differs from frozen market report')
    if verify_render and not market_source:
        from . import report
        accepted = deepcopy(decision['assessment'])
        accepted['report'] = source['layout']
        require(report.render(accepted) == source['body'], 'source body differs from the conclusion-bound rendered layout')
    require(not INTERNAL.search(source['body']), 'source contains internal control data')
    require(not re.search(r'(?i)authorization\s*:|cookie\s*:|(?:api[_-]?key|password|access[_-]?token)\s*[:=]|钱包助记词', source['body']), 'source contains private credentials')
    constraints = source['constraints']
    require(isinstance(constraints, list) and constraints, 'source assertions must be bound')
    seen = set()
    for item in constraints:
        exact(item, 'id kind source_text', 'source assertion')
        text(item['id'], 'assertion id')
        text(item['source_text'], 'source assertion text')
        require(item['id'] not in seen and item['kind'] in KINDS and item['source_text'] in source['body'], 'invalid or unbound source assertion')
        seen.add(item['id'])
    require(set(_numbers(source['body'])) <= set(_numbers(' '.join(c['source_text'] for c in constraints))),
            'numeric source assertions are not fully covered')
    for line in source['body'].splitlines():
        if source['layout'].get('format') != AUTHORED_FORMAT and line.startswith('| ') and ('U名义额' in line or 'U投入' in line or '当前不建议' in line):
            require(any(c['source_text'] == line for c in constraints),
                    'each recommendation row must bind its direction, position and amount together')
    review = source['author_review']
    exact(review, 'role agent_id source_hash coverage_review faithfulness_review', 'source author review')
    require(review['role'] == 'MAIN' and review['source_hash'] == source['body_hash'], 'source author review is stale')
    text(review['agent_id'], 'source author')
    _explanation(review['coverage_review'], 'source assertion coverage')
    _explanation(review['faithfulness_review'], 'source faithfulness')
    return source


def _condition_coverage(root, decision, source):
    from .main_flow import reported_assessment
    previous = reported_assessment(root)
    old = (previous or {}).get('recommendations', {})
    current_recommendations = decision['assessment'].get('recommendations', {})
    changed = any(sorted(current_recommendations.get(side, {}).get('validity', {}).get(field, [])) !=
                  sorted(old.get(side, {}).get('validity', {}).get(field, []))
                  for side in ('upper_short', 'lower_spot') for field in ('conditions', 'invalidations'))
    if changed:
        require(any(c['kind'] == 'condition' for c in source['constraints']),
                'changed support or invalidation conditions need an explicit source assertion')


def enqueue(root, decision_id, destination, source=None):
    root = Path(root)
    with locked(root):
        decision = _decision(root, decision_id)
        if (decision.get('assessment') or {}).get('revision', 1) >= 3:
            require(source is not None, 'main revision 3 or later needs a frozen report source')
            exact(destination, 'project_id thread_id', 'report destination')
            text(destination['project_id'], 'cloud project')
            text(destination['thread_id'], 'cloud thread')
            validate_source(source, decision)
            policy_path=root/'write-policy.json'
            if policy_path.exists() and read(policy_path).get('reporting_policy_revision')==1:
                require(source['schema']=='BTC_REPORT_SOURCE_V4','active reporting policy requires independent report-review source')
            if policy_path.exists() and read(policy_path).get('mobile_report_required'):
                require(source['layout'].get('format') in ('MARKET_MOBILE_V1','MARKET_MOBILE_V2',AUTHORED_FORMAT),'new report requires reviewed report layout')
            if policy_path.exists() and read(policy_path).get('report_care_revision')==1:
                require('maintenance' in source.get('report_review',{}),'new report needs report maintenance obligations')
            require(current(root)['decision_id'] == decision_id, 'report source is no longer the current conclusion')
            from . import reporting
            market_source = source['schema'] == reporting.SOURCE_SCHEMA
            comparison = _report_comparison(root, decision)
            _condition_coverage(root, decision, source)
            if market_source:
                require(reporting.review(root, source['report_review']['review_id']) == source['report_review'], 'source review not persisted')
                if not reporting.due(root, source):
                    return {'status':'NOT_DUE','reason':'REPORT_CONTEXT_CHANGED_OR_INFORMATION_ALREADY_REPORTED'}
            else:
                from .report import validate_changes
                accepted = deepcopy(decision['assessment'])
                accepted['report'] = source['layout']
                validate_changes(accepted, comparison)
                if not comparison['report_required']:
                    return {'status': 'NOT_DUE', 'reason': 'CURRENT_MEANING_ALREADY_REPORTED'}
            artifact = {'schema': ARTIFACT_SCHEMA, 'decision_id': decision_id, 'body': source['body'], 'body_hash': source['body_hash'],
                        'semantic_hash': source['semantic_hash'], 'source': source, 'presentation_mode': 'VERBATIM' if source['layout'].get('format') == AUTHORED_FORMAT else 'EDIT_ONCE',
                        'decision_created_at': decision['created_at']}
            if market_source:
                artifact.update(schema=reporting.ARTIFACT_SCHEMA, purposes=source['report_review']['purposes'],
                                coverage=source['report_review']['context']['coverage'],
                                report_created_at=source['report_review']['created_at'])
            artifact['report_id'] = digest(artifact)
        else:
            require(source is None, 'legacy reports must retain their original frozen body')
            require(decision['report_required'] and decision.get('assessment'), 'no completed report due')
            body = (root / 'versions' / decision_id / 'report.md').read_bytes()
            require(digest(body) == decision['view_hashes']['report.md'], 'body differs from committed version')
            artifact = freeze_body(body.decode('utf-8'), decision_id)
        artifact['destination'] = destination
        identity = digest(artifact)[7:]
        outbox = root / 'outbox' / identity
        if outbox.exists():
            require(read(outbox / 'artifact.json') == artifact, 'outbox artifact changed')
        else:
            create(outbox / 'artifact.json', artifact)
            create(outbox / 'status.json', {'state': 'PENDING', 'updated_at': now(), 'attempts': 0})
        return {'delivery_id': identity, 'artifact': artifact, 'status': read(outbox / 'status.json'),
                **({'request_body':request_body(artifact)} if verbatim(artifact) else {})}


def _numbers(value):
    return Counter(str(Decimal(m.replace(',', '')).normalize()) for m in NUMBER.findall(value))


def _units(value):
    return {('名义' if x.startswith('名义') else '投入' if x.startswith('投入') else x) for x in UNIT.findall(value)}


def _relations(value):
    result = set()
    for key, pattern in [('LT', r'低于|小于|(?<![<>=])<(?![=>])'), ('GT', r'高于|大于|(?<![<>=])>(?![=>])'),
                         ('LE', r'不高于|不超过|至多|<='), ('GE', r'不低于|不少于|至少|>=')]:
        if re.search(pattern, value):
            result.add(key)
    if 'LE' in result:
        result.discard('GT')
    if 'GE' in result:
        result.discard('LT')
    return result


def _directions(value):
    return {kind for kind, pattern in [('SHORT', r'空单|空头|做空|卖空'), ('LONG', r'多单|多头|做多'),
            ('SPOT', r'现货'), ('UPPER', r'上沿|溢价'), ('LOWER', r'下沿|超卖'),
            ('WITHDRAW', r'撤回|撤销建议'), ('BUY', r'买入|购入'), ('SELL', r'卖出|出售')]
            if re.search(pattern, value)}


def _qualifiers(value):
    return {kind for kind, pattern in [('UNCONFIRMED', r'未(?:经)?(?:确认|证实)|尚待确认|待确认|未确定'),
            ('UNKNOWN', r'未知|不明|未明'), ('NOT_RECOMMENDED', r'不建议|暂不推荐'),
            ('NOT_PROVEN', r'不能证明|无法证明|不证明'), ('POSSIBLE', r'可能|或许'),
            ('LIMITED', r'有限|不足')]
            if re.search(pattern, value)}


def _receipt_link(artifact, status, receipt, absence=False):
    exact(receipt, 'schema destination checked_at attempt_id request_ref assistant record_refs absence_confirmed review' + (' dispatch_evidence' if absence else ''), 'edited report readback')
    require(receipt['schema'] == RECEIPT_SCHEMA and receipt['destination'] == artifact['destination'], 'wrong readback contract or destination')
    attempt = status['attempt']
    require(receipt['attempt_id'] == attempt['attempt_id'], 'receipt belongs to another send attempt')
    require(instant(receipt['checked_at']) >= instant(attempt['started_at']), 'readback predates this attempt')
    refs = receipt['record_refs']
    require(isinstance(refs, list) and refs and all(isinstance(x, str) and x for x in refs), 'actual cloud records required')
    if absence:
        require(receipt['absence_confirmed'] is True and receipt['assistant'] is None and receipt['review'] is None,
                'absence not established')
        require(receipt['request_ref'] == attempt.get('request_ref'), 'absence belongs to another request')
        proof = receipt['dispatch_evidence']
        exact(proof, 'outcome record_ref detail', 'dispatch failure evidence')
        require(proof['outcome'] == 'NOT_DISPATCHED' and proof['record_ref'] in refs,
                'empty readback is not proof of non-dispatch')
        _explanation(proof['detail'], 'authoritative non-dispatch evidence')
        require(not attempt.get('request_ref'), 'a bound cloud request was already dispatched')
        return
    require(receipt['absence_confirmed'] is False, 'displayed report cannot be absent')
    require(attempt.get('request_ref') and receipt['request_ref'] == attempt['request_ref'] and receipt['request_ref'] in refs,
            'receipt has no bound sent request')
    assistant = receipt['assistant']
    care='maintenance' in artifact.get('source',{}).get('report_review',{})
    exact(assistant, 'record_ref parent_request_ref role body completed tool_calls'+(' completed_at' if care else ''), 'actual assistant presentation')
    if care:
        require(instant(attempt['started_at'])<=instant(assistant['completed_at'])<=instant(receipt['checked_at']),'actual assistant completion time is invalid')
    require(assistant['role'] == 'assistant' and assistant['completed'] is True, 'user or unfinished messages are not delivery receipts')
    require(assistant['record_ref'] in refs and assistant['record_ref'] != receipt['request_ref'] and assistant['parent_request_ref'] == receipt['request_ref'], 'assistant message is unrelated to this request')
    text(assistant['body'], 'actual presentation')


def presentation_errors(artifact, receipt, mode='EDIT_ONCE'):
    """Validate bindings mechanically; authored semantic review remains MAIN-owned."""
    source, body = artifact['source'], receipt['assistant']['body']
    review = receipt['review']
    mobile=source['layout'].get('format') in ('MARKET_MOBILE_V1','MARKET_MOBILE_V2')
    exact(review, 'reviewer_role agent_id source_hash display_hash bindings checks unresolved_issues', 'presentation review')
    require(review['reviewer_role'] == 'MAIN' and review['source_hash'] == source['body_hash'] and review['display_hash'] == digest(body.encode()), 'presentation review is stale or from wrong owner')
    text(review['agent_id'], 'presentation reviewer')
    if verbatim(artifact):
        # Content was reviewed before freezing. Readback proves only actual
        # delivery and exact text; it must not start another semantic rewrite.
        require(review['bindings'] == [] and review['checks'] == {}, 'verbatim readback uses no second content review')
        require(isinstance(review['unresolved_issues'], list), 'presentation issues must be recorded')
        errors = [str(issue) for issue in review['unresolved_issues']]
        if not same_display_text(source['body'], body):
            errors.append('VERBATIM_SOURCE_CHANGED')
        if receipt['assistant']['tool_calls'] != []:
            errors.append('CLOUD_USED_TOOLS')
        if INTERNAL.search(body):
            errors.append('DISPLAY_CONTAINS_CONTROL_DATA')
        return sorted(set(errors))
    require(isinstance(review['bindings'], list), 'presentation assertions must be mapped')
    bindings = {}
    for item in review['bindings']:
        exact(item, 'constraint_id display_text', 'presentation assertion binding')
        require(item['constraint_id'] not in bindings, 'duplicate presentation binding')
        text(item['display_text'], 'bound display text')
        require(item['display_text'] in body, 'assertion binding is not in actual assistant text')
        bindings[item['constraint_id']] = item['display_text']
    require(set(bindings) == {c['id'] for c in source['constraints']}, 'presentation omits or invents source assertions')
    require(isinstance(review['checks'], dict) and set(review['checks']) == (CHECKS | {'phone_readability','decision_usefulness'} if mobile else CHECKS), 'complete presentation review required')
    errors = []
    for key, finding in review['checks'].items():
        exact(finding, 'passed explanation', 'presentation review check')
        require(type(finding['passed']) is bool, 'review verdict must be a boolean')
        _explanation(finding['explanation'], key + ' review')
        if not finding['passed']:
            errors.append(key)
    require(isinstance(review['unresolved_issues'], list), 'presentation issues must be recorded')
    errors.extend(str(issue) for issue in review['unresolved_issues'])
    if receipt['assistant']['tool_calls'] != []:
        errors.append('CLOUD_USED_TOOLS')
    if INTERNAL.search(body):
        errors.append('DISPLAY_CONTAINS_CONTROL_DATA')
    # Repeated prose may be compressed, but new or missing numeric values cannot be.
    if set(_numbers(body)) != set(_numbers(source['body'])):
        errors.append('DISPLAY_NUMBER_SET_CHANGED')
    if _units(body) != _units(source['body']):
        errors.append('DISPLAY_UNITS_CHANGED')
    if _directions(body) != _directions(source['body']):
        errors.append('DISPLAY_DIRECTION_CHANGED')
    for item in source['constraints']:
        old, new = item['source_text'], bindings[item['id']]
        if (_numbers(old) != _numbers(new) or _units(old) != _units(new) or _relations(old) != _relations(new)
                or _directions(old) != _directions(new)):
            errors.append('BOUND_FACT_CHANGED:' + item['id'])
        if _qualifiers(old) != _qualifiers(new):
            errors.append('QUALIFIER_REMOVED:' + item['id'])
    if mode == 'CORRECTION' and body != source['body']:
        errors.append('CORRECTION_MUST_SHOW_FROZEN_SOURCE')
    return sorted(set(errors))


def _save(outbox, prefix, value):
    path = outbox / (prefix + '-' + digest(value)[7:] + '.json')
    if not path.exists():
        create(path, value)
    return path.name


def _advance_reported(root, identity, artifact, status):
    decision = _decision(root, artifact['decision_id'])
    path = root / 'reported-current.json'
    prior = read(path) if path.exists() else None
    # Keep actual displayed content separate from the monotone coverage watermark.
    display_path = root / 'last-display-current.json'
    display_prior = read(display_path) if display_path.exists() else None
    send_order = status.get('attempt', {}).get('started_at', status['updated_at'])
    sequence=status.get('attempt',{}).get('send_sequence')
    newer_display=(display_prior is None or
                   (sequence is not None and sequence>display_prior.get('send_sequence',0)) or
                   (sequence is None and display_prior.get('send_sequence',0)==0 and instant(send_order)>instant(display_prior['send_order'])))
    if newer_display:
        replace(display_path, {'delivery_id':identity, 'send_order':send_order, 'send_sequence':sequence or 0})
    # Report chronology is independent of strategy-version chronology.
    coverage = artifact.get('coverage', {}).get('end', decision['assessment']['temporal_scope']['market_cutoff'])
    order = [coverage, artifact.get('report_created_at', decision['created_at'])]
    old_order = prior.get('report_order') if prior else None
    if prior and prior.get('delivery_id') == identity:
        return
    newer = old_order is None or tuple(map(instant, order)) >= tuple(map(instant, old_order))
    if prior is None or (newer and instant(decision['created_at']) >= instant(prior['decision_created_at'])):
        value = {'delivery_id': identity, 'decision_id': artifact['decision_id'], 'decision_created_at': decision['created_at'], 'report_order':order}
        if artifact.get('coverage'):
            value['coverage']=artifact['coverage']
        if artifact['schema'] in EDITED_SCHEMAS:
            value.update(semantic_hash=artifact['semantic_hash'], source_hash=artifact['body_hash'], presentation=status['presentation'])
        replace(path, value)
    elif artifact['schema'] in EDITED_SCHEMAS:
        # Keep semantic baseline monotonic while exposing a possibly stale late display.
        status['late_confirmation_review_required'] = True
        replace(root / 'outbox' / identity / 'status.json', status)


ATTEMPT_FIELDS = ('state', 'attempt', 'request_binding', 'receipt', 'presentation',
                  'presentation_errors', 'reconciliation', 'retry', 'unknown_readbacks', 'transport_receipt')


def _journal_attempt(outbox, status):
    if status.get('attempt'):
        snapshot = {k: deepcopy(status[k]) for k in ATTEMPT_FIELDS if k in status}
        status.setdefault('attempt_records', {})[status['attempt']['attempt_id']] = _save(outbox, 'attempt-state', snapshot)


def _next_sequence(root):
    values = [0]
    for path in (Path(root) / 'outbox').glob('*/status.json'):
        s = read(path)
        values.append(s.get('attempt', {}).get('send_sequence', 0))
        values.extend(read(path.parent / name)['attempt'].get('send_sequence', 0)
                      for name in s.get('attempt_records', {}).values())
    return max(values) + 1


def _check_recovery_budget(root, run_ref, destination):
    text(run_ref, 'actual recovery run')
    for pattern in ('*/retry-*.json', '*/recovery-send-*.json'):
        for path in (Path(root) / 'outbox').glob(pattern):
            prior = read(path)
            require(not (prior['run_ref'] == run_ref and prior['destination'] == destination),
                    'one recovery resend per run and destination; delivery exception remains open')


def _unconfirmed_readback(outbox, record):
    """Only a recorded read after the latest transport/binding can defer work."""
    since = instant(record['attempt']['started_at'])
    for field, timestamp in (('transport_receipt', 'recorded_at'), ('request_binding', 'request_started_at')):
        if record.get(field):
            value = read(outbox / record[field])
            if value.get(timestamp):
                since = max(since, instant(value[timestamp]))
    found = []
    for name in record.get('unknown_readbacks', []):
        value = read(outbox / name)
        if (value.get('schema') == 'BTC_UNCONFIRMED_READBACK_V1'
                and value.get('attempt_id') == record['attempt']['attempt_id']
                and value.get('record_refs') and all(isinstance(v, str) and v for v in value['record_refs'])
                and name == 'unconfirmed-readback-' + digest(value)[7:] + '.json'
                and instant(value['checked_at']) >= since):
            found.append((instant(value['checked_at']), str(outbox / name)))
    return max(found)[1] if found else None


def _delivered_coverage(root, identity, deliveries, successors, verified):
    """Find a validated same-report or explicit coverage path; never infer meaning."""
    artifact = deliveries[identity][0]
    original = artifact.get('source', {}).get('report_review', {}).get('review_id')
    queue = deque([(original, [])])
    seen = set()
    while queue:
        review_id, chain = queue.popleft()
        if review_id in seen:
            continue
        seen.add(review_id)
        for other, (candidate, status) in deliveries.items():
            same_review = review_id and candidate.get('source', {}).get('report_review', {}).get('review_id') == review_id
            if (not same_review and other != identity) or status['state'] != 'DELIVERED':
                continue
            if candidate['destination'] != artifact['destination']:
                continue
            if other not in verified:
                try:
                    validate_delivered(root, other)
                    verified[other] = True
                except (Invalid, OSError, ValueError, KeyError, TypeError):
                    verified[other] = False
            if verified[other]:
                return {'delivery_id': other, 'coverage_review_ids': chain,
                        'receipt_ref': str(root / 'outbox' / other / status['receipt'])}
        for successor in successors.get(review_id, []):
            queue.append((successor, chain + [successor]))
    return None


def attempt_recovery(root):
    """Read-only recovery classification; UNKNOWN history never becomes delivery."""
    root = Path(root)
    result = []
    deliveries, candidates = {}, []
    for path in sorted((Path(root) / 'outbox').glob('*/status.json')):
        s = read(path)
        deliveries[path.parent.name] = (read(path.parent / 'artifact.json'), s)
        records = {key: read(path.parent / name) for key, name in s.get('attempt_records', {}).items()}
        if s.get('attempt'):
            records[s['attempt']['attempt_id']] = s
        for key, record in records.items():
            if record['state'] in ('SENDING', 'UNKNOWN', 'CORRECTION_REQUIRED'):
                transport = read(path.parent / record['transport_receipt']) if record.get('transport_receipt') else None
                item = {'delivery_id': path.parent.name, 'attempt_id': key,
                               'state': record['state'], 'request_ref': record['attempt'].get('request_ref'),
                               'transport_status': (transport or {}).get('status', 'UNKNOWN'),
                               'next_action': 'WAIT_FOR_PROCESSING_THEN_READ_BACK' if transport and transport['status'] == 'PROCESSING' and record['state'] == 'SENDING' else 'READ_BACK_THIS_ATTEMPT',
                               'blocks_new_reports': False}
                result.append(item)
                if record['state'] == 'UNKNOWN':
                    evidence = _unconfirmed_readback(path.parent, record)
                    if evidence:
                        candidates.append((item, evidence))
    if candidates:
        from .reporting import review
        reports = {p.stem: review(root, 'sha256:' + p.stem) for p in (root / 'report-reviews').glob('*.json')}
        successors, verified = {}, {}
        for value in reports.values():
            if value['action'] != 'REPORT':
                continue
            for coverage in value.get('delivery_coverage', []):
                prior = coverage['review_id']
                if (prior[7:] in reports and reports[prior[7:]]['action'] == 'REPORT'
                        and prior in value.get('supersedes', []) and coverage.get('explanation', '').strip()):
                    successors.setdefault(prior, []).append(value['review_id'])
        for item, evidence in candidates:
            covered = _delivered_coverage(root, item['delivery_id'], deliveries, successors, verified)
            if covered:
                item.update(next_action='WAIT_FOR_NEW_EVIDENCE',
                            recovery_evidence={'unconfirmed_readback_ref': evidence, **covered})
    return result


def recovery_actions(root, attempts=None):
    """Operational view; complete historical attempts remain in attempt_recovery."""
    attempts = attempt_recovery(root) if attempts is None else attempts
    result = [deepcopy(v) for v in attempts if v['next_action'] != 'WAIT_FOR_NEW_EVIDENCE']
    for path in sorted((Path(root) / 'outbox').glob('*/status.json')):
        state = read(path)['state']
        if state in ('PENDING', 'NOT_SENT_CONFIRMED'):
            result.append({'delivery_id': path.parent.name, 'state': state,
                           'next_action': 'REVIEW_EXISTING_DELIVERY', 'blocks_new_reports': False})
    return result


def _begin(root, outbox, artifact, status, action, receipt):
    # Destination uncertainty is not a lock on a distinct, currently reviewed report.
    correction = action == 'BEGIN_CORRECTION'
    if correction:
        require(status['state'] == 'CORRECTION_REQUIRED' and not status.get('correction_started'), 'only one local correction is allowed')
    else:
        require(status['state'] in ('PENDING', 'NOT_SENT_CONFIRMED'), 'uncertain delivery requires readback before resend')
        latest = current(root)
        retrying_correction = status.get('attempt', {}).get('mode') == 'CORRECTION'
        from . import reporting
        still_due = reporting.due(root, artifact['source']) if artifact['schema']==reporting.ARTIFACT_SCHEMA else _report_due(root, latest)
        if not retrying_correction and (latest['decision_id'] != artifact['decision_id'] or not still_due):
            status.update(state='SUPERSEDED', reason='REVIEW_CURRENT_CONCLUSION_BEFORE_SENDING')
            return
    exact(receipt, 'schema destination anchor_ref started_at' + (' run_ref' if receipt and 'run_ref' in receipt else ''), 'delivery attempt')
    require(receipt['schema'] == 'BTC_DELIVERY_ATTEMPT_V1' and receipt['destination'] == artifact['destination'], 'invalid send attempt')
    text(receipt['anchor_ref'], 'actual pre-send thread cursor')
    instant(receipt['started_at'])
    run_ref = (receipt.get('run_ref') or status.get('attempt', {}).get('run_ref')
               or artifact.get('source', {}).get('report_review', {}).get('provenance', {}).get('run_ref'))
    if correction and run_ref:
        _check_recovery_budget(root, run_ref, artifact['destination'])
    _journal_attempt(outbox, status)
    mode = 'CORRECTION' if correction or status.get('attempt', {}).get('mode') == 'CORRECTION' else ('VERBATIM' if verbatim(artifact) else 'EDIT_ONCE')
    attempt = dict(receipt, delivery_id=digest(artifact)[7:], attempt_number=status['attempts'] + 1, mode=mode)
    attempt['send_sequence'] = _next_sequence(root)
    attempt['attempt_id'] = digest(attempt)
    _save(outbox, 'attempt', attempt)
    for key in ATTEMPT_FIELDS:
        if key not in ('state', 'attempt'):
            status.pop(key, None)
    status.update(state='SENDING', attempts=attempt['attempt_number'], attempt=attempt)
    if correction:
        status['correction_started'] = True
        if run_ref:
            _save(outbox, 'recovery-send', {'run_ref':run_ref, 'destination':artifact['destination'],
                'attempt_id':attempt['attempt_id'], 'action':'BEGIN_CORRECTION'})


def _retry_unknown(root, outbox, artifact, status, receipt):
    from . import reporting
    exact(receipt, 'schema destination anchor_ref started_at prior_attempt_id run_ref current_context_id readback applicability_review duplicate_risk_accepted', 'unknown delivery retry')
    require(receipt['schema'] == 'BTC_DELIVERY_RETRY_V1' and receipt['destination'] == artifact['destination'], 'invalid retry contract')
    require(status['state'] in ('SENDING', 'UNKNOWN') and receipt['prior_attempt_id'] == status['attempt']['attempt_id'], 'retry must follow the current uncertain attempt')
    transport = read(outbox / status['transport_receipt']) if status.get('transport_receipt') else None
    require(not (status['state'] == 'SENDING' and transport and transport['status'] == 'PROCESSING'),
            'request is still processing; wait for its completion or a later failed/unconfirmed readback')
    check = receipt['readback']
    exact(check, 'checked_at record_refs outcome', 'retry readback')
    require(check['outcome'] == 'UNCONFIRMED' and isinstance(check['record_refs'], list) and check['record_refs']
            and all(isinstance(v, str) and v for v in check['record_refs']), 'retry needs actual unconfirmed readback records')
    require(instant(status['attempt']['started_at']) <= instant(check['checked_at']) <= instant(receipt['started_at']), 'retry readback must follow the prior send')
    _explanation(receipt['applicability_review'], 'current evidence and outstanding report applicability')
    text(receipt['run_ref'], 'actual recovery run')
    text(receipt['anchor_ref'], 'actual pre-retry thread cursor')
    require(receipt['duplicate_risk_accepted'] is True, 'at-least-once retry must retain possible duplication')
    ctx = reporting.context(root)
    require(receipt['current_context_id'] == ctx['context_id'] and current(root)['decision_id'] == artifact['decision_id'], 'retry requires current evidence review; replace stale report')
    # A replacement already carries this obligation; never resend its obsolete source.
    rid = artifact.get('source', {}).get('report_review', {}).get('review_id')
    if rid:
        require(any(v['review_id'] == rid for v in reporting.obligations(root)), 'report obligation already covered; recover old request only')
    _check_recovery_budget(root, receipt['run_ref'], receipt['destination'])
    # Persist original UNKNOWN and its binding before beginning a separate attempt.
    status['state'] = 'UNKNOWN'
    _journal_attempt(outbox, status)
    start = {k: receipt[k] for k in ('destination', 'anchor_ref', 'started_at', 'run_ref')}
    start['schema'] = 'BTC_DELIVERY_ATTEMPT_V1'
    # Current applicability was explicitly reviewed above; source clock drift is
    # not non-delivery and cannot erase the outstanding obligation.
    _new_retry_attempt(root, outbox, artifact, status, start)
    status['retry'] = _save(outbox, 'retry', receipt)
    status['possible_duplicate'] = True


def _new_retry_attempt(root, outbox, artifact, status, start):
    mode = status['attempt']['mode']
    attempt = dict(start, delivery_id=digest(artifact)[7:], attempt_number=status['attempts'] + 1, mode=mode)
    attempt['send_sequence'] = _next_sequence(root)
    attempt['attempt_id'] = digest(attempt)
    _save(outbox, 'attempt', attempt)
    for key in ATTEMPT_FIELDS:
        status.pop(key, None)
    status.update(state='SENDING', attempts=attempt['attempt_number'], attempt=attempt)


def _validate_request_binding(artifact, status, receipt):
    exact(receipt, 'schema attempt_id destination request_ref record_refs request_body anchor_ref request_started_at', 'sent request binding')
    attempt = status['attempt']
    require(receipt['schema'] == 'BTC_SENT_REQUEST_V1' and receipt['attempt_id'] == attempt['attempt_id'] and receipt['destination'] == artifact['destination'], 'request binding belongs to another attempt')
    text(receipt['request_ref'], 'actual sent message reference')
    require(isinstance(receipt['record_refs'], list) and receipt['request_ref'] in receipt['record_refs'], 'actual sent request record required')
    require(receipt['anchor_ref'] == attempt['anchor_ref'] and receipt['request_ref'] != receipt['anchor_ref'], 'request is not after the saved send boundary')
    require(isinstance(receipt['request_body'], str) and _request_contains_source(artifact, receipt['request_body']), 'actual sent request does not contain the frozen source')
    if verbatim(artifact):
        require(same_display_text(receipt['request_body'], request_body(artifact)), 'authored source requires the frozen verbatim send instruction')
    require(instant(receipt['request_started_at']) >= instant(attempt['started_at']), 'cloud request predates this send attempt')


def _reconcile_request(root, outbox, artifact, status, receipt):
    """Confirm a real reply after repairing a proven earlier-attempt binding."""
    exact(receipt, 'schema previous_request binding readback reason', 'request reconciliation')
    require(receipt['schema'] == 'BTC_REQUEST_RECONCILIATION_V1', 'invalid request reconciliation')
    if status.get('reconciliation') and read(outbox / status['reconciliation'])['evidence'] == receipt:
        return
    require(status['state'] in ('SENDING', 'UNKNOWN'), 'only uncertain delivery can reconcile a request')
    _explanation(receipt['reason'], 'request reconciliation reason')
    previous = receipt['previous_request']
    exact(previous, 'record_ref started_at body', 'previous cloud request')
    old_binding = read(outbox / status['request_binding'])
    require(previous['record_ref'] == status['attempt'].get('request_ref') == old_binding['request_ref'], 'previous request differs from saved binding')
    require(previous['body'] == old_binding['request_body'], 'previous request body differs from saved binding')
    require(instant(previous['started_at']) < instant(status['attempt']['started_at']), 'previous request is not a proven earlier attempt')
    binding, readback = receipt['binding'], receipt['readback']
    _validate_request_binding(artifact, status, binding)
    require(binding['request_ref'] != previous['record_ref'], 'reconciliation needs the actual replacement request')
    candidate = deepcopy(status)
    candidate['attempt']['request_ref'] = binding['request_ref']
    _receipt_link(artifact, candidate, readback)
    require(instant(binding['request_started_at']) <= instant(readback['assistant'].get('completed_at', readback['checked_at'])), 'reply predates its cloud request')
    require(not presentation_errors(artifact, readback, candidate['attempt']['mode']), 'reconciled presentation must pass semantic review')
    # Validate everything before replacing the mutable status; retain old journals.
    journal = {'previous_binding': status['request_binding'], 'previous_receipt': status.get('receipt'), 'evidence': receipt}
    candidate['request_binding'] = _save(outbox, 'request', binding)
    candidate['reconciliation'] = _save(outbox, 'reconciliation', journal)
    _edited_transition(root, outbox, artifact, candidate, 'CONFIRM', readback)
    status.update(candidate)


def _edited_transition(root, outbox, artifact, status, action, receipt):
    state = status['state']
    if action in ('BEGIN', 'BEGIN_CORRECTION'):
        _begin(root, outbox, artifact, status, action, receipt)
    elif action == 'RETRY_UNKNOWN':
        _retry_unknown(root, outbox, artifact, status, receipt)
    elif action == 'BIND_REQUEST':
        if state == 'DELIVERED':
            require(read(outbox / status['request_binding']) == receipt, 'delivered request binding is immutable')
            return
        require(state in ('SENDING', 'UNKNOWN', 'CORRECTION_REQUIRED'), 'no send attempt to bind')
        if state == 'CORRECTION_REQUIRED':
            previous = read(outbox / status['request_binding'])
            require(verbatim(artifact) and same_display_text(previous['request_body'], receipt['request_body'])
                    and all(previous[key] == receipt[key] for key in ('attempt_id', 'destination', 'request_ref', 'anchor_ref'))
                    and instant(previous['request_started_at']) == instant(receipt['request_started_at']),
                    'readback repair may only correct the same request terminal LF')
        _validate_request_binding(artifact, status, receipt)
        require(status['attempt'].get('request_ref') in (None, receipt['request_ref']), 'cannot replace a bound cloud request')
        status['attempt']['request_ref'] = receipt['request_ref']
        status['request_binding'] = _save(outbox, 'request', receipt)
    elif action == 'ACKNOWLEDGE':
        exact(receipt, 'schema attempt_id recorded_at status record_refs detail', 'transport acknowledgement')
        if status.get('transport_receipt') and read(outbox / status['transport_receipt']) == receipt:
            return  # Replay cannot undo a later UNKNOWN or confirmed display.
        require(state in ('SENDING', 'UNKNOWN') and receipt['schema'] == 'BTC_TRANSPORT_RECEIPT_V1'
                and receipt['attempt_id'] == status['attempt']['attempt_id'], 'transport receipt belongs to another attempt')
        require(receipt['status'] in ('ACCEPTED', 'PROCESSING', 'FAILED'), 'invalid transport status')
        require(instant(receipt['recorded_at']) >= instant(status['attempt']['started_at']), 'transport receipt predates send')
        require(isinstance(receipt['record_refs'], list) and receipt['record_refs']
                and all(isinstance(v, str) and v for v in receipt['record_refs']), 'actual transport records required')
        _explanation(receipt['detail'], 'transport result')
        if status.get('transport_receipt'):
            previous = read(outbox / status['transport_receipt'])
            require(instant(receipt['recorded_at']) >= instant(previous['recorded_at']), 'late transport acknowledgement cannot regress request state')
        require(all(instant(receipt['recorded_at']) >= instant(read(outbox / path)['checked_at'])
                    for path in status.get('unknown_readbacks', [])), 'late transport acknowledgement cannot undo a newer readback')
        status['transport_receipt'] = _save(outbox, 'transport', receipt)
        status['state'] = 'UNKNOWN' if receipt['status'] == 'FAILED' else 'SENDING'
    elif action == 'RECONCILE_REQUEST':
        _reconcile_request(root, outbox, artifact, status, receipt)
    elif action == 'UNKNOWN':
        require(state in ('SENDING', 'UNKNOWN'), 'no in-flight delivery')
        if receipt:
            exact(receipt, 'schema attempt_id checked_at record_refs detail', 'unconfirmed delivery readback')
            require(receipt['schema'] == 'BTC_UNCONFIRMED_READBACK_V1'
                    and receipt['attempt_id'] == status['attempt']['attempt_id'], 'unconfirmed readback belongs to another attempt')
            require(instant(receipt['checked_at']) >= instant(status['attempt']['started_at']), 'readback predates this attempt')
            require(isinstance(receipt['record_refs'], list) and receipt['record_refs']
                    and all(isinstance(v, str) and v for v in receipt['record_refs']), 'actual unconfirmed readback references required')
            _explanation(receipt['detail'], 'unconfirmed readback detail')
            record = _save(outbox, 'unconfirmed-readback', receipt)
            records = status.setdefault('unknown_readbacks', [])
            if record not in records:records.append(record)
        transport = read(outbox / status['transport_receipt']) if status.get('transport_receipt') else None
        if not receipt or not transport or instant(receipt['checked_at']) >= instant(transport['recorded_at']):
            status['state'] = 'UNKNOWN'
    elif action == 'NOT_SENT':
        require(state in ('SENDING', 'UNKNOWN'), 'absence cannot undo a delivered report')
        _receipt_link(artifact, status, receipt, absence=True)
        status.update(state='NOT_SENT_CONFIRMED', receipt=_save(outbox, 'receipt', receipt))
    elif action == 'CONFIRM':
        require(state in ('SENDING', 'UNKNOWN', 'DELIVERED', 'CORRECTION_REQUIRED'), 'receipt cannot skip send/readback')
        _receipt_link(artifact, status, receipt)
        if state == 'CORRECTION_REQUIRED':
            previous = read(outbox / status['receipt'])['assistant']
            actual = receipt['assistant']
            require(verbatim(artifact) and same_display_text(artifact['body'], actual['body'])
                    and {k:v for k,v in previous.items() if k != 'completed_at'} ==
                        {k:v for k,v in actual.items() if k != 'completed_at'}
                    and previous.get('completed_at') == actual.get('completed_at'),
                    'readback repair must preserve the same actual assistant body and identity')
        binding = read(outbox / status['request_binding'])
        if 'request_started_at' in binding:
            require(instant(binding['request_started_at']) <= instant(receipt['assistant'].get('completed_at', receipt['checked_at'])), 'reply predates its cloud request')
        if state == 'DELIVERED':
            require(read(outbox / status['receipt']) == receipt, 'delivered presentation is immutable')
            return
        # Preserve the actual response even if the local review needs repair.
        _save(outbox, 'observed-readback', receipt)
        errors = presentation_errors(artifact, receipt, status['attempt']['mode'])
        presentation = {'source_hash': artifact['body_hash'], 'display_hash': digest(receipt['assistant']['body'].encode()),
                        'assistant': receipt['assistant'], 'request_ref': receipt['request_ref'], 'attempt_id': receipt['attempt_id'],
                        'review': receipt['review'], 'errors': errors}
        if verbatim(artifact):
            presentation['text_match'] = ('EXACT' if artifact['body'] == receipt['assistant']['body']
                                          else 'TERMINAL_LF_ONLY' if not errors else 'MISMATCH')
        status.update(presentation=_save(outbox, 'presentation', presentation), receipt=_save(outbox, 'receipt', receipt),
                      state='CORRECTION_REQUIRED' if errors else 'DELIVERED')
        if errors:
            status['presentation_errors'] = errors
        else:
            status.pop('presentation_errors', None)
    else:
        require(False, 'unknown delivery transition')


def _legacy_transition(outbox, artifact, status, action, receipt):
    state = status['state']
    if action == 'BEGIN':
        require(state in ('PENDING', 'NOT_SENT_CONFIRMED'), 'uncertain delivery requires readback before resend')
        status.update(state='SENDING', attempts=status['attempts'] + 1)
    elif action == 'UNKNOWN':
        require(state == 'SENDING', 'no in-flight delivery')
        status['state'] = 'UNKNOWN'
    elif action in ('CONFIRM', 'NOT_SENT'):
        require(state in ('SENDING', 'UNKNOWN', 'DELIVERED'), 'receipt cannot skip send/readback')
        exact(receipt, 'schema destination checked_at visible_report_text assistant_responses record_refs absence_confirmed', 'cloud receipt')
        require(receipt['schema'] == 'BTC_CLOUD_READBACK_V4' and receipt['destination'] == artifact['destination'], 'wrong cloud readback destination')
        require(isinstance(receipt['record_refs'], list) and receipt['record_refs'], 'actual cloud record references required')
        if action == 'CONFIRM':
            require(receipt['visible_report_text'] == artifact['body'] and receipt['assistant_responses'] == ['已收录'] and receipt['absence_confirmed'] is False, 'cloud receipt does not equal frozen report')
            status['state'] = 'DELIVERED'
        else:
            require(state != 'DELIVERED' and receipt['absence_confirmed'] is True and receipt['visible_report_text'] is None and receipt['assistant_responses'] == [], 'absence not established')
            status['state'] = 'NOT_SENT_CONFIRMED'
        status['receipt'] = _save(outbox, 'receipt', receipt)
    else:
        require(False, 'unknown legacy delivery transition')


def validate_delivered(root, identity):
    root = Path(root)
    require(isinstance(identity, str) and re.fullmatch('[a-f0-9]{64}', identity), 'delivery id invalid')
    outbox = root / 'outbox' / identity
    artifact, status = read(outbox / 'artifact.json'), read(outbox / 'status.json')
    require(digest(artifact)[7:] == identity and digest(artifact['body'].encode()) == artifact['body_hash'], 'frozen artifact changed')
    require(status['state'] == 'DELIVERED', 'report has no actual confirmed delivery')
    receipt = read(outbox / status['receipt'])
    if artifact['schema'] in EDITED_SCHEMAS:
        validate_source(artifact['source'], _decision(root, artifact['decision_id']), verify_render=False)
        binding = read(outbox / status['request_binding'])
        require(binding['request_ref'] == status['attempt']['request_ref'] and binding['attempt_id'] == status['attempt']['attempt_id']
                and binding['anchor_ref'] == status['attempt']['anchor_ref'] and _request_contains_source(artifact, binding['request_body']), 'sent request journal differs from confirmed source')
        _receipt_link(artifact, status, receipt)
        if 'request_started_at' in binding:
            _validate_request_binding(artifact, status, binding)
            require(instant(binding['request_started_at']) <= instant(receipt['assistant'].get('completed_at', receipt['checked_at'])), 'reply predates its cloud request')
        require(not presentation_errors(artifact, receipt, status['attempt']['mode']), 'display review no longer validates')
        saved = read(outbox / status['presentation'])
        require(saved['assistant'] == receipt['assistant'] and saved['review'] == receipt['review'] and saved['source_hash'] == artifact['body_hash'] and saved['display_hash'] == digest(receipt['assistant']['body'].encode()) and saved['errors'] == [], 'presentation journal differs from confirmed receipt')
        if 'text_match' in saved:
            require(saved['text_match'] == ('EXACT' if artifact['body'] == receipt['assistant']['body'] else 'TERMINAL_LF_ONLY'),
                    'presentation text comparison differs from actual source and reply')
    else:
        require(receipt['schema'] == 'BTC_CLOUD_READBACK_V4' and receipt['destination'] == artifact['destination'] and receipt['visible_report_text'] == artifact['body'] and receipt['assistant_responses'] == ['已收录'] and receipt['record_refs'] and receipt['absence_confirmed'] is False, 'legacy report readback mismatch')
    return artifact, status, receipt


def transition(root, identity, action, receipt=None):
    root = Path(root)
    require(isinstance(identity, str) and re.fullmatch('[a-f0-9]{64}', identity), 'delivery id invalid')
    with locked(root):
        outbox = root / 'outbox' / identity
        artifact, status = read(outbox / 'artifact.json'), read(outbox / 'status.json')
        require(digest(artifact)[7:] == identity and digest(artifact['body'].encode()) == artifact['body_hash'], 'frozen artifact changed')
        if action in ('BEGIN', 'BEGIN_CORRECTION', 'RETRY_UNKNOWN') and status.get('attempt'):
            same_begin = (action != 'RETRY_UNKNOWN' and status['state'] in ('SENDING', 'UNKNOWN', 'DELIVERED')
                          and (action != 'BEGIN_CORRECTION' or status['attempt']['mode'] == 'CORRECTION')
                          and receipt == {k: status['attempt'][k] for k in ('schema', 'destination', 'anchor_ref', 'started_at', 'run_ref') if k in status['attempt']})
            same_retry = action == 'RETRY_UNKNOWN' and status.get('retry') and read(outbox / status['retry']) == receipt
            if same_begin or same_retry:
                return dict(status, ready_to_send=False, next_action='READ_BACK_EXISTING_ATTEMPT')
        if action == 'SUPERSEDE_PENDING':
            exact(receipt, 'schema replacement_delivery_id reason', 'pending report replacement')
            require(receipt['schema'] == 'BTC_REPORT_REPLACEMENT_V1', 'invalid replacement contract')
            replacement = read(root / 'outbox' / receipt['replacement_delivery_id'] / 'artifact.json')
            rid = artifact.get('source', {}).get('report_review', {}).get('review_id')
            review = replacement.get('source', {}).get('report_review', {})
            require(rid and rid in review.get('supersedes', []) and replacement['destination'] == artifact['destination'], 'replacement must explicitly cover the outstanding report')
            require(status['state'] in ('PENDING', 'NOT_SENT_CONFIRMED', 'SUPERSEDED'), 'uncertain dispatched attempts remain recoverable')
            _explanation(receipt['reason'], 'report replacement reason')
            status.update(state='SUPERSEDED', replacement_delivery_id=receipt['replacement_delivery_id'], replacement=_save(outbox, 'replacement', receipt))
        elif artifact['schema'] in EDITED_SCHEMAS:
            validate_source(artifact['source'], _decision(root, artifact['decision_id']), verify_render=False)
            # Late receipts address their own attempt, never the latest retry's binding.
            target_id = (receipt or {}).get('attempt_id')
            if (action in ('CONFIRM', 'BIND_REQUEST', 'NOT_SENT', 'ACKNOWLEDGE') or (action == 'UNKNOWN' and receipt)) and target_id != status.get('attempt', {}).get('attempt_id'):
                require(target_id in status.get('attempt_records', {}), 'receipt belongs to an unknown send attempt')
                previous = read(outbox / status['attempt_records'][target_id])
                _edited_transition(root, outbox, artifact, previous, action, receipt)
                status['attempt_records'][target_id] = _save(outbox, 'attempt-state', previous)
                if previous['state'] == 'DELIVERED' and (status['state'] != 'DELIVERED' or previous['attempt']['send_sequence'] > status['attempt']['send_sequence']):
                    _journal_attempt(outbox, status)
                    for key in ATTEMPT_FIELDS:
                        status.pop(key, None)
                    status.update(previous)
            else:
                _edited_transition(root, outbox, artifact, status, action, receipt)
            _journal_attempt(outbox, status)
        else:
            _legacy_transition(outbox, artifact, status, action, receipt)
        status['updated_at'] = now()
        replace(outbox / 'status.json', status)
        if status['state'] == 'DELIVERED':
            validate_delivered(root, identity)
            _advance_reported(root, identity, artifact, status)
        return (dict(status, ready_to_send=status['state'] == 'SENDING',
                     **({'request_body':request_body(artifact)} if verbatim(artifact) and status['state'] == 'SENDING' else {}))
                if action in ('BEGIN', 'BEGIN_CORRECTION', 'RETRY_UNKNOWN') else status)
