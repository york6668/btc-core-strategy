"""Synthetic reporting and delivery; no cloud calls or personal asset writes."""
from copy import deepcopy
from pathlib import Path
import sys,unittest
from unittest import mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'scripts'),str(Path(__file__).parent)]
import test_workflow_delivery as fixture
from v4 import reporting,delivery,storage,semantics
from v4.common import create,read,digest,Invalid

class MarketReportingTests(unittest.TestCase):
    def test_old_model_cannot_write_new_report_review(self):
        spec = self.spec(action='SILENT')
        spec['provenance']['model'] = 'gpt-5.6-sol'
        with self.assertRaisesRegex(Invalid, 'Sol/high'):
            reporting.record(self.root, spec)
        self.assertFalse((self.root / 'report-review-current.json').exists())

    def setUp(self):
        self.f=fixture.WorkflowDeliveryTests('test_reordered_edited_display_persists_source_and_presentation');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root;self.seq=1
        self.addCleanup(mock.patch.stopall)
        mock.patch('v4.reporting.context',side_effect=self.context).start()
        mock.patch('v4.reporting.facts',return_value=self.f.decision['assessment']['fact_version']).start()
    def context(self,root):
        p=reporting.reported(root);identity={'decision_id':self.f.decision['decision_id'],'observation_id':'SYNTHETIC_OBS_'+str(self.seq),'reported_identity':p['identity']}
        return {'context_id':digest(identity),'identity':identity,'reported':p,'coverage':{'start':'2030-03-01T10:00:00Z','end':'2030-03-01T%02d:00:00Z'%(10+self.seq)},
                'knowledge_cutoff':self.f.decision['assessment']['temporal_scope']['knowledge_cutoff'],
                'metrics':{'binance_spot_1h':{'latest':{'close':80001.25}},'binance_futures_1h':{'latest':{'close':79000.15}}}}
    def spec(self,action='REPORT',meaning='现货修复但方向尚未确认'):
        return {'schema':'BTC_REPORT_REVIEW_V1','context_id':self.context(self.root)['context_id'],'action':action,'purposes':['MARKET_SUMMARY'],
                'reason':'本轮现货修复变化尚未向用户实际展示，值得汇报。','next_observation':'继续观察现货买盘是否持续以及价格能否留存。',
                'baseline_review':'已对照此前真实展示稿，新解释与观察结果没有重复。','applicability_review':'策略维持有本轮观察证据支持，正文保留方向未知及风险。',
                'information':{'market':meaning},'provenance':{'role':'MAIN','model':'gpt-6.1-sol','effort':'high','agent_id':'SYNTHETIC_MAIN','run_ref':'SYNTHETIC_MANUAL_RUN'},
                'layout':{'format':'MARKET_CONCISE_V1','overview':'维持现有策略，暂不追加仓位。',
                          'market':'现货最新收盘为{{/metrics/binance_spot_1h/latest/close}}USDT，永续为{{/metrics/binance_futures_1h/latest/close}}USDT。',
                          'explanation':'局部修复仍不足以确认趋势改变。','risk':'方向与资金来源尚未确认，保留风险。','next_observation':'继续观察现货买盘是否持续及有效回测。','include_recommendations':False} if action=='REPORT' else None}
    def queue(self,meaning='现货修复但方向尚未确认'):
        value=reporting.record(self.root,self.spec(meaning=meaning));source=reporting.source(self.root,value['review_id'])
        result=delivery.enqueue(self.root,self.f.decision['decision_id'],self.f.destination,source)
        return result['delivery_id'],source
    def begin(self,identity,source):
        self.f.body=source['body'];self.f.items=[(x['id'],x['kind'],x['source_text']) for x in source['constraints']]
        return self.f.begin(identity)
    def confirm(self,identity):return delivery.transition(self.root,identity,'CONFIRM',self.f.receipt(identity))
    def test_same_cutoff_supplement_heading_preserves_frozen_legacy(self):
        value=reporting.record(self.root,self.spec())
        value=reporting.review(self.root,value['review_id'])
        value['context']['coverage']['start']=value['context']['coverage']['end']
        legacy=reporting.render(value,self.f.decision)
        self.assertIn('覆盖：',legacy)
        value['context']['coverage_display_revision']=2
        updated=reporting.render(value,self.f.decision)
        self.assertIn('同一截止时点的补充',updated)
        self.assertNotIn('｜覆盖：',updated)
        self.assertEqual(legacy.split('\n\n')[1:],updated.split('\n\n')[1:])
        del value['context']['coverage_display_revision']
        self.assertEqual(legacy,reporting.render(value,self.f.decision))
    def test_same_strategy_report_then_duplicate_silence(self):
        current=storage.current(self.root)['decision_id'];identity,source=self.queue();self.begin(identity,source);self.assertEqual(self.confirm(identity)['state'],'DELIVERED')
        self.assertEqual(storage.current(self.root)['decision_id'],current)
        with self.assertRaisesRegex(Invalid,'same information'):reporting.record(self.root,self.spec())
        before=read(self.root/'reported-current.json');result=reporting.record(self.root,self.spec(action='SILENT'))
        self.assertEqual(result['action'],'SILENT');self.assertEqual(before,read(self.root/'reported-current.json'))
    def test_same_strategy_can_report_distinct_later_information(self):
        first,src=self.queue();self.begin(first,src);self.confirm(first);self.seq=2
        second,src=self.queue('卖压重新增强，修复未延续');self.assertNotEqual(first,second);self.begin(second,src);self.confirm(second)
        self.assertEqual(read(self.root/'reported-current.json')['delivery_id'],second)
    def test_recover_finds_second_review_of_same_strategy(self):
        first,src=self.queue();self.begin(first,src);self.confirm(first);self.seq=2
        r=reporting.record(self.root,self.spec(meaning='独立窗口现货买盘衰减'))
        self.assertEqual(storage.recover(self.root)['report_reviews_not_enqueued'][0]['review_id'],r['review_id'])
    def test_timeout_existing_message_is_confirmed_without_resend(self):
        i,s=self.queue();self.begin(i,s);delivery.transition(self.root,i,'UNKNOWN')
        self.assertFalse(self.f.begin(i)['ready_to_send'])
        self.assertFalse((self.root/'reported-current.json').exists());self.assertEqual(self.confirm(i)['attempts'],1)
    def test_confirm_interruption_repairs_without_resend(self):
        i,s=self.queue();self.begin(i,s)
        with mock.patch('v4.delivery._advance_reported',side_effect=OSError('synthetic crash')):
            with self.assertRaises(OSError):self.confirm(i)
        self.assertFalse((self.root/'reported-current.json').exists())
        self.assertEqual(self.confirm(i)['attempts'],1);self.assertEqual(read(self.root/'reported-current.json')['delivery_id'],i)
    def test_repeated_confirmation_cannot_roll_back_same_strategy_coverage(self):
        i,s=self.queue();self.begin(i,s);old_receipt=self.f.receipt(i);delivery.transition(self.root,i,'CONFIRM',old_receipt)
        self.seq=2;j,s=self.queue('新的独立买盘观察已经形成');self.begin(j,s);self.confirm(j)
        delivery.transition(self.root,i,'CONFIRM',old_receipt)
        self.assertEqual(read(self.root/'reported-current.json')['delivery_id'],j)
        self.assertEqual(reporting.reported(self.root)['display_delivery_id'],j)
    def test_late_correction_tracks_actual_display_without_rewinding_coverage(self):
        i,old=self.queue();self.begin(i,old)
        bad=self.f.receipt(i,'已收录');bad['review']['bindings']=[{'constraint_id':c['id'],'display_text':'已收录'} for c in old['constraints']]
        delivery.transition(self.root,i,'CONFIRM',bad)
        self.seq=2;j,new=self.queue('随后新的观察已经完成');self.begin(j,new);self.confirm(j)
        self.f.body=old['body'];self.f.items=[(x['id'],x['kind'],x['source_text']) for x in old['constraints']]
        self.f.begin(i,action='BEGIN_CORRECTION');self.confirm(i)
        self.assertEqual(read(self.root/'reported-current.json')['delivery_id'],j)
        self.assertEqual(reporting.reported(self.root)['display_delivery_id'],i)
        self.assertFalse(reporting.reported(self.root)['display_matches_baseline'])
        result=reporting.record(self.root,self.spec(meaning='随后新的观察已经完成'))
        self.assertEqual(result['action'],'REPORT')
    def test_unsent_context_change_supersedes_but_unknown_is_retained(self):
        i,s=self.queue();self.seq=2;self.assertEqual(self.begin(i,s)['state'],'SUPERSEDED')
        j,s=self.queue('当前新窗口需要总结');self.begin(j,s);delivery.transition(self.root,j,'UNKNOWN');self.seq=3
        self.assertFalse(self.f.begin(j)['ready_to_send'])
        self.assertEqual(read(self.root/'outbox'/j/'status.json')['state'],'UNKNOWN')
    def test_bound_source_tampering_is_rejected(self):
        i,s=self.queue();s['body']=s['body'].replace('80,001.25','80,100.25');s['body_hash']=digest(s['body'].encode());s['author_review']['source_hash']=s['body_hash']
        with self.assertRaises(Invalid):delivery.validate_source(s,self.f.decision)
    def test_ack_only_cannot_pass_new_report_acceptance(self):
        i,s=self.queue();self.begin(i,s);bad=self.f.receipt(i,'已收录');bad['review']['bindings']=[{'constraint_id':c['id'],'display_text':'已收录'} for c in s['constraints']]
        self.assertEqual(delivery.transition(self.root,i,'CONFIRM',bad)['state'],'CORRECTION_REQUIRED')
        self.assertFalse((self.root/'reported-current.json').exists())
    def test_bound_request_cannot_be_retried_after_empty_readback(self):
        i,s=self.queue();self.begin(i,s);delivery.transition(self.root,i,'UNKNOWN');receipt=self.f.receipt(i)
        receipt.update(assistant=None,review=None,absence_confirmed=True)
        receipt['dispatch_evidence']={'outcome':'NOT_DISPATCHED','record_ref':receipt['request_ref'],'detail':'空回读不能否定已经取得真实请求身份的派发结果。'}
        with self.assertRaisesRegex(Invalid,'already dispatched'):
            delivery.transition(self.root,i,'NOT_SENT',receipt)
        self.assertEqual(delivery.transition(self.root,i,'CONFIRM',self.f.receipt(i))['attempts'],1)
    def test_evidence_limited_does_not_advance_reported(self):
        result=reporting.record(self.root,self.spec(action='EVIDENCE_LIMITED'))
        self.assertEqual(result['action'],'EVIDENCE_LIMITED');self.assertFalse((self.root/'reported-current.json').exists())
    def test_old_unknown_never_fabricates_baseline_or_swallows_material_review(self):
        old=deepcopy(self.f.decision['assessment']);old['revision']=2;old.pop('conclusion');before=deepcopy(old)
        for meaning in ('first distinct explanation','second distinct explanation'):
            new=deepcopy(self.f.decision['assessment']);new['conclusion']['business_meaning']['novel']=meaning
            result=semantics.compare(new,old);self.assertEqual(result['unknown'],['conclusion']);self.assertEqual(old,before)
            self.assertFalse(result['report_required']);self.assertTrue(semantics.compare(new,old,material_new_outlook=True)['report_required'])
    def test_changed_strategy_cannot_be_hidden_as_market_only_or_silence(self):
        i,s=self.queue();self.begin(i,s);self.confirm(i)
        self.f.decision=self.f.publish(2);self.seq=2
        with self.assertRaisesRegex(Invalid,'STRATEGY_UPDATE'):reporting.record(self.root,self.spec(action='SILENT',meaning='价值解释发生改变'))
        with self.assertRaisesRegex(Invalid,'STRATEGY_UPDATE'):reporting.record(self.root,self.spec(meaning='价值解释发生改变'))
        spec=self.spec(meaning='价值解释发生改变');spec['purposes']=['STRATEGY_UPDATE']
        self.assertEqual(reporting.record(self.root,spec)['action'],'REPORT')
    def test_current_numeric_literals_cannot_bypass_evidence(self):
        spec=self.spec();spec['layout']['market']='现货收盘为81000USDT。'
        with self.assertRaisesRegex(Invalid,'pointers'):reporting.record(self.root,spec)
    def test_unit_partition_and_no_cross_instrument_extrema(self):
        import test_market_observation as f
        raw=f.evidence();result=reporting.metrics(raw,raw['observed_hour'])
        for key in ('binance_spot_1h','coinbase_spot_1h','kraken_spot_1h'):self.assertEqual(result[key]['market'],'SPOT')
        self.assertEqual(result['binance_futures_1h']['market'],'PERPETUAL')
        self.assertEqual(result['binance_spot_1h']['quote_unit'],'USDT');self.assertEqual(result['coinbase_spot_1h']['quote_unit'],'USD')
        self.assertEqual(result['binance_spot_1h']['latest'],raw['datasets']['binance_spot_1h']['rows'][-1])


class RealContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        import test_workflow_main as main_fixture
        import test_market_observation as market_fixture
        from v4 import sensing
        self.f=main_fixture.MainTests('test_sol_publishes_complete_strategy_without_astra_or_report');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.commit();ctx=self.f.bind()
        candidate=market_fixture.review(self.f.raw,{'context_id':ctx['context_id'],'baselines':{}},required=False)
        value={'schema':'BTC_SENSING_REVIEW_V4','revision':3,'context_id':ctx['context_id'],'market_review':candidate,
               'recommendation_checks':[{'side':side,'findings':['NO_MATERIAL_CHANGE'],'refs':['windows.4h.binance_spot_1h'],'explanation':'Synthetic unchanged recommendation evidence reviewed.'} for side in ('upper_short','lower_spot')],
               'extra_findings':[],'main_evaluation':{'required':False,'reasons':[]},'research_findings':[]}
        sensing.record(self.f.store,self.f.project/'.strategy/market_observation.sqlite3',self.f.project,self.f.raw,value)
    def test_real_context_cli_and_silence_roundtrip_without_mocks(self):
        import strategy_v4,io,json
        from contextlib import redirect_stdout
        stream=io.StringIO()
        with redirect_stdout(stream):code=strategy_v4.main(['--project',str(self.f.project),'--root',str(self.f.store),'report-context'])
        self.assertEqual(code,0);ctx=json.loads(stream.getvalue())
        self.assertEqual(ctx['pending_delivery'],[]);self.assertEqual(ctx['pending_report_reviews'],[])
        self.assertEqual(ctx['metrics']['binance_spot_1h']['market'],'SPOT')
        self.assertTrue(reporting.review_required(self.f.store))
        spec={'schema':'BTC_REPORT_REVIEW_V1','context_id':ctx['context_id'],'action':'SILENT','purposes':[],
              'reason':'本次是合成测试，模型明确记录信息重复而静默。','next_observation':'继续观察独立窗口是否出现新的买盘证据。','baseline_review':'合成历史没有完整展示记录，保持未知且不补造。',
              'applicability_review':'合成观察与当前策略事实及证据完全匹配，未改变策略。','information':{},'layout':None,
              'provenance':{'role':'MAIN','model':'gpt-6.1-sol','effort':'high','agent_id':'SYNTHETIC_MAIN','run_ref':'SYNTHETIC_CONTEXT_INTEGRATION'}}
        reporting.record(self.f.store,spec)
        self.assertFalse(reporting.review_required(self.f.store))
        self.assertEqual(reporting.context(self.f.store)['previous_silence']['action'],'SILENT')

if __name__=='__main__':unittest.main()
