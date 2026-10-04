"""Synthetic report obligations and controlled at-least-once recovery."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from test_market_reporting import MarketReportingTests
from test_market_state import zone
from v4 import delivery, reporting, storage, market_state
from v4.common import read, create, digest, Invalid


class DeliveryObligationTests(MarketReportingTests):
    def unconfirmed(self, identity, checked='2030-03-01T15:09:00Z'):
        attempt = read(self.root/'outbox'/identity/'status.json')['attempt']['attempt_id']
        return delivery.transition(self.root, identity, 'UNKNOWN', {
            'schema':'BTC_UNCONFIRMED_READBACK_V1', 'attempt_id':attempt,
            'checked_at':checked, 'record_refs':['SYNTHETIC_BOUNDED_READ'],
            'detail':'The bounded read does not contain this request; dispatch remains uncertain.'})

    def replacement(self, old_source, meaning):
        self.seq += 1
        spec = self.spec(meaning=meaning)
        spec['delivery_coverage'] = [{'review_id':old_source['report_review']['review_id'],
            'explanation':'This replacement explicitly carries all still-important meaning of the old report.'}]
        rid = reporting.record(self.root, spec)['review_id']
        source = reporting.source(self.root, rid)
        identity = delivery.enqueue(self.root, self.f.decision['decision_id'], self.f.destination, source)['delivery_id']
        return identity, source

    def test_covered_unknown_waits_readonly_across_runs_and_late_receipt_still_confirms(self):
        old, source = self.queue(); self.begin(old, source)
        late = self.f.receipt(old)
        self.unconfirmed(old)
        newer, new_source = self.replacement(source, 'Explicitly corrected report')
        self.begin(newer, new_source); self.confirm(newer)
        status_path = self.root/'outbox'/old/'status.json'
        before, baseline = status_path.read_bytes(), read(self.root/'reported-current.json')
        for _ in range(2):
            recovered = storage.recover(self.root)
            item = next(v for v in recovered['attempt_recovery'] if v['delivery_id'] == old)
            self.assertEqual(item['state'], 'UNKNOWN')
            self.assertEqual(item['next_action'], 'WAIT_FOR_NEW_EVIDENCE')
            self.assertEqual(item['recovery_evidence']['delivery_id'], newer)
            self.assertEqual(item['recovery_evidence']['coverage_review_ids'], [new_source['report_review']['review_id']])
            self.assertFalse(recovered['delivery_recovery'])
        self.assertEqual(status_path.read_bytes(), before)
        self.assertEqual(read(self.root/'reported-current.json'), baseline)
        delivery.transition(self.root, old, 'CONFIRM', late)
        self.assertEqual(read(status_path)['state'], 'DELIVERED')
        self.assertEqual(read(self.root/'reported-current.json'), baseline)

    def test_pending_replacement_and_plain_later_report_do_not_defer_unknown(self):
        old, source = self.queue(); self.begin(old, source); self.unconfirmed(old)
        self.seq += 1
        other, other_source = self.queue('Unrelated subsequent report')
        self.begin(other, other_source); self.confirm(other)
        self.assertEqual(delivery.attempt_recovery(self.root)[0]['next_action'], 'READ_BACK_THIS_ATTEMPT')
        newer, _ = self.replacement(source, 'Explicit but not yet delivered replacement')
        self.assertEqual(delivery.attempt_recovery(self.root)[0]['next_action'], 'READ_BACK_THIS_ATTEMPT')
        self.assertTrue(reporting.obligations(self.root))
        self.assertIn(newer, {v['delivery_id'] for v in delivery.recovery_actions(self.root)})

    def test_multihop_coverage_requires_actual_readback_and_valid_receipt(self):
        old, source = self.queue(); self.begin(old, source); delivery.transition(self.root, old, 'UNKNOWN')
        middle, middle_source = self.replacement(source, 'Intermediate correction')
        last, last_source = self.replacement(middle_source, 'Complete correction')
        self.begin(last, last_source); self.confirm(last)
        self.assertEqual(delivery.attempt_recovery(self.root)[0]['next_action'], 'READ_BACK_THIS_ATTEMPT')
        self.unconfirmed(old)
        item = delivery.attempt_recovery(self.root)[0]
        self.assertEqual(item['next_action'], 'WAIT_FOR_NEW_EVIDENCE')
        self.assertEqual(item['recovery_evidence']['coverage_review_ids'],
                         [middle_source['report_review']['review_id'], last_source['report_review']['review_id']])
        with patch('v4.delivery.validate_delivered', side_effect=Invalid('damaged receipt')):
            self.assertEqual(delivery.attempt_recovery(self.root)[0]['next_action'], 'READ_BACK_THIS_ATTEMPT')
        # A malformed historical cycle must terminate without inventing delivery.
        deliveries = {old:(read(self.root/'outbox'/old/'artifact.json'), read(self.root/'outbox'/old/'status.json'))}
        rid = source['report_review']['review_id']
        self.assertIsNone(delivery._delivered_coverage(self.root, old, deliveries, {rid:['cycle'], 'cycle':[rid]}, {}))

    def test_new_transport_reopens_covered_unknown_until_read_again(self):
        old, source = self.queue(); self.begin(old, source); self.unconfirmed(old)
        newer, new_source = self.replacement(source, 'Confirmed correction')
        self.begin(newer, new_source); self.confirm(newer)
        self.assertEqual(delivery.attempt_recovery(self.root)[0]['next_action'], 'WAIT_FOR_NEW_EVIDENCE')
        attempt = read(self.root/'outbox'/old/'status.json')['attempt']['attempt_id']
        delivery.transition(self.root, old, 'ACKNOWLEDGE', {'schema':'BTC_TRANSPORT_RECEIPT_V1',
            'attempt_id':attempt, 'recorded_at':'2030-03-01T15:10:00Z', 'status':'FAILED',
            'record_refs':['SYNTHETIC_NEW_TRANSPORT'], 'detail':'New provider failure evidence for this original request.'})
        self.assertEqual(delivery.attempt_recovery(self.root)[0]['next_action'], 'READ_BACK_THIS_ATTEMPT')
        self.unconfirmed(old, '2030-03-01T15:11:00Z')
        self.assertEqual(delivery.attempt_recovery(self.root)[0]['next_action'], 'WAIT_FOR_NEW_EVIDENCE')

    def test_successful_same_report_retry_defers_only_the_read_original_attempt(self):
        identity, source = self.queue(); self.begin(identity, source)
        original = self.unconfirmed(identity)['attempt']['attempt_id']
        retried = delivery.transition(self.root, identity, 'RETRY_UNKNOWN', self.retry_spec(identity))
        attempt = retried['attempt']
        delivery.transition(self.root, identity, 'BIND_REQUEST', {
            'schema':'BTC_SENT_REQUEST_V1', 'attempt_id':attempt['attempt_id'],
            'destination':self.f.destination, 'request_ref':'CONFIRMED_RETRY_REQUEST',
            'record_refs':['CONFIRMED_RETRY_REQUEST'], 'request_body':source['body'],
            'anchor_ref':attempt['anchor_ref'], 'request_started_at':'2030-03-01T15:10:01Z'})
        self.confirm(identity)
        pending = delivery.attempt_recovery(self.root)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['attempt_id'], original)
        self.assertEqual(pending[0]['next_action'], 'WAIT_FOR_NEW_EVIDENCE')
        self.assertEqual(pending[0]['recovery_evidence']['coverage_review_ids'], [])
        self.assertFalse(delivery.recovery_actions(self.root, pending))

    def test_transport_acceptance_and_processing_are_not_display_or_retry_permission(self):
        identity,source=self.queue();self.begin(identity,source)
        status=read(self.root/'outbox'/identity/'status.json')
        receipt={'schema':'BTC_TRANSPORT_RECEIPT_V1','attempt_id':status['attempt']['attempt_id'],
            'recorded_at':'2030-03-01T15:05:00Z','status':'PROCESSING','record_refs':['synthetic-tool-receipt'],
            'detail':'Provider explicitly reports the request is still running; no completed assistant body exists.'}
        value=delivery.transition(self.root,identity,'ACKNOWLEDGE',receipt)
        self.assertEqual(value['state'],'SENDING')
        self.assertTrue(reporting.obligations(self.root))
        self.assertEqual(delivery.attempt_recovery(self.root)[0]['next_action'],'WAIT_FOR_PROCESSING_THEN_READ_BACK')
        with self.assertRaisesRegex(Invalid,'still processing'):
            delivery.transition(self.root,identity,'RETRY_UNKNOWN',self.retry_spec(identity))
        failed=dict(receipt,status='FAILED',recorded_at='2030-03-01T15:06:00Z')
        self.assertEqual(delivery.transition(self.root,identity,'ACKNOWLEDGE',failed)['state'],'UNKNOWN')
        with self.assertRaisesRegex(Invalid,'late transport'):
            delivery.transition(self.root,identity,'ACKNOWLEDGE',receipt)
        self.assertTrue(reporting.obligations(self.root))
    def test_legacy_automatic_replacement_is_not_coverage_evidence(self):
        old = reporting.record(self.root, self.spec())['review_id']
        new = reporting.record(self.root, self.spec(meaning='Later report without an explicit coverage review'))['review_id']
        legacy = deepcopy(reporting.review(self.root, new))
        legacy.pop('review_id');legacy.pop('delivery_coverage', None)
        legacy['supersedes'] = [old]
        legacy['review_id'] = digest(legacy)
        create(self.root/'report-reviews'/(legacy['review_id'][7:]+'.json'), legacy)
        self.assertIn(old, {v['review_id'] for v in reporting.obligations(self.root)})

    def test_silent_and_limited_reviews_preserve_queued_and_unqueued_reports(self):
        first = reporting.record(self.root, self.spec())['review_id']
        for action in ('SILENT', 'EVIDENCE_LIMITED'):
            reporting.record(self.root, self.spec(action=action))
            self.assertIn(first, {v['review_id'] for v in reporting.obligations(self.root)})
        source = reporting.source(self.root, first)
        identity = delivery.enqueue(self.root, self.f.decision['decision_id'], self.f.destination, source)['delivery_id']
        self.assertFalse(reporting.pending(self.root))
        self.assertEqual(reporting.obligations(self.root)[0]['deliveries'][0]['delivery_id'], identity)
        reporting.record(self.root, self.spec(action='SILENT'))
        self.assertTrue(reporting.obligations(self.root))

    def test_replacement_transfers_obligation_until_actual_display(self):
        old, source = self.queue()
        old_rid = source['report_review']['review_id']
        spec = self.spec(meaning='Corrected boundary and all retained risk implications')
        spec['delivery_coverage'] = [{'review_id':old_rid, 'explanation':'The new report corrects the old boundary and covers every still-important risk implication.'}]
        rid = reporting.record(self.root, spec)['review_id']
        new_source = reporting.source(self.root, rid)
        new = delivery.enqueue(self.root, self.f.decision['decision_id'], self.f.destination, new_source)['delivery_id']
        delivery.transition(self.root, old, 'SUPERSEDE_PENDING', {'schema':'BTC_REPORT_REPLACEMENT_V1',
            'replacement_delivery_id':new, 'reason':'The reviewed corrected report retains all material obligations of the unsent old draft.'})
        self.assertEqual([v['review_id'] for v in reporting.obligations(self.root)], [rid])
        self.begin(new, new_source)
        self.assertTrue(reporting.obligations(self.root))
        self.confirm(new)
        self.assertFalse(reporting.obligations(self.root))

    def retry_spec(self, identity, run='SYNTHETIC_RECOVERY_RUN'):
        attempt = read(self.root/'outbox'/identity/'status.json')['attempt']
        return {'schema':'BTC_DELIVERY_RETRY_V1','destination':self.f.destination,
            'anchor_ref':'ACTUAL_NEW_CURSOR','started_at':'2030-03-01T15:10:00Z',
            'prior_attempt_id':attempt['attempt_id'],'run_ref':run,
            'current_context_id':self.context(self.root)['context_id'],
            'readback':{'checked_at':'2030-03-01T15:09:00Z','record_refs':['ACTUAL_THREAD_READBACK'], 'outcome':'UNCONFIRMED'},
            'applicability_review':'Current public evidence still supports this completed important report and its implications.',
            'duplicate_risk_accepted':True}

    def test_controlled_retry_is_idempotent_and_late_old_receipt_keeps_newer_baseline(self):
        identity, source = self.queue(); self.begin(identity, source)
        old_receipt = self.f.receipt(identity)
        delivery.transition(self.root, identity, 'UNKNOWN')
        retry = self.retry_spec(identity)
        second = delivery.transition(self.root, identity, 'RETRY_UNKNOWN', retry)
        self.assertTrue(second['ready_to_send']);self.assertEqual(second['attempts'], 2)
        self.assertNotEqual(second['attempt']['attempt_id'], old_receipt['attempt_id'])
        self.assertEqual(second['attempt']['run_ref'], retry['run_ref'])
        self.assertFalse(delivery.transition(self.root, identity, 'RETRY_UNKNOWN', retry)['ready_to_send'])
        delivery.transition(self.root, identity, 'UNKNOWN')
        repeat = self.retry_spec(identity);repeat['readback']['checked_at']='2030-03-01T15:11:00Z';repeat['started_at']='2030-03-01T15:12:00Z'
        with self.assertRaisesRegex(Invalid, 'one recovery resend'):
            delivery.transition(self.root, identity, 'RETRY_UNKNOWN', repeat)
        self.seq=2; newer, new_source=self.queue('New important event while the retry remains uncertain')
        self.begin(newer,new_source);self.confirm(newer)
        late=delivery.transition(self.root,identity,'CONFIRM',old_receipt)
        self.assertEqual(late['state'],'DELIVERED')
        self.assertEqual(read(self.root/'reported-current.json')['delivery_id'],newer)
        pending=delivery.attempt_recovery(self.root)
        self.assertIn(second['attempt']['attempt_id'],{v['attempt_id'] for v in pending})
        self.assertEqual(late['attempts'],2)
        self.assertTrue(list((self.root/'outbox'/identity).glob('attempt-state-*.json')))

    def test_retry_requires_readback_and_current_evidence(self):
        identity, source=self.queue();self.begin(identity,source);delivery.transition(self.root,identity,'UNKNOWN')
        for field in ('readback','current_context_id'):
            retry=self.retry_spec(identity)
            if field=='readback':retry[field]['record_refs']=[]
            else:retry[field]='STALE_CONTEXT'
            with self.assertRaises(Invalid):delivery.transition(self.root,identity,'RETRY_UNKNOWN',retry)
        self.assertEqual(read(self.root/'outbox'/identity/'status.json')['attempts'],1)

    def test_each_unconfirmed_readback_stays_with_its_attempt(self):
        identity,source=self.queue();self.begin(identity,source)
        first=read(self.root/'outbox'/identity/'status.json')['attempt']['attempt_id']
        receipt={'schema':'BTC_UNCONFIRMED_READBACK_V1','attempt_id':first,
            'checked_at':'2030-03-01T15:09:00Z','record_refs':['ACTUAL_READBACK_ONE'],
            'detail':'The thread returned no completed assistant for this request; absence is not non-dispatch.'}
        state=delivery.transition(self.root,identity,'UNKNOWN',receipt)
        self.assertEqual(len(state['unknown_readbacks']),1)
        self.assertEqual(delivery.transition(self.root,identity,'UNKNOWN',receipt)['unknown_readbacks'],state['unknown_readbacks'])
        retry=self.retry_spec(identity)
        second=delivery.transition(self.root,identity,'RETRY_UNKNOWN',retry)
        receipt.update(checked_at='2030-03-01T15:12:00Z',record_refs=['ACTUAL_READBACK_TWO'])
        state=delivery.transition(self.root,identity,'UNKNOWN',receipt)
        self.assertEqual(state['attempt']['attempt_id'],second['attempt']['attempt_id'])
        self.assertEqual(state['state'],'SENDING')
        prior=read(self.root/'outbox'/identity/state['attempt_records'][first])
        self.assertEqual(len(prior['unknown_readbacks']),2)
        self.assertNotIn('unknown_readbacks',state)

    def test_retry_and_display_correction_share_one_run_budget(self):
        identity,source=self.queue();self.begin(identity,source)
        delivery.transition(self.root,identity,'UNKNOWN')
        retry=self.retry_spec(identity)
        state=delivery.transition(self.root,identity,'RETRY_UNKNOWN',retry)
        attempt=state['attempt']
        delivery.transition(self.root,identity,'BIND_REQUEST',{
            'schema':'BTC_SENT_REQUEST_V1','attempt_id':attempt['attempt_id'],
            'destination':self.f.destination,'request_ref':'RETRY_REQUEST',
            'record_refs':['RETRY_REQUEST'],'request_body':source['body'],
            'anchor_ref':attempt['anchor_ref'],'request_started_at':'2030-03-01T15:10:01Z'})
        receipt=self.f.receipt(identity,body=source['body']+' 99999')
        state=delivery.transition(self.root,identity,'CONFIRM',receipt)
        self.assertEqual(state['state'],'CORRECTION_REQUIRED')
        with self.assertRaisesRegex(Invalid,'one recovery resend'):
            delivery.transition(self.root,identity,'BEGIN_CORRECTION',{
                'schema':'BTC_DELIVERY_ATTEMPT_V1','destination':self.f.destination,
                'anchor_ref':'RETRY_RESPONSE','started_at':'2030-03-01T16:05:00Z'})

    def test_parameter_rejection_never_counts_as_delivery(self):
        identity,source=self.queue();self.begin(identity,source)
        self.assertTrue(reporting.obligations(self.root))
        self.assertFalse((self.root/'reported-current.json').exists())
        with self.assertRaises(Invalid):delivery.transition(self.root,identity,'CONFIRM',{'tool_receipt':'OK'})
        self.assertTrue(reporting.obligations(self.root))

    def test_pending_repricing_and_completed_result_are_distinct_events(self):
        for phase in ('IMPACT_PENDING','COMPLETED','CORRECTION'):
            spec=self.spec();impact=spec['layout']['overview']
            spec['important_events']=[{'id':'repricing:'+phase,'kind':'REPRICING','phase':phase,'impact':impact}]
            self.assertEqual(reporting.record(self.root,spec)['action'],'REPORT')
            spec['action']='EVIDENCE_LIMITED';spec['layout']=None
            with self.assertRaisesRegex(Invalid,'must be reported'):reporting.record(self.root,spec)
        self.assertEqual(len(reporting.obligations(self.root)),3)

    def test_deep_completion_requires_feedback_even_with_identical_rating(self):
        spec=self.spec(action='SILENT');ctx=self.context(self.root)
        event={'id':'deep-result:actual-result','kind':'DEEP_REVIEW','phase':'COMPLETED','impact':None}
        ctx['required_report_events']=[event]
        with patch('v4.reporting.context',return_value=ctx):
            with self.assertRaisesRegex(Invalid,'completed research'):reporting.record(self.root,spec)
            spec=self.spec();spec['important_events']=[dict(event,impact=spec['layout']['overview'])]
            self.assertEqual(reporting.record(self.root,spec)['action'],'REPORT')


class BoundaryReplayTests(unittest.TestCase):
    def test_retired_replacement_parses_before_history_comparison(self):
        ref={'source_id':'synthetic-public','pointer':'/bars','value_hash':'synthetic'}
        original=zone('2030-03-01T14:00:00Z',[ref],stage='CONTESTED')
        retired=deepcopy(original)
        retired.update(status='RETIRED',retirement_reason='The old combined range mixed two roles.')
        replacement=deepcopy(original)
        replacement.update(id='recent-main',supersedes=[original['id']])
        values=[retired,replacement]
        market_state.validate_zones(values,lambda ref:None)
        market_state.validate_zones(values,lambda ref:None,prior=[original])
        with self.assertRaisesRegex(Invalid,'explicitly retire'):
            market_state.validate_zones(values,lambda ref:None,prior=[])
        with self.assertRaisesRegex(Invalid,'explicitly retire'):
            market_state.validate_zones([replacement],lambda ref:None)
        with self.assertRaisesRegex(Invalid,'explicitly retire'):
            market_state.validate_zones([original,replacement],lambda ref:None)

    def test_recent_untested_candidate_and_independent_unknown_side(self):
        ref={'source_id':'synthetic-public','pointer':'/flow','value_hash':'synthetic'}
        z=zone('2030-03-01T14:00:00Z',[ref],stage='UNKNOWN')
        z['area']={'lower':None,'upper':85500,'label':'Recent candidate; lower unknown'}
        side={'formation':'RECENT_INFERENCE','validation':'UNTESTED',
              'evidence_window':{'start':'2030-03-01T10:00:00Z','end':'2030-03-01T14:00:00Z'},
              'identified_at':'2030-03-01T14:00:00Z','tested_at':None,'horizon':'Next several hours',
              'basis':'Recent executed flow suggests a candidate; no order-book claim.',
              'refs':[ref],'next_check':'Observe the first price response and whether recent flow persists.'}
        z['boundary_review']={'revision':2,'lower':dict(side,formation='UNKNOWN',validation='UNKNOWN',refs=[]),
                              'upper':side,'prior_zone_assessment':'Do not backfill the missing current lower side from a historical extreme.'}
        market_state.validate_zones([z],lambda ref:None)
        replay=deepcopy(z);replay['boundary_review']['upper']['identified_at']='2030-03-01T14:30:00Z'
        market_state.validate_zones([replay],lambda ref:None,cutoff=z['as_of'])
        supported=deepcopy(z);supported['as_of']='2030-03-01T15:00:00Z'
        supported['boundary_review']['upper'].update(formation='RECENT_TESTED',validation='SUPPORTED',tested_at='2030-03-01T15:00:00Z')
        market_state.validate_zones([supported],lambda ref:None,[z])
        self.assertEqual(supported['boundary_review']['upper']['identified_at'],side['identified_at'])
        self.assertIsNone(supported['area']['lower'])
        historical=deepcopy(supported);historical['boundary_review']['upper']['formation']='HISTORICAL_REFERENCE'
        market_state.validate_zones([historical],lambda ref:None,[supported])
        bad=deepcopy(z);bad['boundary_review']['upper']['tested_at']='2030-03-01T15:00:00Z'
        with self.assertRaisesRegex(Invalid,'future evidence'):market_state.validate_zones([bad],lambda ref:None)
        bad=deepcopy(z);bad['boundary_review']['upper']['validation']='SUPPORTED'
        with self.assertRaisesRegex(Invalid,'untested'):market_state.validate_zones([bad],lambda ref:None)
        bad=deepcopy(z);bad['area']['lower']=82500
        with self.assertRaisesRegex(Invalid,'unknown side'):market_state.validate_zones([bad],lambda ref:None)

    def test_internal_crossing_outer_break_return_and_boundary_correction(self):
        ref={'source_id':'synthetic-public','pointer':'/bars','value_hash':'synthetic'}
        main=zone('2030-03-01T14:00:00Z',[ref],stage='CONTESTED')
        main.update(role='MAIN_DISPUTE', area={'lower':82500,'upper':85600,'label':'Current assessed main range'},
            boundary_review={'lower':{'observed_at':'2030-02-28T00:00:00Z','basis':'Observed lower reactions','refs':[ref]},
                'upper':{'observed_at':'2030-03-01T14:00:00Z','basis':'New upper rejection first known at 14:00','refs':[ref]},
                'prior_zone_assessment':'Old upper level described an internal gate and cannot define the whole range.'})
        internal=deepcopy(main);internal.update(id='internal',role='INTERNAL_LEVEL',horizon='Intrahour',
            area={'lower':83500,'upper':83500,'label':'Internal gate'})
        internal['breakout'].update(direction='UP',stage='INITIAL_BREAK')
        market_state.validate_zones([main,internal],lambda ref:None,cutoff=main['as_of'])
        self.assertEqual(main['breakout']['stage'],'CONTESTED')
        outer=deepcopy(main);outer['breakout'].update(direction='UP',stage='INITIAL_BREAK');outer['as_of']='2030-03-01T15:00:00Z'
        market_state.validate_zones([outer,internal],lambda ref:None,[main,internal],outer['as_of'])
        returned=deepcopy(outer);returned['breakout'].update(stage='FAILED');returned['as_of']='2030-03-01T16:00:00Z'
        market_state.validate_zones([returned,internal],lambda ref:None,[outer,internal],returned['as_of'])
        self.assertEqual(internal['horizon'],'Intrahour');self.assertNotEqual(main['horizon'],internal['horizon'])
        backdated=deepcopy(main);backdated['as_of']='2030-03-01T13:00:00Z'
        with self.assertRaisesRegex(Invalid,'future evidence'):market_state.validate_zones([backdated],lambda ref:None)
        retired=deepcopy(main);retired.update(status='RETIRED',retirement_reason='New independent evidence rejects these boundaries.')
        corrected=deepcopy(main);corrected.update(id='corrected-main',supersedes=[main['id']])
        corrected['area']['upper']=86000
        market_state.validate_zones([retired,corrected],lambda ref:None,[main],corrected['as_of'])


if __name__=='__main__':unittest.main()
