"""Generated ordered/independent observations; no real market performance claim."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json,tempfile,unittest
from unittest import mock
from v4 import evidence,forward_lifecycle as life
from v4.common import Invalid,create,digest,instant,read

T0='2024-05-01T21:00:00Z';T3='2024-06-03T00:00:00Z'


def yahoo(day,close):
    def unix(at):return int(instant(day+at).timestamp())
    return {'test_context':'SYNTHETIC_ENGINEERING_ONLY','chart':{'error':None,'result':[{'meta':{
        'symbol':'QQQ','currency':'USD','exchangeName':'NGM','instrumentType':'ETF','dataGranularity':'1d',
        'currentTradingPeriod':{'regular':{'start':unix('T13:30:00Z'),'end':unix('T20:00:00Z')}}},
        'timestamp':[unix('T13:30:00Z')],'indicators':{'quote':[{'close':[close]}]}}]}}


def relation(identity,kind):
    return {'id':identity,'kind':kind,'expectation_ids':['retention'],
        'left':{'sample':'retention','path':'/value'},'right':{'sample':'response','path':'/value'}}


class TemporalRelationsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='btc-temporal-contract-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);sources=[];files={}
        for sid,day,value in [('before','2024-05-01','100'),('response','2024-05-02','110'),
                              ('retention','2024-05-03','112'),('alias','2024-05-02','111'),
                              ('failure','2024-05-03','90')]:
            raw=yahoo(day,value);path=self.root/(sid+'.json');create(path,raw)
            known=T0 if sid=='before' else T3
            sources.append({'id':sid,'origin':evidence.canonical_origin('https://query1.finance.yahoo.com/v8/finance/chart/QQQ'),
                'url':'https://query1.finance.yahoo.com/v8/finance/chart/QQQ','trust':'PUBLIC_RAW',
                'published_at':None,'retrieved_at':known,'start':day+'T13:30:00Z','end':day+'T20:00:00Z',
                'raw_hash':digest(path.read_bytes())});files[sid]=str(path)
        self.directory=self.root/'evidence'
        spec={'parent_id':None,'cutoff':T3,'sources':sources,'measurements':[],'verified_facts':[],
              'candidates':[],'gaps':[],'handoff':{'previous_hour':None,'successful_deep':None,
              'changes_since_hour':[],'changes_since_deep':[],'active_hypotheses':[],'open_questions':[]}}
        self.bundle=evidence.freeze(spec,files,self.directory);self.resolve=evidence.forward_resolver(self.bundle,self.directory)
        self.refs={sid:evidence.observation_refs(self.bundle,self.directory,sid)[0] for sid in files}
        initial=self.resolve(self.refs['before']);self.scope={k:v for k,v in initial['scope'].items() if k not in ('period','as_of')}
        anchor=dict(self.refs['before'],pointer=self.refs['before']['pointer']+'/value',value_hash=digest(initial['value']))
        self.claim={'id':'GENERATED_ORDERED_REPAIR','formed_at':T0,'data_cutoff':T0,'scope':self.scope,
            'basis_kind':'FACTS','basis_refs':[anchor],'scale':{'process':'Generated response followed by independent retained session.','end':None,'end_ref':None},
            'expectations':[{'id':role,'mechanism':'Generated scoped observation component: '+role,'scope':deepcopy(self.scope),
                'support':{'left':{'sample':role,'path':'/value'},'op':'GT','right':{'anchor':anchor}},
                'falsification':{'left':{'sample':role,'path':'/value'},'op':'LT','right':{'anchor':anchor}}} for role in ('response','retention')],
            'observation_relations':[relation('later','AFTER'),relation('distinct','DISTINCT_OBSERVATION'),relation('nonoverlap','NON_OVERLAPPING_PERIOD')]}
        self.register(self.claim)

    def register(self,claim):
        path=self.root/'generated-owners'/(digest(claim)[7:]+'.json')
        create(path,{'label':'SYNTHETIC_ENGINEERING_ONLY','created_at':claim['formed_at'],
                     'market_assessment':{'markets':[{'horizons':[{'forward':{'lifecycle':{'claim':claim}}}]}]}})
        with mock.patch('v4.forward_lifecycle.now',return_value=claim['formed_at']):
            life.register(self.root,read(path)['market_assessment'],path,'/created_at')

    def review(self,mode='ordered',claim=None):
        claim=claim or self.claim
        ids=('retention','response') if mode=='reversed' else ('response','response') if mode=='same' else ('response','alias') if mode=='alias' else ('response','failure') if mode=='failure' else ('response','retention')
        samples=[]
        for role,sid in zip(('response','retention'),ids):
            if mode=='missing' and role=='retention':continue
            fact=self.resolve.fact(self.refs[sid],'/observed_at')
            samples.append({'id':role,'ref':self.refs[sid],'time_pointer':'/observed_at','scope':fact['scope']})
        outcomes=[{'expectation_id':p['id'],'verdict':'SUPPORTED','supported_scope':deepcopy(self.scope),
                   'explanation':'Generated measured value under original controlled contract.'} for p in claim['expectations']]
        if mode=='missing':outcomes[1].update(verdict='UNKNOWN',supported_scope={})
        if mode=='failure':outcomes[1].update(verdict='FALSIFIED',supported_scope={})
        return {'prior_claim_hash':digest(claim),'kind':'FORWARD_OBSERVATION','evaluated_at':T3,'samples':samples,'outcomes':outcomes}

    def validate(self,review=None,claim=None,current=None,dependencies=()):
        claim=claim or self.claim;review=review or self.review(claim=claim)
        with mock.patch('v4.forward_lifecycle.now',return_value=T3):
            return life.validate({'claim':current or claim,'reviews':[review],'dependencies':list(dependencies)},T3,
                self.resolve,{'forward':{'lifecycle':{'claim':claim}}},fact_view=self.resolve.fact,root=self.root)

    def successor(self,claim=None,related=False):
        value=deepcopy(claim or self.claim);value.update(id='GENERATED_SUCCESSOR',formed_at=T3,data_cutoff=T3,basis_kind='VALIDATED_PREDECESSOR')
        if not related:value['observation_relations']=[]
        return value

    def dependency(self,review,kind='DECLARED_RELATIONS',ids=None):
        return {'claim_hash':review['prior_claim_hash'],'expectation_id':'retention','review_hash':digest(review),
                'scope':deepcopy(self.scope),'support_kind':kind,
                'relation_ids':ids if ids is not None else ['later','distinct','nonoverlap'] if kind=='DECLARED_RELATIONS' else []}

    def test_original_ordered_distinct_sessions_support_only_declared_components(self):
        result=self.validate();self.assertTrue(result['complete_prior_support'])
        self.assertEqual({v['status'] for v in result['observation_relation_checks']},{'SUPPORTED'})
        self.assertEqual([g['support_kind'] for g in result['component_support']],['VALUE_ONLY','DECLARED_RELATIONS'])

    def test_reversed_and_same_event_cannot_be_a_retention_relationship(self):
        for mode in ('reversed','same','alias'):
            with self.subTest(mode=mode),self.assertRaisesRegex(Invalid,'observation relation'):
                self.validate(self.review(mode))

    def test_source_alias_or_revised_value_is_not_a_new_economic_observation(self):
        first=self.resolve.fact(self.refs['response'],'/observed_at');alias=self.resolve.fact(self.refs['alias'],'/observed_at')
        self.assertNotEqual(self.refs['response'],self.refs['alias'])
        self.assertNotEqual(first['value']['value'],alias['value']['value'])
        self.assertEqual(first['field_observations']['/value']['identity'],alias['field_observations']['/value']['identity'])
        with self.assertRaisesRegex(Invalid,'observation relation'):self.validate(self.review('alias'))

    def test_missing_related_observation_is_unknown_and_cannot_grant_a_successor(self):
        review=self.review('missing');result=self.validate(review)
        self.assertFalse(result['complete_prior_support'])
        self.assertEqual({v['status'] for v in result['observation_relation_checks']},{'UNKNOWN'})
        with self.assertRaisesRegex(Invalid,'unverified predecessor'):
            self.validate(review,current=self.successor(related=True),dependencies=[self.dependency(review)])

    def test_known_numeric_falsifier_survives_unmet_observation_relation(self):
        review=self.review('failure');current=self.successor();current['basis_kind']='FACT_CORRECTION'
        result=self.validate(review,current=current)
        self.assertTrue(result['falsified_prior']);self.assertFalse(result['complete_prior_support'])
        self.assertEqual(result['verification'][1]['verdict'],'FALSIFIED')

    def test_independent_conditions_do_not_receive_a_program_invented_order(self):
        claim=deepcopy(self.claim);claim['id']='GENERATED_INDEPENDENT_COMPONENTS';claim['observation_relations']=[];self.register(claim)
        result=self.validate(self.review('reversed',claim),claim)
        self.assertTrue(result['complete_prior_support']);self.assertEqual(result['observation_relation_checks'],[])

    def test_legacy_numeric_support_cannot_be_full_or_relational_support(self):
        old=deepcopy(self.claim);old['id']='GENERATED_LEGACY_WITHOUT_RELATIONS';old.pop('observation_relations');self.register(old)
        review=self.review('same',old);result=self.validate(review,old)
        self.assertFalse(result['complete_prior_support'])
        self.assertEqual({g['support_kind'] for g in result['component_support']},{'VALUE_ONLY'})
        current=self.successor(old);dependency=self.dependency(review,'VALUE_ONLY')
        accepted=self.validate(review,old,current,[dependency]);self.assertEqual(accepted['dependency_support'][0]['support_kind'],'VALUE_ONLY')
        with self.assertRaisesRegex(Invalid,'verified ex-ante relation'):
            self.validate(review,old,current,[self.dependency(review)])

    def test_value_only_successor_cannot_expand_scope_or_establish_a_strong_relationship(self):
        old=deepcopy(self.claim);old['id']='GENERATED_VALUE_ONLY';old.pop('observation_relations');self.register(old)
        review=self.review(claim=old);dependency=self.dependency(review,'VALUE_ONLY')
        broad=self.successor(old);broad['scope']['subject']=['QQQ','VOO']
        with self.assertRaisesRegex(Invalid,'cannot widen'):self.validate(review,old,broad,[dependency])
        related=self.successor(old,related=True);related['observation_relations']=deepcopy(self.claim['observation_relations'])
        with self.assertRaisesRegex(Invalid,'cannot establish a successor relationship'):
            self.validate(review,old,related,[dependency])

    def test_strong_successor_binds_all_original_relation_ids_and_review_identity(self):
        review=self.review();current=self.successor(related=True)
        result=self.validate(review,current=current,dependencies=[self.dependency(review)])
        self.assertEqual(result['dependency_support'][0]['relation_ids'],['later','distinct','nonoverlap'])
        with self.assertRaisesRegex(Invalid,'every actual verified'):
            self.validate(review,current=current,dependencies=[self.dependency(review,ids=['later'])])
        dependency=self.dependency(review);dependency['review_hash']=digest('Different review.')
        with self.assertRaisesRegex(Invalid,'unverified predecessor'):
            self.validate(review,current=current,dependencies=[dependency])

    def test_relation_declaration_cannot_be_added_to_the_original_identity_after_registration(self):
        original=deepcopy(self.claim);original['id']='GENERATED_OLD_IDENTITY';original.pop('observation_relations');self.register(original)
        revised=deepcopy(original);revised['observation_relations']=deepcopy(self.claim['observation_relations'])
        with self.assertRaisesRegex(Invalid,'immutable'):
            self.validate(self.review(claim=original),original,revised)
        self.assertEqual(life.registration(self.root,digest(original))['claim'],original)

    def test_relationship_uses_original_field_clock_and_ignores_a_sample_role_name(self):
        review=self.review();review['samples'][0]['id']='Not an event-order label'
        with self.assertRaisesRegex(Invalid,'support label|observation relation'):
            self.validate(review)
        with self.assertRaisesRegex(Invalid,'invalid fields'):
            claim=deepcopy(self.claim);claim['observation_relations'][0]['left']['observed_at']=T3
            with mock.patch('v4.forward_lifecycle.now',return_value=T3):life.claim(claim,T3,self.resolve)

    def test_unregistered_or_uninvolved_operands_cannot_grant_a_component_relation(self):
        for mode in ('unregistered','unrelated_components'):
            claim=deepcopy(self.claim)
            if mode=='unregistered':
                for item in claim['observation_relations']:
                    item['left']['sample']='foreign-after';item['right']['sample']='foreign-before'
            else:
                third=deepcopy(claim['expectations'][0]);third['id']='unrelated'
                third['support']['left']['sample']='unrelated';third['falsification']['left']['sample']='unrelated'
                claim['expectations'].append(third)
                for item in claim['observation_relations']:item['expectation_ids']=['unrelated']
            with self.subTest(mode=mode),self.assertRaisesRegex(Invalid,'original registered component|must participate'):
                life.claim(claim,T3,self.resolve)

    def test_relation_clock_cannot_borrow_an_original_field_outside_its_component_scope(self):
        review=self.review();native=self.resolve.fact
        def wrong_subject(ref,clock):
            fact=deepcopy(native(ref,clock))
            if ref==review['samples'][0]['ref']:
                fact['field_scopes']['/value']['subject']='VOO'
            return fact
        with self.assertRaisesRegex(Invalid,'observation relation|scope'):
            life.validate({'claim':self.claim,'reviews':[review],'dependencies':[]},T3,self.resolve,
                {'forward':{'lifecycle':{'claim':self.claim}}},fact_view=wrong_subject,root=self.root)

    def test_empty_explicit_contract_supports_values_but_no_strong_successor(self):
        claim=deepcopy(self.claim);claim['id']='GENERATED_EMPTY_CONTRACT';claim['observation_relations']=[];self.register(claim)
        review=self.review(claim=claim);result=self.validate(review,claim)
        self.assertTrue(result['complete_prior_support'])
        self.assertEqual({g['support_kind'] for g in result['component_support']},{'VALUE_ONLY'})
        successor=self.successor(claim);successor['observation_relations']=deepcopy(self.claim['observation_relations'])
        with self.assertRaisesRegex(Invalid,'verified ex-ante relation'):
            self.validate(review,claim,successor,[self.dependency(review,ids=[])])

    def test_author_declared_cross_measure_relation_keeps_each_components_actual_scope(self):
        def row(day,close,volume):
            start=int(instant(day+'T00:00:00Z').timestamp()*1000)
            end=int(instant(day+'T23:59:59Z').timestamp()*1000)
            return [start,'100','120','90',close,volume,end,'1000',1,'5','500','0']
        start='2024-05-02T00:00:00Z';sources=[];files={}
        for sid,rows,known in [('before',[row('2024-05-01','100','10')],start),
                               ('later',[row('2024-05-02','110','20'),row('2024-05-03','112','30')],T3)]:
            path=self.root/(sid+'-cross.json');create(path,rows)
            url='https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d'
            sources.append({'id':sid,'origin':evidence.canonical_origin(url),'url':url,'trust':'PUBLIC_RAW',
                'published_at':None,'retrieved_at':known,'start':'2024-05-01T00:00:00Z','end':start if sid=='before' else T3,'raw_hash':digest(path.read_bytes())})
            files[sid]=str(path)
        directory=self.root/'cross-measure-package';spec={k:deepcopy(v) for k,v in self.bundle.items() if k not in ('schema','evidence_id')}
        spec['sources']=sources;package=evidence.freeze(spec,files,directory);resolve=evidence.forward_resolver(package,directory)
        claim=deepcopy(self.claim);claim.update(id='GENERATED_PRICE_AND_VOLUME_RELATION',formed_at=start,data_cutoff=start)
        claim['basis_refs']=[];samples=[];outcomes=[]
        for part,role,row_id,field in zip(claim['expectations'],('response','retention'),(0,1),('/5','/4')):
            ref={'source_id':'later','pointer':'/'+str(row_id),'value_hash':digest(read(directory/'raw'/(sources[1]['raw_hash'][7:]+'.json'))[row_id])}
            fact=resolve.fact(ref,'/6');actual_scope=fact['field_scopes'][field];part['scope']=actual_scope
            anchor={'source_id':'before','pointer':'/0'+field,'value_hash':digest(read(directory/'raw'/(sources[0]['raw_hash'][7:]+'.json'))[0][int(field[1:])])}
            claim['basis_refs'].append(anchor)
            for key in ('support','falsification'):part[key]['left']['path']=field;part[key]['right']['anchor']=anchor
            samples.append({'id':role,'ref':ref,'time_pointer':'/6','scope':fact['scope']})
            outcomes.append({'expectation_id':role,'verdict':'SUPPORTED','supported_scope':actual_scope,'explanation':'Generated independently scoped volume and later price response.'})
        claim['scope']=dict(claim['expectations'][0]['scope'],measure=['TRADED_VOLUME','PRICE'])
        for relation in claim['observation_relations']:relation['left']['path']='/4';relation['right']['path']='/5'
        self.register(claim)
        review={'prior_claim_hash':digest(claim),'kind':'FORWARD_OBSERVATION','evaluated_at':T3,'samples':samples,'outcomes':outcomes}
        result=life.validate({'claim':claim,'reviews':[review],'dependencies':[]},T3,resolve,
            {'forward':{'lifecycle':{'claim':claim}}},fact_view=resolve.fact,root=self.root)
        self.assertTrue(result['complete_prior_support'])
        self.assertEqual({x['status'] for x in result['observation_relation_checks']},{'SUPPORTED'})
        self.assertEqual([x['scope']['measure'] for x in result['component_support']],['TRADED_VOLUME','PRICE'])

    def test_first_registration_cannot_precede_claim_formation(self):
        claim=deepcopy(self.claim);claim.update(id='GENERATED_LATER_FORMATION',formed_at=T3,data_cutoff=T3)
        path=self.root/'backdated-owner.json'
        create(path,{'created_at':T0,'market_assessment':{'markets':[{'horizons':[{'forward':{'lifecycle':{'claim':claim}}}]}]}})
        with mock.patch('v4.forward_lifecycle.now',return_value=T3),self.assertRaisesRegex(Invalid,'predates claim formation'):
            life.register(self.root,read(path)['market_assessment'],path,'/created_at')
        self.assertIsNone(life.registration(self.root,digest(claim)))

    def test_relation_schema_is_controlled_and_initial_facts_need_no_later_observation(self):
        initial=deepcopy(self.claim);initial.update(id='GENERATED_CURRENT_FACTS_ZERO_REVIEW',formed_at=T3,data_cutoff=T3)
        value={'claim':initial,'reviews':[],'dependencies':[]}
        with mock.patch('v4.forward_lifecycle.now',return_value=T3):
            result=life.validate(value,T3,self.resolve,fact_view=self.resolve.fact,root=self.root)
        self.assertFalse(result['complete_prior_support']);self.assertEqual(result['verification'],[])
        for change in (lambda c:c.update(observation_relations=None),
                       lambda c:c['observation_relations'][0].update(kind='GUESS_FROM_PROSE'),
                       lambda c:c['observation_relations'][0].update(expectation_ids=['not-original-component'])):
            bad=deepcopy(self.claim);change(bad)
            with self.subTest(change=change),self.assertRaises(Invalid):life.claim(bad,T3,self.resolve)


    def later_component_failure(self, claim=None):
        claim = claim or self.claim
        raw = yahoo('2024-05-06', '90')
        path = self.root / 'generated-later-falsifier.json'
        create(path, raw)
        spec = {k:deepcopy(v) for k,v in self.bundle.items() if k not in ('schema', 'evidence_id')}
        spec['sources'].append({'id': 'later-falsifier', 'origin': self.bundle['sources'][0]['origin'],
            'url': self.bundle['sources'][0]['url'], 'trust': 'PUBLIC_RAW', 'published_at': None,
            'retrieved_at': T3, 'start': '2024-05-06T13:30:00Z', 'end': '2024-05-06T20:00:00Z',
            'raw_hash': digest(path.read_bytes())})
        files = {s['id']:str(self.directory / 'raw' / (s['raw_hash'][7:] + '.json'))
                 for s in self.bundle['sources']}
        files['later-falsifier'] = str(path)
        directory = self.root / 'later-falsifier-package'
        package = evidence.freeze(spec, files, directory)
        resolve = evidence.forward_resolver(package, directory)
        ref = evidence.observation_refs(package, directory, 'later-falsifier')[0]
        fact = resolve.fact(ref, '/observed_at')
        failed = self.review(claim=claim)
        failed['samples'][1].update(ref=ref, scope=fact['scope'])
        failed['outcomes'][1].update(verdict='FALSIFIED', supported_scope={})
        self.assertGreater(instant(fact['field_times']['/value']),
                           instant(self.resolve(self.refs['retention'])['observed_at']))
        return resolve, failed

    def review_set(self, reviews, current, dependencies, resolve):
        with mock.patch('v4.forward_lifecycle.now', return_value=T3):
            return life.validate({'claim': current, 'reviews': reviews, 'dependencies': dependencies},
                T3, resolve, {'forward': {'lifecycle': {'claim': self.claim}}},
                fact_view=resolve.fact, root=self.root)

    def test_old_review_hash_cannot_grant_a_component_known_falsified_before_successor(self):
        earlier = self.review()
        resolve, failed = self.later_component_failure()
        current = self.successor(related=True)
        original_reviews = deepcopy([earlier, failed])
        original_registration = deepcopy(life.registration(self.root, digest(self.claim)))
        for reviews in ([earlier, failed], [failed, earlier]):
            with self.subTest(order=[r['outcomes'][1]['verdict'] for r in reviews]), self.assertRaisesRegex(
                    Invalid, 'falsified or unverified predecessor'):
                self.review_set(reviews, current, [self.dependency(earlier)], resolve)
        self.assertEqual([earlier, failed], original_reviews)
        self.assertEqual(life.registration(self.root, digest(self.claim)), original_registration)

    def test_value_only_old_review_cannot_revive_a_known_falsified_component(self):
        earlier = self.review()
        resolve, failed = self.later_component_failure()
        for reviews in ([earlier, failed], [failed, earlier]):
            with self.subTest(order=[r['outcomes'][1]['verdict'] for r in reviews]), self.assertRaisesRegex(
                    Invalid, 'falsified or unverified predecessor'):
                self.review_set(reviews, self.successor(), [self.dependency(earlier, 'VALUE_ONLY')], resolve)

    def test_other_independent_component_keeps_support_and_falsified_history_is_preserved(self):
        earlier = self.review()
        resolve, failed = self.later_component_failure()
        dependency = self.dependency(earlier, 'VALUE_ONLY')
        dependency['expectation_id'] = 'response'
        for reviews in ([earlier, failed], [failed, earlier]):
            result = self.review_set(reviews, self.successor(), [dependency], resolve)
            self.assertTrue(result['falsified_prior'])
            self.assertFalse(result['complete_prior_support'])
            self.assertEqual(result['granted_dependencies'], 1)
            self.assertEqual({g['expectation_id'] for g in result['component_support']}, {'response'})
            self.assertEqual({v['verdict'] for v in result['verification']
                              if v['expectation_id'] == 'retention'}, {'SUPPORTED', 'FALSIFIED'})

    def test_unknown_review_does_not_revoke_prior_verified_component_support(self):
        earlier = self.review()
        unknown = self.review('missing')
        for reviews in ([earlier, unknown], [unknown, earlier]):
            result = self.review_set(reviews, self.successor(related=True),
                                     [self.dependency(earlier)], self.resolve)
            self.assertFalse(result['falsified_prior'])
            self.assertEqual(result['granted_dependencies'], 1)
            self.assertEqual(result['dependency_support'][0]['review_hash'], digest(earlier))

    def test_falsified_namesake_in_another_original_claim_does_not_revoke_this_claim(self):
        other = deepcopy(self.claim)
        other['id'] = 'GENERATED_SEPARATE_ORIGINAL_COMPONENT'
        self.register(other)
        earlier = self.review()
        resolve, failed = self.later_component_failure(other)
        for reviews in ([earlier, failed], [failed, earlier]):
            result = self.review_set(reviews, self.successor(related=True),
                                     [self.dependency(earlier)], resolve)
            self.assertFalse(result['falsified_prior'])
            self.assertEqual(result['granted_dependencies'], 1)



class RawAggregatePeriodTests(unittest.TestCase):
    FORM = '2024-05-02T00:30:00Z'
    CHECK = '2024-05-02T03:00:00Z'
    FIELDS = {
        'raw': ('/1', '/2', '/3', '/4', '/5', '/7', '/9', '/10'),
        'dataset': ('/open', '/high', '/low', '/close', '/volume_btc',
                    '/quote_volume', '/taker_buy_btc', '/taker_buy_quote'),
    }

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='btc-aggregate-period-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sequence = 0

    def verify(self, shape, field, bar='cross', verdict='SUPPORTED', formed=None,
               registered=None, retrieved=None):
        formed = formed or self.FORM
        registered = registered or formed
        retrieved = retrieved or self.CHECK
        self.sequence += 1
        root = self.root / str(self.sequence)
        files = {}
        sources = []
        refs = {}
        url = 'https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h'
        for sid, start, end, price, volume in (
                ('before', '2024-05-01T00:00:00Z', '2024-05-01T00:59:59Z', 100, 10),
                ('cross', '2024-05-02T00:00:00Z', '2024-05-02T00:59:59Z', 110, 20),
                ('after', '2024-05-02T01:00:00Z', '2024-05-02T01:59:59Z', 110, 20)):
            if shape == 'raw':
                row = [int(instant(start).timestamp() * 1000), str(price), str(price),
                       str(price), str(price), str(volume),
                       int(instant(end).timestamp() * 1000), str(volume * 100), 1,
                       str(volume / 2), str(volume * 50), '0']
                payload = [row]
                pointer = '/0'
            else:
                row = {'time': start, 'end': end, 'close_time': end,
                       'open': str(price), 'high': str(price), 'low': str(price),
                       'close': str(price), 'volume_btc': str(volume),
                       'quote_volume': str(volume * 100), 'taker_buy_btc': str(volume / 2),
                       'taker_buy_quote': str(volume * 50)}
                payload = {'datasets': {'generated': {'group': 'BINANCE', 'market': 'SPOT',
                    'instrument': 'BTCUSDT', 'quote_unit': 'USDT', 'rows': [row]}}}
                pointer = '/datasets/generated/rows/0'
            path = root / (sid + '.json')
            create(path, payload)
            sources.append({'id': sid, 'origin': evidence.canonical_origin(url), 'url': url,
                'trust': 'PUBLIC_RAW', 'published_at': None,
                'retrieved_at': formed if sid == 'before' else retrieved if sid == bar else self.CHECK,
                'start': start, 'end': end, 'raw_hash': digest(path.read_bytes())})
            files[sid] = str(path)
            refs[sid] = {'source_id': sid, 'pointer': pointer, 'value_hash': digest(row)}
        directory = root / 'evidence'
        bundle = evidence.freeze({'parent_id': None, 'cutoff': self.CHECK,
            'sources': sources, 'measurements': [], 'verified_facts': [], 'candidates': [], 'gaps': [],
            'handoff': {'previous_hour': None, 'successful_deep': None, 'changes_since_hour': [],
                        'changes_since_deep': [], 'active_hypotheses': [], 'open_questions': []}},
            files, directory)
        resolve = evidence.forward_resolver(bundle, directory)
        time_pointer = '/6' if shape == 'raw' else '/close_time'
        fact = resolve.fact(refs[bar], time_pointer)
        scope = fact['field_scopes'][field]
        before = resolve(refs['before'])
        value = before[int(field[1:])] if shape == 'raw' else before[field[1:]]
        anchor = dict(refs['before'], pointer=refs['before']['pointer'] + field, value_hash=digest(value))
        claim = {'id': 'SYNTHETIC_FIELD_PERIOD:' + shape + ':' + field + ':' + str(self.sequence),
            'formed_at': formed, 'data_cutoff': formed, 'scope': scope,
            'basis_kind': 'FACTS', 'basis_refs': [anchor],
            'scale': {'process': 'Generated original point or accumulated-period observation.',
                      'end': None, 'end_ref': None},
            'expectations': [{'id': 'response', 'mechanism': 'Generated original-field response.',
                'scope': scope,
                'support': {'left': {'sample': 'later', 'path': field}, 'op': 'GT',
                            'right': {'anchor': anchor}},
                'falsification': {'left': {'sample': 'later', 'path': field}, 'op': 'LT',
                                 'right': {'anchor': anchor}}}],
            'observation_relations': []}
        owner = root / 'original-owner.json'
        assessment = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': claim}}}]}]}
        create(owner, {'label': 'SYNTHETIC_ENGINEERING_ONLY', 'created_at': registered,
                       'market_assessment': assessment})
        with mock.patch('v4.forward_lifecycle.now', return_value=self.CHECK):
            life.register(root / 'owner', assessment, owner, '/created_at')
            review = {'prior_claim_hash': digest(claim), 'kind': 'FORWARD_OBSERVATION',
                'evaluated_at': self.CHECK,
                'samples': [{'id': 'later', 'ref': refs[bar], 'time_pointer': time_pointer,
                             'scope': fact['scope']}],
                'outcomes': [{'expectation_id': 'response', 'verdict': verdict,
                    'supported_scope': scope if verdict == 'SUPPORTED' else {},
                    'explanation': 'Generated field-period experiment; no market outcome claimed.'}]}
            result = life.validate({'claim': claim, 'reviews': [review], 'dependencies': []},
                self.CHECK, resolve, {'forward': {'lifecycle': {'claim': claim}}},
                fact_view=resolve.fact, root=root / 'owner')
        return result, fact

    def test_straddling_ranges_and_accumulated_trades_cannot_be_future_support(self):
        for shape, fields in self.FIELDS.items():
            for field in (fields[1], fields[2], *fields[4:]):
                with self.subTest(shape=shape, field=field), self.assertRaisesRegex(
                        Invalid, 'economic observation period includes pre-formation events'):
                    self.verify(shape, field)

    def test_later_close_point_is_valid_but_formation_era_open_is_not(self):
        for shape, fields in self.FIELDS.items():
            with self.subTest(shape=shape, field=fields[3]):
                result, fact = self.verify(shape, fields[3])
                self.assertTrue(result['complete_prior_support'])
                self.assertNotIn(fields[3], fact['field_period_starts'])
                self.assertGreater(instant(fact['field_times'][fields[3]]), instant(self.FORM))
                self.assertLess(instant(fact['field_observations'][fields[3]]['economic_period']['start']),
                                instant(self.FORM))
            with self.subTest(shape=shape, field=fields[0]), self.assertRaisesRegex(
                    Invalid, 'field predates formation'):
                self.verify(shape, fields[0])

    def test_wholly_postregistration_bars_allow_points_ranges_and_accumulated_trades(self):
        for shape, fields in self.FIELDS.items():
            for field in fields:
                with self.subTest(shape=shape, field=field):
                    result, _ = self.verify(shape, field, 'after')
                    self.assertTrue(result['complete_prior_support'])

    def test_aggregate_period_cannot_precede_actual_first_registration(self):
        formed = '2024-05-02T00:00:00Z'
        for shape, fields in self.FIELDS.items():
            with self.subTest(shape=shape), self.assertRaisesRegex(
                    Invalid, 'economic observation period includes pre-formation events'):
                self.verify(shape, fields[4], formed=formed, registered=self.FORM)
            result, _ = self.verify(shape, fields[3], formed=formed, registered=self.FORM)
            self.assertTrue(result['complete_prior_support'])

    def test_unavailable_straddling_period_remains_unknown_without_grants(self):
        for shape, fields in self.FIELDS.items():
            with self.subTest(shape=shape):
                result, _ = self.verify(shape, fields[4], verdict='UNKNOWN')
                self.assertFalse(result['complete_prior_support'])
                self.assertEqual(result['component_support'], [])
                self.assertEqual(result['verification'][0]['verdict'], 'UNKNOWN')

    def test_source_retrieved_before_declared_close_is_already_rejected(self):
        for shape, fields in self.FIELDS.items():
            for field in (fields[0], fields[3], fields[4]):
                with self.subTest(shape=shape, field=field), self.assertRaisesRegex(
                        Invalid, 'source exceeds assessment time boundary'):
                    self.verify(shape, field, 'after', retrieved='2024-05-02T01:30:00Z')



class EconomicInstantIdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='btc-economic-instant-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def point_case(self, second_time='2024-05-03T04:00:00+08:00', second_field='/sumOpenInterest'):
        url = 'https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h'
        sources = []
        files = {}
        refs = {}
        for sid, stamp, quantity, notional in (
                ('anchor', '2024-05-01T20:00:00Z', '100', '1000'),
                ('first', '2024-05-02T20:00:00Z', '110', '1100'),
                ('second', second_time, '110', '1100')):
            row = {'symbol': 'BTCUSDT', 'sumOpenInterest': quantity,
                   'sumOpenInterestValue': notional, 'timestamp': stamp}
            path = self.root / (sid + '.json')
            create(path, [row])
            sources.append({'id': sid, 'origin': 'BINANCE', 'url': url, 'trust': 'PUBLIC_RAW',
                'published_at': None, 'retrieved_at': T0 if sid == 'anchor' else T3,
                'start': '2024-05-01T00:00:00Z', 'end': T0 if sid == 'anchor' else T3,
                'raw_hash': digest(path.read_bytes())})
            files[sid] = str(path)
            refs[sid] = {'source_id': sid, 'pointer': '/0', 'value_hash': digest(row)}
        directory = self.root / 'point-evidence'
        package = evidence.freeze({'parent_id': None, 'cutoff': T3, 'sources': sources,
            'measurements': [], 'verified_facts': [], 'candidates': [], 'gaps': [],
            'handoff': {'previous_hour': None, 'successful_deep': None, 'changes_since_hour': [],
                        'changes_since_deep': [], 'active_hypotheses': [], 'open_questions': []}},
            files, directory)
        resolve = evidence.forward_resolver(package, directory)
        parts = []
        anchors = []
        samples = []
        outcomes = []
        for role, field in (('first', '/sumOpenInterest'), ('second', second_field)):
            fact = resolve.fact(refs[role], '/timestamp')
            scope = fact['field_scopes'][field]
            anchor = dict(refs['anchor'], pointer='/0' + field,
                          value_hash=digest(resolve(refs['anchor'])[field[1:]]))
            anchors.append(anchor)
            parts.append({'id': role, 'mechanism': 'Generated independently scoped point measure.',
                'scope': scope, 'support': {'left': {'sample': role, 'path': field},
                    'op': 'GT', 'right': {'anchor': anchor}},
                'falsification': {'left': {'sample': role, 'path': field},
                    'op': 'LT', 'right': {'anchor': anchor}}})
            samples.append({'id': role, 'ref': refs[role], 'time_pointer': '/timestamp', 'scope': fact['scope']})
            outcomes.append({'expectation_id': role, 'verdict': 'SUPPORTED', 'supported_scope': scope,
                             'explanation': 'Generated point-measure test; no actual market conclusion.'})
        scope = deepcopy(parts[0]['scope'])
        if second_field != '/sumOpenInterest':
            scope['measure'] = [parts[0]['scope']['measure'], parts[1]['scope']['measure']]
        claim = {'id': 'GENERATED_POINT_IDENTITY', 'formed_at': T0, 'data_cutoff': T0,
            'scope': scope, 'basis_kind': 'FACTS', 'basis_refs': anchors,
            'scale': {'process': 'Generated economic event identity comparison.', 'end': None, 'end_ref': None},
            'expectations': parts, 'observation_relations': [{'id': 'distinct',
                'kind': 'DISTINCT_OBSERVATION', 'expectation_ids': ['second'],
                'left': {'sample': 'second', 'path': second_field},
                'right': {'sample': 'first', 'path': '/sumOpenInterest'}}]}
        owner = self.root / 'original-owner.json'
        market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': claim}}}]}]}
        create(owner, {'label': 'SYNTHETIC_ENGINEERING_ONLY', 'created_at': T0, 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=T3):
            life.register(self.root, market, owner, '/created_at')
        review = {'prior_claim_hash': digest(claim), 'kind': 'FORWARD_OBSERVATION',
                  'evaluated_at': T3, 'samples': samples, 'outcomes': outcomes}
        return claim, review, resolve

    def check_point_case(self, claim, review, resolve, fact_view=None):
        with mock.patch('v4.forward_lifecycle.now', return_value=T3):
            return life.validate({'claim': claim, 'reviews': [review], 'dependencies': []},
                T3, resolve, {'forward': {'lifecycle': {'claim': claim}}},
                fact_view=fact_view or resolve.fact, root=self.root)

    def test_same_instant_with_offset_alias_is_one_event_even_for_legacy_period_representation(self):
        claim, review, resolve = self.point_case()
        facts = [resolve.fact(s['ref'], s['time_pointer']) for s in review['samples']]
        self.assertNotEqual(facts[0]['field_times']['/sumOpenInterest'], facts[1]['field_times']['/sumOpenInterest'])
        self.assertEqual(instant(facts[0]['field_times']['/sumOpenInterest']),
                         instant(facts[1]['field_times']['/sumOpenInterest']))
        self.assertEqual(facts[0]['field_observations']['/sumOpenInterest']['identity'],
                         facts[1]['field_observations']['/sumOpenInterest']['identity'])
        self.assertNotEqual(review['samples'][0]['ref']['value_hash'], review['samples'][1]['ref']['value_hash'])
        def legacy_period(ref, clock):
            fact = deepcopy(resolve.fact(ref, clock))
            for field, observation in fact['field_observations'].items():
                original_clock = fact['field_times'][field]
                observation['economic_period'] = {'start': original_clock, 'end': original_clock}
            return fact
        for adapter in (resolve.fact, legacy_period):
            with self.subTest(adapter=adapter.__name__), self.assertRaisesRegex(Invalid, 'observation relation'):
                self.check_point_case(claim, review, resolve, adapter)

    def test_genuinely_later_economic_point_retains_independence(self):
        claim, review, resolve = self.point_case(second_time='2024-05-03T20:00:00Z')
        result = self.check_point_case(claim, review, resolve)
        self.assertTrue(result['complete_prior_support'])
        self.assertEqual(result['observation_relation_checks'][0]['status'], 'SUPPORTED')

    def test_same_instant_independent_quantity_and_notional_measures_are_not_merged(self):
        claim, review, resolve = self.point_case(second_field='/sumOpenInterestValue')
        result = self.check_point_case(claim, review, resolve)
        self.assertTrue(result['complete_prior_support'])
        self.assertEqual({g['scope']['measure'] for g in result['component_support']},
                         {'OPEN_INTEREST_QUANTITY', 'OPEN_INTEREST_NOTIONAL'})
        self.assertEqual(result['observation_relation_checks'][0]['status'], 'SUPPORTED')



class NonprospectiveHistoryTests(unittest.TestCase):
    setUp = TemporalRelationsTests.setUp
    register = TemporalRelationsTests.register
    review = TemporalRelationsTests.review
    # Reuse the generated source constructor, without rerunning inherited tests.
    def history_owner(self, kind='POSTHOC', null_target=True, unvalidated=False):
        old = deepcopy(self.claim)
        old['id'] = 'GENERATED_LEGACY_WITHOUT_FIRST_REGISTRATION'
        current = deepcopy(self.claim)
        current['id'] = 'GENERATED_NEW_WITH_ARCHIVED_HISTORY:' + kind + ':' + str(null_target)
        historical = {'prior_claim_hash': None if null_target else digest(old), 'kind': kind,
            'evaluated_at': T0, 'samples': [], 'outcomes': [{'expectation_id': 'legacy-check',
                'verdict': 'POSTHOC_ONLY', 'supported_scope': {},
                'explanation': 'Generated legacy history without an original prospective clock.'}]}
        lifecycle = {'claim': current, 'reviews': [historical], 'dependencies': []}
        if not unvalidated:
            result = life.validate(lifecycle, T0, self.resolve,
                None if null_target else {'forward': {'lifecycle': {'claim': old, 'reviews': [], 'dependencies': []}}},
                fact_view=self.resolve.fact, root=self.root)
            self.assertFalse(result['complete_prior_support'])
            self.assertEqual(result['granted_dependencies'], 0)
        owner = self.root / ('history-' + kind + '-' + str(null_target) + '.json')
        market = {'markets': [{'id': 'GENERATED', 'horizons': [
            {'id': 'HISTORY', 'forward': {'lifecycle': lifecycle}}]}]}
        create(owner, {'label': 'SYNTHETIC_ENGINEERING_ONLY', 'created_at': T0, 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=T3):
            life.register(self.root, market, owner, '/created_at')
        return lifecycle, historical

    def carry(self, lifecycle, prior=None, root=None):
        return life.validate(lifecycle, T3, self.resolve,
            {'forward': {'lifecycle': prior or lifecycle}}, fact_view=self.resolve.fact,
            root=self.root if root is None else root)

    def test_archived_exact_legacy_review_survives_the_actual_predecessor_change(self):
        for kind in ('POSTHOC', 'BACKTEST', 'ENGINEERING', 'FROZEN_REPLAY'):
            for null_target in (True, False):
                with self.subTest(kind=kind, null_target=null_target):
                    lifecycle, historical = self.history_owner(kind, null_target)
                    original_record = deepcopy(life.registration(self.root, digest(lifecycle['claim'])))
                    result = self.carry(lifecycle)
                    self.assertEqual(result['verification'], historical['outcomes'])
                    self.assertFalse(result['complete_prior_support'])
                    self.assertEqual(result['component_support'], [])
                    self.assertEqual(result['granted_dependencies'], 0)
                    self.assertEqual(life.registration(self.root, digest(lifecycle['claim'])), original_record)

    def test_changed_unknown_or_prior_only_history_cannot_be_carried(self):
        lifecycle, historical = self.history_owner()
        for change in ('explanation', 'target', 'new_review'):
            candidate = deepcopy(lifecycle)
            if change == 'explanation':
                candidate['reviews'][0]['outcomes'][0]['explanation'] += ' altered'
            elif change == 'target':
                candidate['reviews'][0]['prior_claim_hash'] = digest('Unregistered fabricated predecessor')
            else:
                extra = deepcopy(historical)
                extra['outcomes'][0]['expectation_id'] = 'new-forged-review'
                candidate['reviews'].append(extra)
            for prior in (lifecycle, candidate):
                with self.subTest(change=change, fake_prior=prior == candidate), self.assertRaisesRegex(
                        Invalid, 'actual predecessor'):
                    self.carry(candidate, prior)

    def test_prior_history_without_immutable_claim_owner_proof_remains_rejected(self):
        lifecycle, _ = self.history_owner()
        temp = tempfile.TemporaryDirectory(prefix='btc-no-history-owner-')
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(Invalid, 'actual predecessor'):
            self.carry(lifecycle, root=Path(temp.name))
        bare = deepcopy(lifecycle)
        bare['claim']['id'] = 'GENERATED_OWNER_WITHOUT_REVIEW_PROOF'
        self.register(bare['claim'])
        with self.assertRaisesRegex(Invalid, 'actual predecessor'):
            self.carry(bare)

    def test_exact_forward_history_is_never_a_nonprospective_carry_exception(self):
        lifecycle, _ = self.history_owner('FORWARD_OBSERVATION', unvalidated=True)
        with self.assertRaisesRegex(Invalid, 'actual predecessor'):
            self.carry(lifecycle)

    def test_carried_history_cannot_grant_a_supported_label_or_successor_dependency(self):
        lifecycle, historical = self.history_owner()
        supported = deepcopy(lifecycle)
        supported['claim']['id'] = 'GENERATED_BAD_NONPROSPECTIVE_SUPPORT'
        supported['reviews'][0]['outcomes'][0].update(verdict='SUPPORTED', supported_scope=self.scope)
        # Even an archived unvalidated label cannot bypass the ordinary verdict gate.
        owner = self.root / 'invalid-label-owner.json'
        market = {'markets': [{'horizons': [{'forward': {'lifecycle': supported}}]}]}
        create(owner, {'label': 'SYNTHETIC_ENGINEERING_ONLY', 'created_at': T0, 'market_assessment': market})
        life.register(self.root, market, owner, '/created_at')
        with self.assertRaisesRegex(Invalid, 'cannot grant prospective support'):
            self.carry(supported)
        successor = deepcopy(lifecycle)
        successor['claim'].update(id='GENERATED_FALSE_HISTORY_DEPENDENCY', formed_at=T3,
                                  data_cutoff=T3, basis_kind='VALIDATED_PREDECESSOR')
        successor['dependencies'] = [{'claim_hash': historical['prior_claim_hash'],
            'expectation_id': 'legacy-check', 'review_hash': digest(historical), 'scope': self.scope}]
        with self.assertRaisesRegex(Invalid, 'unverified predecessor'):
            self.carry(successor, lifecycle)

    def test_carried_legacy_history_cannot_hide_actual_forward_falsification(self):
        lifecycle, _ = self.history_owner()
        failed = self.review('failure', lifecycle['claim'])
        candidate = deepcopy(lifecycle)
        candidate['reviews'].append(failed)
        with self.assertRaisesRegex(Invalid, 'falsified original claim'):
            self.carry(candidate, lifecycle)

    def test_actual_registered_forward_support_still_passes_with_carried_legacy_history(self):
        lifecycle, _ = self.history_owner()
        candidate = deepcopy(lifecycle)
        candidate['reviews'].append(self.review(claim=lifecycle['claim']))
        result = self.carry(candidate, lifecycle)
        self.assertTrue(result['complete_prior_support'])
        self.assertEqual(len(result['component_support']), 2)



if __name__=='__main__':unittest.main()
