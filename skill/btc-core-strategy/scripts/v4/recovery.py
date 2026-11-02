"""Model-facing recovery views; storage.recover remains an internal inventory."""
from pathlib import Path

from .common import digest, read, require, replace


def public_inventory(value):
    """Construct a public contract, rather than subtracting known private fields."""
    return {'scope': 'PUBLIC', 'current': value.get('current'),
            'report_review_required': bool(value.get('report_review_required')),
            'counts': {key: len(value.get(key) or []) for key in (
                'reports_not_enqueued', 'report_reviews_not_enqueued',
                'report_delivery_obligations', 'delivery_recovery', 'outbox', 'staging')},
            'details_after': 'recover --run <original run.json> --run-ref <this execution reference>; requires its frozen independent market result'}


def public_care(value):
    return {'scope': 'PUBLIC', **{key: value[key] for key in (
        'enabled', 'day', 'daily_due', 'review_required', 'time_due',
        'event_check_required', 'fulfilled', 'missed_day_count') if key in value},
        'details_after': 'report-care --run <original run.json> --run-ref <this execution reference>; requires its frozen independent market result'}


def freeze_binding(run, frozen):
    return {'schema': 'BTC_RUN_MARKET_FREEZE_V1', 'run_hash': digest(run),
            'run_ref': run['run_ref'], 'root': run['root'], 'project': run['project'],
            'directory': run['directory'], 'frozen_hash': digest(frozen)}


def record_freeze(run, frozen):
    """Called only after the real market transition has produced portfolio material."""
    path = Path(run['directory']) / 'market-freeze-binding.json'
    value = freeze_binding(run, frozen)
    if path.exists():
        require(read(path) == value, 'original market freeze binding changed')
    else:
        replace(path, value)


def frozen_run(root, project, run_path, run_ref):
    """Validate the supplied original run, never the global formal-market pointer."""
    from . import run_flow
    require(run_path is not None and run_ref, 'original run and this execution run-ref required')
    require(Path(root).resolve() == (Path(project).resolve() / '.strategy/v4'),
            'recovery store must belong to this project')
    run = run_flow.read_run(run_path, root, project, resolve_aliases=True)
    require(run['run_ref'] == run_ref, 'recovery run-ref belongs to another execution')
    d = Path(run['directory'])
    context = read(d / 'sensing-context.json')
    require(context['context_id'] == run['context_id'], 'original run context changed')
    require(context.get('revision', 1) >= 5, 'run has no independent market phase')
    require((d / 'market-result.json').is_file(), 'freeze this run independent market result first')
    frozen = read(d / 'market-result.json')
    require(set(frozen) in ({'payload', 'result_hash', 'market_context_id'},
                           {'payload', 'result_hash', 'market_context_id', 'lifecycle_frozen_at'}),
            'invalid independent market freeze')
    require(frozen['result_hash'] == digest(frozen['payload']), 'independent market result changed')
    require(frozen['market_context_id'] == read(d / 'context.json')['market_context_id'],
            'independent market freeze belongs to another public context')
    binding = d / 'market-freeze-binding.json'
    if binding.exists():
        require(read(binding) == freeze_binding(run, frozen), 'original market freeze binding changed')
    else:
        require(not run.get('recovery_contract_revision'), 'independent market transition has not completed')
        # Pre-contract runs already have the original portfolio stage output.
        # A candidate/result filename alone never grants personal disclosure.
        portfolios = [d / 'portfolio-context.json', *d.glob('portfolio-context-*.json')]
        require(any(path.is_file() and read(path).get('market_result') == frozen for path in portfolios),
                'legacy run lacks its completed original portfolio transition')
    return run, frozen


def inventory(root, project, run_path=None, run_ref=None):
    from . import storage
    if run_path is None:
        return public_inventory(storage.recover(root))
    run, frozen = frozen_run(root, project, run_path, run_ref)
    return {**storage.recover(root), 'scope': 'PERSONAL_REPORT',
            'run_ref': run['run_ref'], 'market_result_hash': frozen['result_hash']}
