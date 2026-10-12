"""Immutable, confirmed-source portfolio views and bounded net measurement.

The ledger is a projection of user-confirmed files, never an account query,
position owner, order payload or replacement for absent history.
"""
from decimal import Decimal
from pathlib import Path
from .common import create, digest, exact, facts, instant, locked, now, numbered, read, replace, require, text
from .investment import source_refs

KINDS = {'NAV', 'EXTERNAL_FLOW', 'FILL', 'FEE', 'FUNDING', 'POSITION', 'CASH', 'LIABILITY', 'COMPONENT_PNL', 'RISK_CONSTRAINTS'}
DETAILS = {
    'RISK_CONSTRAINTS':'exposure_complete cross_margin_complete funding_complete',
    'NAV':'value scope_complete costs_included cash_flows_complete',
    'EXTERNAL_FLOW':'amount timing',
    'FILL':'instrument side quantity price fee fee_currency',
    'FEE':'instrument amount', 'FUNDING':'instrument amount',
    'POSITION':'instrument quantity cost purpose', 'CASH':'amount', 'LIABILITY':'amount purpose',
    'COMPONENT_PNL':'component pnl period_start period_end',
}


def decimal(value):
    return Decimal(numbered(value))


def validate(item, project):
    exact(item, 'id kind observed_at known_at currency fact_version source_refs details', 'portfolio ledger item')
    text(item['id'], 'ledger event identity'); text(item['currency'], 'ledger original currency')
    require(item['kind'] in KINDS, 'unknown ledger kind')
    instant(item['known_at'])
    if item['observed_at'] is not None:
        require(instant(item['observed_at']) <= instant(item['known_at']), 'ledger cannot be known before the fact occurred')
    require(item['fact_version'] == facts(project), 'ledger view binds changed facts')
    source_refs(item['source_refs'], project, item['fact_version'])
    details = item['details']; exact(details, DETAILS[item['kind']], 'ledger details')
    for key in ('value', 'amount', 'pnl', 'quantity', 'price', 'fee', 'cost'):
        if key in details and details[key] is not None:
            decimal(details[key])
    if item['kind'] == 'RISK_CONSTRAINTS':
        require(item['observed_at'] is not None, 'account coverage needs an actual source snapshot time')
        for flag in details.values():
            require(flag is None or type(flag) is bool, 'coverage must be confirmed or explicitly unknown')
    if item['kind'] == 'NAV':
        require(details['value'] is None or decimal(details['value']) > 0, 'NAV must be positive or unknown')
        for key in ('scope_complete', 'costs_included', 'cash_flows_complete'):
            require(type(details[key]) is bool, 'NAV completeness must be explicitly supported')
    if item['kind'] == 'EXTERNAL_FLOW':
        require(details['timing'] in ('BEFORE_NAV', 'UNKNOWN'), 'cash flow timing must be known for time-weighting')
    if item['kind'] == 'FILL':
        require(details['side'] in ('BUY', 'SELL'), 'confirmed fill direction required')
        require(all(details[k] is None or decimal(details[k]) > 0 for k in ('quantity','price')), 'fill price and quantity must be positive or unknown')
        require(details['fee'] is None or decimal(details['fee']) >= 0, 'fee cannot be negative')
        require(details['fee'] is None or isinstance(details['fee_currency'], str), 'known fee needs its original unit')
    return item


def append(root, project, spec):
    exact(spec, 'items expected_versions', 'ledger append')
    require(isinstance(spec['items'], list) and spec['items'], 'actual confirmed ledger views required')
    require(isinstance(spec['expected_versions'], dict), 'explicit ledger concurrency versions required')
    for item in spec['items']:
        validate(item, project)
        require(instant(item['known_at']) <= instant(now()), 'cannot record a future known fact')
    require(len({i['id'] for i in spec['items']}) == len(spec['items']), 'duplicate event in append')
    base = Path(root) / 'investment/ledger'
    with locked(root):
        index = read(base/'index.json') if (base/'index.json').exists() else {}
        pending=[]
        for item in spec['items']:
            require(item['id'] in spec['expected_versions'], 'each changed event needs its expected prior view')
            previous = index.get(item['id'])
            if previous is not None:
                old=read(base/'versions'/(previous[7:]+'.json'))
                require(digest(old)==previous and old['item']['id']==item['id'],'ledger prior view damaged')
                if old['item']==item:
                    continue
                require(instant(item['known_at'])>instant(old['item']['known_at']), 'corrected view needs a later actual known time')
            require(previous == spec['expected_versions'][item['id']], 'ledger view changed concurrently')
            pending.append((item,previous))
        require(all(i['fact_version'] == facts(project) for i in spec['items']), 'facts changed during ledger append')
        # Check every batch member before creating any immutable view. Only
        # the final index replacement commits the complete append.
        for item,previous in pending:
            stored={'schema':'BTC_LEDGER_VIEW_V1','recorded_at':now(),'previous_view':previous,'item':item}
            identity=digest(stored)
            path = base/'versions'/(identity[7:]+'.json')
            if not path.exists(): create(path,stored)
            index[item['id']] = identity
        require(all(i['fact_version'] == facts(project) for i in spec['items']), 'facts changed during ledger append')
        replace(base/'index.json',index)
    return {'schema':'BTC_PORTFOLIO_LEDGER_RECEIPT_V1','views':{i['id']:index[i['id']] for i in spec['items']},
            'authority':'SOURCE_BOUND_VIEW_ONLY','position_facts_changed':False}


def records(root, as_of=None):
    base = Path(root) / 'investment/ledger'
    if not (base/'index.json').exists(): return []
    # Only committed index roots and their prior chain are authoritative.
    # An interrupted append can leave immutable orphan files; those are never
    # adopted as confirmed views merely because they exist on disk.
    items=[]
    for event_id,identity in read(base/'index.json').items():
        seen=set();selected=None
        while identity is not None:
            require(identity not in seen,'ledger version chain cannot cycle')
            seen.add(identity)
            import re
            require(isinstance(identity,str) and re.fullmatch(r'sha256:[0-9a-f]{64}',identity),'invalid committed ledger view identity')
            stored=read(base/'versions'/(identity[7:]+'.json'))
            require(digest(stored)==identity and stored['schema']=='BTC_LEDGER_VIEW_V1','ledger content damaged')
            item=stored['item'];require(item['id']==event_id,'ledger chain changed event identity')
            if selected is None and (as_of is None or max(instant(item['known_at']),instant(stored['recorded_at']))<=instant(as_of)):
                selected=item
            identity=stored['previous_view']
        if selected is not None:items.append(selected)
    return items


def measure(items, currency, start, end):
    require(instant(start) < instant(end), 'measurement period reversed')
    scoped = [i for i in items if i['currency']==currency and i['observed_at'] is not None and instant(start)<=instant(i['observed_at'])<=instant(end)]
    navs = sorted([i for i in scoped if i['kind']=='NAV'],key=lambda i:instant(i['observed_at']))
    gaps = []
    if any(i['kind'] in ('NAV','EXTERNAL_FLOW') and i['observed_at'] is None for i in items):
        gaps.append('A portfolio NAV or external cash flow has an unknown time; its effect on this period cannot be excluded.')
    require(len({instant(n['observed_at']) for n in navs})==len(navs), 'multiple NAV views at the same point need reconciliation')
    if any(i['kind']=='EXTERNAL_FLOW' and i['currency']!=currency and i['observed_at'] is not None and instant(start)<instant(i['observed_at'])<=instant(end) for i in items):
        gaps.append('A cash flow in another currency has no confirmed conversion into the measurement unit.')
    if not navs or instant(navs[0]['observed_at'])!=instant(start) or instant(navs[-1]['observed_at'])!=instant(end):
        gaps.append('Complete portfolio NAV is missing at a measurement endpoint.')
    for nav in navs:
        d=nav['details']
        if d['value'] is None or not all(d[k] for k in ('scope_complete','costs_included','cash_flows_complete')):
            gaps.append('NAV or portfolio/fee/funding/cash-flow coverage is incomplete at '+nav['observed_at'])
    output={'schema':'BTC_PORTFOLIO_MEASUREMENT_V1','result_kind':'ACTUAL_CONFIRMED_PORTFOLIO',
            'currency':currency,'period':{'start':start,'end':end},'net_gain':None,'net_time_weighted_return':None,
            'attribution':None,'status':'DATA_LIMITED','limitations':gaps,
            'source_views':[digest(i) for i in scoped],'effectiveness':'NOT_VALIDATED'}
    if gaps: return output
    flows=[i for i in scoped if i['kind']=='EXTERNAL_FLOW' and instant(i['observed_at'])>instant(start)]
    if any(i['details']['amount'] is None for i in flows):
        output['limitations'].append('An external cash flow amount is unknown.');return output
    net_gain=decimal(navs[-1]['details']['value'])-decimal(navs[0]['details']['value'])-sum((decimal(i['details']['amount']) for i in flows),Decimal(0))
    output['net_gain']=numbered(net_gain)
    checkpoint={instant(n['observed_at']) for n in navs}
    if any(i['details']['timing']!='BEFORE_NAV' or instant(i['observed_at']) not in checkpoint for i in flows):
        output['limitations'].append('A cash-flow checkpoint NAV or flow timing is missing; no invented time-weighted return.')
        return output
    growth=Decimal(1)
    for previous,current in zip(navs,navs[1:]):
        flow=sum((decimal(i['details']['amount']) for i in flows if instant(i['observed_at'])==instant(current['observed_at'])),Decimal(0))
        before_flow=decimal(current['details']['value'])-flow
        require(before_flow>=0,'cash flow exceeds measured pre-flow NAV')
        growth*=before_flow/decimal(previous['details']['value'])
    output['net_time_weighted_return']=numbered(growth-1)
    components=[i for i in scoped if i['kind']=='COMPONENT_PNL' and i['details']['period_start']==start and i['details']['period_end']==end]
    if components and all(i['details']['pnl'] is not None for i in components):
        values={}
        for item in components:
            key=item['details']['component'];values[key]=values.get(key,Decimal(0))+decimal(item['details']['pnl'])
        residual=net_gain-sum(values.values(),Decimal(0))
        output['attribution']={'kind':'PROFIT_CONTRIBUTION_NOT_CAUSAL_FACTOR_ATTRIBUTION',
             'basis':'NET_PNL_OVER_STARTING_NAV_NOT_TWR_ATTRIBUTION',
             'contributions':{k:numbered(v/decimal(navs[0]['details']['value'])) for k,v in values.items()},
             'residual_pnl':numbered(residual),'status':'RECONCILED' if residual==0 else 'INCOMPLETE'}
    else:
        output['limitations'].append('Asset/strategy contributions are not yet supported by a complete reconciled ledger.')
    output['status']='MEASURED_NOT_EFFECTIVENESS_VALIDATED'
    return output


def context(root):
    items=records(root)
    base=Path(root)/'investment/ledger'
    index=read(base/'index.json') if (base/'index.json').is_file() else {}
    views=[]
    for identity in index.values():
        path=base/'versions'/(identity[7:]+'.json')
        stored=read(path)
        views.append({'view_id':identity,'record_path':str(path),'recorded_at':stored['recorded_at'],
                      'previous_view':stored['previous_view'],'item':stored['item']})
    return {'authority':'CONFIRMED_FILE_VIEWS','item_count':len(items),'committed_views':views,
            'measurement_interface':{'command':'portfolio-performance','required_inputs':['currency','start','end','as_of'],
                                     'prerequisite':'Complete period NAV, external flows, currencies and net-cost coverage; missing history remains unknown.'},
            'fact_versions':list({digest(i['fact_version']):i['fact_version'] for i in items}.values()),
            'kinds':sorted({i['kind'] for i in items}), 'performance_status':'NOT_MEASURED' if not items else 'REQUIRES_COMPLETE_PERIOD_DATA',
            'strategy_advice_is_not_execution':True}


def constraints(root, project, at):
    """Confirmed completeness, not inferred safety, cash, size or net direction."""
    version = facts(project)
    result = {'fact_version': version, 'record_id': None,
              'exposure_complete': None, 'cross_margin_complete': None, 'funding_complete': None}
    views = records(root, at)
    candidates = [v for v in views if v['kind'] == 'RISK_CONSTRAINTS'
                  and v['fact_version'] == version and v['observed_at'] is not None]
    if not candidates: return result
    chosen = max(candidates, key=lambda v: instant(v['observed_at']))
    changed = [v for v in views if v['kind'] in ('POSITION','FILL','CASH','LIABILITY','FEE','FUNDING','EXTERNAL_FLOW')
               and (v['observed_at'] is None or instant(v['observed_at']) > instant(chosen['observed_at']))]
    if changed: return result
    validate(chosen, project)
    result.update(chosen['details'])
    result['record_id'] = digest(chosen)
    return result
