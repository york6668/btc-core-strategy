"""Phone-readable summaries of the adopted report; rendering adds no analysis."""
import re
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta, timezone
from .common import exact, require, text, pointer, instant

FORMAT='MARKET_MOBILE_V1'
MINIMAL_FORMAT='MARKET_MOBILE_V2'
FORMATS={FORMAT,MINIMAL_FORMAT}
TOKEN=re.compile(r'\{\{(/[^{}|]+)(?:\|(?:(wan|plain):([0-2])|excerpt:([^{}|\r\n]+)))?\}\}')
QUALITY={'decision_and_advice','reasoning','necessary_numbers','material_risk','phone_readability'}
INDEX_NAME=re.compile(r'(?:标普\s*500|纳指\s*100|纳斯达克\s*100|S&P\s*500|Nasdaq[- ]?100)(?!\d|[.,]\d)', re.IGNORECASE)

def evaluation_time_line(value):
    """New sectioned reports get a frozen clock; historical sources stay intact."""
    layout=value.get('layout') or {};context=value.get('context') or {}
    if layout.get('format') == 'AUTHORED_REPORT_V1':
        from .authored_report import evaluation_time
        line = evaluation_time(context)
        bound = any(b.get('scope') == 'clock' and b.get('text') == line and b.get('refs') == ['/evaluation_time']
                    for b in layout.get('bindings', []))
        return line if line and bound and layout.get('body', '').startswith(line+'\n\n') else None
    if (layout.get('format') != MINIMAL_FORMAT or not layout.get('blocks')
            or context.get('coverage_display_revision', 1) < 3):
        return None
    dt=instant(context['evaluated_at']).astimezone(timezone(timedelta(hours=9)))
    return '评估时间：'+str(dt.year)+'年'+str(dt.month)+'月'+str(dt.day)+'日 '+dt.strftime('%H:%M')+'（日本时间）'

def without_evaluation_time(body, line):
    """Remove only the controlled, bound first line; market clocks remain data."""
    if line and body and body.startswith(line+'\n\n'):
        return body[len(line)+2:]
    return body

def displayed_evaluation_time(source, receipt):
    line=evaluation_time_line(source.get('report_review', {}))
    if not line:
        return None
    if source.get('layout', {}).get('format') == 'AUTHORED_REPORT_V1':
        body = receipt.get('assistant', {}).get('body', '')
        from .delivery import same_display_text
        return line if same_display_text(source['body'], body) and body.startswith(line+'\n\n') else None
    assertion=next((c for c in source.get('constraints', []) if c['source_text']==line), None)
    if not assertion:
        return None
    bound=next((b['display_text'] for b in receipt.get('review', {}).get('bindings', [])
                if b['constraint_id']==assertion['id']), None)
    body=receipt.get('assistant', {}).get('body', '')
    return bound if bound and '\n' not in bound and '\r' not in bound and body.startswith(bound+'\n\n') else None

def render(value,decision):
    layout=value['layout'];minimal=layout.get('format')==MINIMAL_FORMAT
    sectioned = minimal and bool(layout.get('blocks'))
    exact(layout,'format lead blocks include_recommendations quality_review'+(' price_status' if minimal else '')+(' consensus_price_revision' if 'consensus_price_revision' in layout else ''),'mobile layout')
    require(layout.get('consensus_price_revision', 1) in (1, 2), 'invalid consensus price revision')
    require(layout['format'] in FORMATS and type(layout['include_recommendations']) is bool,'invalid mobile layout')
    if minimal:
        require(layout['include_recommendations'], 'minimal report uses reviewed market, risk and personal advice')
        exact(layout['price_status'],'lower_spot upper_short','minimal price status')
        require(set(layout['price_status'].values()) <= {'VALID','ADJUSTED','REVIEW_REQUIRED','WITHDRAWN'}, 'invalid price applicability')
    from .cycle import REPORT_CHECKS, SUMMARY_CHECKS, validate_report_basis
    cycle_workflow = value['context'].get('cycle_policy_revision') == 1
    expected = QUALITY | REPORT_CHECKS if cycle_workflow else QUALITY
    if value['context'].get('deep_report_policy_revision') == 1:
        expected |= SUMMARY_CHECKS
        validate_report_basis(value, value['context'])
    q=layout['quality_review'];require(isinstance(q,dict) and set(q)==expected,'complete authored expression review required')
    pending_cycle = cycle_workflow and value['context']['cycle_baseline']['status'] != 'CURRENT'
    pending_personal = (value['context'].get('market_revision') == 5 and
        value['context'].get('identity', {}).get('fact_version', decision['assessment'].get('fact_version')) != decision['assessment'].get('fact_version'))
    if pending_cycle and not minimal:
        require(not layout['include_recommendations'], 'pending cycle review cannot present old proposals as current executable advice')
    for key,explanation in q.items():
        text(explanation,key);require(len(explanation.strip())>=12,'expression review needs concrete reasoning')
    require(isinstance(layout['blocks'],list) and (minimal or layout['blocks']),'mobile report needs authored explanation and advice')
    text(layout['lead'],'mobile lead')
    for block in layout['blocks']:
        exact(block,'kind text','mobile block')
        require(block['kind'] in {'judgement','direction','condition','uncertainty','limitation','negation','amount','position','dispute_zone'},'invalid mobile meaning')
        text(block['text'],'mobile block text')
    if sectioned:
        personal_sources=('/assessment/domain/account_facts', '/assessment/domain/portfolio_decision',
                          '/assessment/portfolio_analysis', '/assessment/recommendations/portfolio')
        public_kinds = ({'judgement','direction','condition','uncertainty','limitation','negation','dispute_zone'}
                        if value['context'].get('market_revision') == 5 else {'judgement','dispute_zone'})
        for paragraph in [layout['lead']]+[b['text'] for b in layout['blocks'] if b['kind'] in public_kinds]:
            for match in TOKEN.finditer(paragraph):
                path=match.group(1)
                personal_amount = path.startswith('/assessment/recommendations/') and path.endswith(('/amount_u','/capital_u'))
                require(not path.startswith(personal_sources) and not personal_amount,
                        'market conclusion cannot bind personal portfolio data')
                if value['context'].get('market_revision') == 5:
                    require(path.startswith(('/assessment/outlook/', '/assessment/factor_assessment/',
                        '/observation/review/market_assessment/', '/observation/review/dispute_zones/',
                        '/observation/review/factor_checks/', '/metrics/', '/public_evidence/')),
                        'public market blocks require pure market references, not mixed historical prose')
    values={'assessment':decision['assessment'],'metrics':value['context']['metrics'],
            'observation':value['context'].get('observation'), 'public_evidence':value['context'].get('public_evidence')}
    if 'maintenance' in value:
        from .report_care import local
        values['maintenance']=dict(value['maintenance'],recheck_local=local(value['maintenance']['recheck_at']))
    def bind(template):
        text(template,'mobile paragraph')
        literal=TOKEN.sub('',template)
        if minimal:
            literal=INDEX_NAME.sub('',literal)
        require(not re.search(r'\d',literal),'mobile numbers must bind frozen evidence')
        def replace(m):
            p,scale,digits,excerpt=m.groups();result=pointer(values,p)
            require(not isinstance(result,(dict,list,bool)) and result is not None,'mobile reference must be a known scalar')
            if excerpt is not None:
                require(p == '/assessment/conclusion/narrative' and isinstance(result,str)
                        and excerpt.strip() and excerpt in result,
                        'mobile excerpt must occur exactly in the approved core report')
                return excerpt
            if scale:
                require(p.startswith('/metrics/'),'formal advice cannot be rounded or scaled')
                n=Decimal(str(result));require(n.is_finite(),'nonfinite market number')
                if scale=='wan':n/=10000
                return format(n.quantize(Decimal(1).scaleb(-int(digits)),rounding=ROUND_HALF_UP),',f')+('万' if scale=='wan' else '')
            if isinstance(result,(int,float)) or (isinstance(result,str) and re.fullmatch(r'-?\d+(?:\.\d+)?',result)):
                n=Decimal(str(result));require(n.is_finite(),'nonfinite number');return format(n,',f').rstrip('0').rstrip('.') if '.' in format(n,',f') else format(n,',f')
            return str(result)
        result=TOKEN.sub(replace,template)
        require('\n' not in result and '\r' not in result and '|' not in result,'use separate mobile blocks, not tables or embedded lines')
        require(not re.search(r'\d{4}-\d\d-\d\dT|UTC|取证截至|覆盖：|:::writing',result),'internal time or document wrapper in mobile report')
        return result
    templates=layout['lead']+' '+ ' '.join(b.get('text','') for b in layout['blocks'])
    if 'maintenance' in value and not minimal:
        require('/maintenance/recheck_local' in templates,'show the report next review time')
        require(any('/maintenance/triggers/'+str(i)+'/condition' in templates for i in range(len(value['maintenance']['triggers']))),'show one meaningful report revision condition')
    for module,item in decision['assessment']['domain']['portfolio_decision'].items():
        if not minimal and not pending_cycle and isinstance(item,dict) and Decimal(str(item.get('amount_u','0'))) != 0:
            require('/assessment/domain/portfolio_decision/'+module+'/amount_u' in templates,'report omits a nonzero formal adjustment')
    market=[('judgement','**'+bind(layout['lead']).strip('*')+'**')]
    risk=[];personal=[];zones=[]
    pieces=market
    for block in layout['blocks']:
        target = (market if block['kind']=='judgement' else zones if block['kind']=='dispute_zone' else personal if block['kind'] in {'position','amount'} else risk) if sectioned else pieces
        target.append((block['kind'],bind(block['text'])))
    if sectioned:
        require((risk or zones) and personal, 'sectioned report needs price/risk and personal response blocks')
        if value['context'].get('market_revision') == 5:
            latest_zones = (decision['assessment']['outlook'].get('dispute_zones', [])
                if value.get('report_basis', {}).get('mode') == 'CYCLE_SUMMARY'
                else ((value['context'].get('observation') or {}).get('review') or {}).get('dispute_zones', []))
            if any(z['status']=='ACTIVE' for z in latest_zones):
                require(zones, 'active dispute zones need their independent report item')
        if zones:
            risk += [('dispute_zone', '**多空分歧区与突破迹象**')] + zones
        # Routing uses authored block kinds, never keywords or a market scorer.
        pieces=personal if value['context'].get('market_revision') == 5 else risk
    if layout['include_recommendations']:
        from .brief_report import _number
        sides=[('lower_spot','现货参考','U投入'),('upper_short','保护空单参考','U名义额')] if minimal else [('upper_short','新增保护空单','U名义额'),('lower_spot','分批买入现货篮子','U投入')]
        for side,label,unit in sides:
            rec=decision['assessment']['recommendations'][side]
            if minimal:
                status=layout['price_status'][side]
                if status == 'WITHDRAWN' and layout.get('consensus_price_revision', 1) >= 2:
                    require(rec['status'] == 'NOT_RECOMMENDED', 'withdrawn price needs an inactive formal recommendation')
                    if not sectioned:
                        pieces.append(('condition','- '+label+'：原建议已撤回，暂无有效价位。'))
                    continue
                checked_at=max(instant(value['context']['knowledge_cutoff']),
                               instant(value['context'].get('evaluated_at', value['context']['knowledge_cutoff'])))
                expired=instant(rec['validity']['until'])<=checked_at
                tiers=rec['tiers']
                if rec['status']=='NOT_RECOMMENDED':
                    require(status in {'REVIEW_REQUIRED','WITHDRAWN'},'inactive advice cannot be presented as valid')
                    prior=value['context'].get('reported',{}).get('consensus_prices',{}).get(side)
                    if not tiers and prior:
                        tiers=prior['tiers']
                    require(status!='REVIEW_REQUIRED' or tiers,'pending consensus prices need an actual prior formal source')
                elif pending_cycle or pending_personal or expired:
                    require(status=='REVIEW_REQUIRED','unreviewed consensus prices must show pending applicability')
                else:
                    # A source gap may limit current applicability before a new
                    # cycle result exists. Never force MAIN to call it valid.
                    require(status in {'VALID','ADJUSTED','REVIEW_REQUIRED'},'active approved advice needs actual applicability')
                prices=[]
                from .recommendations import spot_label
                named_spot = side == 'lower_spot' and any('allocation_percent' in tier for tier in tiers)
                for tier in tiers:
                    price=_number(tier['price_low_usd'])
                    if Decimal(tier['price_low_usd'])!=Decimal(tier['price_high_usd']):price+='–'+_number(tier['price_high_usd'])
                    prices.append((spot_label(tier)+' ' if named_spot else '')+price)
                status_text={'VALID':'继续有效','ADJUSTED':'已调整','REVIEW_REQUIRED':'待复核','WITHDRAWN':'已撤回'}[status]
                levels='、'.join(prices)+' USDT' if prices else '暂无有效价位'
                pieces.append(('condition','- '+label+'：'+levels+'（'+status_text+'）。'))
                continue
            require(instant(rec['validity']['until'])>instant(value['context']['knowledge_cutoff']),'expired recommendations require main reassessment')
            if rec['status']=='NOT_RECOMMENDED':pieces.append(('direction','- '+label+'：当前不建议。'));continue
            for tier in rec['tiers']:
                from .recommendations import spot_label
                tier_label=spot_label(tier) if side=='lower_spot' and 'allocation_percent' in tier else label
                price=_number(tier['price_low_usd'])
                if Decimal(tier['price_low_usd'])!=Decimal(tier['price_high_usd']):price+='–'+_number(tier['price_high_usd'])
                pieces.append(('amount','- '+tier_label+'：BTC参考位置 '+price+' USDT，'+_number(tier['amount_u'])+unit+'。'))
    if sectioned:
        personal_heading='个人组合应对' if value['context'].get('coverage_display_revision', 1) >= 3 else '个人资产应对'
        pieces=[('judgement','**市场结论与依据**')]+market+[('condition','**价格与风险建议**')]+risk+[('position','**'+personal_heading+'**')]+personal
        time_line=evaluation_time_line(value)
        if time_line:
            pieces.insert(0,('deadline',time_line))
    if not minimal:
        dt=instant(value['context']['coverage']['end']).astimezone(timezone(timedelta(hours=9)))
        pieces.append(('deadline','行情截至 '+str(dt.month)+'月'+str(dt.day)+'日 '+dt.strftime('%H:%M')))
    body='\n\n'.join(p[1] for p in pieces)
    constraints=[{'id':'mobile-'+str(i),'kind':kind,'source_text':paragraph} for i,(kind,paragraph) in enumerate(pieces)]
    return body,constraints
