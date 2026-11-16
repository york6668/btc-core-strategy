"""Cycle argument -> relative opportunity -> personal choice -> expression.

Models own financial judgements. This module checks input lineage, time and
decision references, never infers a stance, scores prose or executes an order.
Historical snapshots without this design remain readable as historical results.
"""
from copy import deepcopy
from pathlib import Path

from .common import create, digest, exact, instant, read, require, text
from .report_intent import OPPORTUNITY_CHOICE_REVIEW, MARKET_HANDLING

REVISION = 2
PUBLIC_INSTRUCTIONS = (
    '从本轮原始公开证据独立建立未来1–3个月的周期论证，再比较现价下各资产与等待的机会；不是继承旧周期标签后寻找支持，也不是填写报告六个栏目。'
    'cycle_case是本轮公开决策主干：解释关键驱动如何经融资、需求供给、盈利或其他适用渠道传导，核对已观察到的响应与市场已计价程度，比较最强竞争解释和最强反证。主线不明可以明确UNCLEAR，不以旧结论或个人仓位填空。'
    '主线成立与现价值得参与分别判断。比较至少两个真实备选（含等待），各自收益来源、下行风险、成本及关键限制，形成有根据的相对选择；价格透支可以令主线成立而参与不划算。缺数据不造目标价、概率或风险收益比。'
    'cycle_case的窗口是1–3个月的前瞻问题，不是保证状态维持的期限。旧研究只在prior_review中作为待核假设，反证可推翻旧论点；保留其原身份和时间，不能让历史一致性成为新结论约束。'
    '既有周度研究、预警和其他相关资料按覆盖范围交叉核对，说明同源依赖、重要分歧及其影响；没有独立后续证据就如实说明，引用数量不是投票。关键事件只复核是否改变驱动、定价或选择，不新建报告追踪/评分体系。'
    '底层market_assessment及factor字段承载来源与兼容读接口，须与cycle_case一致，不能让旧模块、区域或原期限反过来限定本轮问题。公开结果先冻结，之后才读取工作站个人配置。'
)
PERSONAL_INSTRUCTIONS = (
    'allocation/risk/investment结果的market_result_hash填写正常输入portfolio_result_binding.market_result_hash，即market_content_hash公开语义哈希；外层研究整包market_result_hash及research_phase_result_hash包含market_report，不是该字段。两种身份保留，不混用，也不用同一个误填参数自证局部校验通过。'
    '以冻结cycle_case及其机会比较为公开前提，再读取工作站持久化的最新已知整体实际配置、目标和风险约束。actual、target、recommendation、execution是不同语义。'
    'allocation_choice把动态配置与持仓纪律作为同一个决定：比较保留现状、调整和等待中的适用备选，给出本轮总体取舍、所选候选依据及改变条件；不能仅罗列如果怎样就怎样。'
    '后续部分更新只更新有证据变化的部分，沿用其余最后已知事实并保留时间/来源；冲突或时效不足限制依赖它的结论，不能清空已知配置、假定已平仓或永久沿用原规模。没有肯定持仓证据的项目才可按未持有作分析假设。'
    '目标不是已成交，建议不更新事实。未知净值、可用资金、规模或保证金不造精确权重/数量；方向性选择与可执行范围分别判断，不因缺数放弃能完成的机会取舍。配置目标、实际风险和持仓纪律必须互相一致；市场论点不受个人输入改变。'
    'V2的exposure_decisions逐个真实决策范围区分新增风险与存量管理，先说明当前敞口、目标风险及本次选择准则，再比较同一周期路径下候选的收益、损失、成本和等待机会成本。'
    '准则可以是相对价值、已知风险约束、配置用途或有依据的稳健取舍；不必证明所有情景必胜。不把建议的稳健准则伪称用户既有约束。条件偏好写conditional_preferences，不进入本轮selected_ids，不能为选它把ACTION缺口降成VALUE。'
    '不宜新增、短期反弹、未知预算或不能证明续持最好，单独都不能推出减险，也不能推出续持。列出得失不等于完成排序；只有所选准则及证据支持时RANKED，依赖明确前提时CONDITIONAL，证据不足则UNRANKED且不选择该范围动作。'
    '未决只约束其依赖范围，其他有据选择继续；目标风险可定性或未决，不造精确比例。事实引用、候选和周期路径必须可追溯，报告不得把条件排序写成无条件动作。'
)
REPORT_INSTRUCTIONS = (
    '报告是本时点决策输出，由cycle_case和allocation_choice的已完成取舍组织，而非旧六项栏目或内部字段堆叠。'
    '先写实际评估时间，再以简短加粗的完整结论领起自然段；段落随本轮重要问题变化。突出未来1–3个月周期主线、市场已计价程度、资产相对机会及参与/等待的实际取舍，随后用必要篇幅交代配置与持仓纪律、改变判断的条件。个人持仓不占据主体。'
    '来源、时点、重要分歧与限制只保留会改变本次理解或执行的部分；程序审批、修订过程、票据、旧问题清理及内部测试不进正文。未形成决策返回所属阶段，成稿不能代替决策或增加未经分析的操作。'
    '现有云端推送及关键事件可触发下一次复盘；报告本身不建立持续追踪、评分或新调度。'
    '原六点内容基线持续有效：定价和方向把握、1–3月主导及竞争路径、机会风险收益、配置持仓纪律、交叉分歧、失效与对应应对；自由组织版面不删除这些投资问题。'
    '消费exposure_decisions的实际排序范围、选择准则和依赖；不把新增风险等待改成存量减仓，不把UNRANKED改成续持或减险，不把CONDITIONAL改成无条件选择。'
)
PUBLIC_INSTRUCTIONS += (
    '原六点是持续有效的内容责任而非固定栏目。V2 path_comparison必须比较真实主路径和竞争路径，即使总体UNCLEAR，也解释机制传导、延续和早期转折证据及其区别、现价体现与反证。'
    'path_comparison的ranking为RANKED或UNRANKED，preferred_ids与排序一致；不足以排序时说明具体鉴别缺口，不免除比较，不强造中性或概率。'
)
PUBLIC_INSTRUCTIONS += OPPORTUNITY_CHOICE_REVIEW
PERSONAL_INSTRUCTIONS += OPPORTUNITY_CHOICE_REVIEW
REPORT_INSTRUCTIONS += MARKET_HANDLING


def revision(case):
    return 2 if (case or {}).get('schema') == 'BTC_CYCLE_DECISION_V2' else 1


def public_case(outlook):
    return ((outlook or {}).get('market_assessment') or {}).get('cycle_case')


def _refs(values, resolve, label, required=False):
    require(isinstance(values, list) and (bool(values) or not required), label + ' references required')
    for ref in values:
        resolve(ref)


def register_case_source(root, case, source):
    """Index an already saved original PUBLIC artifact, never a new opinion.

    The existing research registration owns author/guard verification. This
    small index only lets later evidence re-read its exact original argument;
    it has no current pointer, review clock, score or scheduled execution.
    """
    root = Path(root).resolve()
    source = Path(source).resolve()
    require(source.parent == root / 'research/independent', 'cycle source must be the original independent artifact')
    from . import research
    value = research._load(root, 'independent', 'sha256:' + source.stem)
    require(public_case(value['market_result']['outlook']) == case, 'original PUBLIC source has a different cycle argument')
    path = root / 'research/public_case_sources' / (digest(case)[7:] + '.json')
    if path.exists():
        validate_case_source(root, case)
        return
    case_resolver(root, value, case)
    create(path, {'schema':'BTC_PUBLIC_CASE_SOURCE_V1', 'case_hash':digest(case),
                  'independent_id':value['independent_id'], 'source_hash':digest(source.read_bytes())})


def validate_case_source(root, case):
    root = Path(root)
    index = root / 'research/public_case_sources' / (digest(case)[7:] + '.json')
    require(index.is_file() and not index.is_symlink(), 'actual immutable PUBLIC case source is unavailable')
    entry = read(index)
    exact(entry, 'schema case_hash independent_id source_hash', 'original public case source')
    require(entry['schema'] == 'BTC_PUBLIC_CASE_SOURCE_V1' and entry['case_hash'] == digest(case), 'cycle source identity changed')
    from . import research
    source = root / 'research/independent' / (entry['independent_id'][7:] + '.json')
    require(source.is_file() and not source.is_symlink(), 'original PUBLIC artifact is unavailable')
    value = research._load(root, 'independent', entry['independent_id'])
    require(digest(source.read_bytes()) == entry['source_hash']
            and public_case(value['market_result']['outlook']) == case, 'frozen cycle source changed')
    return value


def case_resolver(root, original, case):
    """The case's own input archive, not the caller's current batch."""
    from . import evidence
    identity = original.get('cycle_case_evidence_id')
    import re
    require(isinstance(identity, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', identity),
            'original PUBLIC case evidence binding is unavailable')
    directory = Path(root) / 'evidence' / identity[7:]
    require(directory.is_dir() and not directory.is_symlink(), 'original PUBLIC evidence archive is unavailable')
    package = evidence.validate(read(directory / 'evidence.json'), directory)
    require(package['evidence_id'] == identity and instant(package['cutoff']) == instant(case['knowledge_cutoff']),
            'original PUBLIC evidence identity or cutoff changed')
    return evidence.forward_resolver(package, directory)


def validate_public(value, resolve, cutoff, root=None):
    v2 = revision(value) >= 2
    exact(value, 'schema as_of knowledge_cutoff horizon thesis opportunities preferred_ids comparison prior_review crosschecks events' + (' path_comparison' if v2 else ''), 'cycle decision')
    require(value['schema'] in ('BTC_CYCLE_DECISION_V1', 'BTC_CYCLE_DECISION_V2'), 'unsupported cycle decision design')
    require(instant(value['knowledge_cutoff']) <= instant(cutoff), 'cycle decision cannot use evidence later than the available package')
    if instant(value['knowledge_cutoff']) < instant(cutoff):
        require(root is not None, 'older cycle requires its actual immutable PUBLIC source')
        original = validate_case_source(root, value)
        resolve = case_resolver(root, original, value)
    require(instant(value['as_of']) >= instant(value['knowledge_cutoff']), 'decision formation cannot precede its frozen evidence')
    h = value['horizon']
    exact(h, 'start end basis', 'cycle horizon')
    require(instant(h['start']) == instant(value['as_of']), 'cycle horizon must start at its real formation time')
    days = (instant(h['end']) - instant(h['start'])).total_seconds() / 86400
    require(28 <= days <= 93, 'cycle thesis must answer the requested 1–3 month horizon')
    text(h['basis'], 'economic horizon basis, not a guarantee or review schedule')
    t = value['thesis']
    exact(t, 'status conclusion confidence drivers priced_in strongest_alternative strongest_falsifier', 'cycle thesis')
    require(t['status'] in ('ESTABLISHED', 'UNCLEAR'), 'cycle thesis may explicitly remain unclear')
    for k in ('conclusion', 'confidence', 'priced_in'):
        text(t[k], 'cycle thesis ' + k)
    require(isinstance(t['drivers'], list), 'cycle drivers must be a list')
    require(t['status'] != 'ESTABLISHED' or t['drivers'], 'established thesis needs an evidence chain')
    for driver in t['drivers']:
        exact(driver, 'mechanism transmission observed_response refs counter_refs', 'cycle driver')
        for k in ('mechanism', 'transmission', 'observed_response'):
            text(driver[k], 'cycle driver ' + k)
        _refs(driver['refs'], resolve, 'driver', True)
        _refs(driver['counter_refs'], resolve, 'driver counter')
    for k in ('strongest_alternative', 'strongest_falsifier'):
        item = t[k]
        exact(item, 'argument implication refs', k)
        text(item['argument'], k + ' argument')
        text(item['implication'], k + ' decision effect')
        _refs(item['refs'], resolve, k)
    require(isinstance(value['opportunities'], list) and len(value['opportunities']) >= 2,
            'compare actual asset opportunities and waiting')
    ids, waits = set(), 0
    for item in value['opportunities']:
        exact(item, 'id asset stance return_basis downside net_cost limitation rationale refs', 'relative opportunity')
        require(item['id'] not in ids, 'duplicate public opportunity')
        ids.add(item['id'])
        require(item['stance'] in ('PARTICIPATE', 'WAIT', 'AVOID', 'UNRESOLVED'), 'invalid public opportunity stance')
        waits += item['stance'] == 'WAIT'
        for k in ('id', 'asset', 'return_basis', 'downside', 'net_cost', 'limitation', 'rationale'):
            text(item[k], 'opportunity ' + k)
        _refs(item['refs'], resolve, 'opportunity')
    require(waits, 'waiting is an explicit opportunity-cost alternative')
    selected = value['preferred_ids']
    require(isinstance(selected, list) and (bool(selected) or v2) and len(set(selected)) == len(selected) and set(selected) <= ids,
            'public relative choice must name assessed opportunities')
    require(all(x['stance'] not in ('AVOID', 'UNRESOLVED') for x in value['opportunities'] if x['id'] in selected),
            'unresolved or avoided opportunities cannot be preferred')
    text(value['comparison'], 'evidence-based relative public choice')
    require(isinstance(value['prior_review'], list), 'prior review list required')
    for item in value['prior_review']:
        exact(item, 'prior_id claim outcome reason refs', 'prior thesis review')
        require(item['outcome'] in ('SUPPORTED', 'WEAKENED', 'REFUTED', 'UNRESOLVED', 'NOT_APPLICABLE'), 'invalid prior thesis outcome')
        for k in ('prior_id', 'claim', 'reason'):
            text(item[k], 'prior review ' + k)
        _refs(item['refs'], resolve, 'prior review')
    require(isinstance(value['crosschecks'], list), 'crosschecks must be a list')
    for item in value['crosschecks']:
        exact(item, 'source origin_group as_of finding disagreement influence refs', 'crosscheck')
        require(instant(item['as_of']) <= instant(value['knowledge_cutoff']), 'crosscheck exceeds snapshot')
        for k in ('source', 'origin_group', 'finding', 'disagreement', 'influence'):
            text(item[k], 'crosscheck ' + k)
        _refs(item['refs'], resolve, 'crosscheck', True)
    require(isinstance(value['events'], list), 'event reconsiderations must be a list')
    for item in value['events']:
        exact(item, 'event occurred_at effect decision_changed reason refs', 'event reconsideration')
        require(instant(item['occurred_at']) <= instant(value['knowledge_cutoff']), 'future event cannot be observed evidence')
        require(type(item['decision_changed']) is bool, 'event impact needs a real judgement')
        for k in ('event', 'effect', 'reason'):
            text(item[k], 'event ' + k)
        _refs(item['refs'], resolve, 'event', True)
    if v2:
        _validate_paths(value['path_comparison'], resolve)
        require(t['status'] != 'ESTABLISHED' or value['path_comparison']['ranking'] == 'RANKED',
                'established cycle needs its actual preferred path, not an unranked comparison')
    return value


def _validate_paths(value, resolve):
    exact(value, 'paths ranking preferred_ids basis unresolved', 'cycle path comparison')
    require(isinstance(value['paths'], list) and len(value['paths']) >= 2, 'compare a working cycle path and a real competitor even when unclear')
    ids = set()
    for path in value['paths']:
        exact(path, 'id mechanism transmission continuation early_turn priced_in invalidation implications refs counter_refs', 'cycle path')
        for key in ('id', 'mechanism', 'transmission', 'continuation', 'early_turn', 'priced_in', 'invalidation', 'implications'):
            text(path[key], 'cycle path ' + key)
        require(path['id'] not in ids, 'duplicate cycle path')
        ids.add(path['id'])
        _refs(path['refs'], resolve, 'cycle path', True)
        _refs(path['counter_refs'], resolve, 'cycle path counter')
    require(value['ranking'] in ('RANKED', 'UNRANKED'), 'invalid cycle ranking')
    preferred = value['preferred_ids']
    require(isinstance(preferred, list) and len(set(preferred)) == len(preferred) and set(preferred) <= ids, 'cycle ranking must use actual paths')
    require(bool(preferred) == (value['ranking'] == 'RANKED'), 'unranked paths cannot claim a preferred path')
    text(value['basis'], 'cycle relative judgement, not a field-completion claim')
    require(isinstance(value['unresolved'], list), 'cycle ranking limits required')
    for item in value['unresolved']:
        text(item, 'cycle unresolved discriminant')
    require(value['ranking'] != 'UNRANKED' or value['unresolved'], 'unranked paths need their actual missing discriminant')


def validate_personal(choice, case, investment, version, project=None):
    v2 = revision(case) >= 2
    from .position_analysis import directional
    scoped = directional(investment)
    exact(choice, 'schema public_case_hash fact_version actual_configuration target_configuration constraints selected_ids allocation_rationale holding_discipline change_conditions' + (' exposure_decisions' if v2 else '') + (' analysis_scope price_baseline' if scoped else ''), 'allocation choice')
    if scoped:
        from .position_analysis import validate_personal_refs
        validate_personal_refs(choice, investment['policy'])
        require(choice['analysis_scope'] == investment['analysis_scope'] and
                choice['price_baseline'] == investment['policy']['economic_inputs']['baseline'],
                'joint choice must retain the exact opening-price analysis scope and source meaning')
    require(choice['schema'] == ('BTC_ALLOCATION_CHOICE_V2' if v2 else 'BTC_ALLOCATION_CHOICE_V1'), 'unsupported personal decision design')
    require(choice['public_case_hash'] == digest(case), 'allocation must consume the exact frozen public argument')
    require(choice['fact_version'] == version == investment['fact_version'], 'allocation and discipline must use the same persistent facts')
    require(instant(investment['decided_at']) >= instant(case['as_of']), 'personal choice must form after its actual frozen public argument')
    for k in ('actual_configuration', 'target_configuration', 'constraints', 'allocation_rationale'):
        text(choice[k], 'joint allocation ' + k)
    require(choice['selected_ids'] == investment['selected_ids'], 'allocation and holding decision select different candidates')
    require(isinstance(choice['holding_discipline'], list), 'holding discipline must be a list')
    if not investment['selected_ids']:
        require(investment.get('status') == 'UNDETERMINED' and investment.get('gaps'), 'empty personal selection needs real unresolved decision dependencies')
        require(not choice['holding_discipline'], 'an unresolved arrangement cannot invent a selected holding instruction')
    selected = set(investment['selected_ids'])
    for item in choice['holding_discipline']:
        exact(item, 'candidate_id decision basis change_condition execution_limit', 'holding discipline')
        require(item['candidate_id'] in selected, 'holding discipline must belong to a selected arrangement')
        for k in ('decision', 'basis', 'change_condition', 'execution_limit'):
            text(item[k], 'holding discipline ' + k)
    require({x['candidate_id'] for x in choice['holding_discipline']} == selected,
            'every selected arrangement needs its linked holding discipline')
    require(isinstance(choice['change_conditions'], list) and choice['change_conditions'], 'joint choice needs changing conditions')
    for condition in choice['change_conditions']:
        text(condition, 'joint choice changing condition')
    if v2:
        _validate_exposures(choice['exposure_decisions'], case, investment, version, project)
    return choice


def _validate_exposures(values, case, investment, version, project):
    """Check causal links and unresolved scope, never choose a financial action.

    The actual reviewer must assess whether the evidence supports the criterion
    and the ranking. A filled comparison or RANKED label is not such proof.
    """
    require(isinstance(values, list) and values, 'personal choice needs scope-specific exposure comparisons')
    candidates = {c['id']: c for c in investment['candidates']}
    paths = {p['id'] for p in case['path_comparison']['paths']}
    opportunities = {p['id'] for p in case['opportunities']}
    gaps = {g['id']: g for g in investment.get('gaps', [])}
    scopes, covered, selected = set(), set(), set()
    for value in values:
        exact(value, 'scope purpose current_exposure target_risk opportunity_ids criterion comparisons gap_effects ranking selected_ids conditional_preferences basis change_conditions', 'exposure decision')
        text(value['scope'], 'exposure scope')
        require(value['scope'] not in scopes, 'duplicate exposure scope')
        scopes.add(value['scope'])
        require(value['purpose'] in ('INCREMENTAL', 'INCUMBENT', 'JOINT'), 'distinguish incumbent management from new participation')
        actual, target = value['current_exposure'], value['target_risk']
        exact(actual, 'state basis', 'actual risk exposure')
        require(actual['state'] in ('FLAT', 'NET_LONG', 'NET_SHORT', 'MIXED', 'UNKNOWN'), 'invalid actual exposure state')
        text(actual['basis'], 'actual exposure source and time boundary')
        exact(target, 'status description basis', 'target risk exposure')
        require(target['status'] in ('DETERMINED', 'CONDITIONAL', 'UNRESOLVED'), 'invalid target exposure status')
        text(target['description'], 'target risk, not an inferred trade')
        text(target['basis'], 'target exposure basis and limits')
        require(isinstance(value['opportunity_ids'], list) and value['opportunity_ids'] and set(value['opportunity_ids']) <= opportunities, 'exposure must consume actual public opportunities')
        criterion = value['criterion']
        exact(criterion, 'kind origin rule fact_refs', 'exposure selection criterion')
        require(criterion['kind'] in ('RELATIVE_VALUE', 'RISK_CONSTRAINT', 'MANDATE', 'ROBUSTNESS', 'UNRESOLVED'), 'invalid decision criterion')
        require(criterion['origin'] in ('CONFIRMED', 'ANALYTICAL_PROPOSAL', 'UNRESOLVED'), 'criterion origin must separate confirmed policy and new advice')
        text(criterion['rule'], 'actual decision criterion and why it applies')
        require(isinstance(criterion['fact_refs'], list), 'criterion fact references required')
        if criterion['origin'] == 'CONFIRMED':
            require(criterion['fact_refs'], 'confirmed criterion needs actual confirmed evidence')
        if criterion['fact_refs']:
            if project is not None:
                from .investment import source_refs
                source_refs(criterion['fact_refs'], project, version)
            else:
                for ref in criterion['fact_refs']:
                    exact(ref, 'file line_start line_end quote', 'criterion fact reference')
                    require(ref['file'] in version and version[ref['file']] is not None, 'criterion must bind available persistent facts')
                    text(ref['quote'], 'criterion literal fact quote')
        require(isinstance(value['comparisons'], list) and len(value['comparisons']) >= 2, 'compare retaining/current arrangement with an applicable alternative')
        compared = set()
        for item in value['comparisons']:
            exact(item, 'candidate_id path_outcomes opportunity_cost dependencies', 'exposure alternative')
            cid = item['candidate_id']
            require(cid in candidates and cid not in compared, 'exposure comparison candidate missing or duplicate')
            compared.add(cid)
            require(isinstance(item['dependencies'], list) and set(item['dependencies']) == set(candidates[cid]['dependencies']) and set(item['dependencies']) <= gaps.keys(), 'exposure comparison must retain the actual candidate dependencies')
            text(item['opportunity_cost'], 'retaining, changing or waiting opportunity cost')
            require(isinstance(item['path_outcomes'], list), 'cycle-conditioned alternatives required')
            seen = set()
            for outcome in item['path_outcomes']:
                exact(outcome, 'path_id benefit downside net_cost', 'cycle-conditioned exposure outcome')
                require(outcome['path_id'] in paths and outcome['path_id'] not in seen, 'alternative uses an unknown or duplicate cycle path')
                seen.add(outcome['path_id'])
                for key in ('benefit', 'downside', 'net_cost'):
                    text(outcome[key], 'exposure path ' + key)
            require(seen == paths, 'compare alternatives across the same working and competing cycle paths')
        require(any(candidates[c]['is_current'] for c in compared), 'incumbent/current arrangement must enter the comparison')
        covered |= compared
        relevant = {gid for gid, gap in gaps.items() if set(gap['affected_candidates']) & compared}
        require(isinstance(value['gap_effects'], list), 'exposure ranking must assess actual related gaps')
        assessed, ordering_gaps = set(), set()
        for item in value['gap_effects']:
            exact(item, 'gap_id effect basis', 'scope gap effect')
            require(item['gap_id'] in relevant and item['gap_id'] not in assessed, 'scope gap missing or duplicate')
            assessed.add(item['gap_id'])
            require(item['effect'] in ('RANKING', 'IMPLEMENTATION_ONLY', 'NOT_MATERIAL'), 'unknown dependency effect')
            text(item['basis'], 'why this gap can or cannot reverse the relative choice')
            if item['effect'] == 'RANKING':
                ordering_gaps.add(item['gap_id'])
        require(assessed == relevant, 'policy naming cannot omit gaps in the same exposure comparison')
        require(value['ranking'] in ('RANKED', 'CONDITIONAL', 'UNRANKED'), 'invalid exposure ranking')
        preferred = value['selected_ids']
        require(isinstance(preferred, list) and len(set(preferred)) == len(preferred) and set(preferred) <= compared, 'exposure selection must follow its actual comparison')
        require(bool(preferred) == (value['ranking'] == 'RANKED'), 'conditional or unranked exposure cannot silently become a current hold, wait or reduce selection')
        conditionals = value['conditional_preferences']
        require(isinstance(conditionals, list) and bool(conditionals) == (value['ranking'] == 'CONDITIONAL'), 'conditional preference must be separate from current selections')
        condition_ids = set()
        for item in conditionals:
            exact(item, 'candidate_id premise otherwise', 'conditional exposure preference')
            cid = item['candidate_id']
            require(cid in compared and cid not in condition_ids, 'conditional preference needs an actual compared alternative')
            require(cid not in investment['selected_ids'], 'conditional preference cannot be selected through another exposure scope')
            condition_ids.add(cid)
            require(candidates[cid]['verdict'] in ('CONDITIONAL', 'UNDETERMINED'), 'hypothetical exposure preference is not a supported current arrangement')
            require(ordering_gaps <= set(candidates[cid]['dependencies']), 'conditional policy must carry the gaps that can reverse its ranking')
            text(item['premise'], 'unconfirmed premise, not a current action')
            text(item['otherwise'], 'decision implication if the premise fails')
        if preferred or conditionals:
            require(target['status'] != 'UNRESOLVED' and criterion['kind'] != 'UNRESOLVED' and criterion['origin'] != 'UNRESOLVED', 'ranked exposure needs a supported target and applicable selection criterion')
        if value['ranking'] == 'RANKED':
            require(target['status'] == 'DETERMINED', 'conditional target cannot grant unconditional ranking')
            require(not ordering_gaps, 'ordering uncertainty cannot grant unconditional exposure preference')
        for cid in preferred:
            require(cid in investment['selected_ids'], 'exposure selection differs from the joint investment choice')
            require(ordering_gaps <= set(candidates[cid]['dependencies']), 'conditional policy must carry the gaps that can reverse its ranking')
            require(not any(gaps[g]['impact'] == 'ACTION' for g in candidates[cid]['dependencies']), 'action-level unknown cannot select a scoped action')
        selected |= set(preferred)
        text(value['basis'], 'why this criterion orders alternatives, or why they remain unranked')
        require(isinstance(value['change_conditions'], list) and value['change_conditions'], 'invalidation must reach its affected exposure decision')
        for condition in value['change_conditions']:
            text(condition, 'exposure changing condition and corresponding response')
    require(selected == set(investment['selected_ids']), 'every actual selected candidate needs its scoped evidence-based ranking')
    require(covered == set(candidates), 'all actual alternatives must retain their decision comparison scope')


def validate_link(assessment, project=None):
    case = public_case(assessment.get('outlook'))
    personal = (assessment.get('recommendations', {}).get('portfolio') or {}).get('investment_decision')
    if case is not None:
        require(personal is not None and 'allocation_choice' in personal,
                'cycle decision must reach a joint allocation/holding decision before reporting')
        validate_personal(personal['allocation_choice'], case, personal, assessment['fact_version'], project)
    elif personal and 'allocation_choice' in personal:
        require(False, 'allocation choice has no frozen public cycle argument')


def report_results(strategy, current_facts, evaluated_at, *, current_analysis=None):
    """A primary finished-result projection, not another research or state owner."""
    case = public_case(strategy.get('outlook'))
    if case is None:
        return None
    personal = strategy.get('recommendations', {}).get('portfolio', {}).get('investment_decision') or {}
    from .position_analysis import matches
    if current_analysis is not None and not matches(personal, current_analysis):
        return {'design_revision': revision(case), 'cycle_case': deepcopy(case),
                'public_applicability': 'REASSESSMENT_REQUIRED' if instant(case['horizon']['end']) <= instant(evaluated_at) else 'WITHIN_ORIGINAL_WINDOW',
                'allocation_choice': None, 'prior_allocation_choice': None,
                'personal_applicability': 'REASSESSMENT_REQUIRED', 'valid_until': None,
                'prior_valid_until': personal.get('valid_until'), 'validation_limits': [],
                'limitations': ['Active price-baseline personal analysis has not been formed; no legacy personal advice is renewed.'],
                'investment_decision_hash': digest(personal) if personal else None}
    require('allocation_choice' in personal, 'return upstream: joint configuration decision missing')
    choice = personal['allocation_choice']
    validate_personal(choice, case, personal, choice['fact_version'])
    require(choice['fact_version'] == current_facts or matches(personal, current_analysis),
            'persistent configuration changed: reassess personal choice before report')
    from .investment import validation_limits
    limits = validation_limits(personal)
    expired = instant(personal['valid_until']) <= instant(evaluated_at)
    public_expired = instant(case['horizon']['end']) <= instant(evaluated_at)
    return {'design_revision': revision(case), 'cycle_case': deepcopy(case),
            'public_applicability': 'REASSESSMENT_REQUIRED' if public_expired else 'WITHIN_ORIGINAL_WINDOW',
            'allocation_choice': None if expired else deepcopy(choice),
            'prior_allocation_choice': deepcopy(choice) if expired else None,
            'personal_applicability': 'REASSESSMENT_REQUIRED' if expired else 'CURRENT_FACTS_AND_WINDOW',
            'valid_until': personal['valid_until'], 'validation_limits': limits,
            'limitations': ['Previous personal choice expired; it does not grant a current action.'] if expired else [],
            'investment_decision_hash': digest(personal)}
