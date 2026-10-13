"""Actual-value/time/scope regressions, generated offline; no performance proof."""
from copy import deepcopy
from pathlib import Path
import tempfile, unittest
from unittest import mock

from v4 import forward_lifecycle as life, closed_loop, evidence, market_state, research
from v4.common import Invalid, digest, create, read

def register_public(root, market):
    path=Path(root)/'test-freezes'/(digest(market)[7:]+'.json')
    create(path, {'created_at':life.now(), 'market_assessment':market})
    life.register(root,market,path,'/created_at')

BASE={'subject':'BTC','venue':'BINANCE','product':'SPOT','quote_unit':'USDT','measure':'PRICE'}
T0='2024-01-01T00:00:00Z';T1='2024-01-02T00:00:00Z';T2='2024-01-03T00:00:00Z'


def anchor():return {'source_id':'before','pointer':'/0/4','value_hash':digest('100')}


def proposition(identity='old',at=T0,kind='FACTS'):
    return {'id':identity,'formed_at':at,'data_cutoff':at,'scope':deepcopy(BASE),'basis_kind':kind,
        'basis_refs':[anchor()], 'observation_relations':[], 'scale':{'process':'A later actual price response to a real shock.','end':None,'end_ref':None},
        'expectations':[{'id':'price-response','mechanism':'Only the scoped price response, not external capital or whole-cycle reversal.',
            'scope':deepcopy(BASE),
            'support':{'left':{'sample':'later','path':'/4'},'op':'GT','right':{'anchor':anchor()}},
            'falsification':{'left':{'sample':'later','path':'/4'},'op':'LT','right':{'anchor':anchor()}}}]}


def forward(c, reviews=(),dependencies=()):
    return {'observed_window':{'start':c['data_cutoff'],'end':c['data_cutoff']},
        'judgement_window':{'start':c['formed_at'],'end':c['scale']['end']},'realization_window':None,
        'timing_basis':'Mechanism progress, no inherited calendar.', 'conditions':['Check the registered later price response.'],
        'invalidations':['The scoped response fails.'],
        'prior_check':{'outcome':'EVIDENCE_LIMITED' if reviews else 'INITIAL','explanation':'Evaluate only the actual observed component.','refs':[]},
        'lifecycle':{'claim':c,'reviews':list(reviews),'dependencies':list(dependencies)}}


class ForwardTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='prospective-contract-');self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.directory=self.root/'evidence';self.directory.mkdir()
        row0=[1704067199999,'100','100','100','100','1',1704067199999,'100',1,'0.5','50','0']
        row1=[1704153599999,'100','111','99','110','1',1704153600000,'110',1,'0.5','55','0']
        self.raw=[row0,row1];payload=(self.directory/'raw.json');create(payload,self.raw)
        h=digest(payload.read_bytes());target=self.directory/'raw'/(h[7:]+'.json');create(target,payload.read_bytes())
        self.bundle={'sources':[{'id':'raw','origin':'BINANCE','url':'https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT',
            'trust':'PUBLIC_RAW','start':'2023-12-31T23:00:00Z','end':T2,'retrieved_at':T2,'published_at':None,'raw_hash':h}]}
        old_payload=self.directory/'old.json';create(old_payload,[row0]);old_hash=digest(old_payload.read_bytes())
        create(self.directory/'raw'/(old_hash[7:]+'.json'),old_payload.read_bytes())
        old_source=dict(self.bundle['sources'][0],id='before',end=T0,retrieved_at=T0,raw_hash=old_hash)
        self.bundle['sources'].append(old_source)
        self.resolve=evidence.forward_resolver(self.bundle,self.directory)
        self.old=proposition()
        self.prior={'forward':forward(self.old)}
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[self.prior]}]})
        ref={'source_id':'raw','pointer':'/1','value_hash':digest(row1)}
        self.review={'prior_claim_hash':digest(self.old),'kind':'FORWARD_OBSERVATION','evaluated_at':T2,
            'samples':[{'id':'later','ref':ref,'time_pointer':'/6',
                       'scope':{'subject':'BTC','venue':'BINANCE','product':'SPOT'}}],
            'outcomes':[{'expectation_id':'price-response','verdict':'SUPPORTED','supported_scope':deepcopy(BASE),
                         'explanation':'The referenced later close is 110 versus the original 100; only this response is supported.'}]}

    def derived(self,review=None):
        r=deepcopy(review or self.review);c=proposition('next',T2,'VALIDATED_PREDECESSOR')
        return forward(c,[r],[{'claim_hash':digest(self.old),'expectation_id':'price-response',
                              'review_hash':digest(r),'scope':deepcopy(BASE)}])

    def check(self,v=None,prior=None):
        with mock.patch('v4.forward_lifecycle.now',return_value=T2):
            return closed_loop.validate_forward(v or self.derived(),T2,self.resolve,
                                                self.prior if prior is None else prior,revision=9,lifecycle_root=self.root)

    def test_actual_later_value_and_source_scope_grant_one_limited_successor(self):
        result=self.check();self.assertEqual(result['granted_dependencies'],1)
        self.assertEqual(result['verification'][0]['verdict'],'SUPPORTED')

    def test_support_label_with_failing_real_value_is_rejected(self):
        bad_row=deepcopy(self.raw[1]);bad_row[4]='90'
        payload=self.directory/'down.json';create(payload,[bad_row]);h=digest(payload.read_bytes())
        create(self.directory/'raw'/(h[7:]+'.json'),payload.read_bytes())
        self.bundle['sources'].append(dict(self.bundle['sources'][0],id='down',raw_hash=h))
        r=deepcopy(self.review);r['samples'][0]['ref']={'source_id':'down','pointer':'/0','value_hash':digest(bad_row)}
        with self.assertRaisesRegex(Invalid,'falsified'):self.check(self.derived(r))

    def test_late_read_old_observation_is_not_prediction_after_fact(self):
        self.bundle['sources'][0]['retrieved_at']=T2
        r=deepcopy(self.review);r['samples'][0]['ref']={'source_id':'raw','pointer':'/0','value_hash':digest(self.raw[0])}
        with self.assertRaisesRegex(Invalid,'late-read|same-sample'):self.check(self.derived(r))

    def test_same_sample_reread_and_self_review_do_not_advance(self):
        r=deepcopy(self.review);r['kind']='FROZEN_REPLAY'
        with self.assertRaisesRegex(Invalid,'cannot grant'):self.check(self.derived(r))

    def test_engineering_backtest_adoption_are_not_market_evidence(self):
        for kind in ('ENGINEERING','BACKTEST','POSTHOC'):
            r=deepcopy(self.review);r['kind']=kind
            with self.subTest(kind=kind),self.assertRaisesRegex(Invalid,'cannot grant'):self.check(self.derived(r))

    def test_partial_support_cannot_expand_to_funding_or_other_venues(self):
        for scope in (dict(BASE,measure='EXTERNAL_CAPITAL'),dict(BASE,venue=['BINANCE','COINBASE','KRAKEN'])):
            bad=self.derived();bad['lifecycle']['claim']['scope']=scope
            with self.assertRaisesRegex(Invalid,'scope'):self.check(bad)

    def test_sample_single_venue_cannot_be_declared_multi_venue(self):
        r=deepcopy(self.review);r['samples'][0]['scope']['venue']=['BINANCE','COINBASE','KRAKEN']
        with self.assertRaisesRegex(Invalid,'single-source'):self.check(self.derived(r))

    def test_price_row_cannot_validate_a_flow_component(self):
        bad=self.derived();bad['lifecycle']['reviews'][0]['outcomes'][0]['supported_scope']['measure']='EXTERNAL_CAPITAL'
        with self.assertRaisesRegex(Invalid,'scope'):self.check(bad)

    def test_falsified_original_condition_cannot_continue(self):
        old=deepcopy(self.old);part=old['expectations'][0]
        part['support']['op']='LT';part['falsification']['op']='GT'
        prior={'forward':forward(old)}
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[prior]}]})
        r=deepcopy(self.review);r['prior_claim_hash']=digest(old)
        bad=forward(proposition('bad-next',T2,'VALIDATED_PREDECESSOR'),[r],
            [{'claim_hash':digest(old),'expectation_id':'price-response','review_hash':digest(r),'scope':deepcopy(BASE)}])
        with self.assertRaisesRegex(Invalid,'falsified'):self.check(bad,prior)

    def test_unknown_predecessor_does_not_support_successor(self):
        r=deepcopy(self.review);r['samples']=[];r['outcomes'][0].update(verdict='UNKNOWN',supported_scope={})
        with self.assertRaisesRegex(Invalid,'unverified predecessor'):self.check(self.derived(r))

    def test_first_registration_clock_prevents_forged_backdating(self):
        record=life.registration(self.root,digest(self.old));record['registered_at']=T2
        from v4.common import replace
        replace(self.root/'market-lifecycle/claims'/(digest(self.old)[7:]+'.json'),record)
        with self.assertRaisesRegex(Invalid,'freeze'):self.check()

    def test_original_condition_is_immutable(self):
        bad=deepcopy(self.prior['forward']);bad['lifecycle']['claim']['expectations'][0]['support']['op']='GE'
        bad['prior_check']['outcome']='EVIDENCE_LIMITED'
        with self.assertRaisesRegex(Invalid,'immutable'):self.check(bad)

    def test_unknown_first_formation_or_missing_registration_is_posthoc_only(self):
        path=self.root/'market-lifecycle/claims'/(digest(self.old)[7:]+'.json');path.unlink()
        with self.assertRaisesRegex(Invalid,'posthoc'):self.check()

    def test_unverified_predecessor_does_not_block_independent_facts_correction_or_risk(self):
        for kind in ('FACTS','INDEPENDENT_NEW_FACTS','FACT_CORRECTION','RISK_RESPONSE'):
            c=proposition('independent-'+kind,T2,kind);f=forward(c);f['prior_check']['outcome']='EVIDENCE_LIMITED'
            with self.subTest(kind=kind):self.assertEqual(self.check(f)['granted_dependencies'],0)

    def test_new_forecast_cannot_inherit_november_deadline(self):
        bad=self.derived();bad['lifecycle']['claim']['scale'].update(end='2024-11-25T00:00:00Z',end_ref=None)
        bad['judgement_window']['end']='2024-11-25T00:00:00Z'
        with self.assertRaisesRegex(Invalid,'calendar evidence'):self.check(bad)

    def test_new_forecast_cannot_inherit_old_start(self):
        bad=self.derived();bad['judgement_window']['start']=T0
        with self.assertRaisesRegex(Invalid,'historical prediction start'):self.check(bad)

    def test_legacy_record_is_readable_but_downgraded(self):
        old=deepcopy(self.prior['forward']);old.pop('lifecycle');old['judgement_window']['end']=T2
        result=closed_loop.validate_forward(old,T0,self.resolve,revision=8)
        self.assertEqual(result['status'],'POSTHOC_ONLY');self.assertFalse(result['prospective_support'])

    def test_revision9_reviews_actual_legacy_without_backdating_registration(self):
        old=deepcopy(self.prior['forward']);old.pop('lifecycle')
        reviewed=deepcopy(old);reviewed['observed_window']['end']=T2
        reviewed['prior_check']['outcome']='EVIDENCE_LIMITED'
        reviewed['prior_check']['explanation']='Later facts remain insufficient to validate the old prediction.'
        reviewed['timing_basis']='Original timing is retained; later evidence is posthoc only.'
        result=closed_loop.validate_forward(reviewed,T2,self.resolve,{'forward':old},revision=9,lifecycle_root=self.root)
        self.assertEqual(result['status'],'POSTHOC_ONLY')
        self.assertFalse(result['prospective_support'])
        self.assertEqual(reviewed['judgement_window'],old['judgement_window'])
        for changed in ('conditions','invalidations','judgement_window','realization_window'):
            invalid=deepcopy(reviewed)
            invalid[changed]=['New invented criterion'] if changed in ('conditions','invalidations') else {'start':T2,'end':T2}
            with self.subTest(changed=changed),self.assertRaisesRegex(Invalid,'legacy forward cannot rewrite'):
                closed_loop.validate_forward(invalid,T2,self.resolve,{'forward':old},revision=9,lifecycle_root=self.root)

    def test_revision9_new_and_registered_forward_cannot_drop_lifecycle(self):
        no_life=deepcopy(self.prior['forward']);no_life.pop('lifecycle')
        for prior in (None,self.prior):
            with self.subTest(prior=prior is not None),self.assertRaisesRegex(Invalid,'requires a new registered lifecycle'):
                closed_loop.validate_forward(no_life,T0,self.resolve,prior,revision=9,lifecycle_root=self.root)

    def test_actual_public_research_save_registers_and_public_feedback_consumes(self):
        payload={'outlook':{'market_assessment':{'markets':[{'id':'BTC','horizons':[dict(self.prior,id='h')]}]}}}
        fresh=proposition('frozen-public',T2);payload['outlook']['market_assessment']['markets'][0]['horizons'][0]['forward']=forward(fresh)
        with mock.patch('v4.forward_lifecycle.now',return_value=T2):
            result=research._save(self.root,'independent','independent_id',{'market_result':payload})
        feedback=life.public_feedback(self.root,payload['outlook']['market_assessment'])
        self.assertEqual(feedback['claims'][0]['first_registration'],T2)
        self.assertNotIn('fact_version',str(feedback));self.assertTrue(feedback['ledger_is_not_market_verification'])

    def test_public_preflight_calls_actual_forward_lifecycle(self):
        import test_investment as f
        from test_closed_loop import market
        m=market(T2);f.public_research({'market_assessment':m})
        for item in m['markets']:
            for h in item['horizons']:h['forward']=forward(proposition(item['id']+'-'+h['id'],T2))
        with mock.patch('v4.forward_lifecycle.now',return_value=T2):
            market_state.validate_assessment(m,self.resolve,T2,revision=9,lifecycle_root=self.root)
            m['markets'][0]['horizons'][0]['forward']['lifecycle']['claim']['basis_kind']='VALIDATED_PREDECESSOR'
            with self.assertRaisesRegex(Invalid,'cite actual supported'):
                market_state.validate_assessment(m,self.resolve,T2,revision=9,lifecycle_root=self.root)

    def test_revision9_public_preflight_revisits_stored_revision8_markets(self):
        import test_investment as f
        from test_closed_loop import market
        prior=market(T2);f.public_research({'market_assessment':prior})
        current=deepcopy(prior)
        for item in current['markets']:
            for horizon in item['horizons']:
                horizon['forward']['prior_check']['outcome']='EVIDENCE_LIMITED'
                horizon['forward']['prior_check']['explanation']='Retained legacy thesis reviewed without new prospective support.'
        before=list((self.root/'market-lifecycle/claims').glob('*.json'))
        market_state.validate_assessment(current,self.resolve,T2,revision=9,prior=prior,lifecycle_root=self.root)
        self.assertEqual(list((self.root/'market-lifecycle/claims').glob('*.json')),before)

    def test_unsafe_claim_identity_cannot_resolve_other_files(self):
        with self.assertRaisesRegex(Invalid,'identity'):life.registration(self.root,'../../anything')


    def _daily_interval_case(self, observed_interval):
        self.bundle['sources'][0]['url'] += '&interval=' + observed_interval
        self.bundle['sources'][1]['url'] += '&interval=1d'
        for scoped in (self.old['scope'], self.old['expectations'][0]['scope']):
            scoped['interval'] = '1d'
        self.prior = {'forward': forward(self.old)}
        with mock.patch('v4.forward_lifecycle.now', return_value=T0):
            register_public(self.root, {'markets':[{'horizons':[self.prior]}]})
        r = deepcopy(self.review)
        r['prior_claim_hash'] = digest(self.old)
        r['outcomes'][0]['supported_scope']['interval'] = '1d'
        value = self.derived(r)
        for scoped in (value['lifecycle']['claim']['scope'],
                       value['lifecycle']['claim']['expectations'][0]['scope'],
                       value['lifecycle']['dependencies'][0]['scope']):
            scoped['interval'] = '1d'
        return value

    def test_same_daily_interval_can_support_scoped_daily_expectation(self):
        value = self._daily_interval_case('1d')
        self.assertEqual(self.check(value)['granted_dependencies'], 1)

    def test_hourly_observation_cannot_grant_daily_expectation(self):
        value = self._daily_interval_case('1h')
        with self.assertRaisesRegex(Invalid, 'scope|qualifier'):
            self.check(value)

if __name__=='__main__':unittest.main()
