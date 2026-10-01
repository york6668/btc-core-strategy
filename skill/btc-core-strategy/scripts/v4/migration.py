"""Import only verifiable historical semantics; never invent an old forecast."""
from pathlib import Path
from . import domain
from .common import create, digest, facts, locked, now, read, replace, require
from .storage import _publish, current


def migrate(root, project, state, plan):
    canonical, binding = domain.load_old(state, plan)
    with locked(root):
        require(current(root) is None, 'migration already initialized')
        value = {'schema': 'BTC_DECISION_SNAPSHOT_V4', 'parent_id': None, 'created_at': now(),
                 'canonical_domain': canonical, 'assessment': None, 'historical_outlook': {'status': 'PREVIOUSLY_UNKNOWN'},
                 'source_binding': binding, 'fact_version': facts(project), 'report_required': False,
                 'migration_status': 'BASELINE_IMPORTED_DEEP_PENDING'}
        views = {'state.md': Path(state).read_bytes(), 'plan.md': Path(plan).read_bytes()}
        return _publish(root, value, views, None)


def import_delivery(root, report_path, projection_path, provider_readback_path):
    """Verify actual ordered provider records, not a locally fabricated success flag."""
    import decision_report
    root = Path(root)
    body = Path(report_path).read_text()
    projection = read(projection_path)
    errors = decision_report.validate_report_text(body, projection)
    require(not errors, 'historical report invalid: ' + '; '.join(errors))
    provider = read(provider_readback_path)
    require(provider['thread']['id'] == '6a95a45c-cb24-83ee-a64a-52eee69f4fe7', 'wrong historical cloud target')
    matching = []
    allowed_prefixes = ('', '《BTC预期评估报告》\n\n', '这是本地 Owner 的完成稿，只收录、不重算、不摘要。请仅回复“已收录”。\n')
    for turn in provider['turns']:
        items = turn['items']
        if turn['status'] != 'completed' or len(items) != 2:
            continue
        user, response = items
        if user['type'] != 'userMessage' or response['type'] != 'agentMessage' or response['text'] != '已收录':
            continue
        content = user.get('content', [])
        if len(content) != 1 or content[0].get('type') != 'text':
            continue
        if any(content[0]['text'] == prefix + body for prefix in allowed_prefixes):
            matching.append({'turn_id': turn['id'], 'message_id': user['id'], 'response_id': response['id']})
    require(len(matching) == 1, 'historical report not uniquely verified in actual cloud records')
    payload = {'schema': 'BTC_IMPORTED_DELIVERY_V4', 'body': body, 'body_hash': digest(body.encode()),
               'projection': projection, 'provider_readback': provider, 'record_refs': matching,
               'historical_outlook': 'PREVIOUSLY_UNKNOWN', 'imported_at': now()}
    identity = digest(payload)[7:]
    with locked(root):
        path = root / 'imported-delivery' / (identity + '.json')
        if not path.exists():
            create(path, payload)
        require(not (root / 'reported-current.json').exists(), 'cannot replace an existing delivered baseline with import')
        replace(root / 'imported-reported-current.json', {'identity': identity})
    return {'identity': identity, 'state': 'HISTORICAL_DELIVERY_VERIFIED', 'record_refs': matching}
