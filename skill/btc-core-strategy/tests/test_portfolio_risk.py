"""Risk assessment may stand alone; advice requires an actual model judgement."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest import mock

import test_closed_loop as legacy
from v4 import closed_loop as loop, run_flow, sensing, storage, reporting, delivery, market_state
from v4 import cycle, research, authored_report
from v4.common import Invalid, create, digest, instant, read, replace


def risk(core, stamp, response_needed=False):
    return {'summary': '资产风险已评估，当前不需要新增风险应对操作。',
            'basis': '市场短期走弱需要观察；结合持有目的和已确认组合事实，尚不足以推出减仓。',
            'response_needed': response_needed,
            'response_reason': '市场风险迹象与当前组合需要操作分开判断。',
            'actions': [], 'limitations': ['部分账户信息未知，不能声称实时安全。'],
            'market_result_hash': loop.public_hash(core), 'fact_version': core['fact_version'],
            'assessed_at': stamp, 'valid_until': (instant(stamp) + timedelta(days=2)).isoformat(),
            'review_conditions': ['实际风险传导、持有论点或已确认资产情况改变。']}


class MomentumContinuityTests(unittest.TestCase):
    """Authored synthetic judgments, not an indicator classifier or BTC verdict."""
    def exercise(self, case):
        for original, opposite in (('BULLISH','BEARISH'), ('BEARISH','BULLISH')):
            with self.subTest(case=case, original_direction=original):
                f=legacy.PublicationTests();f.setUp()
                try:
                    draft=f.f.draft;draft['revision']=7
                    current=original;confirmation='PARTIAL';reversal='NOT_CONFIRMED'
                    original_name='上涨' if original=='BULLISH' else '下跌'
                    opposite_name='下跌' if opposite=='BEARISH' else '上涨'
                    if case=='exhaustion_only':
                        stage='原方向推动力有衰减证据，但相反趋势尚未成立。'
                        basis='新增推动减弱且价格效果下降；反向需求尚未得到独立支持。'
                        change='相较上次新增的是原动量减弱证据，未新增可持续反向推动。'
                        invalidation='原方向重新获得持续资金支持，将削弱枯竭解释。'
                        next_check='核验反向实际需求能否持续及价格是否接受。'
                    elif case=='temporary_countermove':
                        stage='目前只是短暂反向运动，未确认反转趋势。'
                        basis='价格反向运动伴随去仓，后续需求及价格接受没有延续。'
                        change='相较上次出现反向价格变化，但持续驱动仍未建立。'
                        invalidation='有来源支持的持续新增需求会削弱仅去仓解释。'
                        next_check='区分去仓结束后的需求延续与重新返回原走势。'
                    elif case=='reversal_formed':
                        current=opposite;confirmation='CONFIRMED';reversal='CONFIRMED'
                        stage='相反方向的趋势已由独立驱动和后续价格延续共同支持。'
                        basis='原推动力减弱，反向实际需求延续，逆向冲击后的价格接受仍保持。'
                        change='相较上次反向尝试，本轮增加了驱动延续与价格接受证据。'
                        invalidation='反向需求撤退且重新接受原区间，将使本次反转判断失效。'
                        next_check='检验反向需求和价格接受能否经受新的相反冲击。'
                    elif case=='reversal_failed':
                        stage='先前反转尝试失败，原方向仍得到当前证据支持。'
                        basis='反向需求消退，价格重新回到原结构，原方向推动恢复。'
                        change='相较上次形成中的反转，本轮反证否定其延续；旧判断保留。'
                        invalidation='新一轮独立反向需求与价格接受可能再次改变当前判断。'
                        next_check='核验原方向恢复是否持续，并保留失败尝试的原始证据。'
                    else:
                        current='UNKNOWN';confirmation='UNKNOWN';reversal='UNKNOWN'
                        stage='当前周期状态尚未能确认。'
                        basis='现有价格窗口可见，但驱动力及后续延续资料缺失，枯竭与反转均未获确认。'
                        change='相较上次只有价格更新，缺失部分没有取得新的确认。'
                        invalidation='资料缺失既不证明原方向保持，也不证明相反方向形成。'
                        next_check='先取得实际驱动及后续价格接受资料，再区分两种解释。'
                    price_risk='现价回报补偿仍未确认：未来价值和下行区间依据不足，等待比较保留未决。'
                    summary='BTC：原方向为'+original_name+'，待核验相反方向为'+opposite_name+'。'+stage+basis+change+next_check+price_risk
                    assessed=draft['outlook']['market_assessment'];assessed['summary']=summary
                    btc=next(m for m in assessed['markets'] if m['id']=='BTC')
                    for horizon in btc['horizons']:
                        horizon.update(direction=current,structure=stage,change=change,basis=basis,confirmation=confirmation)
                        horizon['forward']['conditions']=[next_check]
                        horizon['forward']['invalidations']=[invalidation]
                    btc['reversal'].update(status=reversal,periods=[h['id'] for h in btc['horizons']],assessment=stage+basis)
                    btc.update(conflicts=invalidation,next_check=next_check)
                    draft['outlook']['semantic']['baseline']['direction']={
                        'BULLISH':'UPSIDE_SKEW','BEARISH':'DOWNSIDE_SKEW','UNKNOWN':'UNRESOLVED'}[current]
                    draft['outlook']['narrative']=summary
                    draft['factor_assessment']['summary']=basis+invalidation
                    draft['conclusion']['business_meaning']['momentum_transition']=summary
                    portfolio=draft['recommendations']['portfolio'];portfolio.pop('allocation_decision')
                    portfolio['risk_assessment']=risk(draft,draft['temporal_scope']['knowledge_cutoff'])
                    portfolio['strategy_advice']=[]
                    personal=portfolio['risk_assessment']['summary']
                    draft['conclusion']['narrative']=summary+'\n\n'+personal
                    public={'outlook':deepcopy(draft['outlook']),'factor_assessment':deepcopy(draft['factor_assessment']),
                            'market_report':summary}
                    policy=read(f.f.store/'write-policy.json');replace(f.f.store/'write-policy.json',dict(policy,minimum_assessment_revision=7))
                    f.c.d.bind()
                    # Establish a real prior observation through the normal
                    # phases. The new decision must supply its own market to
                    # the report instead of borrowing that older observation.
                    adapter=legacy.adapter.RunFlowTests();adapter.f=f.f;adapter.seq=0
                    adapter.d=f.f.base/'before-momentum-adoption'
                    raw_file=f.f.base/'momentum-raw.json';create(raw_file,f.f.raw)
                    prepared=run_flow.prepare(f.f.store,f.f.project,'SYNTHETIC_MOMENTUM',adapter.d,raw_file,
                        agent_id='SYNTHETIC_MAIN',workflow_revision=7)
                    authored=adapter.authored(prepared)
                    observed_public={k:authored[k] for k in market_state.MARKET_FIELDS if k in authored}
                    observed_public.update(market_assessment=legacy.market(f.f.raw['cutoff']),dispute_zones=[])
                    if case in ('reversal_formed','reversal_failed'):
                        old_btc=next(m for m in observed_public['market_assessment']['markets'] if m['id']=='BTC')
                        old_btc['reversal'].update(status='FORMING',assessment='Synthetic prior reversal attempt awaiting sustained demand.')
                    observed_public['applicability_review'].update(outcome='EVIDENCE_LIMITED',refs=[],
                        explanation='The synthetic prior decision has no adopted momentum review.',
                        limitations='The actual new study is still required.')
                    frozen_observation=run_flow.advance(prepared['run'],'market',adapter.file(observed_public))
                    private={k:authored[k] for k in ('recommendation_checks','main_evaluation')}
                    private['allocation_review']={'market_result_hash':frozen_observation['market_result_hash'],
                        'portfolio_risk_hash':None,'strategy_advice_hash':None,
                        'fact_version':read(frozen_observation['portfolio_context'])['fact_version'],
                        'outcome':'EVIDENCE_LIMITED','explanation':'No current independently assessed risk result.',
                        'limitations':['The actual risk review is still required.'],'material':False}
                    run_flow.advance(prepared['run'],'observe',template_path=frozen_observation['observation_template'],
                        answers_path=adapter.file(private))
                    coverage={key:'Synthetic actual review of '+key+' without invented evidence.' for key in cycle.COVERAGE}
                    coverage.update(cycle_structure=stage+basis,verification=change+next_check,value_pricing=price_risk)
                    request,saved,_=f.c.study(authored=True,coverage=coverage,independent_output=json.dumps(public))
                    published=f.c.d.commit()
                    packet=research.read_input(f.f.store,request['request_id'])['payload']
                    self.assertIn(market_state.MOMENTUM_REVIEW,packet['cycle_assignment']['instructions'])
                    self.assertIn('momentum_transition',packet['cycle_assignment']['method_references'])
                    personal_input=research.read_input(f.f.store,request['request_id'],'portfolio',independent_id=saved['independent_id'])['payload']
                    self.assertEqual(personal_input['market_result'],public)
                    ctx=reporting.context(f.f.store);brief=run_flow.report_brief(ctx)
                    self.assertEqual(brief['market_result']['market_assessment'],assessed)
                    self.assertEqual(brief['portfolio_risk'],portfolio['risk_assessment'])
                    self.assertEqual(brief['portfolio_risk']['actions'],[])
                    self.assertIn(market_state.MOMENTUM_REPORT_REVIEW,brief['result_instructions'])
                    baseline=ctx['cycle_baseline'];body=summary+'\n\n'+personal
                    quality=authored_report.QUALITY|cycle.REPORT_CHECKS|cycle.SUMMARY_CHECKS
                    spec={'context':ctx,'result_binding':brief['result_binding'],
                          'report_basis':{'result_id':baseline['result_id'],'report_hash':baseline['report_hash'],
                              'mode':'CYCLE_SUMMARY' if baseline['status']=='CURRENT' else 'LIMITED_FEEDBACK',
                              'verification':change+next_check,'observation_id':None,'evidence_id':ctx['identity'].get('factor_evidence_id')},
                          'layout':{'format':authored_report.FORMAT,'body':body,
                              'bindings':[{'text':summary,'scope':'market','refs':['/market_result/market_assessment/summary']},
                                          {'text':personal,'scope':'personal','refs':['/portfolio_risk/summary']}],
                              'price_status':{'lower_spot':'REVIEW_REQUIRED','upper_short':'REVIEW_REQUIRED'},
                              'quality_review':{k:'本合成案例对照已冻结判断，保留状态、依据、变化、下一验证与独立个人风险。' for k in quality}}}
                    self.assertEqual(authored_report.render(spec,published)[0],body)
                    frozen=research._load(f.f.store,'independent',saved['independent_id'])
                    bad=cycle.candidate(draft)
                    wrong=next(m for m in bad['assessment']['outlook']['market_assessment']['markets'] if m['id']=='BTC')
                    wrong['reversal']['status']='NOT_CONFIRMED' if reversal=='CONFIRMED' else 'CONFIRMED'
                    with self.assertRaises(Invalid):market_state.ensure_research_mapping(frozen,bad)
                    self.assertEqual(research._load(f.f.store,'independent',saved['independent_id']),frozen)
                    self.assertEqual(len(list((f.f.store/'research/requests').glob('*.json'))),1)
                finally:
                    f.doCleanups()

    def test_exhaustion_without_reversal_is_preserved(self):self.exercise('exhaustion_only')
    def test_temporary_countermove_is_not_upgraded(self):self.exercise('temporary_countermove')
    def test_supported_reversal_keeps_independent_price_and_personal_risk(self):self.exercise('reversal_formed')
    def test_failed_reversal_retains_counterevidence_and_change(self):self.exercise('reversal_failed')
    def test_insufficient_evidence_remains_unknown(self):self.exercise('insufficient_evidence')


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy.AllocationTests(); self.f.setUp()
        self.value = risk(self.f.core, self.f.stamp)

    def validate(self, value):
        loop.validate_risk(value, self.f.stamp, loop.public_hash(self.f.core), self.f.core['fact_version'])

    def test_no_response_and_unknown_are_complete_without_invented_actions(self):
        for needed in (False, None, True):
            value = deepcopy(self.value); value['response_needed'] = needed
            self.validate(value)
        value['response_needed'] = None; value['limitations'] = []
        with self.assertRaises(Invalid): self.validate(value)

    def test_actions_require_risk_need_and_keep_quantity_coverage_limits(self):
        value = deepcopy(self.value)
        value['actions'] = deepcopy(self.f.value['actions'])
        for needed in (False, None, 1):
            value['response_needed'] = needed
            with self.assertRaises(Invalid): self.validate(value)
        value['response_needed'] = True; self.validate(value)
        value['actions'][1]['decision'] = 'HOLD'
        with self.assertRaises(Invalid): self.validate(value)

    def test_risk_has_its_own_evidence_facts_and_expiry(self):
        for changes in ({'market_result_hash': digest('other')}, {'fact_version': {'changed': 'facts'}},
                        {'valid_until': self.f.stamp}, {'basis': ''}, {'response_reason': ''}):
            value = deepcopy(self.value); value.update(changes)
            with self.assertRaises(Invalid): self.validate(value)
        # The legacy record remains exactly a legacy record, not converted.
        with self.assertRaises(Invalid): self.validate(self.f.value)
        self.f.validate()


class PublicationTests(unittest.TestCase):
    def test_new_decision_report_uses_its_market_until_matching_observation(self):
        f = legacy.PublicationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        root = f.f.store
        draft = f.f.draft; draft['revision'] = 7
        draft['recommendations']['portfolio'].pop('allocation_decision')
        draft['recommendations']['portfolio']['risk_assessment'] = risk(draft, draft['temporal_scope']['knowledge_cutoff'])
        draft['recommendations']['portfolio']['strategy_advice'] = []
        policy = read(root / 'write-policy.json')
        replace(root / 'write-policy.json', dict(policy, minimum_assessment_revision=7))
        adapter = legacy.adapter.RunFlowTests()
        adapter.f = f.f; adapter.seq = 0

        def observe(label):
            adapter.d = f.f.base / label
            raw_file = f.f.base / (label + '-raw.json'); create(raw_file, f.f.raw)
            prepared = run_flow.prepare(root, f.f.project, label, adapter.d, raw_file,
                                        agent_id='SYNTHETIC_MAIN', workflow_revision=7)
            authored = adapter.authored(prepared)
            public = {key: authored[key] for key in market_state.MARKET_FIELDS if key in authored}
            public.update(market_assessment=legacy.market(f.f.raw['cutoff']), dispute_zones=[])
            public['market_assessment']['summary'] = 'Synthetic observation ' + label
            if storage.current(root)['assessment']['outlook'].get('market_assessment'):
                for assessed in public['market_assessment']['markets']:
                    for horizon in assessed['horizons']:
                        horizon['forward']['prior_check'].update(outcome='PENDING',
                            explanation='Synthetic follow-up retains the unexpired formal thesis for verification.')
            public['applicability_review'].update(outcome='EVIDENCE_LIMITED',
                explanation='Synthetic public thesis check retains an explicit evidence gap.',
                refs=[], limitations='Synthetic independent confirmation remains incomplete.')
            frozen = run_flow.advance(prepared['run'], 'market', adapter.file(public))
            core = storage.current(root)['assessment']; current = loop.risk(core)
            private = {key: authored[key] for key in ('recommendation_checks', 'main_evaluation')}
            private['allocation_review'] = {
                'market_result_hash': frozen['market_result_hash'],
                'portfolio_risk_hash': digest(current) if current else None,
                'strategy_advice_hash': digest(core['recommendations']['portfolio']['strategy_advice']) if current else None,
                'fact_version': read(frozen['portfolio_context'])['fact_version'],
                'outcome': 'VALID' if current else 'EVIDENCE_LIMITED',
                'explanation': 'Synthetic review of the actual current risk assessment.',
                'limitations': [] if current else ['No current risk assessment.'], 'material': False}
            result = run_flow.advance(prepared['run'], 'observe',
                template_path=frozen['observation_template'], answers_path=adapter.file(private))
            return result, public

        original = storage.current(root)
        old, old_public = observe('before-adoption')
        history = {path: path.read_bytes() for path in (root / 'sensing').glob('*.json')}
        f.c.d.bind()
        f.c.study(authored=True, independent_output=json.dumps(f.public))
        adopted = f.c.d.commit()
        self.assertNotEqual(original['decision_id'], adopted['decision_id'])
        ctx = reporting.context(root)
        self.assertEqual(ctx['identity']['decision_id'], adopted['decision_id'])
        self.assertEqual(ctx['observation']['baseline_decision_id'], original['decision_id'])
        immediate = run_flow.report_brief(ctx)
        self.assertEqual(immediate['market_result']['market_assessment'],
                         adopted['assessment']['outlook']['market_assessment'])
        self.assertNotEqual(immediate['market_result']['market_assessment'], old_public['market_assessment'])
        self.assertEqual(immediate['portfolio_risk'], loop.risk(adopted['assessment']))
        self.assertEqual(immediate['applicability']['market']['outcome'], 'EVIDENCE_LIMITED')
        self.assertEqual(immediate['applicability']['portfolio']['outcome'], 'EVIDENCE_LIMITED')
        new, new_public = observe('after-adoption')
        current = read(new['report_context'])
        self.assertEqual(current['market_result']['market_assessment'], new_public['market_assessment'])
        self.assertEqual(current['applicability']['portfolio']['outcome'], 'VALID')
        self.assertNotEqual(old['observation_id'], new['observation_id'])
        self.assertEqual(storage.current(root), adopted)
        self.assertTrue(all(path.read_bytes() == content for path, content in history.items()))

    def test_actual_research_mapping_and_publication_with_no_risk_actions(self):
        f = legacy.PublicationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        draft = f.f.draft; draft['revision'] = 7
        draft['recommendations']['portfolio'].pop('allocation_decision')
        draft['recommendations']['portfolio']['risk_assessment'] = risk(draft, draft['temporal_scope']['knowledge_cutoff'])
        draft['recommendations']['portfolio']['strategy_advice'] = []
        policy = read(f.f.store / 'write-policy.json')
        replace(f.f.store / 'write-policy.json', dict(policy, minimum_assessment_revision=7))
        f.c.d.bind()
        request, _, _ = f.c.study(authored=True, independent_output=json.dumps(f.public))
        saved = f.c.d.commit()
        self.assertEqual(saved['assessment']['revision'], 7)
        self.assertEqual(loop.risk(saved['assessment'])['actions'], [])
        self.assertIsNone(loop.allocation(saved['assessment']))
        self.assertEqual(storage.current(f.f.store)['decision_id'], saved['decision_id'])


class HourlyTests(unittest.TestCase):
    def test_new_hour_reads_old_state_without_manufacturing_risk_assessment(self):
        f = legacy.HourlyClosedLoopTests(); f.setUp(); self.addCleanup(f.doCleanups)
        p = run_flow.prepare(f.root, f.f.f.project, 'SYNTHETIC_R7', f.f.d,
                            f.f.raw_file, agent_id='SYNTHETIC_MAIN', workflow_revision=7)
        public = f.public(p)
        frozen = run_flow.advance(p['run'], 'market', f.f.file(public))
        context = read(frozen['portfolio_context'])
        self.assertIn('portfolio_risk', context)
        self.assertIsNone(context['portfolio_risk'])
        form = f.personal(frozen)
        form['allocation_review']['portfolio_risk_hash'] = form['allocation_review'].pop('allocation_decision_hash')
        form['allocation_review']['strategy_advice_hash'] = None
        finished = run_flow.advance(p['run'], 'observe', template_path=frozen['observation_template'], answers_path=f.f.file(form))
        brief = read(finished['report_context'])
        self.assertEqual(brief['market_revision'], 7)
        self.assertIsNone(brief['portfolio_risk'])
        self.assertNotIn('allocation_decision', brief)
        self.assertEqual(brief['applicability']['portfolio']['outcome'], 'EVIDENCE_LIMITED')
        self.assertEqual(storage.current(f.root)['assessment']['revision'], 3)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy.FinalReportTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root, self.core, self.stamp = self.f.root, self.f.core, self.f.stamp
        self.core['revision'] = 7
        self.core['recommendations']['portfolio'].pop('allocation_decision')
        self.core['recommendations']['portfolio']['risk_assessment'] = risk(self.core, self.stamp)
        self.core['recommendations']['portfolio']['strategy_advice'] = []
        mock.patch('v4.reporting.context', side_effect=self.context).start()

    def context(self, root):
        ctx = self.f.context(root)
        ctx['market_revision'] = 7
        review = ctx['observation']['review']['allocation_review']
        review['portfolio_risk_hash'] = digest(loop.risk(self.core))
        review['strategy_advice_hash'] = digest(self.core['recommendations']['portfolio']['strategy_advice'])
        review.pop('allocation_decision_hash')
        return ctx

    def spec(self):
        value = self.f.f.spec()
        market = '市场短线风险仍在，长期判断按原证据复核。'
        personal = loop.risk(self.core)['summary']
        value['layout']['body'] = market + '\n\n' + personal
        value['information'] = {'market': market, 'allocation': personal}
        value['layout']['bindings'] = [
            {'text': market, 'scope': 'market', 'refs': ['/market_result/market_assessment/summary']},
            {'text': personal, 'scope': 'personal', 'refs': ['/portfolio_risk/summary']}]
        value['result_binding'] = loop.report_inputs(self.context(self.root))['result_binding']
        return value

    def test_risk_only_report_survives_unknown_delivery_and_real_readback(self):
        spec = self.spec()
        identity, source = self.f.f.queue(spec)
        self.assertEqual(source['body'], spec['layout']['body'])
        self.f.f.begin(identity, source)
        delivery.transition(self.root, identity, 'UNKNOWN')
        self.assertFalse(self.f.f.begin(identity, source)['ready_to_send'])
        confirmed = delivery.transition(self.root, identity, 'CONFIRM', self.f.f.receipt(identity))
        self.assertEqual((confirmed['state'], confirmed['attempts']), ('DELIVERED', 1))
        self.assertEqual(reporting.reported(self.root)['display'], spec['layout']['body'])

    def test_legacy_action_cannot_be_the_source_of_a_new_risk_report(self):
        spec = self.spec()
        spec['layout']['bindings'][1]['refs'] = ['/allocation_decision/summary']
        with self.assertRaises(Invalid): self.f.f.source(spec)

    def test_observation_selection_preserves_fact_and_expiry_limits(self):
        for baseline in ('current', 'previous'):
            for changed in ('facts', 'expiry'):
                with self.subTest(baseline=baseline, changed=changed):
                    ctx = self.context(self.root)
                    if baseline == 'previous':
                        ctx['observation']['baseline_decision_id'] = digest('previous decision')
                    if changed == 'facts':
                        ctx['identity']['fact_version'] = {'synthetic': digest('changed facts')}
                    else:
                        ctx['evaluated_at'] = loop.risk(self.core)['valid_until']
                    before = deepcopy(ctx)
                    result = loop.report_inputs(ctx)
                    self.assertEqual(result['applicability']['portfolio']['outcome'], 'REVISE')
                    self.assertEqual(result['portfolio_risk'], loop.risk(self.core))
                    self.assertEqual(result['strategy_advice'], [])
                    self.assertEqual(ctx, before)

    def test_independent_opportunity_advice_does_not_need_a_risk_trigger(self):
        action = deepcopy(legacy.allocation(self.core, self.stamp)['actions'][0])
        action.update(id='opportunity', decision='INCREASE', scope='Independent equity programme',
                      arrangement='独立策略支持正常分期，实际金额仍受已确认资金限制。', reentry_conditions=[])
        self.core['recommendations']['portfolio']['strategy_advice'] = [action]
        spec = self.spec(); line = action['arrangement']
        spec['layout']['body'] += '\n\n' + line
        spec['layout']['bindings'].append({'text': line, 'scope': 'personal', 'refs': ['/strategy_advice/0/arrangement']})
        _, source = self.f.f.queue(spec)
        self.assertIn(line, source['body'])
        self.assertFalse(loop.risk(self.core)['response_needed'])
        spec = self.spec(); spec['result_binding']['portfolio_risk_hash'] = digest('different')
        with self.assertRaises(Invalid): self.f.f.source(spec)


if __name__ == '__main__':
    unittest.main()
