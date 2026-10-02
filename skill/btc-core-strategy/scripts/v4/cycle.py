"""Cycle research provenance and reuse, derived from the single DecisionSnapshot.

This module checks authorship and applicability. It never infers a cycle, a
price target, a factor weight, or a trading action from market measurements.
"""
from copy import deepcopy
import json
import re
from pathlib import Path

from .common import digest, exact, instant, now, read, require, text
from . import market_state

ISSUE_KEY = 'BTC_CYCLE'
SECTIONS = ('domain', 'outlook', 'robustness', 'portfolio_analysis',
            'recommendations', 'conclusion', 'factor_assessment')
SCOPE = ['/' + name for name in SECTIONS]
COVERAGE = ('cycle_structure', 'value_pricing', 'downside_paths',
            'price_levels', 'portfolio_waiting', 'verification')
REPORT_CHECKS = {'cycle_basis', 'downside_and_levels'}
SUMMARY_CHECKS = {'deep_report_summary'}
CORE_REPORT_REVIEW = (
    '先按 method_references 阅读方法，仅依据公开证据完成市场结论与价格风险，再由同一代理读取本地事实完成个人应对。'
    'report_boundary 统一研判与报告职责：先在内部完成因子取舍及反证影响判断，正文明确呈现综合方向、周期、依据和安排，不把冲突交给用户裁决。'
    '报告已采用的来源按发布节奏核验是否有新原件；包内缺件不等于客观不可得，可取得先补入同一研究，失败说明实际尝试及影响，不以旧行情缺口充当最终结论。'
    '用户报告对象为币安QQQ、VOO与BTC；SPX/NDX继续作为底层研究。具体产品版本与报价依公开核验，缺产品价不抹去市场结论。方向、周期、结构变化、依据和确认程度自然表达且对象明确；未知不写中性。'
    '区分小时承接、波段形态与中期趋势，历史结构锚未迁移不等于风险或形态未变。市场依据不得包含持仓、成本、偏好或操作。'
    '个人应对以当前已确认美股组合和加密现货组合为单位；成分权重可调整，独立BTC/黄金是额外机会，保护另评，成员研究不替代核心市场结论。'
    'historical_anchor 仅保留带日期的历史版本出处；历史市场数据从公开原始来源读取；相同证据与期限下持仓差异只改变个人应对。Sol 映射不得改写市场论点。'
)
APPLICABILITY_REVIEW = (
    '用实际原始引用填写 applicability_review；未触发研究不等于 VALID，缺证保留具体限制。'
    '到期核验适用性；实质反证、遗漏或原判断失去支持才恢复同一 BTC_CYCLE，普通小时和时间到期不重跑。'
    '市场识别、深评、报告分别判断；重要结构变化、背离和波段风险可以报告，评级未变、中期未反转和当日已送达不能屏蔽。'
    '已有研究从真实进度接续，建议到期独立维护；只有明确用户请求可使用 USER_REQUESTED_REVIEW。'
)
ANALYSIS_FOCUS_REVIEW = (
    '主动识别当前定价与需求、供给、融资、盈利等因素的背离，比较竞争解释与抵消因素，形成有条件的前瞻路径、判断期限和预期兑现窗口；证据不足说明时间缺口，不编造精确目标或日期。'
    '内部完整评估币安QQQ/VOO与BTC的方向、周期、结构、依据、确认程度及价格风险，完成综合判断后再适配当前组合；这些分析项不是正文必展示清单。'
    '价格判断与个人数量分开，数量未知不撤销有依据的价格判断，不为金额校验编造额度。'
    '结构位、观察价、候选价与有效建议分开；未知不等于中性，宏观利空而上涨不自动等于看跌。'
)
MAPPING_REVIEW = (
    '审阅实际 candidate、补充证据及事实版本，核对七个 section 与独立正文一致，市场判断与个人应对归属清楚。'
    '按 report_boundary 区分内部完整分析与用户正文；映射和汇报不重新排序因子或另立解释，正文呈现已经形成的综合判断。'
    '继承旧版本的假设、证据缺口、宏观字段、建议目的与期限也须回到本轮来源和作者判断；正文修订同步更新全部受影响映射，不只替换摘要，历史依据保留其日期而不冒充当前结论。'
    'coverage 六项实际核验：cycle_structure/value_pricing/downside_paths/price_levels/portfolio_waiting/verification；cycle_structure须核对三个核心市场的评级、期限、结构变化及确认级别。'
    '结构实质变化写入outlook、business_meaning及正文，评级未变也不能遗漏；组合成员版本与独立机会身份须一致。'
    'verification 说明因子取舍、证据限制及相同市场输入下仓位差异未改变评级；字段齐全不等于分析完整。'
    '返回 v4-contract 的 cycle-review JSON：candidate_hash、approved、coverage、counterevidence、unresolved、'
    'reopen_conditions、recheck_at；正文可返回 report_narrative，或用 report_hash=digest(candidate.assessment.conclusion.narrative)'
    '绑定本人已撰写且本次已读的冻结正文，二选一，避免重复输出全文。需修改时由你重写正文并再次审阅实际候选。'
)
REPORT_SCOPE_REVIEW = (
    '先核验当前市场变化并完成对结论、风险和安排的影响判断，再独立判断深评和报告；新数据、新冲突或未知解除不自动要求汇报。'
    'baseline_review 先指出当前重要含义在 reported.display 的对应原句，未出现则明确未展示；'
    '不得以同版报告ID、上一小时分析或 maintenance 代替已展示依据。风险较上一小时减弱也不证明用户已收到该风险。'
    '本轮市场核验、共振/反转变化及分歧区突破迹象可走 MARKET_UPDATE，重要风险仍可走 URGENT_RISK；不以评级变化、中期反转、仓位改变或当天未报告为前提；新正式建议仍须深评采纳。'
    '先理解 report_boundary，REPORT 时展开 when_report，用 AUTHORED_REPORT_V1.body 表达已完成的综合判断；不拆lead/blocks、不设固定篇幅或栏目、不自动追加建议。'
    '汇报不产生第二套判断：Sol、其他模型、程序或云端都不能在整理时重排因子、补充因果解释或改变已形成的结论；需要改变判断时返回对应研判。'
    'cycle_basis与deep_report_summary在冻结前对照研究、当前判断和最终正文核验忠实性；完整分析保留内部，正文保留综合结果及影响当前判断和行动的必要信息。'
    '市场结论、价格风险和分歧区只绑定纯市场字段与当前观察；个人建议价、生效或撤回状态放在个人应对，文字概括同样遵守此边界。'
    '方向与适用周期明确，依据解释主导因素及重要反证怎样支持当前判断；每段都应帮助理解结论、关键依据或行动，不为数字绑定照搬 horizon.change、basis 或 narrative。'
    '分歧区、双方风险、全部期限和未知在内部完整维护，正文按其对判断与安排的影响呈现，不逐项展开；重要风险、事实纠错和建议有效性不能因去噪遗漏。'
    '来源核验过程、重复否定、完整未知清单和内部调度留在维护材料；必要限制直接说明对判断或执行的影响。已对外承诺失约或建议失效，简明说明实际影响与补救。'
    '正文按内容使用加粗结论、短段或列表，使市场判断与个人应对容易找到；版式不能代替信息取舍。'
    '定稿前逐段检查：删去本段会使哪项结论、依据、重要风险或行动被误解？没有实际损失就删除或合并。phone_readability说明实际删并的重复和过程信息、保留限制的理由，不能以“短段落”“已检查”自证。'
    '核对结果和对应安排，不以“尚未证伪”“仍有冲突”等状态替代方向，也不提高事实确认程度。冻结后VERBATIM交付，回读只核验真实关联与逐字一致。'
    '完整稿缺项回原研究，最终报告表达缺项在本地修正，不能补造；information引用实际正文原句，受控评估时间更新不是新报告。'
    '每日送达为最低要求；无实质变化、无重要纠错且每日反馈未到期时可静默。实际发送才读 when_delivery 并回读，未知不写中性。'
)
SPOT_RISK_REVIEW = (
    '下行风险时在完成独立市场判断之后，分别核验已成交篮子与独立 BTC 的减仓依据和保护覆盖。'
    '波段风险可触发评估，不要求需求退出与未来价值受损共同确认；未记录成交保持未知，篮子 BTC 不重复计数。'
    '现货减少后复查保护是否过量，重要个人建议改变交回原深评。'
)

from .closed_loop import INSTRUCTIONS as CLOSED_LOOP_REVIEW
CORE_REPORT_REVIEW += CLOSED_LOOP_REVIEW
MAPPING_REVIEW += CLOSED_LOOP_REVIEW


def method_references(stage, project=None):
    """Point the model at phase-specific prose; never parse it as market rules."""
    references = Path(__file__).resolve().parents[2] / 'references'
    paths = {
        'research': {'analysis': 'core-strategy.md', 'recommendations': 'recommendation-maintenance.md'},
        'mapping': {'mapping': 'v4-contract.md#周期深评与嗅探', 'recommendations': 'recommendation-maintenance.md'},
        'observe': {'observation': 'market-observation.md'},
        'report': {'report_decision': 'report-care.md', 'when_report': 'mobile-report.md', 'when_delivery': 'market-reporting.md'},
    }
    result = {key: str(references / value) for key, value in paths[stage].items()}
    result['market_state'] = str(references / 'market-state.md')
    result['closed_loop'] = str(references / 'closed-loop.md')
    result['report_boundary'] = str(references / 'mobile-report.md') + '#研判与报告的职责边界'
    if project is not None and stage in ('research', 'observe'):
        from node_config import factor_guide
        result['factor_discovery'] = str(factor_guide())
    return result


def enabled(root):
    path = Path(root) / 'write-policy.json'
    return path.exists() and read(path).get('cycle_review_required') is True


def report_required(root):
    path = Path(root) / 'write-policy.json'
    return path.exists() and read(path).get('deep_report_required') is True


def parse_review(analysis):
    """Accept one real JSON review and at most the known host notification.

    The caller retains the full actual response as provenance. Never search for
    a convenient JSON object inside prose or discard unrecognized trailing text.
    """
    text(analysis, 'actual cycle response')
    body = analysis.strip()
    suffix = ''
    if body.startswith('```json\n'):
        end = body.find('\n```', 8)
        require(end >= 0, 'cycle review JSON fence is incomplete')
        suffix, body = body[end + 4:].strip(), body[8:end]
        body = body.strip()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate cycle review key')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('nonfinite cycle review value')
    try:
        value, end = json.JSONDecoder(object_pairs_hook=unique, parse_constant=invalid_constant).raw_decode(body)
    except (TypeError, ValueError):
        require(False, 'cycle final answer must contain the structured candidate review')
    remainder = body[end:].strip()
    if analysis.strip().startswith('```json\n'):
        require(not remainder, 'cycle JSON fence contains extra content')
    else:
        suffix = remainder
    quoted = r'"(?:[^"\\\r\n]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
    notification = r'::inbox-item\{title=' + quoted + r' summary=' + quoted + r'\}'
    require(not suffix or re.fullmatch(notification, suffix), 'unrecognized content after cycle review')
    require(isinstance(value, dict), 'cycle review must be one JSON object')
    return value


def content(assessment):
    """Reviewed market meaning and explanations; omit raw refs and run clocks.

    Explanations are protected too: a different price hidden in prose cannot
    bypass a stable-semantic comparison. Facts can be synchronized separately;
    their portfolio impact still needs a new cycle review before repricing.
    """
    from . import main_flow
    value = deepcopy(main_flow.content(assessment))
    if value is not None:
        value.pop('research_absorptions', None)
        value['semantic'].pop('facts', None)
        rec = assessment.get('recommendations')
        if rec:
            value['portfolio_explanation'] = {k: rec['portfolio'][k] for k in
                ('existing_exposure', 'uncertainty', 'narrative')}
    return value


def candidate(assessment):
    """The final mapped sections Astra must actually inspect before approval."""
    return {'assessment': {k: deepcopy(assessment[k]) for k in
            (*SECTIONS, 'fact_version', 'temporal_scope')}}


def validate_candidate(value):
    exact(value, 'assessment', 'cycle candidate')
    exact(value['assessment'], ' '.join((*SECTIONS, 'fact_version', 'temporal_scope')), 'cycle candidate assessment')
    from . import assessment, recommendations
    from .closed_loop import public_hash
    revision = market_state.revision(value['assessment'])
    # Apply the existing recommendation contract before spending an Astra
    # review on an unpublishable mapping. Raw refs are resolved separately by
    # the evidence-bound preflight and final assessment validation.
    assessment.validate_analysis_sections(value['assessment'], revision, lambda ref: None)
    recommendations.validate(value['assessment']['recommendations'],
                             value['assessment']['temporal_scope']['knowledge_cutoff'], lambda ref: None,
                             revision, public_hash(value['assessment']) if revision >= 6 else None, value['assessment']['fact_version'])
    # The existing content functions need a revision, which is not an opinion.
    content(dict(value['assessment'], revision=market_state.revision(value['assessment'])))


def review_output(req, disclosed, analysis, supplied):
    """Bind the actual final Astra response, not a MAIN-authored approval flag."""
    if req['issue_key'] != ISSUE_KEY:
        return None
    validate_candidate(disclosed['candidate'])
    value = parse_review(analysis)
    exact(value, 'candidate_hash approved coverage counterevidence unresolved reopen_conditions recheck_at'
          + ''.join(' ' + key for key in ('report_narrative', 'report_hash') if key in value), 'actual cycle review')
    require(not ('report_narrative' in value and 'report_hash' in value), 'cycle review must choose report text or hash')
    require(value['candidate_hash'] == disclosed['candidate_hash'], 'cycle approval references a different candidate')
    require(type(value['approved']) is bool, 'Astra cycle approval must be explicit')
    exact(value['coverage'], ' '.join(COVERAGE), 'cycle research coverage')
    for key in COVERAGE:
        text(value['coverage'][key], 'cycle coverage ' + key)
    for key in ('counterevidence', 'unresolved', 'reopen_conditions'):
        require(isinstance(value[key], list), 'cycle review ' + key + ' must be a list')
        for item in value[key]:
            text(item, 'cycle review ' + key)
        require(value[key] == supplied[key], 'recorded cycle findings differ from actual Astra output')
    require(supplied['coverage'] == [value['coverage'][k] for k in COVERAGE], 'recorded cycle coverage differs from actual Astra output')
    require(value['reopen_conditions'], 'cycle baseline needs evidence or event invalidators')
    final_evidence = disclosed.get('evidence_context', req)
    require(instant(value['recheck_at']) > instant(final_evidence['knowledge_cutoff']), 'cycle review deadline must follow its evidence cutoff')
    narrative = disclosed['candidate']['assessment']['conclusion']['narrative']
    if 'report_hash' in value:
        require(value['report_hash'] == digest(narrative), 'cycle report hash differs from reviewed candidate')
        # The raw final response stays in research.analysis. Only restore the
        # exact frozen candidate that the real Astra response explicitly binds.
        value['report_narrative'] = narrative
    elif 'report_narrative' in value:
        text(value['report_narrative'], 'Astra authored full cycle report')
        require(value['report_narrative'].rstrip('\n') == narrative.rstrip('\n'),
                'published report must match the actual Astra authored report')
        # Keep the actual response in research.analysis; trailing LF is a text
        # envelope difference, not a new report. Persist the approved candidate
        # spelling so its publication and downstream report hash stay stable.
        value['report_narrative'] = narrative
    return value


def adopted(root, assessment):
    """Read only adopted, approved whole-cycle results; rejected studies do not reset time."""
    from . import research
    found = []
    for absorption in (assessment or {}).get('research_absorptions', []):
        if absorption['decision'] != 'ADOPT':
            continue
        saved = research._load(root, 'results', absorption['result_id'])
        proof = saved.get('cycle_review')
        if not proof:
            continue
        require(proof['approved'], 'cycle adoption requires an approved actual Astra mapping')
        req = research._load(root, 'requests', saved['request_id'])
        require(req['issue_key'] == ISSUE_KEY and set(SCOPE) <= set(req['scope']), 'incomplete cycle research scope')
        require(set(SCOPE) <= set(absorption['assessment_refs']), 'cycle adoption must cover the complete mapped assessment')
        disclosed = research._load(root, 'candidates', saved['candidate_id'])
        require(disclosed['candidate_hash'] == digest(disclosed['candidate']) == proof['candidate_hash'], 'adopted cycle candidate changed')
        # Recheck the frozen actual response; old specialist artifacts remain readable.
        require(review_output(req, disclosed, saved['analysis'], saved) == proof, 'cycle review proof changed')
        found.append((saved, disclosed))
    return max(found, key=lambda pair: (instant(pair[0]['market_cutoff']), instant(pair[0]['completed_at']))) if found else (None, None)


def pending_reassessment(root, saved):
    """Carry actual open work until adoption, without another current-state file."""
    from . import research, sensing
    root = Path(root)
    reasons = []
    latest = root / 'sensing-current.json'
    if latest.exists():
        observed = sensing.observation(root, read(latest)['observation_id'])
        gate = observed.get('gate', {}).get('cycle_review', {})
        if gate.get('required') and gate.get('basis_result_id') == saved['result_id']:
            # A historical timer-only request must not become a permanent
            # material-change flag after the scheduling contract is corrected.
            reasons.extend(r for r in gate.get('reasons', []) if r != 'REVIEW_DUE')
    adopted_request = research._load(root, 'requests', saved['request_id'])
    for path in (root / 'research' / 'requests').glob('*.json'):
        request = research._load(root, 'requests', 'sha256:' + path.stem)
        if (request['issue_key'] == ISSUE_KEY and request['request_id'] != saved['request_id']
                and instant(request['created_at']) > instant(adopted_request['created_at'])):
            reasons.append('UNADOPTED_CYCLE_REQUEST')
    return sorted(set(reasons))


def baseline(root, assessment, fact_version=None, as_of=None):
    saved, disclosed = adopted(root, assessment)
    if saved is None:
        return {'status': 'INITIAL_REVIEW_PENDING', 'result_id': None, 'market_cutoff': None,
                'recheck_at': None, 'coverage': None, 'unresolved': [], 'reopen_conditions': [],
                'report_narrative': None, 'report_hash': None}
    proof = saved['cycle_review']
    separated = market_state.enabled(root, assessment)
    matches = (market_state.public_content(assessment) == market_state.public_content(disclosed['candidate']['assessment'])
               if separated else content(assessment) == content(dict(disclosed['candidate']['assessment'], revision=market_state.revision(disclosed['candidate']['assessment']))))
    facts_match = saved['fact_version'] == (fact_version or assessment['fact_version'])
    due = instant(as_of or now()) >= instant(proof['recheck_at'])
    verified = None
    applicability_outcome = None
    latest = Path(root) / 'sensing-current.json'
    if latest.exists():
        from . import sensing, reporting
        observed = sensing.observation(root, read(latest)['observation_id'])
        gate = observed.get('gate', {}).get('cycle_review', {})
        authored = observed.get('review', {}).get('applicability_review') or {}
        matches_basis = (gate.get('basis_result_id') == saved['result_id']
                         and (separated or observed['context']['identity']['fact_version'] == saved['fact_version'])
                         and instant(observed['created_at']) <= instant(as_of or now()))
        if gate.get('applicability_revision') == 3 and matches_basis:
            applicability_outcome = authored.get('outcome')
        if (gate.get('applicability_revision') == 3 and gate.get('verification_completed')
                and authored.get('outcome') == 'VALID' and authored.get('refs')
                and gate.get('basis_result_id') == saved['result_id']
                and (separated or observed['context']['identity']['fact_version'] == saved['fact_version'])
                and instant(proof['recheck_at']) <= instant(observed['created_at']) <= instant(as_of or now())):
            raw = reporting.raw_for_observation(root, observed)
            retrieved = max(instant(v['retrieved_at']) for v in raw['retrievals'].values())
            if instant(proof['recheck_at']) <= retrieved <= instant(observed['created_at']):
                verified = {'observation_id': observed['observation_id'], 'verified_at': observed['created_at']}
    narrative = proof.get('report_narrative')
    status = ('MARKET_BASELINE_PENDING' if separated and market_state.public_content(assessment) is None
              else 'MAPPING_CHANGED' if not matches else 'FACTS_CHANGED' if not facts_match and not separated
              else 'REPORT_AUTHORSHIP_PENDING' if report_required(root) and not narrative
              else 'EVIDENCE_LIMITED' if applicability_outcome == 'EVIDENCE_LIMITED'
              else 'REVIEW_DUE' if due and not verified else 'CURRENT')
    pending = pending_reassessment(root, saved)
    if status in ('CURRENT', 'REVIEW_DUE', 'EVIDENCE_LIMITED') and pending:
        status = 'REASSESSMENT_PENDING'
    public_report = None
    if separated and assessment.get('revision', 1) >= 5 and matches:
        from . import research
        independent = research._load(root, 'independent', saved['independent_id'])
        market_state.ensure_research_mapping(independent, {'assessment': assessment})
        public_report = independent['market_result']['market_report']
    return {'status': status, 'market_status': status,
            'portfolio_status': 'FACTS_CHANGED' if not facts_match else 'CURRENT', 'result_id': saved['result_id'], 'request_id': saved['request_id'],
            'market_cutoff': saved['market_cutoff'], 'market_evidence_id': saved['market_evidence_id'],
            'evidence_id': saved['evidence_id'], 'completed_at': saved['completed_at'],
            'recheck_at': proof['recheck_at'], 'coverage': proof['coverage'],
            'verification_due': due and not verified, 'applicability_verification': verified,
            'applicability_outcome': applicability_outcome,
            'counterevidence': proof['counterevidence'], 'unresolved': proof['unresolved'],
            'reopen_conditions': proof['reopen_conditions'],
            'report_narrative': narrative, 'report_hash': digest(narrative) if narrative else None,
            'market_report': public_report,
            **({'pending_reasons': pending} if pending else {})}


def validate_publication(root, prior, draft):
    if not enabled(root):
        return
    previous = (prior or {}).get('assessment')
    if content(draft) == content(previous):
        # Observation/report clocks cannot refresh or create a deep baseline.
        old, _ = adopted(root, previous)
        saved, disclosed = adopted(root, draft)
        if (saved or {}).get('result_id') == (old or {}).get('result_id'):
            return
    else:
        saved, disclosed = adopted(root, draft)
    require(saved is not None, 'cycle changes require an adopted actual Astra/max cycle review')
    if report_required(root):
        require(saved['cycle_review'].get('report_narrative') == draft['conclusion']['narrative'],
                'new cycle publication requires the actual Astra authored full report')
    require(saved['fact_version'] == draft['fact_version'], 'cycle repricing requires Astra review of the current confirmed facts')
    require(content(draft) == content(dict(disclosed['candidate']['assessment'], revision=market_state.revision(disclosed['candidate']['assessment']))),
            'published cycle differs from the candidate actually approved by Astra')
    if draft.get('revision', 1) >= 6:
        from .closed_loop import allocation
        require(instant(allocation(draft)['valid_until']) > instant(now()),
                'allocation decision expired during review; reassess its applicability without renewing the report clock')
    # A check time is not an expiry of the actual authorship/approval proof.
    # Executable proposals retain their separate, immutable real expiry.
    for side in ('upper_short', 'lower_spot'):
        rec = draft['recommendations'][side]
        if rec['status'] == 'RECOMMENDED':
            require(instant(rec['validity']['until']) > instant(now()),
                    'executable recommendation expired; keep pending and reassess applicability')


def validate_report_basis(spec, ctx):
    """Bind summaries to the adopted report; interpretation remains model work."""
    if ctx.get('deep_report_policy_revision') != 1:
        return
    basis = ctx['cycle_baseline']
    binding = spec.get('report_basis')
    exact(binding, 'result_id report_hash mode verification' + (' observation_id evidence_id' if ctx.get('market_revision', 0) >= 5 else ''), 'report source binding')
    require(binding['result_id'] == basis['result_id'] and binding['report_hash'] == basis.get('report_hash'),
            'summary must bind the adopted full cycle report')
    require(binding['mode'] in ('CYCLE_SUMMARY', 'LIMITED_FEEDBACK', 'URGENT_RISK', 'MARKET_UPDATE'), 'invalid cycle report use')
    if binding['mode'] == 'MARKET_UPDATE':
        require((ctx.get('observation') or {}).get('review', {}).get('market_assessment') is not None,
                'market update requires a current independent market observation')
        require(binding.get('observation_id') == ctx['observation']['observation_id'], 'market update binds a different observation')
        require(binding.get('evidence_id') == ctx['identity'].get('factor_evidence_id'), 'market update needs its frozen supplementary evidence')
        require(ctx['observation'].get('baseline_decision_id') == ctx['identity']['decision_id'],
                'market update observation must review the current formal baseline')
    text(binding['verification'], 'how sensing validates the full report')
    require(len(binding['verification'].strip()) >= 12, 'report verification requires substantive reasoning')
    if binding['mode'] == 'CYCLE_SUMMARY':
        require(basis['status'] == 'CURRENT' and basis.get('report_narrative'),
                'cycle summary needs a current Astra authored report')
    elif binding['mode'] == 'LIMITED_FEEDBACK':
        require(basis['status'] != 'CURRENT' or not basis.get('report_narrative'),
                'limited feedback requires a real report applicability gap')
    if not basis.get('report_narrative'):
        require(spec.get('maintenance', {}).get('kind') != 'DEEP_REVIEW',
                'missing full report cannot be presented as a completed deep review')


def validate_applicability(value, ctx, package, directory):
    """Check explicit authorship and real references, never infer evidence sufficiency."""
    from . import evidence
    exact(value, 'basis_result_id outcome explanation refs limitations', 'cycle applicability review')
    require(value['basis_result_id'] == ctx['cycle_baseline']['result_id'], 'stale applicability basis')
    require(value['outcome'] in ('VALID', 'EVIDENCE_LIMITED', 'REVISE'), 'invalid applicability outcome')
    text(value['explanation'], 'actual applicability reasoning')
    text(value['limitations'], 'applicability limitations and impact')
    require(isinstance(value['refs'], list), 'applicability references required')
    if value['outcome'] == 'VALID':
        require(value['basis_result_id'] is not None and value['refs'], 'valid applicability needs an adopted basis and actual evidence')
    for ref in value['refs']:
        evidence.resolve(package, directory, ref)


def review_gate(root, ctx, review, research_issues):
    """Scheduling conditions, not automated market judgments or trade triggers."""
    basis = ctx['cycle_baseline']
    reasons = []
    applicability = review.get('applicability_review') or {}
    if basis['status'] not in ('CURRENT', 'REVIEW_DUE', 'EVIDENCE_LIMITED'):
        reasons.append(basis['status'])
    if applicability.get('outcome') == 'REVISE':
        reasons.append('APPLICABILITY_REVISION_REQUIRED')
    reasons.extend(basis.get('pending_reasons', []))
    if review['market_review']['deep_review']['required']:
        reasons.append('MODEL_IDENTIFIED_CYCLE_CHANGE')
    for check in ([] if market_state.enabled(root) else review['recommendation_checks']):
        if set(check['findings']) & {'COUNTEREVIDENCE', 'AMOUNT_BASIS_CHANGED', 'OTHER_MATERIAL'}:
            reasons.append(check['side'] + ':MATERIAL_REASSESSMENT')
    if any(f['material'] for f in review['extra_findings']):
        reasons.append('UNCOVERED_MATERIAL_EVIDENCE')
    if any(f['eligible'] for f in research_issues):
        reasons.append('RESEARCH_FINDING')
    known = next((i for i in (ctx.get('research') or {}).get('issues', []) if i['issue_key'] == ISSUE_KEY), None)
    progress = (known or {}).get('latest_request', {})
    active = progress if progress and progress.get('request_id') != basis.get('request_id') else None
    return {'required': bool(reasons), 'reasons': sorted(set(reasons)),
            'applicability_revision': 3,
            'verification_due': basis.get('verification_due', basis['status'] == 'REVIEW_DUE'),
            'verification_completed': not reasons and applicability.get('outcome') == 'VALID',
            'applicability_outcome': applicability.get('outcome'),
            'existing_request': active,
            'issue_key': ISSUE_KEY, 'basis_result_id': basis['result_id'],
            'action': 'REVIEW_OR_RESUME_CYCLE_RESEARCH' if reasons else 'VERIFY_ADOPTED_CYCLE',
            'risk_reporting_available': True}


def request_template(ctx):
    known = next((i for i in (ctx.get('research') or {}).get('issues', []) if i['issue_key'] == ISSUE_KEY), None)
    return {'issue_key': ISSUE_KEY, 'scope': SCOPE, 'question': None,
            'decision_id': ctx['identity']['decision_id'], 'evidence_id': ctx['identity'].get('factor_evidence_id'),
            'change': {'kind': 'NEW_KEY_EVIDENCE' if known else 'INITIAL_QUESTION',
                       'reason': None, 'refs': [], 'reopen_condition': None}, 'reopen_conditions': []}


def deadline_reopen(root, assessment, change):
    """Timers require Sol verification, never manufacture discriminating evidence."""
    if change['kind'] != 'REOPEN_CONDITION_MET':
        return None
    basis = baseline(root, assessment)
    if basis['result_id'] and change['reopen_condition'] == 'RECHECK_AT:' + basis['recheck_at']:
        require(False, 'cycle check time requires applicability verification, not a new research request')
    return None


def raw_baseline(root, basis):
    if not basis['result_id']:
        return None
    import market_observation as market
    from . import evidence
    directory = Path(root) / 'evidence' / basis['evidence_id'][7:]
    package = evidence.validate(read(directory / 'evidence.json'), directory)
    for source in package['sources']:
        raw = read(directory / 'raw' / (source['raw_hash'][7:] + '.json'))
        if isinstance(raw, dict) and raw.get('evidence_id') == basis['market_evidence_id']:
            market.validate_evidence(raw)
            return raw
    require(False, 'adopted cycle raw evidence is unavailable')
