from copy import deepcopy
from datetime import timedelta
from unittest import mock
import unittest
import test_mobile_report as fixture
from v4 import reporting,report_care,delivery,storage
from v4.common import create,read,replace,Invalid,instant

class ReportCareTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.MobileReportTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.f.root;self.clock='2030-03-01T13:05:00Z';self.run='SYNTHETIC_RUN_1'
        create(self.root/'write-policy.json',{'report_care_revision':1,'report_care_activated_at':self.clock})
        self.base=self.f.f.context
        mock.patch('v4.reporting.context',side_effect=self.context).start()
        mock.patch('v4.reporting.now',side_effect=lambda:self.clock).start()
        mock.patch('v4.report_care.now',side_effect=lambda:self.clock).start()
    def context(self,root):
        v=self.base(root);v['evaluated_at']=self.clock;v['report_care']=report_care.status(root,self.clock);return v
    def spec(self,action='REPORT',kind='MARKET_REVIEW'):
        s=self.f.spec();s['context_id']=self.context(self.root)['context_id'];s['action']=action;s['provenance']['run_ref']=self.run
        prev=report_care.latest(self.root);st=report_care.status(self.root);future=(instant(self.clock)+timedelta(minutes=30)).isoformat();deadline=st.get('report_by') or future
        s['maintenance']={'kind':kind,'thesis':'当前震荡解释仍覆盖这次观察，价格变化尚未改变持仓理由。','support':'现货与衍生品观察仍存在竞争解释，不能只凭一次报价改变建议。','unresolved':'方向资金来源仍未完全识别，必须继续核对这一解释的边界。','triggers':[{'id':'LOWER','condition':'若下方区域失守后仍不能收回，我会重新评估当前判断。','reason':'这是当前震荡解释的重要反证，触及后必须检查而不能继续套用。','refs':['/metrics/binance_spot_1h/latest/close']}],'recheck_at':future,'daily':{'day':report_care.day(self.clock),'report_by':deadline,'reason':'选择下一观察轮完成判断，并为真实发送与回读保留必要时间。'},'checked':{'prior_review_id':prev['review_id'] if prev else None,'basis_review':'已检查上份报告的解释与反证，新信息仍能由当前判断解释。','trigger_results':[{'id':t['id'],'state':'NOT_MET','evidence':'实际观察仍未满足所述反证，不把盘中报价当作确认。'} for t in prev['maintenance']['triggers']] if prev else [],'outcome':'VALID'}}
        if action=='REPORT':s['layout']['blocks'][-1]['text']='{{/maintenance/triggers/0/condition}}最迟在{{/maintenance/recheck_local}}复核。'
        else:s['layout']=None
        return s
    def queue(self,s=None):
        result=reporting.record(self.root,s or self.spec());source=reporting.source(self.root,result['review_id']);i=delivery.enqueue(self.root,self.f.f.f.decision['decision_id'],self.f.f.f.destination,source)['delivery_id'];return i,source
    def begin(self,i,s):
        self.f.f.f.body=s['body'];self.f.f.f.items=[(x['id'],x['kind'],x['source_text']) for x in s['constraints']]
        st=delivery.transition(self.root,i,'BEGIN',{'schema':'BTC_DELIVERY_ATTEMPT_V1','destination':self.f.f.f.destination,'anchor_ref':'SYNTHETIC_ANCHOR','started_at':self.clock})
        delivery.transition(self.root,i,'BIND_REQUEST',{'schema':'BTC_SENT_REQUEST_V1','attempt_id':st['attempt']['attempt_id'],'destination':self.f.f.f.destination,'request_ref':'REQUEST_'+i,'record_refs':['REQUEST_'+i],'request_body':s['body'],'anchor_ref':'SYNTHETIC_ANCHOR','request_started_at':self.clock})
    def receipt(self,i,completed=None,checked=None):
        r=self.f.f.f.receipt(i);r['checked_at']=checked or self.clock;r['assistant']['completed_at']=completed or self.clock
        for k in ('phone_readability','decision_usefulness'):r['review']['checks'][k]={'passed':True,'explanation':'实际正文明确解释当前判断与下一复核条件，手机段落清楚可理解。'}
        return r
    def confirm(self,i,**kw):return delivery.transition(self.root,i,'CONFIRM',self.receipt(i,**kw))
    def test_daily_unchanged_report_without_deep_and_repeat_silence(self):
        i,s=self.queue();self.begin(i,s);self.confirm(i);self.assertTrue(report_care.status(self.root)['fulfilled'])
        self.clock='2030-03-02T01:00:00Z';self.run='DAY2';reporting.record(self.root,self.spec('SILENT'))
        self.clock='2030-03-02T01:31:00Z';self.assertTrue(report_care.status(self.root)['daily_due'])
        with self.assertRaisesRegex(Invalid,'daily feedback is due'):reporting.record(self.root,self.spec('SILENT'))
        j,s=self.queue();self.begin(j,s);self.confirm(j);self.assertTrue(report_care.status(self.root)['fulfilled']);reporting.record(self.root,self.spec('SILENT'))

    def test_wave_risk_after_daily_delivery_without_cycle_or_position_change(self):
        first=self.spec();first['layout']=self.f.minimal()['layout']
        first['layout']['lead']='BTC波段利空：仍在高位整理，卖压增加；中期反转未确认。'
        # Incident replay: the old internal index mentioned a risk which its
        # actual phone body omitted. No new decision is needed to disclose it.
        risk='BTC波段仍利空：高位整理→失守近期承接平台，小时破位已确认，波段走弱；中期反转未确认。'
        first['information']={'risk':risk}
        identity,source=self.queue(first);self.begin(identity,source);self.confirm(identity)
        decision_before=storage.current(self.root)
        self.clock='2030-03-01T13:15:00Z';self.run='SYNTHETIC_WAVE_RISK'
        self.assertTrue(report_care.status(self.root)['fulfilled'])
        revised=self.spec();revised['layout']=self.f.sectioned()['layout'];revised['information']={'risk':risk}
        revised['layout']['lead']=risk
        revised['maintenance']['checked']['outcome']='REVISE'
        revised['maintenance']['checked']['trigger_results'][0].update(state='MET',evidence='风险已核验，但旧手机正文没有展示；中期判断及正式仓位建议未变。')
        next_id,next_source=self.queue(revised)
        self.assertTrue(reporting.due(self.root,next_source))
        self.begin(next_id,next_source);self.confirm(next_id)
        self.assertEqual(storage.current(self.root),decision_before)
        repeated=self.spec();repeated['layout']=revised['layout'];repeated['information']=revised['information']
        with self.assertRaisesRegex(Invalid,'same mobile meaning'):self.queue(repeated)

    def test_sectioned_timestamp_change_alone_silent_but_next_daily_report_allowed(self):
        def context(root):
            return dict(self.context(root),coverage_display_revision=3)
        def spec():
            value=self.spec();layout=self.f.sectioned();value['layout']=layout['layout'];value['information']=layout['information'];return value
        with mock.patch('v4.reporting.context',side_effect=context):
            i,source=self.queue(spec());self.begin(i,source);self.confirm(i)
            self.clock='2030-03-01T13:15:00Z'
            with self.assertRaisesRegex(Invalid,'same mobile meaning'):self.queue(spec())
            self.clock='2030-03-02T01:00:00Z';self.run='SECTIONED_DAY2'
            reporting.record(self.root,self.spec('SILENT'))
            self.clock='2030-03-02T01:31:00Z'
            j,next_source=self.queue(spec())
            self.assertNotEqual(source['body'].splitlines()[0],next_source['body'].splitlines()[0])
            self.assertTrue(reporting.due(self.root,next_source))
            self.begin(j,next_source);self.confirm(j)
            self.assertTrue(report_care.status(self.root)['fulfilled'])

    def test_minimal_identical_daily_feedback_still_requires_delivery(self):
        def spec():
            value=self.spec();value['layout']=self.f.minimal()['layout'];return value
        i,source=self.queue(spec());self.begin(i,source);self.confirm(i)
        self.clock='2030-03-02T01:00:00Z';self.run='MINIMAL_DAY2'
        reporting.record(self.root,self.spec('SILENT'))
        self.clock='2030-03-02T01:31:00Z'
        j,next_source=self.queue(spec())
        self.assertEqual(source['body'],next_source['body'])
        self.assertTrue(reporting.due(self.root,next_source))
        self.begin(j,next_source);self.confirm(j)
        self.assertTrue(report_care.status(self.root)['fulfilled'])
    def test_local_deep_does_not_count_but_delivered_deep_report_does(self):
        create(self.root/'SYNTHETIC_DEEP_DONE.json',{'completed':True});self.assertFalse(report_care.status(self.root)['fulfilled'])
        i,s=self.queue(self.spec(kind='DEEP_REVIEW'));self.begin(i,s);self.assertFalse(report_care.status(self.root)['fulfilled']);self.confirm(i);self.assertTrue(report_care.status(self.root)['fulfilled'])
    def test_clock_deadline_and_new_run_event_check_without_new_observation(self):
        reporting.record(self.root,self.spec('SILENT'));self.clock='2030-03-01T13:10:00Z'
        self.assertFalse(report_care.status(self.root,run_ref=self.run)['review_required']);self.assertTrue(report_care.status(self.root,run_ref='NEXT_RUN')['event_check_required'])
        self.clock='2030-03-01T13:36:00Z';self.assertTrue(reporting.review_required(self.root));self.assertTrue(storage.recover(self.root)['report_care']['time_due'])
    def test_daily_deadline_cannot_be_pushed_forward_by_silence(self):
        reporting.record(self.root,self.spec('SILENT'));s=self.spec('SILENT');s['maintenance']['daily']['report_by']='2030-03-01T14:45:00Z'
        with self.assertRaisesRegex(Invalid,'cannot be postponed'):reporting.record(self.root,s)
    def test_all_previous_triggers_must_be_checked(self):
        reporting.record(self.root,self.spec('SILENT'));s=self.spec('SILENT');s['maintenance']['checked']['trigger_results']=[]
        with self.assertRaisesRegex(Invalid,'all prior'):reporting.record(self.root,s)

    def test_unshown_equity_condition_survives_crypto_only_summary(self):
        s=self.spec('SILENT')
        s['maintenance']['triggers'].append({'id':'EQUITY_EARNINGS',
            'condition':'核验美股盈利预期是否持续下修及原定投安排是否仍成立。',
            'reason':'核心报告的美股条件即使未在短稿展示，仍需维护减仓和回补依据。',
            'refs':[]})
        reporting.record(self.root,s)
        later=self.spec('SILENT')
        self.assertEqual({v['id'] for v in later['maintenance']['checked']['trigger_results']}, {'LOWER','EQUITY_EARNINGS'})
        later['maintenance']['checked']['trigger_results'] = [v for v in later['maintenance']['checked']['trigger_results'] if v['id']!='EQUITY_EARNINGS']
        with self.assertRaisesRegex(Invalid,'all prior'):
            reporting.record(self.root,later)
        limited=self.spec('SILENT')
        limited['maintenance']['checked']['trigger_results'][1].update(state='UNKNOWN',evidence='盈利公告尚未取得，不能把未核实解释为没有变化。')
        with self.assertRaisesRegex(Invalid,'unknown trigger'):
            reporting.record(self.root,limited)
    def test_unknown_send_is_not_silence_and_recovery_no_duplicate(self):
        i,s=self.queue();self.begin(i,s);delivery.transition(self.root,i,'UNKNOWN')
        q=self.spec('SILENT');ctx=self.context(self.root);ctx['pending_delivery']=[{'state':'UNKNOWN'}]
        with mock.patch('v4.reporting.context',return_value=ctx):
            self.assertEqual(reporting.record(self.root,q)['action'],'SILENT')
            self.assertTrue(reporting.obligations(self.root))
        self.confirm(i);self.assertTrue(report_care.status(self.root)['fulfilled']);self.assertEqual(read(self.root/'outbox'/i/'status.json')['attempts'],1)
    def test_actual_reply_day_not_late_confirmation_day(self):
        self.clock='2030-03-01T14:58:00Z';q=self.spec();q['maintenance']['daily']['report_by']='2030-03-01T14:59:00Z';i,s=self.queue(q);self.begin(i,s)
        self.clock='2030-03-01T15:02:00Z';self.confirm(i,completed='2030-03-01T14:59:00Z');days=report_care.deliveries(self.root);self.assertIn('2030-03-01',days);self.assertNotIn('2030-03-02',days)
    def test_reconciled_old_reply_preserves_date_and_current_day_still_needs_delivery(self):
        i,source=self.queue();self.begin(i,source)
        outbox=self.root/'outbox'/i;status=read(outbox/'status.json');binding=read(outbox/status['request_binding'])
        readback=self.receipt(i,completed='2030-03-01T13:06:00Z',checked='2030-03-02T01:00:00Z')
        old=deepcopy(binding);old.pop('request_started_at');old['request_ref']='EARLIER_REQUEST';old['record_refs']=['EARLIER_REQUEST']
        status['request_binding']=delivery._save(outbox,'request',old);status['attempt']['request_ref']='EARLIER_REQUEST';status['state']='UNKNOWN';replace(outbox/'status.json',status)
        repair={'schema':'BTC_REQUEST_RECONCILIATION_V1','previous_request':{'record_ref':'EARLIER_REQUEST','started_at':'2030-03-01T13:04:00Z','body':old['request_body']},'binding':binding,'readback':readback,'reason':'真实历史请求早于本次尝试，恢复同次实际完成回复并保留真实完成日期。'}
        invalid=deepcopy(repair);invalid['readback']['assistant']['completed_at']='2030-03-01T13:04:30Z'
        with self.assertRaisesRegex(Invalid,'completion time'):
            delivery.transition(self.root,i,'RECONCILE_REQUEST',invalid)
        self.assertEqual(read(outbox/'status.json'),status)
        self.clock='2030-03-02T01:00:00Z'
        delivery.transition(self.root,i,'RECONCILE_REQUEST',repair)
        self.assertIn('2030-03-01',report_care.deliveries(self.root));self.assertFalse(report_care.status(self.root)['fulfilled'])
        reporting.record(self.root,self.spec('SILENT'));self.clock='2030-03-02T01:31:00Z'
        j,current_source=self.queue();self.begin(j,current_source);self.confirm(j)
        self.assertTrue(report_care.status(self.root)['fulfilled'])
        self.assertEqual(read(outbox/'status.json')['attempts'],1)

    def test_reply_cannot_complete_before_bound_cloud_request(self):
        i,source=self.queue();self.begin(i,source)
        status=read(self.root/'outbox'/i/'status.json');binding=read(self.root/'outbox'/i/status['request_binding'])
        binding['request_started_at']='2030-03-01T13:06:00Z'
        delivery.transition(self.root,i,'BIND_REQUEST',binding)
        with self.assertRaisesRegex(Invalid,'reply predates'):
            self.confirm(i,completed='2030-03-01T13:05:30Z',checked='2030-03-01T13:07:00Z')

    def test_late_confirmation_retains_newer_maintenance_and_daily_deadline(self):
        i,source=self.queue();self.begin(i,source)
        self.clock='2030-03-02T01:00:00Z'
        spec=self.spec('EVIDENCE_LIMITED');spec['maintenance']['checked']['outcome']='EVIDENCE_LIMITED'
        spec['maintenance']['unresolved']='今天核验的新增未决限制必须继续保留，不能被晚确认的旧展示覆盖。'
        later=reporting.record(self.root,spec)
        self.clock='2030-03-02T01:10:00Z';self.confirm(i,completed='2030-03-01T13:06:00Z')
        actual=report_care.status(self.root)
        self.assertEqual(actual['prior_review_id'],later['review_id'])
        self.assertEqual(actual['report_by'],spec['maintenance']['daily']['report_by'])
        self.assertEqual(actual['active_maintenance']['unresolved'],spec['maintenance']['unresolved'])
        self.assertFalse(actual['fulfilled']);self.assertFalse(actual['daily_plan_required'])
    def test_previous_day_reply_cannot_fulfil_next_day(self):
        self.clock='2030-03-01T14:58:00Z';q=self.spec();q['maintenance']['daily']['report_by']='2030-03-01T14:59:00Z';i,s=self.queue(q);self.begin(i,s)
        self.clock='2030-03-01T15:02:00Z';self.confirm(i);self.assertFalse(report_care.status(self.root)['fulfilled']);self.assertIn('2030-03-01',report_care.status(self.root)['missed_days'])
    def test_expression_only_does_not_count(self):
        i,s=self.queue(self.spec(kind='EXPRESSION_ONLY'));self.begin(i,s);self.confirm(i);self.assertFalse(report_care.status(self.root)['fulfilled'])
    def test_reply_arriving_after_recheck_deadline_is_not_current_daily_feedback(self):
        i,s=self.queue();self.begin(i,s);self.clock='2030-03-01T13:36:00Z';self.confirm(i)
        state=report_care.status(self.root)
        self.assertFalse(state['fulfilled']);self.assertTrue(state['time_due']);self.assertTrue(state['review_required'])
    def test_missing_maintenance_or_expired_recheck_rejected(self):
        s=self.spec();del s['maintenance']
        with self.assertRaisesRegex(Invalid,'obligations required'):reporting.record(self.root,s)
        s=self.spec();s['maintenance']['recheck_at']=self.clock
        with self.assertRaisesRegex(Invalid,'future'):reporting.record(self.root,s)
    def test_offline_gap_requires_current_feedback_not_fake_backfill(self):
        self.clock='2030-03-03T01:00:00Z';st=report_care.status(self.root);self.assertEqual(st['missed_days'],['2030-03-01','2030-03-02']);self.assertTrue(st['daily_due'])
    def test_report_must_show_meaningful_condition_and_review_time(self):
        s=self.spec();s['layout']['blocks'][-1]['text']='接下来继续观察当前行情是否发生变化。'
        with self.assertRaisesRegex(Invalid,'next review time'):reporting.record(self.root,s)

    def test_unknown_event_evidence_cannot_be_silenced(self):
        reporting.record(self.root,self.spec('SILENT'));s=self.spec('SILENT');s['maintenance']['checked']['trigger_results'][0]['state']='UNKNOWN'
        with self.assertRaisesRegex(Invalid,'unknown trigger'):reporting.record(self.root,s)
    def test_unstarted_expired_report_is_superseded(self):
        i,s=self.queue();self.clock='2030-03-01T13:36:00Z'
        state=delivery.transition(self.root,i,'BEGIN',{'schema':'BTC_DELIVERY_ATTEMPT_V1','destination':self.f.f.f.destination,'anchor_ref':'SYNTHETIC_ANCHOR','started_at':self.clock})
        self.assertEqual(state['state'],'SUPERSEDED')
    def test_event_met_can_revise_without_deep_or_new_observation(self):
        reporting.record(self.root,self.spec('SILENT'));s=self.spec();s['maintenance']['checked']['trigger_results'][0]['state']='MET';s['maintenance']['checked']['outcome']='REVISE';s['information']={'market':'原判断的反证成立，本轮必须更新实际展示解释'}
        i,src=self.queue(s);self.begin(i,src);self.confirm(i);self.assertTrue(report_care.status(self.root)['fulfilled'])
    def test_cli_status_does_not_require_a_new_market_package(self):
        import subprocess,json,sys
        from pathlib import Path
        cli=Path(__file__).resolve().parents[1]/'scripts/strategy_v4.py'
        result=subprocess.run([sys.executable,str(cli),'--project',str(self.root.parent.parent),'--root',str(self.root),'report-care','--run-ref','SYNTHETIC_NEW_RUN'],capture_output=True,text=True,check=True)
        self.assertTrue(json.loads(result.stdout)['enabled'])
