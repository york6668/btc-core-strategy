"""Mechanical draft preparation; does not infer market decisions."""
from copy import deepcopy
from datetime import timedelta, timezone
from .common import instant, pointer, require


def prepare_domain(pinned, at, trigger, replacements):
    require(trigger in ('PLAN_GENERATION', 'MANUAL_REEVALUATION', 'SCHEDULED_STRATEGY_BOUNDARY'), 'invalid real trigger')
    when = instant(at)
    value = deepcopy(pinned['canonical_domain'])
    value.update(schema='BTC_STRATEGY_DECISION_V3', bundle_phase='PRE_WRITE')
    value['runtime']['trigger_type'] = trigger
    ceiling = when.replace(second=0, microsecond=0)
    if when > ceiling:
        ceiling += timedelta(minutes=1)
    value['runtime']['pricing_as_of'] = ceiling.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M JST')
    value['observation']['observed_at'] = at
    for path, replacement in replacements.items():
        require(not path.startswith(('/account_facts', '/control', '/fingerprints')), 'derived control and facts require their dedicated validation')
        parts = path.rsplit('/', 1)
        parent = pointer(value, parts[0]) if parts[0] else value
        key = parts[1].replace('~1', '/').replace('~0', '~')
        if isinstance(parent, list):
            require(0 <= int(key) < len(parent), 'replacement index missing')
            parent[int(key)] = replacement
        else:
            require(key in parent, 'replacement path missing')
            parent[key] = replacement
    return value
