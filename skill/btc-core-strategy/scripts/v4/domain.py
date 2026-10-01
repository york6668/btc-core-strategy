"""Compatibility adapter: reuse v3 invariant checks, never its write/report control.

No new v4 semantics live in strategy_contract.py. The adapter uses temporary
v3 views solely to exercise proven fact, causal, zone, candidate and event guards.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import strategy_contract as legacy

from .common import require


def synthetic(canonical):
    return dict(deepcopy(canonical), schema=legacy.SCHEMA_VERSION, bundle_phase='PRE_WRITE',
                previous_fingerprints={k: canonical['fingerprints'][k] for k in ('decision_fingerprint', 'report_fingerprint')})


def load_old(state, plan):
    baseline = legacy.load_revision3_baseline(str(state), str(plan), require_owner_paths=False)
    return deepcopy(legacy.extract_projection_sidecar(baseline.state_text)['canonical_semantics']), baseline.binding


def validate(bundle, prior_canonical, project, historical_event_clock=None, *, cycle_recheck_at=None, model_market=False):
    with tempfile.TemporaryDirectory(prefix='btc-v4-domain-') as directory:
        state, plan = Path(directory) / 'state.md', Path(directory) / 'plan.md'
        old = synthetic(prior_canonical)
        state.write_text(legacy.render_projection_document(old, 'STATE'))
        plan.write_text(legacy.render_projection_document(old, 'PLAN'))
        baseline = legacy.load_revision3_baseline(str(state), str(plan), require_owner_paths=False, model_market=model_market)
        value = deepcopy(bundle)
        value['previous_projection_baseline'] = baseline.binding
        value['previous_fingerprints'] = {k: baseline.binding[k] for k in ('decision_fingerprint', 'report_fingerprint')}
        value['fingerprints'] = legacy.compute_fingerprints(value)
        value['control'] = legacy._derive_control_unchecked(value, baseline)
        paths = dict(zip(legacy.FACT_OWNER_PATHS, (Path(project) / n for n in ('资产配置.md', '操作记录.md', '纠正记录.md'))))
        snapshot = legacy.load_fact_owner_snapshot(paths, require_owner_paths=False)
        # Only the verified immutable-section replay path supplies this clock.
        # Public caller validation still uses the real clock; current deadlines
        # and facts are checked separately before accepting a carried section.
        errors = legacy._validate_bundle_internal(value, previous_baseline=baseline, fact_owner_snapshot=snapshot,
                                        require_owner_baseline=False, require_fact_owner_paths=False,
                                        _event_replay_clock=historical_event_clock, _cycle_recheck_at=cycle_recheck_at,
                                        _model_market_assessment=model_market)
        require(not errors, 'domain validation: ' + '; '.join(errors))
        return value


def canonical(bundle):
    return {k: deepcopy(bundle[k]) for k in legacy.PROJECTION_SEMANTICS_KEYS}


def validate_normalization(submitted, accepted):
    """The adapter may replace bookkeeping, never the reviewed assessment."""
    value = deepcopy(accepted)
    for key in ('previous_projection_baseline', 'previous_fingerprints', 'fingerprints', 'control'):
        require(key in value['domain'], 'missing domain normalization field')
        if key not in submitted['domain']:
            # Canonical-domain inputs do not carry the previous temporary v3
            # projection's bookkeeping. The adapter supplies it from the real
            # baseline; preserve the unchanged submitted candidate for audit.
            require(key in ('previous_projection_baseline', 'previous_fingerprints'), 'missing domain normalization field')
            value['domain'].pop(key)
        else:
            value['domain'][key] = deepcopy(submitted['domain'][key])
    require(value == submitted, 'main normalization changed reviewed content')
