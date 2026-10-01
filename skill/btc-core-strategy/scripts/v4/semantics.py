"""Compare reviewed concepts, never prose or collector timestamps."""
from copy import deepcopy
import decision_report
from .common import digest, numbered


def stable(assessment):
    domain = assessment['domain']
    result = {
        'decision': decision_report.semantic_projection(domain['report_semantics']),
        'outlook': assessment['outlook']['semantic'],
        'robustness': assessment['robustness']['semantic'],
        'portfolio': {k: {field: ({q: x for q, x in item.items() if q != 'rationale'} if field == 'scenario_budget' and item else item)
                         for field, item in v['semantic'].items()} for k, v in assessment['portfolio_analysis'].items()},
        'facts': assessment['fact_version'],
    }
    if assessment.get('revision', 1) >= 2:
        from .recommendations import stable as recommendation_semantics
        result['recommendations'] = recommendation_semantics(assessment['recommendations'])
    if assessment.get('revision', 1) >= 3:
        result['conclusion'] = assessment['conclusion']['business_meaning']
    if assessment.get('revision', 1) >= 5:
        def market_meaning(value):
            if isinstance(value, dict):
                return {k: market_meaning(v) for k,v in value.items() if k not in ('as_of','refs','counter_refs')}
            if isinstance(value, list):
                return [market_meaning(v) for v in value]
            return value
        result['market_assessment'] = market_meaning(assessment['outlook']['market_assessment'])
        result['dispute_zones'] = market_meaning(assessment['outlook']['dispute_zones'])
    def canonical(value, key=''):
        if isinstance(value, dict):
            if key == 'horizon' and value.get('unit') in ('HOURS', 'DAYS', 'WEEKS'):
                multiplier = {'HOURS': 1, 'DAYS': 24, 'WEEKS': 168}[value['unit']]
                return {'unit': 'HOURS', 'minimum': value['minimum'] * multiplier, 'maximum': value['maximum'] * multiplier}
            return {k: canonical(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return sorted((canonical(v) for v in value), key=digest)
        if (key.endswith('_u') or key.endswith('_usd')) and value is not None:
            return numbered(value)
        return value
    return canonical(result)


def compare(current, previous, migration_reference=None, material_new_outlook=False):
    left, right = stable(current), stable(previous) if previous else None
    if right is None and migration_reference is not None:
        changes = []
        old = decision_report.semantic_projection(migration_reference['report_semantics'])
        # Both legacy semantic projections use their canonical number representation.
        if decision_report.report_fingerprint(current['domain']['report_semantics']) != decision_report.report_fingerprint(migration_reference['report_semantics']):
            changes.append('decision')
        if material_new_outlook:
            changes.append('new_material_outlook')
        return {'report_required': bool(changes), 'changed': changes, 'semantic_hash': digest(left)}
    if right is not None:
        # Retirement of an empty legacy budget is schema migration, not market news.
        for result in (left, right):
            for module in result['portfolio'].values():
                if module.get('scenario_budget') is None:
                    module.pop('scenario_budget', None)
    # Absence is unknown, never an invented historical conclusion. MAIN must
    # compare the actual old display in the independent report review.
    unknown = ['conclusion'] if right is not None and 'conclusion' in left and 'conclusion' not in right else []
    changes = [key for key in left if (key not in unknown or material_new_outlook)
               and (right is None or key not in right or digest(left[key]) != digest(right[key]))]
    return {'report_required': bool(changes), 'changed': changes, 'unknown': unknown, 'semantic_hash': digest(left)}
