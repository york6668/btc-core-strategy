"""Generated process-evaluation wiring, not market or investment validation."""
from copy import deepcopy
import unittest
from unittest import mock

import test_investment_feedback as fixtures
import test_lifecycle_private as private_fixture
from v4 import closed_loop, investment, research, strategy_validation as validation
from v4.common import Invalid, create, digest, read, replace


class ProcessFeedbackTests(unittest.TestCase):
    def setUp(self):
        # Historical public fixtures supply the generated transport only. The
        # private decision/window and every evaluation below use revision 9.
        self.f=private_fixture.PrivateTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.validate()
        self.root=self.f.root;self.project=self.f.project
        self.core=deepcopy(self.f.f.core);self.core['revision']=9
        self.core['recommendations']['portfolio']['investment_decision']=deepcopy(self.f.value)
        self.e=fixtures.RuleValidationTests();self.e.setUp();self.addCleanup(self.e.doCleanups)
        self.bundle=self.e.bundle;self.directory=self.e.directory
        self.decision_id=self.owner(self.core)
        self.window=deepcopy(investment.decision(self.core)['evaluation_windows'][0])
        self.original=read(self.root/'versions'/self.decision_id/'decision.json')

    def owner(self,core,current=False):
        # An isolated immutable owner exercises the evaluation API without
        # publishing a synthetic assessment or replacing a production pointer.
        value={'assessment':deepcopy(core),'view_hashes':{}}
        identity=digest(value)[7:]
        path=self.root/'versions'/identity/'decision.json'
        owner=dict(value,decision_id=identity)
        if path.exists():self.assertEqual(read(path),owner)
        else:create(path,owner)
        if current:
            link=self.root/'current'
            if link.is_symlink():link.unlink()
            link.symlink_to('versions/'+identity,target_is_directory=True)
        return identity

    def spec(self,outcome='NOT_DUE',status='NOT_OCCURRED'):
        return {'decision_id':self.decision_id,'window_id':self.window['id'],'outcome':outcome,
                'explanation':'Generated later observation evaluates only the original declared mechanism.',
                'observed_at':self.bundle['cutoff'],
                'evidence_refs':[] if outcome in ('NOT_DUE','EVIDENCE_LIMITED') else [self.e.spec['bars'][0]['ref']],
                'process_progress':{'status':status,
                    'explanation':'Generated process has not occurred.' if status=='NOT_OCCURRED' else
                                  'Generated process observation is available.' if status=='OBSERVED' else
                                  'Generated process progress cannot be established from the available source.'}}

    def record(self,spec=None):
        with mock.patch('v4.strategy_validation.now',return_value=self.bundle['cutoff']):
            return validation.evaluate_judgement(self.root,spec or self.spec(),self.bundle,self.directory)

    def test_waiting_process_is_not_due_without_moving_original_conditions(self):
        receipt=self.record();saved=read(receipt['record'])
        self.assertIsNone(saved['baseline_window']['end'])
        self.assertEqual(saved['baseline_window'],self.window)
        self.assertEqual(saved['original_policy_version'],investment.decision(self.core)['policy']['version'])
        self.assertEqual(saved['original_investment_decision_hash'],digest(investment.decision(self.core)))
        self.assertEqual(saved['evidence_id'],self.bundle['evidence_id'])
        self.assertEqual(saved['recorded_at'],self.bundle['cutoff'])
        self.assertEqual(saved['performance'],'NOT_A_RETURN_MEASUREMENT')
        self.assertEqual(read(self.root/'versions'/self.decision_id/'decision.json'),self.original)
        feedback=validation.feedback(self.root,self.core)
        self.assertEqual(feedback['judgement_evaluations'],[saved])
        origin=feedback['judgement_evaluation_origins'][0]
        self.assertEqual(origin['evaluation_id'],receipt['evaluation_id'])
        self.assertEqual(origin['decision_id'],self.decision_id)
        self.assertTrue(origin['applies_to_current_investment_decision'])

    def test_null_end_does_not_exempt_observed_or_unknown_process(self):
        bad=self.spec();bad.pop('process_progress')
        with self.assertRaisesRegex(Invalid,'actual process progress'):self.record(bad)
        for status in ('OBSERVED','UNKNOWN'):
            with self.subTest(status=status),self.assertRaisesRegex(Invalid,'cannot remain NOT_DUE'):
                self.record(self.spec(status=status))
        for status in ('NOT_OCCURRED','UNKNOWN'):
            with self.subTest(status=status),self.assertRaisesRegex(Invalid,'actual observed progress'):
                self.record(self.spec('REALIZED',status))
        bad=self.spec('REALIZED','OBSERVED');bad['evidence_refs']=[]
        with self.assertRaisesRegex(Invalid,'needs evidence'):self.record(bad)

    def test_realized_failed_partial_and_unknown_keep_original_identity_in_feedback(self):
        states=[('REALIZED','OBSERVED'),('FAILED','OBSERVED'),('PARTIAL','OBSERVED'),('EVIDENCE_LIMITED','UNKNOWN')]
        for outcome,status in states:self.record(self.spec(outcome,status))
        feedback=validation.feedback(self.root,self.core)
        self.assertEqual({r['outcome'] for r in feedback['judgement_evaluations']},{s[0] for s in states})
        self.assertEqual(len(feedback['judgement_evaluation_origins']),4)
        for saved in feedback['judgement_evaluations']:
            self.assertEqual(saved['decision_id'],self.decision_id)
            self.assertEqual(saved['baseline_window'],self.window)
            self.assertEqual(saved['performance'],'NOT_A_RETURN_MEASUREMENT')
        self.assertEqual(investment.decision(self.core)['status'],'UNDETERMINED')
        self.assertEqual(investment.decision(self.core)['selected_ids'],[])

    def test_evidence_limited_is_unresolved_instead_of_realized(self):
        saved=read(self.record(self.spec('EVIDENCE_LIMITED','UNKNOWN'))['record'])
        feedback=validation.feedback(self.root,self.core)
        self.assertEqual(feedback['judgement_evaluations'][0]['outcome'],'EVIDENCE_LIMITED')
        self.assertEqual(saved['evidence_refs'],[])
        self.assertEqual(feedback['required_evaluation_windows'],[self.window])
        self.assertEqual(read(self.root/'versions'/self.decision_id/'decision.json'),self.original)

    def test_dated_window_still_cannot_stay_not_due_after_original_end(self):
        core=deepcopy(self.core)
        window=investment.decision(core)['evaluation_windows'][0]
        window['end']='2030-03-02T13:00:00Z'
        identity=self.owner(core)
        spec=self.spec();spec['decision_id']=identity;spec.pop('process_progress')
        with self.assertRaisesRegex(Invalid,'expired evaluation window cannot remain NOT_DUE'):
            self.record(spec)

    def test_changed_original_owner_is_rejected_by_record_and_feedback(self):
        self.record()
        path=self.root/'versions'/self.decision_id/'decision.json'
        damaged=read(path)
        investment.decision(damaged['assessment'])['evaluation_windows'][0]['success_evidence']='Changed after formation.'
        replace(path,damaged)
        with self.assertRaisesRegex(Invalid,'original evaluation decision changed'):self.record()
        with self.assertRaisesRegex(Invalid,'original evaluation decision changed'):
            validation.feedback(self.root,self.core)

    def test_same_policy_history_cannot_masquerade_as_current_decision_validation(self):
        saved=read(self.record(self.spec('REALIZED','OBSERVED'))['record'])
        successor=deepcopy(self.core)
        investment.decision(successor)['summary']='A new generated decision still requires its own evidence and evaluation.'
        self.f.validate(investment.decision(successor))
        feedback=validation.feedback(self.root,successor)
        self.assertEqual(feedback['judgement_evaluations'],[saved])
        self.assertFalse(feedback['judgement_evaluation_origins'][0]['applies_to_current_investment_decision'])
        self.assertEqual(feedback['judgement_evaluation_origins'][0]['decision_id'],self.decision_id)
        self.assertEqual(investment.decision(successor)['selected_ids'],[])

    def test_actual_portfolio_read_receives_recorded_feedback_without_changing_public_result(self):
        self.owner(self.core,current=True)
        public={'outlook':deepcopy(self.core['outlook']),
                'factor_assessment':deepcopy(self.core['factor_assessment']),
                'market_report':'SYNTHETIC unchanged public result; account gaps do not change it.'}
        public_hash=closed_loop.public_hash(public)
        create(self.root/'research/public-test-input.json',{'synthetic_transport':'No market or account observation.'})
        # These two request-phase owners are generated scaffolding for the
        # actual read_input consumer; they do not assert a model research run.
        request=research._save(self.root,'requests','request_id',{
            'workflow_revision':9,'independent_input_path':'research/public-test-input.json',
            'independent_input_id':digest({'synthetic_transport':'No market or account observation.'})})
        independent=research._save(self.root,'independent','independent_id',{
            'request_id':request['request_id'],'market_result':public,'market_result_hash':public_hash})
        before=research.read_input(self.root,request['request_id'],'portfolio',independent_id=independent['independent_id'])['payload']
        self.assertEqual(before['investment_feedback']['judgement_evaluations'],[])
        saved=read(self.record(self.spec('PARTIAL','OBSERVED'))['record'])
        after=research.read_input(self.root,request['request_id'],'portfolio',independent_id=independent['independent_id'])['payload']
        self.assertEqual(after['investment_feedback']['judgement_evaluations'],[saved])
        self.assertEqual(after['investment_feedback']['judgement_evaluation_origins'][0]['decision_id'],self.decision_id)
        self.assertEqual(after['market_result'],before['market_result'])
        self.assertEqual(after['market_result_hash'],before['market_result_hash'])
        self.assertEqual(after['market_content_hash'],public_hash)
        self.assertEqual(after['account_constraints']['exposure_complete'],None)


if __name__=='__main__':unittest.main()
