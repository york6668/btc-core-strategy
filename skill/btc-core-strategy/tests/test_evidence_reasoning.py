"""Authored reasoning cases over generated evidence; never a market classifier.

Expected judgements below are review fixtures. Tests prove that actual phase
inputs carry the contract, rejected research cannot publish, and approved
limitations survive mapping and verbatim delivery. They do not prove that a
live model will correctly judge a new market or report.
"""
from copy import deepcopy
import json
import unittest
from unittest import mock

import test_closed_loop as publication_fixture
import test_portfolio_risk as risk_fixture
import test_authored_report as report_fixture
from v4 import cycle, delivery, evidence
from v4 import market_state, reporting, research, run_flow, storage
from v4.common import Invalid, create, digest, read, replace


def scenario(kind):
    """Small synthetic constructions, not copies of reports or market data."""
    raw = {'synthetic': True, 'venue': 'TEST_SPOT_A', 'product': 'TESTBTC-USD',
           'market': 'SPOT', 'volume_unit': 'BTC', 'timezone': 'UTC',
           'complete': True, 'external_net_flow': None}
    if kind == 'correlation':
        raw.update(active_buy=8, price_change=2, competing_impulse=True,
                   causal_order=None)
        fact = '合成同窗现货主动买入偏多且价格上涨，另一推动来源也在变化。'
        boundary = '这支持承接改善的解释，尚不能确定现货买入导致上涨。'
        trigger = '核对原窗后的真实部署、先后关系与价格反应以区分共同冲击。'
        invalidation = '后续缺少独立买入或价格效果消失时，不能承接现货主导解释。'
        bad = '现货买入已确定推动上涨，因此后续必涨并立即新增。'
    elif kind == 'single_venue':
        raw.update(active_buy=8, other_venue_active_buy=None)
        fact = '合成甲所主动买卖差额为正，其他场所主动成交与外部资金流未知。'
        boundary = '该差额仅描述单所成交方向，不证明全市场资金净流入。'
        trigger = '取得独立资本部署资料并核对可比跨所价格反应。'
        invalidation = '若实际部署不足或吸收没有留存，不能以单所差额确认整体需求。'
        bad = '单所主动净买证明全市场资金进入，所以立即增加风险。'
    elif kind == 'historical_demand':
        raw.update(prior_fund_flow=120, new_fund_flow=None,
                   same_horizon_terminal_value=None, net_cost=None)
        fact = '合成已结束披露期有净购买记录，新期间部署和同期限净补偿未知。'
        boundary = '历史需求未消失不等于未来购买或现价投资价值已经成立。'
        trigger = '核对新完整披露、未来价值依据及与现价一致的净成本。'
        invalidation = '新部署撤回或价值依据失效将削弱需求恢复路径。'
        bad = '历史资金进入证明当前价格值得投入，未来需求已可作为前提。'
    elif kind == 'low_volume':
        raw.update(closed_week_volume=[140, 180, 160],
                   current_partial={'volume': 20, 'complete': False},
                   reverse_driver=None)
        fact = '合成最近完整周量低于前周但高于更早一周，当前周仅有未完成切片。'
        boundary = '没有完整周量新低依据，低量也不能确认卖压耗尽或反向趋势。'
        trigger = '沿原问题比较完整同口径周期及反向驱动和后续价格响应。'
        invalidation = '原驱动恢复或反向需求无法延续会否定反转假设。'
        bad = '用当前周切片确认多周量创新低，因而反转已经成立。'
    elif kind == 'missing_chain':
        raw.update(closed_week_close=[100, 104, 107],
                   established_level=None, tests=[], later_response=[])
        fact = '合成完整周收提高，但所称关键位置、形成依据和后验测试均缺失。'
        boundary = '不能宣称多周关键位置已突破，依赖该突破的规划仍未决。'
        trigger = '先核验已有原件能否建立原结构、位置和实际测试的连续证据。'
        invalidation = '没有形成依据或真实测试时不认可该突破命题。'
        bad = '引用周收数据即证明核心结构已突破，未来上行据此安排。'
    elif kind == 'compression':
        raw.update(known_flow=120, partial=True, missing_fund=True,
                   current_value_confirmed=False)
        fact = '合成已披露部分为正，关键基金仍缺报且当日总量未定。'
        boundary = '这只是部分披露，持续需求和当前投资价值均未确认。'
        trigger = '补齐完整披露后再核验实际需求及现价补偿。'
        invalidation = '补齐后的净流出会推翻部分披露所支持的需求路径。'
        bad = 'ETF全面流入，当前参与价值已经确认，可以立即投入。'
    elif kind == 'non_price_mechanism':
        raw.update(public_redemption_completed=True, financing_access_closed=True)
        fact = '合成公开赎回和融资入口关闭均已核验，尚无新价格位置资料。'
        boundary = '机制受损支持有边界的下行风险判断，无须等待量价指标齐全。'
        trigger = '核验融资入口恢复及新真实资本部署是否改变该风险。'
        invalidation = '融资恢复并有已执行部署时重评机制受损判断。'
        bad = '没有指定量价指标就不能分析融资风险。'
    else:
        raise AssertionError(kind)
    return raw, fact, boundary, trigger, invalidation, bad


CASES = ('correlation', 'single_venue', 'historical_demand', 'low_volume',
         'missing_chain', 'compression', 'non_price_mechanism')


class EvidenceReasoningWorkflowTests(unittest.TestCase):
    def test_direct_report_context_carries_same_review_method_without_rebinding(self):
        import io
        from contextlib import redirect_stdout
        import strategy_v4
        from v4.closed_loop import REPORT_INSTRUCTIONS

        f, public, boundary, trigger, invalidation, _ = self.fixture('compression')
        self.prior_observation(f)
        f.c.study(authored=True, coverage=dict.fromkeys(cycle.COVERAGE, boundary + trigger + invalidation),
                  independent_output=json.dumps(public))
        f.c.d.commit()
        original = reporting.context(f.f.store)
        before = storage.current(f.f.store)
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = strategy_v4.main(['--project', str(f.f.project), '--root', str(f.f.store), 'report-context'])
        self.assertEqual(code, 0)
        packet = json.loads(stream.getvalue())
        self.assertEqual(packet['cycle_report_instructions'], cycle.REPORT_SCOPE_REVIEW)
        self.assertEqual(packet['result_instructions'], REPORT_INSTRUCTIONS)
        self.assertIn(market_state.EVIDENCE_REPORT_REVIEW, packet['result_instructions'])
        self.assertNotIn(market_state.EVIDENCE_REASONING_REVIEW, packet['result_instructions'])
        self.assertEqual(packet['method_references'], cycle.method_references('report'))
        for key in ('context_id', 'identity', 'cycle_baseline', 'current_strategy', 'observation', 'metrics'):
            self.assertEqual(packet[key], original[key])
        brief = run_flow.report_brief(original)
        for key in ('cycle_report_instructions', 'method_references', 'result_instructions',
                    'maintenance_authoring', 'applicability_instructions'):
            self.assertEqual(packet[key], brief[key])
        self.assertEqual(storage.current(f.f.store), before)
        self.assertEqual(list((f.f.store / 'outbox').glob('*/artifact.json')), [])

    def fixture(self, kind, unsafe=False):
        f = publication_fixture.PublicationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        draft = f.f.draft
        raw, fact, boundary, trigger, invalidation, bad = scenario(kind)
        payload = f.f.base / 'generated-reasoning-source.json'
        create(payload, {'observations': raw})
        bundle = deepcopy(f.f.evidence)
        original = f.f.evidence_dir
        files = {s['id']: original / 'raw' / (s['raw_hash'][7:] + '.json')
                 for s in bundle['sources']}
        stamp = draft['temporal_scope']['knowledge_cutoff']
        bundle['parent_id'] = None
        bundle['sources'].append({'id': 'reasoning_case',
            'origin': 'UNESTABLISHED:market.example.org',
            'url': 'https://market.example.org/synthetic', 'trust': 'PUBLIC_RAW',
            'start': stamp, 'end': stamp, 'published_at': stamp,
            'retrieved_at': stamp, 'raw_hash': digest(payload.read_bytes())})
        files['reasoning_case'] = payload
        f.f.evidence_dir = f.f.base / 'reasoning-evidence'
        f.f.evidence = evidence.freeze(bundle, files, f.f.evidence_dir)
        storage.archive_evidence(f.f.store, f.f.evidence_dir)
        draft.update(revision=7, evidence_id=f.f.evidence['evidence_id'])
        for section in draft['section_provenance'].values():
            section['evidence_id'] = draft['evidence_id']
        ref = {'source_id': 'reasoning_case', 'pointer': '/observations', 'value_hash': digest(raw)}
        self.assertEqual(evidence.resolve(f.f.evidence, f.f.evidence_dir, ref, verified=True), raw)
        summary = bad if unsafe else fact + boundary + trigger + invalidation
        assessed = draft['outlook']['market_assessment']
        assessed['summary'] = summary
        btc = next(m for m in assessed['markets'] if m['id'] == 'BTC')
        direction = 'BEARISH' if kind == 'non_price_mechanism' else 'UNKNOWN'
        for horizon in btc['horizons']:
            horizon.update(direction=direction, confirmation='PARTIAL' if direction == 'BEARISH' else 'UNKNOWN',
                           basis=fact + boundary, structure=boundary,
                           change='新增合成证据不自动提高确认程度。', refs=[ref], counter_refs=[ref])
            horizon['forward'].update(conditions=[trigger], invalidations=[invalidation])
        btc.update(conflicts=boundary, next_check=trigger)
        btc['reversal'].update(status='NOT_CONFIRMED', assessment=boundary)
        draft['outlook']['semantic']['baseline']['direction'] = 'DOWNSIDE_SKEW' if direction == 'BEARISH' else 'UNRESOLVED'
        draft['outlook']['narrative'] = summary
        draft['factor_assessment'] = {'summary': fact + boundary, 'items': [{
            'id': 'generated_reasoning', 'name': '合成依据审阅', 'assessment': fact + boundary,
            'change_reason': trigger + invalidation, 'refs': [ref], 'counter_refs': [ref]}]}
        portfolio = draft['recommendations']['portfolio']
        portfolio.pop('allocation_decision')
        portfolio['capital_envelope_u'] = None
        for side in ('upper_short', 'lower_spot'):
            rec = draft['recommendations'][side]
            rec.update(status='UNDETERMINED', tiers=[], rationale=bad if unsafe else boundary + trigger + invalidation,
                       refs=[ref])
            rec['validity'].update(conditions=[kind.upper() + '_SUPPORTED'],
                                   invalidations=[kind.upper() + '_INVALIDATED'])
        portfolio['risk_assessment'] = risk_fixture.risk(draft, stamp, response_needed=None)
        portfolio['risk_assessment'].update(
            summary='合成个人风险应对仍未决，不推定当前安排安全。',
            basis=boundary, response_reason='需沿已确认个人事实进一步判断实际风险传导。')
        portfolio['risk_assessment']['review_conditions'] = [trigger, invalidation]
        portfolio['strategy_advice'] = []
        draft['conclusion']['narrative'] = summary + '\n\n' + portfolio['risk_assessment']['summary']
        public = {'outlook': deepcopy(draft['outlook']),
                  'factor_assessment': deepcopy(draft['factor_assessment']), 'market_report': summary}
        policy = read(f.f.store / 'write-policy.json')
        replace(f.f.store / 'write-policy.json', dict(policy, minimum_assessment_revision=7))
        f.c.d.bind()
        return f, public, boundary, trigger, invalidation, bad

    def prior_observation(self, f):
        adapter = publication_fixture.adapter.RunFlowTests()
        adapter.f, adapter.seq = f.f, 0
        adapter.d = f.f.base / 'prior-observation'
        raw_file = f.f.base / 'prior-raw.json'
        create(raw_file, f.f.raw)
        prepared = run_flow.prepare(f.f.store, f.f.project, 'SYNTHETIC_PRIOR',
            adapter.d, raw_file, agent_id='SYNTHETIC_MAIN', workflow_revision=7)
        authored = adapter.authored(prepared)
        public = {k: authored[k] for k in market_state.MARKET_FIELDS if k in authored}
        public.update(market_assessment=publication_fixture.market(f.f.raw['cutoff']), dispute_zones=[])
        public['applicability_review'].update(outcome='EVIDENCE_LIMITED', refs=[],
            explanation='Synthetic independent confirmation remains incomplete.',
            limitations='Await the actual independent assessment.')
        frozen = run_flow.advance(prepared['run'], 'market', adapter.file(public))
        private = {k: authored[k] for k in ('recommendation_checks', 'main_evaluation')}
        private['allocation_review'] = {
            'market_result_hash': frozen['market_result_hash'],
            'portfolio_risk_hash': None, 'strategy_advice_hash': None,
            'fact_version': read(frozen['portfolio_context'])['fact_version'],
            'outcome': 'EVIDENCE_LIMITED', 'explanation': 'No current synthetic risk assessment.',
            'limitations': ['Await the actual independent assessment.'], 'material': False}
        run_flow.advance(prepared['run'], 'observe', template_path=frozen['observation_template'],
                         answers_path=adapter.file(private))

    def test_rejected_reasoning_cases_cannot_be_adopted(self):
        for kind in CASES:
            with self.subTest(case=kind):
                f, public, boundary, trigger, invalidation, _ = self.fixture(kind, unsafe=True)
                before = storage.current(f.f.store)
                coverage = dict.fromkeys(cycle.COVERAGE, boundary + trigger + invalidation)
                _, saved, _ = f.c.study(approved=False, authored=True, coverage=coverage,
                                        independent_output=json.dumps(public))
                self.assertFalse(saved['cycle_review']['approved'])
                with self.assertRaises(Invalid):
                    f.c.d.commit()
                self.assertEqual(storage.current(f.f.store), before)
                self.assertEqual(list((f.f.store / 'outbox').glob('*/artifact.json')), [])

    def test_reviewed_bounds_and_conditions_survive_existing_pipeline(self):
        for kind in CASES:
            with self.subTest(case=kind):
                f, public, boundary, trigger, invalidation, bad = self.fixture(kind)
                self.prior_observation(f)
                coverage = dict.fromkeys(cycle.COVERAGE, boundary + trigger + invalidation)
                req, saved, _ = f.c.study(authored=True, coverage=coverage,
                                        independent_output=json.dumps(public))
                published = f.c.d.commit()
                packet = research.read_input(f.f.store, req['request_id'])['payload']
                self.assertIn(market_state.EVIDENCE_REASONING_REVIEW,
                              packet['cycle_assignment']['instructions'])
                self.assertIn('evidence_reasoning', packet['cycle_assignment']['method_references'])
                personal = research.read_input(f.f.store, req['request_id'], 'portfolio',
                                                independent_id=saved['independent_id'])['payload']
                self.assertEqual(personal['market_result'], public)
                context = reporting.context(f.f.store)
                brief = run_flow.report_brief(context)
                self.assertEqual(brief['market_result']['market_assessment'], public['outlook']['market_assessment'])
                self.assertIn(market_state.EVIDENCE_REPORT_REVIEW, brief['result_instructions'])
                self.assertNotIn(market_state.EVIDENCE_REASONING_REVIEW, brief['result_instructions'])
                for side in ('upper_short', 'lower_spot'):
                    plan = published['assessment']['recommendations'][side]
                    self.assertEqual(plan['status'], 'UNDETERMINED')
                    self.assertEqual(plan['tiers'], [])
                    self.assertEqual(plan['validity']['conditions'], [kind.upper() + '_SUPPORTED'])
                    self.assertEqual(plan['validity']['invalidations'], [kind.upper() + '_INVALIDATED'])
                    self.assertEqual(plan['rationale'], boundary + trigger + invalidation)
                # Neither mapping nor a later plan edit can silently discard
                # the actual author's evidence boundary after approval.
                independent = research._load(f.f.store, 'independent', saved['independent_id'])
                changed = cycle.candidate(f.f.draft)
                changed['assessment']['outlook']['market_assessment']['summary'] = bad
                with self.assertRaises(Invalid):
                    market_state.ensure_research_mapping(independent, changed)
                f.f.draft['recommendations']['lower_spot']['rationale'] = bad
                with self.assertRaises(Invalid):
                    f.c.d.commit()
                self.assertEqual(storage.current(f.f.store), published)


class EvidenceReportDeliveryTests(unittest.TestCase):
    def test_decisive_limits_survive_freezing_and_dropped_limits_fail_readback(self):
        for kind in CASES:
            with self.subTest(case=kind):
                f = report_fixture.AuthoredReportTests()
                f.setUp()
                try:
                    _, fact, boundary, trigger, invalidation, _ = scenario(kind)
                    body = fact + boundary + '\n\n' + trigger + invalidation
                    spec = f.spec(body)
                    spec['layout']['bindings'] = []  # No numbers or invented price levels.
                    spec['information'] = {'market': fact + boundary}
                    spec['layout']['quality_review']['reasoning'] = boundary + trigger + invalidation
                    identity, source = f.queue(spec)
                    self.assertEqual(source['body'], body)
                    f.begin(identity, source)
                    # This is a simulated journal receipt, never a real send.
                    status = delivery.transition(f.root, identity, 'CONFIRM', f.receipt(identity, fact))
                    self.assertEqual(status['state'], 'CORRECTION_REQUIRED')
                    self.assertFalse((f.root / 'reported-current.json').exists())
                    f.begin(identity, source, 'BEGIN_CORRECTION')
                    self.assertEqual(delivery.transition(f.root, identity, 'CONFIRM',
                                     f.receipt(identity, body))['state'], 'DELIVERED')
                    self.assertEqual(reporting.reported(f.root)['display'], body)
                    self.assertEqual(read(f.root / 'outbox' / identity / 'status.json')['attempts'], 2)
                finally:
                    f.doCleanups()
                    mock.patch.stopall()


if __name__ == '__main__':
    unittest.main()
