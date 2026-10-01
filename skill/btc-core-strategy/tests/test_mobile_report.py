from copy import deepcopy
import unittest
import test_market_reporting as fixture
from v4 import reporting,delivery,mobile_report
from v4.common import Invalid,read
from v4 import cycle

class MobileReportTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.MarketReportingTests('test_same_strategy_report_then_duplicate_silence');self.f.setUp();self.addCleanup(self.f.doCleanups)
    def spec(self):
        s=self.f.spec();s['layout']={'format':'MARKET_MOBILE_V1','lead':'短线偏弱，现有仓位先保持。','blocks':[{'kind':'judgement','text':'目前的卖压还不足以改变原来的持仓理由。'},{'kind':'direction','text':'现货与已有保护空单继续保留，暂不追加仓位。'},{'kind':'condition','text':'如果卖压持续并影响持仓理由，我会重新评估建议。'}],'include_recommendations':False,'quality_review':{k:'已逐段核对，正文先给判断和建议，仅保留影响决策的必要理由。' for k in mobile_report.QUALITY}};return s
    def source(self,s=None):
        r=reporting.record(self.f.root,s or self.spec());return reporting.source(self.f.root,r['review_id'])
    def test_mobile_delivery_requires_expression_acceptance(self):
        s=self.source();i=delivery.enqueue(self.f.root,self.f.f.decision['decision_id'],self.f.f.destination,s)['delivery_id'];self.f.begin(i,s);r=self.f.f.receipt(i)
        with self.assertRaisesRegex(Invalid,'complete presentation'):delivery.transition(self.f.root,i,'CONFIRM',r)
        for k in ('phone_readability','decision_usefulness'):r['review']['checks'][k]={'passed':True,'explanation':'已按手机短段落审阅实际正文，结论和建议清楚且没有数据堆砌。'}
        self.assertEqual(delivery.transition(self.f.root,i,'CONFIRM',r)['state'],'DELIVERED')
        repeated=self.spec();repeated['user_request']={'reference':'synthetic-explicit-request','reason':'用户明确要求把不合格的报告重新表达。'};src=self.source(repeated);j=delivery.enqueue(self.f.root,self.f.f.decision['decision_id'],self.f.f.destination,src)['delivery_id'];self.f.begin(j,src);r=self.f.f.receipt(j)
        for k in ('phone_readability','decision_usefulness'):r['review']['checks'][k]={'passed':True,'explanation':'已核对这次用户要求的重新表达，正文清楚解释判断和具体建议。'}
        delivery.transition(self.f.root,j,'CONFIRM',r)
        repeated['context_id']=self.f.context(self.f.root)['context_id']
        with self.assertRaisesRegex(Invalid,'same information'):self.source(repeated)
    def test_japan_time_rolls_date_and_has_one_time_line(self):
        s=self.source();v=deepcopy(s['report_review']);v['context']['coverage']['end']='2030-03-01T18:05:00Z';body=reporting.render(v,self.f.f.decision)
        self.assertIn('行情截至 3月2日 03:05',body);self.assertNotIn('UTC',body);self.assertNotIn('覆盖',body);self.assertEqual(body.count('行情截至'),1);self.assertFalse(any(l.startswith('|') for l in body.splitlines()))
    def test_controlled_market_rounding_but_exact_formal_advice(self):
        s=self.spec();s['layout']['blocks'][0]['text']='BTC约{{/metrics/binance_spot_1h/latest/close|wan:2}}USDT。';self.assertIn('8.00万USDT',self.source(s)['body'])
        s['layout']['blocks'][0]['text']='金额{{/assessment/recommendations/upper_short/tiers/0/amount_u|plain:0}}U。'
        with self.assertRaisesRegex(Invalid,'formal advice cannot be rounded'):self.source(s)
    def test_literal_numbers_tables_and_raw_times_rejected(self):
        for t in ('价格80000USDT。','| 市场 | 数字 |','数据UTC，截至当前。'):
            s=self.spec();s['layout']['blocks'][0]['text']=t
            with self.assertRaises(Invalid):self.source(s)
    def test_advice_uses_vertical_exact_amounts(self):
        s=self.spec();s['layout']['include_recommendations']=True;body=self.source(s)['body'];self.assertNotIn('|',body);self.assertIn('U投入',body);self.assertIn('U名义额',body)
    def test_risk_and_window_blocks_are_optional_and_not_truncated(self):
        for blocks in ([{'kind':'uncertainty','text':'下跌如果持续，新增现货会扩大回撤，因此先保留现金。'}],[{'kind':'judgement','text':'今天偏弱，但最近一小时卖压减轻，仍需观察能否持续。'}]):
            s=self.spec();s['layout']['blocks']+=blocks;body=self.source(s)['body'];self.assertIn(blocks[0]['text'],body)
    def test_quality_review_cannot_be_omitted(self):
        s=self.spec();s['layout']['quality_review']={}
        with self.assertRaisesRegex(Invalid,'expression review'):self.source(s)

    def test_equal_bytes_with_failed_readability_cannot_advance_baseline(self):
        s=self.source();i=delivery.enqueue(self.f.root,self.f.f.decision['decision_id'],self.f.f.destination,s)['delivery_id'];self.f.begin(i,s);r=self.f.f.receipt(i)
        r['review']['checks']['phone_readability']={'passed':False,'explanation':'实际正文仍然是数据清单，尽管字节一致也不符合手机阅读要求。'}
        r['review']['checks']['decision_usefulness']={'passed':True,'explanation':'实际正文中的方向与当前建议已核对，没有改变源稿的结论。'}
        self.assertEqual(delivery.transition(self.f.root,i,'CONFIRM',r)['state'],'CORRECTION_REQUIRED')
        self.assertFalse((self.f.root/'reported-current.json').exists())

    def minimal(self):
        s=self.spec();s['layout'].update(format='MARKET_MOBILE_V2',
            lead='下跌延续，向上涨切换尚未确认。', blocks=[], include_recommendations=True,
            price_status={'lower_spot':'VALID','upper_short':'VALID'})
        return s

    def deliver_minimal(self,spec=None):
        source=self.source(spec or self.minimal())
        identity=delivery.enqueue(self.f.root,self.f.f.decision['decision_id'],self.f.f.destination,source)['delivery_id']
        self.f.begin(identity,source);receipt=self.f.f.receipt(identity)
        for key in ('phone_readability','decision_usefulness'):
            receipt['review']['checks'][key]={'passed':True,'explanation':'已核对实际形态、建议价格与有效性，手机没有增加内部研究内容。'}
        delivery.transition(self.f.root,identity,'CONFIRM',receipt)
        return identity,source

    def test_minimal_internal_strategy_update_allows_independent_silence(self):
        self.deliver_minimal()
        self.f.f.decision=self.f.f.publish(2);self.f.seq=2
        spec=self.f.spec(action='SILENT',meaning='内部完整评估已更新，但实际手机形态与共识价有效性保持原意。')
        before=read(self.f.root/'reported-current.json')
        self.assertEqual(reporting.record(self.f.root,spec)['action'],'SILENT')
        self.assertEqual(read(self.f.root/'reported-current.json'),before)

    def test_minimal_changed_internal_information_cannot_repeat_display(self):
        self.deliver_minimal()
        spec=self.minimal();spec['information']={'internal_update':'深评已更新，但形态及共识价有效性没有改变。'}
        with self.assertRaisesRegex(Invalid,'same mobile meaning'):
            self.source(spec)
        spec['layout']['price_status']['lower_spot']='REVIEW_REQUIRED'
        self.assertIn('待复核',self.source(spec)['body'])

    def test_value_change_is_reportable_with_identical_shape_and_prices(self):
        spec=self.minimal()
        shape=spec['layout']['lead']
        spec['layout']['price_status']={side:'REVIEW_REQUIRED' for side in ('lower_spot','upper_short')}
        spec['layout']['lead']=shape+'现有持仓仍值得承担等待成本；新增继续暂停，旧价待复核。'
        _,before=self.deliver_minimal(spec)
        changed=deepcopy(spec)
        changed['context_id']=self.f.context(self.f.root)['context_id']
        changed['layout']['lead']=shape+'现有持仓因等待成本上升而价值减弱，仅在补评期间暂保留；新增继续暂停，旧价待复核。'
        changed['information']={'recommendation_value':'形态、价位及其待复核状态不变，现有持仓的价值依据减弱。'}
        after=self.source(changed)
        self.assertEqual(before['body'].split('\n\n')[1:],after['body'].split('\n\n')[1:])
        self.assertIn('等待成本上升',after['body'])
        self.assertTrue(reporting.due(self.f.root,after))
        self.deliver_minimal(changed)
        repeat=deepcopy(changed)
        repeat['context_id']=self.f.context(self.f.root)['context_id']
        repeat['information']={'internal_update':'补充材料更新，但周期、建议价值和共识含义都未变。'}
        with self.assertRaisesRegex(Invalid,'same mobile meaning'):
            self.source(repeat)

    def test_minimal_queued_repeat_is_superseded_before_send(self):
        from unittest import mock
        self.deliver_minimal()
        spec=self.minimal();spec['information']={'internal_update':'模拟旧逻辑已入队的重复稿，正式依据改变而手机含义相同。'}
        with mock.patch('v4.reporting.duplicate_mobile_body',return_value=False):
            source=self.source(spec)
            identity=delivery.enqueue(self.f.root,self.f.f.decision['decision_id'],self.f.f.destination,source)['delivery_id']
        self.assertFalse(reporting.due(self.f.root,source))
        result=delivery.transition(self.f.root,identity,'BEGIN')
        self.assertEqual(result['state'],'SUPERSEDED')
        self.assertEqual(result['attempts'],0)
        self.assertNotIn('attempt',result)

    def test_minimal_explicit_request_still_allows_same_display(self):
        self.deliver_minimal();spec=self.minimal()
        spec['user_request']={'reference':'SYNTHETIC_RESHOW','reason':'用户明确要求再次显示当前形态和共识价位。'}
        self.assertTrue(reporting.due(self.f.root,self.source(spec)))

    def test_minimal_prices_are_exact_and_internal_analysis_stays_internal(self):
        s=self.minimal();s['maintenance']={'recheck_at':'2030-03-02T11:00:00Z','support':'内部融资分析与等待成本',
            'triggers':[{'condition':'内部修正条件'}]}
        v=dict(s,context=self.f.context(self.f.root))
        decision=deepcopy(self.f.f.decision)
        decision['assessment']['domain']['portfolio_decision']['synthetic_adjustment']={'amount_u':'999'}
        body,_=mobile_report.render(v,decision)
        self.assertEqual(len(body.split('\n\n')),3)
        for side in ('lower_spot','upper_short'):
            for t in decision['assessment']['recommendations'][side]['tiers']:
                from v4.brief_report import _number
                self.assertIn(_number(t['price_low_usd']),body)
        for excluded in ('U投入','U名义额','999','复核时间','行情截至','内部','等待成本'):
            self.assertNotIn(excluded,body)
        self.assertEqual(body.count('继续有效'),2)

    def test_independent_btc_keeps_identity_when_prior_price_is_pending(self):
        spec = self.minimal()
        decision = deepcopy(self.f.f.decision)
        rec = decision['assessment']['recommendations']['lower_spot']
        rec['tiers'].append(dict(deepcopy(rec['tiers'][0]), id='BTC_DISCOUNT',
            price_low_usd='60000', price_high_usd='60000', allocation_percent={'BTC':'100'}))
        context = self.f.context(self.f.root)
        value = dict(spec, context=context)
        body, _ = mobile_report.render(value, decision)
        self.assertIn('现货篮子 ', body)
        self.assertIn('独立BTC现货 60,000', body)
        prices = [{k:deepcopy(t[k]) for k in ('price_low_usd','price_high_usd','allocation_percent') if k in t} for t in rec['tiers']]
        context['reported'] = {'consensus_prices':{'lower_spot':{'decision_id':'historical', 'tiers':prices}}}
        rec.update(status='NOT_RECOMMENDED', tiers=[])
        spec['layout']['price_status']['lower_spot'] = 'REVIEW_REQUIRED'
        pending, _ = mobile_report.render(value, decision)
        self.assertIn('独立BTC现货 60,000', pending)
        self.assertIn('待复核', pending)

    def test_delivered_btc_allocation_roundtrips_into_next_pending_report(self):
        f = self.f.f
        rec = f.template['recommendations']['lower_spot']
        rec['tiers'].append(dict(deepcopy(rec['tiers'][0]), id='BTC_ONLY',
            price_low_usd='60000', price_high_usd='60000', amount_u='500', capital_u='500', allocation_percent={'BTC':'100'}))
        f.template['recommendations']['portfolio']['capital_envelope_u'] = '2500'
        f.decision = f.publish(2); self.f.seq = 2
        first, source = self.deliver_minimal()
        confirmed = reporting.reported(self.f.root)
        self.assertEqual(confirmed['delivery_id'], first)
        original = confirmed['consensus_prices']['lower_spot']
        self.assertEqual(original['tiers'][1]['allocation_percent'], {'BTC':'100'})
        rec.update(status='NOT_RECOMMENDED', tiers=[])
        f.decision = f.publish(3); self.f.seq = 3
        pending = self.minimal()
        pending['layout']['price_status']['lower_spot'] = 'REVIEW_REQUIRED'
        pending['information'] = {'spot_applicability':'现货篮子与独立BTC共识由继续有效变为待复核。'}
        second, source = self.deliver_minimal(pending)
        self.assertIn('独立BTC现货 60,000', source['body'])
        confirmed = reporting.reported(self.f.root)
        self.assertEqual(confirmed['delivery_id'], second)
        self.assertEqual(confirmed['consensus_prices']['lower_spot'], original)

    def test_intraday_metrics_do_not_rewrite_approved_cycle_summary(self):
        spec=self.minimal()
        spec['layout']['lead']='周期仍处于大幅下跌后的偏弱修复，上涨切换尚未确认。'
        decision=deepcopy(self.f.f.decision)
        decision['assessment']['conclusion']['narrative']=spec['layout']['lead']
        context=self.f.context(self.f.root)
        expected=mobile_report.render(dict(spec,context=context),decision)[0]
        for close,flow,oi in [(50000,-9000000,500),(90000,9000000,-500)]:
            changed=deepcopy(context)
            changed['metrics']['binance_spot_1h']['latest']['close']=close
            changed['metrics']['intraday']={'net_taker_quote':flow,'oi_change':oi}
            self.assertEqual(mobile_report.render(dict(spec,context=changed),decision)[0],expected)
        self.assertEqual(decision['assessment']['conclusion']['narrative'],spec['layout']['lead'])

    def test_minimal_shape_claims_are_preserved_without_inventing_range(self):
        for lead in ('单边上涨延续。','单边下跌延续。','已由上涨切换为下跌。','处于区间震荡。','向上涨切换尚未确认。'):
            s=self.minimal();s['layout']['lead']=lead
            body=mobile_report.render(dict(s,context=self.f.context(self.f.root)),self.f.f.decision)[0]
            self.assertTrue(body.startswith('**'+lead+'**'))
            self.assertEqual(body.count('USDT'),2)
        s=self.minimal();s['layout']['blocks']=[{'kind':'judgement','text':'扩展宏观解释。'}]
        with self.assertRaisesRegex(Invalid,'price/risk and personal'):self.source(s)

    def sectioned(self):
        spec=self.minimal()
        spec['layout'].update(lead='BTC波段利空：卖压扩张且承接减弱；中期反转未确认。',
            blocks=[{'kind':'judgement','text':'其他资产当前证据不足，未能判断，不能据此归为中性。'},
                    {'kind':'condition','text':'当前观察位尚未形成有效买入条件；反弹收复失守区域会削弱波段风险判断。'},
                    {'kind':'position','text':'个人应对仅按已确认资产安排；数量和保护覆盖不足以精确计算。'}])
        spec['information']={'market':spec['layout']['lead'],'risk':spec['layout']['blocks'][1]['text']}
        return spec

    def test_sectioned_market_is_invariant_to_personal_portfolio(self):
        spec=self.sectioned();context=self.f.context(self.f.root)
        context.update(coverage_display_revision=3,evaluated_at='2030-03-01T18:05:00Z')
        markets=[];risks=[];responses=[]
        for amount,response in [('0','空仓保留现金。'),('1000','已有现货评估降低波段敞口。'),('2000','已有保护仓先检查是否过量。')]:
            decision=deepcopy(self.f.f.decision)
            decision['assessment']['domain']['portfolio_decision']['existing_digital_beta']['amount_u']=amount
            spec['layout']['blocks'][-1]['text']=response
            body,_=mobile_report.render(dict(spec,context=context),decision)
            markets.append(body.split('**价格与风险建议**')[0])
            risks.append(body.split('**价格与风险建议**')[1].split('**个人组合应对**')[0])
            responses.append(body.split('**个人组合应对**')[1])
            self.assertLess(body.index('USDT'),body.index('**个人组合应对**'))
        self.assertEqual(len(set(markets)),1)
        self.assertEqual(len(set(risks)),1)
        self.assertEqual(len(set(responses)),3)

    def test_index_identifiers_do_not_relax_market_number_binding(self):
        for name in ('标普500','标普 500','纳指100','纳斯达克100','S&P 500','Nasdaq-100'):
            spec=self.sectioned();spec['layout']['blocks'][0]['text']=name+'：中性，波段维持高位震荡，尚未确认突破。'
            self.assertIn(name,self.source(spec)['body'])
        for literal in ('标普50000点。','纳指100.5点。','标普500：价格5000点。','纳指100：期限3个月。','标普500：比例60%。'):
            spec=self.sectioned();spec['layout']['blocks'][0]['text']=literal
            with self.subTest(literal=literal),self.assertRaisesRegex(Invalid,'bind frozen evidence'):
                self.source(spec)

    def test_approved_core_excerpt_preserves_index_and_btc_price_meaning(self):
        spec=self.sectioned();decision=deepcopy(self.f.f.decision)
        approved='标普近端观察7,662.57—7,693.08点。\nBTC已收回83,500.01—83,534.01 USDT，留存未确认。'
        decision['assessment']['conclusion']['narrative']=approved
        spec['layout']['blocks'][1]['text']='标普500：{{/assessment/conclusion/narrative|excerpt:7,662.57—7,693.08}}点是观察区。{{/assessment/conclusion/narrative|excerpt:BTC已收回83,500.01—83,534.01 USDT，留存未确认。}}'
        body,constraints=mobile_report.render(dict(spec,context=self.f.context(self.f.root)),decision)
        self.assertIn('标普500：7,662.57—7,693.08点是观察区。',body)
        self.assertIn('BTC已收回83,500.01—83,534.01 USDT，留存未确认。',body)
        self.assertTrue(any('7,662.57—7,693.08' in c['source_text'] for c in constraints))
        self.assertEqual(decision['assessment']['conclusion']['narrative'],approved)

    def test_core_excerpt_cannot_invent_price_status_or_use_unapproved_source(self):
        spec=self.sectioned();decision=deepcopy(self.f.f.decision)
        decision['assessment']['conclusion']['narrative']='BTC观察83,500 USDT，未确认有效入场。'
        for path,excerpt in [('/assessment/conclusion/narrative','83,501'),
                             ('/assessment/conclusion/narrative','已确认有效入场'),
                             ('/metrics/binance_spot_1h/latest/close','80001.25')]:
            spec['layout']['blocks'][1]['text']='{{'+path+'|excerpt:'+excerpt+'}}'
            with self.subTest(path=path,excerpt=excerpt),self.assertRaisesRegex(Invalid,'approved core report'):
                mobile_report.render(dict(spec,context=self.f.context(self.f.root)),decision)

    def test_sectioned_clock_uses_evaluation_japan_time_and_keeps_old_sources(self):
        spec=self.sectioned();context=self.f.context(self.f.root)
        context.update(coverage_display_revision=3,evaluated_at='2030-03-01T18:05:00Z')
        value=dict(spec,context=context)
        body,constraints=mobile_report.render(value,self.f.f.decision)
        self.assertTrue(body.startswith('评估时间：2030年3月2日 03:05（日本时间）\n\n'))
        self.assertEqual(body.count('评估时间：'),1)
        self.assertEqual(constraints[0]['kind'],'deadline')
        context['coverage_display_revision']=2
        legacy,_=mobile_report.render(value,self.f.f.decision)
        self.assertIn('**个人组合应对**',body)
        self.assertIn('**个人资产应对**',legacy)
        self.assertEqual(mobile_report.without_evaluation_time(body,constraints[0]['source_text']).replace('**个人组合应对**','**个人资产应对**'),legacy)
        self.assertNotIn('评估时间：',legacy)

    def test_only_controlled_clock_is_ignored_after_actual_display(self):
        from unittest import mock
        stamp=['2030-03-01T13:05:00Z']
        def context(root):
            return dict(self.f.context(root),coverage_display_revision=3,evaluated_at=stamp[0])
        with mock.patch('v4.reporting.context',side_effect=context):
            _,source=self.deliver_minimal(self.sectioned())
            prior=reporting.reported(self.f.root)
            self.assertEqual(prior['display_evaluation_time'],source['constraints'][0]['source_text'])
            stamp[0]='2030-03-01T14:05:00Z'
            with self.assertRaisesRegex(Invalid,'same mobile meaning'):
                self.source(self.sectioned())
            spec=self.sectioned();spec['user_request']={'reference':'SYNTHETIC_TIME_RESHOW','reason':'用户明确要求再次显示同一完整结论。'}
            self.assertTrue(reporting.due(self.f.root,self.source(spec)))
        line='评估时间：2030年3月1日 22:05（日本时间）'
        market='BTC行情时点：三月一日，小时形态已变化。'
        self.assertEqual(mobile_report.without_evaluation_time(line+'\n\n'+market,line),market)
        self.assertEqual(mobile_report.without_evaluation_time(market+'\n\n'+line,line),market+'\n\n'+line)

    def test_same_rating_structure_change_reaches_display_and_semantic_review(self):
        from v4 import semantics
        spec=self.sectioned();self.deliver_minimal(spec)
        changed=self.sectioned()
        changed['layout']['lead']='BTC波段利空：高位整理→失守近期承接平台，小时破位已确认，波段走弱，中期反转未确认；主动卖出与弱反弹支持。'
        changed['information']={'market':changed['layout']['lead']}
        source=self.source(changed)
        self.assertTrue(reporting.due(self.f.root,source))
        before=deepcopy(self.f.f.decision['assessment']);after=deepcopy(before)
        after['conclusion']['business_meaning']['structure_change']='SYNTHETIC_RANGE_TO_LOST_ACCEPTANCE'
        self.assertIn('conclusion',semantics.compare(after,before)['changed'])
        identity=delivery.enqueue(self.f.root,self.f.f.decision['decision_id'],self.f.f.destination,source)['delivery_id']
        self.f.begin(identity,source);receipt=self.f.f.receipt(identity)
        for key in ('phone_readability','decision_usefulness'):
            receipt['review']['checks'][key]={'passed':True,'explanation':'短段落按三个部分组织，逐项核验了风险、条件和建议对象。'}
        receipt['review']['checks']['decision_usefulness']={'passed':False,'explanation':'语义审阅没有通过，实际表达未清楚区分小时破位与中期反转。'}
        previous=read(self.f.root/'reported-current.json')
        self.assertEqual(delivery.transition(self.f.root,identity,'CONFIRM',receipt)['state'],'CORRECTION_REQUIRED')
        self.assertEqual(read(self.f.root/'reported-current.json'),previous)

    def test_sectioned_information_cannot_claim_unshown_analysis(self):
        spec=self.sectioned();spec['information']['hidden']='内部完成了另一项未展示的资金研究。'
        with self.assertRaisesRegex(Invalid,'actual rendered content'):self.source(spec)

    def test_market_block_cannot_bind_personal_amount(self):
        spec=self.sectioned()
        spec['layout']['lead']='市场利空，金额{{/assessment/domain/portfolio_decision/existing_digital_beta/amount_u}}。'
        with self.assertRaisesRegex(Invalid,'personal portfolio data'):
            mobile_report.render(dict(spec,context=self.f.context(self.f.root)),self.f.f.decision)

    def test_sectioned_withdrawal_preserves_state_without_placeholder_rows(self):
        spec=self.sectioned();spec['layout']['consensus_price_revision']=2
        decision=deepcopy(self.f.f.decision)
        for side in ('lower_spot','upper_short'):
            spec['layout']['price_status'][side]='WITHDRAWN'
            decision['assessment']['recommendations'][side].update(status='NOT_RECOMMENDED',tiers=[])
        body,_=mobile_report.render(dict(spec,context=self.f.context(self.f.root)),decision)
        self.assertNotIn('暂无有效价位',body)
        self.assertIn('尚未形成有效买入条件',body)
        self.assertIn('个人资产应对',body)

    def test_unsized_market_price_survives_unknown_personal_funding(self):
        spec=self.sectioned();spec['layout']['consensus_price_revision']=2
        decision=deepcopy(self.f.f.decision)
        decision['assessment']['conclusion']['business_meaning']['risk_price']='当前减险参考为80,001.25 USDT；已有波段风险成立，这不是新增挂单价。'
        spec['layout']['blocks'][1]['text']='{{/assessment/conclusion/business_meaning/risk_price}}'
        decision['assessment']['recommendations']['portfolio']['capital_envelope_u']=None
        for side in ('lower_spot','upper_short'):
            spec['layout']['price_status'][side]='WITHDRAWN'
            decision['assessment']['recommendations'][side].update(status='NOT_RECOMMENDED',tiers=[])
        body,_=mobile_report.render(dict(spec,context=self.f.context(self.f.root)),decision)
        self.assertIn('减险参考为80,001.25 USDT',body)
        self.assertIn('这不是新增挂单价',body)
        self.assertNotIn('U投入',body)
        self.assertIsNone(decision['assessment']['recommendations']['portfolio']['capital_envelope_u'])

    def test_unshown_historical_information_does_not_block_first_risk_display(self):
        first=self.minimal();risk=self.sectioned()['layout']['lead']
        first['information']={'risk':risk}
        self.deliver_minimal(first)
        prior=reporting.reported(self.f.root)
        self.assertNotIn(risk,prior['information']['display'])
        changed=self.sectioned();changed['information']=deepcopy(first['information'])
        self.assertTrue(reporting.due(self.f.root,self.source(changed)))
        self.deliver_minimal(changed)
        changed['context_id']=self.f.context(self.f.root)['context_id']
        with self.assertRaisesRegex(Invalid,'same mobile meaning'):self.source(changed)

    def test_pending_and_expired_prices_cannot_claim_validity(self):
        s=self.minimal();ctx=self.f.context(self.f.root)
        ctx.update(cycle_policy_revision=1,cycle_baseline={'status':'REASSESSMENT_PENDING'})
        s['layout']['quality_review'].update({k:'已核对内部周期限制，正文保持方向未确认和价位待复核。' for k in cycle.REPORT_CHECKS})
        with self.assertRaisesRegex(Invalid,'pending applicability'):
            mobile_report.render(dict(s,context=ctx),self.f.f.decision)
        s['layout']['price_status']={k:'REVIEW_REQUIRED' for k in ('lower_spot','upper_short')}
        self.assertEqual(mobile_report.render(dict(s,context=ctx),self.f.f.decision)[0].count('待复核'),2)
        ctx['cycle_baseline']['status']='CURRENT';ctx['knowledge_cutoff']='2090-01-01T00:00:00Z'
        self.assertEqual(mobile_report.render(dict(s,context=ctx),self.f.f.decision)[0].count('待复核'),2)

    def test_minimal_delivery_still_requires_actual_expression_acceptance(self):
        s=self.source(self.minimal())
        i=delivery.enqueue(self.f.root,self.f.f.decision['decision_id'],self.f.f.destination,s)['delivery_id']
        self.f.begin(i,s);r=self.f.f.receipt(i)
        with self.assertRaisesRegex(Invalid,'complete presentation'):delivery.transition(self.f.root,i,'CONFIRM',r)
        for k in ('phone_readability','decision_usefulness'):
            r['review']['checks'][k]={'passed':True,'explanation':'实际展示只有形态及精确共识价位与有效性，没有加入内部分析。'}
        self.assertEqual(delivery.transition(self.f.root,i,'CONFIRM',r)['state'],'DELIVERED')

    def test_source_gap_can_leave_one_price_pending_without_inventing_repricing(self):
        s=self.minimal();s['layout']['price_status']['lower_spot']='REVIEW_REQUIRED'
        body=self.source(s)['body']
        self.assertEqual(body.count('待复核'),1)
        self.assertEqual(body.count('继续有效'),1)

    def test_validity_uses_review_time_even_without_new_market_hour(self):
        s=self.minimal();ctx=self.f.context(self.f.root);ctx['evaluated_at']='2090-01-01T00:00:00Z'
        with self.assertRaisesRegex(Invalid,'pending applicability'):
            mobile_report.render(dict(s,context=ctx),self.f.f.decision)

    def test_paused_additions_keep_displayed_consensus_pending_without_reactivation(self):
        source=self.source(self.minimal())
        identity=delivery.enqueue(self.f.root,self.f.f.decision['decision_id'],self.f.f.destination,source)['delivery_id']
        self.f.begin(identity,source);receipt=self.f.f.receipt(identity)
        for key in ('phone_readability','decision_usefulness'):
            receipt['review']['checks'][key]={'passed':True,'explanation':'实际展示的形态与两侧共识价格准确，无金额或内部调查内容。'}
        delivery.transition(self.f.root,identity,'CONFIRM',receipt)
        ctx=self.f.context(self.f.root)
        prior=ctx['reported']['consensus_prices']
        decision=deepcopy(self.f.f.decision)
        for side in ('lower_spot','upper_short'):
            decision['assessment']['recommendations'][side].update(status='NOT_RECOMMENDED',tiers=[])
            self.assertEqual(prior[side]['decision_id'],self.f.f.decision['decision_id'])
            self.assertEqual(set(prior[side]['tiers'][0]),{'price_low_usd','price_high_usd'})
        spec=self.minimal();spec['layout']['price_status']={side:'REVIEW_REQUIRED' for side in prior}
        body,_=mobile_report.render(dict(spec,context=ctx),decision)
        self.assertEqual(body.count('待复核'),2)
        self.assertNotIn('已撤回',body)
        self.assertNotIn('暂无有效价位',body)
        self.assertEqual(decision['assessment']['recommendations']['lower_spot']['tiers'],[])
        from v4.brief_report import _number
        for reference in prior.values():
            for tier in reference['tiers']:
                self.assertIn(_number(tier['price_low_usd']),body)
        # Future reports keep the same formal origin rather than reinterpreting
        # the latest empty recommendation as a new price source.
        from unittest import mock
        origin=deepcopy(decision);origin['decision_id']='synthetic-paused-version'
        artifact={'decision_id':origin['decision_id'],'source':{'layout':spec['layout'],'report_review':{'context':ctx}}}
        real_read=delivery._decision
        with mock.patch('v4.delivery._decision',side_effect=lambda root,key: origin if key==origin['decision_id'] else real_read(root,key)):
            self.assertEqual(reporting.consensus_prices(self.f.root,artifact),prior)
        spec['layout']['price_status']['lower_spot']='VALID'
        with self.assertRaisesRegex(Invalid,'inactive advice'):
            mobile_report.render(dict(spec,context=ctx),decision)

    def test_pending_price_without_actual_consensus_source_is_rejected(self):
        spec=self.minimal();spec['layout']['price_status']['lower_spot']='REVIEW_REQUIRED'
        decision=deepcopy(self.f.f.decision)
        decision['assessment']['recommendations']['lower_spot'].update(status='NOT_RECOMMENDED',tiers=[])
        with self.assertRaisesRegex(Invalid,'actual prior formal source'):
            mobile_report.render(dict(spec,context=self.f.context(self.f.root)),decision)

    def test_withdrawal_removes_prices_and_prevents_later_history_resurrection(self):
        from unittest import mock
        ctx = self.f.context(self.f.root)
        decision = deepcopy(self.f.f.decision)
        old = {side: {'decision_id': decision['decision_id'], 'tiers': deepcopy(rec['tiers'])}
               for side, rec in decision['assessment']['recommendations'].items()
               if side in ('lower_spot', 'upper_short')}
        ctx['reported']['consensus_prices'] = old
        for side in old:
            decision['assessment']['recommendations'][side].update(status='NOT_RECOMMENDED', tiers=[])
        spec = self.minimal()
        spec['layout']['consensus_price_revision'] = 2
        spec['layout']['price_status'] = {side: 'WITHDRAWN' for side in old}
        body, _ = mobile_report.render(dict(spec, context=ctx), decision)
        self.assertEqual(body.count('原建议已撤回'), 2)
        self.assertNotIn('待复核', body)
        from v4.brief_report import _number
        for price in old.values():
            for tier in price['tiers']:
                self.assertNotIn(_number(tier['price_low_usd']), body)
        artifact = {'decision_id': decision['decision_id'],
                    'source': {'layout': spec['layout'], 'report_review': {'context': ctx}}}
        with mock.patch('v4.delivery._decision', return_value=decision):
            projected = reporting.consensus_prices(self.f.root, artifact)
        self.assertEqual(projected, {})
        ctx['reported']['consensus_prices'] = projected
        # A subsequent hourly report cannot recover the superseded formal prices.
        spec['layout']['price_status']['lower_spot'] = 'REVIEW_REQUIRED'
        with self.assertRaisesRegex(Invalid, 'actual prior formal source'):
            mobile_report.render(dict(spec, context=ctx), decision)
