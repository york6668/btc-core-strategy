"""Behavioral regression tests against an explicitly isolated MySQL 8.4 node.

All inputs are built at runtime. Set BTC_TEST_CONFIG to an isolated node config;
this suite never reads local account files, production state or saved fixtures.
"""
import base64
import errno
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import sqlite3
import subprocess
import sys
import unittest
import uuid

from btc_node.api import dispatch, invoke
from btc_node.codec import Conflict, encode, fingerprint, millis
from btc_node.database import Database
from btc_node.entities import Entities
from btc_node.lifecycle import maintain
from btc_node.series import Series
from btc_node.catalog import Catalog
from btc_node.market_store import Connection
from btc_node import backup
from unittest.mock import patch


class NodeContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = os.environ.get('BTC_TEST_CONFIG')
        if not config:
            raise RuntimeError('BTC_TEST_CONFIG must explicitly identify an isolated MySQL node')
        cls.settings = json.loads(Path(config).read_text())
        if cls.settings.get('test_only') is not True:
            raise RuntimeError('test configuration must declare test_only=true')

    def setUp(self):
        self.db = Database(self.settings)
        self.db.initialize()
        with self.db.transaction():
            self.db.metadata('writer',{'owner':'DOCKER'},write=True)
        self.id = 'test-' + uuid.uuid4().hex
        self.now = datetime.now(timezone.utc)
        self.objects, self.prices = Entities(self.db), Series(self.db)

    def tearDown(self):
        self.db.connection.rollback()
        self.db.close()

    def at(self, hours=0):
        return (self.now - timedelta(hours=hours)).isoformat()

    def test_daily_deep_claim_uses_existing_mysql_writer_across_clients(self):
        day_ms = int(datetime(2030, 3, 2, 12, tzinfo=timezone.utc).timestamp() * 1000)
        def attempt(index):
            claim_id = self.id + '-' + str(index)
            request = {'method':'model.deep.claim','operation_id':'claim-op-' + claim_id,
                       'params':{'claim_id':claim_id,'issue_id':self.id,'scope':'btc/research',
                                 'phase':'PUBLIC','input_digest':'sha256:'+'a'*64,'model':'gpt-6-astra'}}
            try: return request, dispatch(self.settings,request)
            except Conflict as exc: return request, str(exc)
        with patch('btc_node.deep_gate.millis',return_value=day_ms):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(attempt,range(2)))
            winners=[pair for pair in results if isinstance(pair[1],dict)]
            self.assertEqual(1,len(winners))
            claimed,result=winners[0]
            self.assertEqual('CLAIMED_NEW',result['action'])
            replay=dispatch(self.settings,claimed)
            self.assertEqual('RECONCILE_EXISTING_NO_LAUNCH',replay['action'])
            self.assertEqual(result['claim']['claim_id'],replay['claim']['claim_id'])
            settlement=dispatch(self.settings,{'method':'model.deep.finish',
                'operation_id':'finish-op-' + result['claim']['claim_id'],
                'params':{'claim_id':result['claim']['claim_id'],'outcome':'COMPLETE',
                          'result_ref':'sha256:'+'b'*64}})
            self.assertEqual('COMPLETE',settlement['claim']['state'])
            self.assertEqual('REUSE',dispatch(self.settings,{
                'method':'model.deep.claim','operation_id':'new-op-' + self.id,
                'params':{'claim_id':self.id+'-later','issue_id':self.id+'-different',
                          'scope':'btc/research','phase':'PUBLIC','input_digest':'sha256:'+'c'*64,
                          'model':'gpt-6-astra'}})['action'])

    def test_late_download_knowledge_correction_is_append_only_and_as_of_safe(self):
        product = self.id
        self.prices.instrument(product, {'market':'synthetic','symbol':'BTC',
            'product_type':'COLLECTOR_SERIES:seller_exhaustion_daily','quote_unit':'fraction',
            'source_identity':self.id})
        def row(value, known):
            return {'item_key':'day','observed_at':self.at(10),'known_at':known,'payload':{'value':value}}
        with tempfile.TemporaryDirectory() as folder:
            original = self.db.put_blob(encode({'synthetic':self.id}))
            connection = sqlite3.connect(Path(folder)/'collector_evidence.sqlite3')
            connection.execute('CREATE TABLE collector_attachments(message_id INTEGER,raw_digest TEXT,download_attempts INTEGER)')
            connection.execute('INSERT INTO collector_attachments VALUES(?,?,?)',(42,original,1))
            connection.commit();connection.close()
            old_reader = self.db.settings.get('reader_data')
            self.db.settings['reader_data'] = folder
            try:
                self.prices.ingest(product,'seller_exhaustion_daily',[row(.01,self.at(5))],{'origin':self.id},interval_ms=86400000)
                provenance = {'origin':self.id,'original_digest':original,'retrieval':{'received_at':self.at(2)}}
                source_digest = self.db.put_blob(encode(provenance))
                self.prices.ingest(product,'seller_exhaustion_daily',[row(.02,self.at(2))],provenance,interval_ms=86400000)
                before = self.db.rows('SELECT * FROM observations WHERE product=%s ORDER BY sequence',(product,))
                result = self.prices.repair_seller_knowledge(source_digest=source_digest)
                self.assertFalse(result['unchanged'])
                bound = result['correction']['effective_known_ms']
                query = lambda cutoff:self.prices.query(product,'seller_exhaustion_daily',start=self.at(11),end=self.at(9),as_of=cutoff)['observations']
                cutoff = datetime.fromtimestamp((bound-1)/1000,timezone.utc).isoformat()
                self.assertEqual(query(cutoff)[0]['payload']['value'],.01)
                visible = query(datetime.fromtimestamp(bound/1000,timezone.utc).isoformat())[0]
                self.assertEqual(visible['payload']['value'],.02)
                self.assertEqual(visible['known_ms'],bound)
                self.assertEqual(visible['original_known_ms'],millis(self.at(2)))
                self.assertTrue(visible['knowledge_correction']['first_raw_available_at_unknown'])
                self.assertTrue(self.prices.repair_seller_knowledge(source_digest=source_digest)['unchanged'])
                self.assertEqual(self.db.rows('SELECT * FROM observations WHERE product=%s ORDER BY sequence',(product,)),before)
                with self.assertRaises(ValueError):
                    self.prices.repair_seller_knowledge(source_digest='not-a-digest')
            finally:
                self.db.settings['reader_data'] = old_reader

    def test_waiting_delivery_remains_active_and_queryable_outside_operational_index(self):
        spec = self.spec('DELIVERY', scope='PERSONAL', payload={
            'status':{'state':'UNKNOWN', 'attempts':1},
            'recovery':{'attempts':[{'attempt_id':'SYNTHETIC_ATTEMPT', 'next_action':'WAIT_FOR_NEW_EVIDENCE'}], 'actions':[]}})
        self.objects.put(spec, expected_version=0)
        identity = spec['object_id']
        self.assertNotIn(identity, {v['object_id'] for v in self.objects.list(scope='PERSONAL')['objects']})
        self.assertIn(identity, {v['object_id'] for v in self.objects.list(scope='PERSONAL', kind='DELIVERY')['objects']})
        actual = self.objects.get('DELIVERY', identity)
        self.assertTrue(actual['active'])
        self.assertEqual(actual['payload']['status'], {'state':'UNKNOWN', 'attempts':1})
        # A new actionable attempt on the same delivery must not be hidden.
        spec['payload']['recovery']['actions'] = [{'attempt_id':'SYNTHETIC_NEW', 'next_action':'READ_BACK_THIS_ATTEMPT'}]
        self.objects.put(spec, expected_version=1)
        self.assertIn(identity, {v['object_id'] for v in self.objects.list(scope='PERSONAL')['objects']})

    def spec(self, kind='REGION', **changes):
        value = {'kind': kind, 'object_id': self.id + kind, 'scope': 'PUBLIC',
                 'product': None, 'observed_at': self.at(4), 'known_at': self.at(3),
                 'change_reason': 'first evidence-bound assessment', 'source_ref': 'test:generated',
                 'method_version': 'test-method', 'payload': {'asset': 'GENERATED', 'horizon': 'SWING',
                 'state': 'OBSERVED', 'unknown_exposure': None}, 'dependencies': []}
        value.update(changes)
        return value

    def test_retest_continuity_version_retirement_and_duplicate_content(self):
        spec = self.spec()
        self.objects.put(spec, expected_version=0)
        first = self.objects.get('REGION', spec['object_id'])
        original_bytes = self.db.blobs.get(first['digest'])
        self.assertFalse(self.objects.put(spec, expected_version=1)['changed'])
        touch = deepcopy(spec)
        touch.update(observed_at=self.at(2), known_at=self.at(2))
        touch['payload']['touches'] = [{'at': self.at(2), 'observed_price': '100'}]
        self.objects.put(touch, expected_version=1)
        current = self.objects.get('REGION', spec['object_id'])
        self.assertEqual(current['payload']['state'], 'OBSERVED')
        self.assertEqual(current['first_identified_ms'], first['first_identified_ms'])
        self.assertEqual(self.db.blobs.get(first['digest']), original_bytes)
        with self.assertRaises(Conflict):
            self.objects.put(spec, expected_version=1)
        retired = deepcopy(touch); retired['active'] = False
        self.objects.put(retired, expected_version=2)
        with self.assertRaises(Conflict):
            self.objects.put(touch, expected_version=3)
        self.assertEqual(len(self.objects.history('REGION', spec['object_id'])['versions']), 3)

    def test_product_units_late_data_and_revisions_are_as_known(self):
        product = self.id
        ident = {'market': 'TEST', 'symbol': 'X', 'product_type': 'SPOT', 'quote_unit': 'USDT',
                 'source_identity': 'runtime-generated'}
        self.prices.instrument(product, ident)
        with self.assertRaises(Conflict):
            self.prices.instrument(product, {**ident, 'quote_unit': 'INDEX_POINTS'})
        def insert(price, known):
            return self.prices.ingest(product, 'price', [{'item_key': 't', 'observed_at': self.at(10),
                'known_at': self.at(known), 'payload': {'price': price}}], {'quality': 'TEST'}, interval_ms=60000)
        insert('110', 5)
        insert('100', 8)  # arrives late, known earlier: must not replace the latest
        insert('111', 5)  # same clock tick: ingestion sequence decides the revision
        insert('111', 4)
        query = dict(start=self.at(12), end=self.at(1), limit=10)
        self.assertEqual(self.prices.query(product, 'price', as_of=self.at(9), **query)['observations'], [])
        self.assertEqual(self.prices.query(product, 'price', as_of=self.at(7), **query)['observations'][0]['payload']['price'], '100')
        current = self.prices.query(product, 'price', as_of=self.at(), **query)['observations']
        self.assertEqual(current[0]['payload']['price'], '111')
        self.assertEqual(current[0]['known_ms'], millis(self.at(5)))

    def test_reader_history_precedes_latest_model_snapshot_and_replay_is_idempotent(self):
        from btc_node.reader_prices import project
        from btc_node.collector_series import ingest
        directory = Path(os.environ['BTC_COLLECTOR_CONTRACT_PATH']).parent
        sys.path[:0] = [str(directory), str(directory/'tests')]
        from btc_evidence_reader import EvidenceReader
        from btc_evidence import encode_bundle
        from test_evidence_v2 import generated, rehash
        bundle, text = generated('BTC')
        closing = 100.1 + int(self.id[-4:], 16) % 900 / 1000
        bundle['measurements']['derivative_cvd_long']['rows'][-1]['close'] = closing
        rehash(bundle); raw = encode_bundle(bundle)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with sqlite3.connect(root/'messages.sqlite3') as config:
                config.execute('CREATE TABLE reader_config(singleton_id INTEGER,chat_id INTEGER)')
                config.execute('INSERT INTO reader_config VALUES(1,-100123)')
            clock = [millis(self.at(4))/1000]
            reader = EvidenceReader(root/'collector_evidence.sqlite3', allowed_chat_id=-100123, clock=lambda: clock[0])
            self.addCleanup(reader.close)
            def post(parent):
                base = {'chat': {'id': -100123, 'type': 'channel'}, 'date': int(clock[0])}
                reader.ingest_update({'update_id':parent,'channel_post':{**base,'message_id':parent,'text':text}})
                reader.ingest_update({'update_id':parent+1,'channel_post':{**base,'message_id':parent+1,
                    'reply_to_message': {'message_id':parent},
                    'document':{'file_id':str(parent),'file_name':'BTC.json','file_size':len(raw)}}})
                reader.process_pending(lambda _: raw)
            post(10)
            clock[0] = millis(self.at(2))/1000; post(20)
            latest = reader.observations(latest_only=True)[0]
            frozen = root/'frozen.json'; frozen.write_bytes(encode({'assets': {'BTC': latest}}))
            ingest(self.db, {'assets': {'BTC': latest}}, frozen)
            product = next(row['product'] for row in self.prices.products(limit=500)['products']
                           if row['identity']['product_type']=='COLLECTOR_SERIES:derivative_cvd_long'
                           and row['identity']['symbol']=='BTCUSDT')
            event = latest['datasets']['derivative_cvd_long']['rows'][-1]['observed_ms']
            at = datetime.fromtimestamp(event/1000, timezone.utc).isoformat()
            query = {'start': at, 'end': at, 'limit':10}
            before = self.prices.query(product,'derivative_cvd_long',as_of=self.at(3),**query)['observations']
            self.assertFalse(before and before[0]['payload']['close']==closing)
            self.db.settings['reader_data'] = str(root)
            first = project(self.db)
            self.assertEqual(first['processed'],2)
            received = self.prices.query(product,'derivative_cvd_long',as_of=self.at(3),**query)['observations'][0]
            self.assertEqual(received['payload']['close'],closing)
            self.assertEqual(received['known_ms'],millis(self.at(4)))
            count = self.db.one('SELECT COUNT(*) AS n FROM observations')['n']
            self.assertEqual(project(self.db)['processed'],0)
            self.assertEqual(self.db.one('SELECT COUNT(*) AS n FROM observations')['n'],count)
            clock[0] = millis(self.at(1))/1000; post(30)
            self.assertEqual(project(self.db)['processed'],1)
            self.assertEqual(self.db.one('SELECT COUNT(*) AS n FROM observations')['n'],count)
            self.assertEqual(reader.observations(latest_only=True)[0]['transport']['document_message_id'],31)
            del self.db.settings['reader_data']

    def test_expiry_does_not_roll_forward_and_unreported_dependencies_are_due(self):
        hypothesis = self.spec('HYPOTHESIS', valid_until=self.at(1))
        self.objects.put(hypothesis, expected_version=0)
        plan = self.spec('PLAN', dependencies=[{'type': 'ENTITY', 'kind': 'HYPOTHESIS',
                          'object_id': hypothesis['object_id']}])
        self.objects.put(plan, expected_version=0)
        extended = deepcopy(hypothesis)
        extended['valid_until'] = (self.now + timedelta(days=7)).isoformat()
        with self.assertRaises(Conflict):
            self.objects.put(extended, expected_version=1)
        changed = deepcopy(hypothesis); changed['payload']['counterevidence'] = ['new information']
        self.objects.put(changed, expected_version=1)
        due = self.objects.due(scope='PUBLIC')['objects']
        self.assertIn(plan['object_id'], [row['object_id'] for row in due])
        self.assertIn(hypothesis['object_id'], [row['object_id'] for row in due])

    def test_formal_plan_replacement_preserves_deadline_history_and_pending_outcome(self):
        from btc_node.projection import Projection
        from btc_node.execution import full_context
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'decision.json'
            source.write_text(json.dumps({'generated': self.id}))
            projection = Projection(self.db, 'generated-method')
            def binding(suffix, hours):
                value = projection.put('DECISION', self.id + suffix, {'generated': suffix}, source,
                                       self.at(hours), active=False)
                return {field: value[field] for field in ('object_id', 'version', 'digest')}
            first, second = binding('first', 12), binding('second', 2)
            name = self.id + '-side'
            legacy_id = 'recommendation:' + name
            old_plan = {'validity': {'until': self.at(6)}, 'execution': 'CONDITIONAL', 'filled': None}
            new_plan = {**old_plan, 'validity': {'until': self.at(-24)}}
            legacy = projection.put('PLAN', legacy_id,
                {'market_binding': second, 'recommendation': new_plan}, source, self.at(1),
                scope='PERSONAL', expires=old_plan['validity']['until'])
            legacy_bytes = self.db.blobs.get(legacy['digest'])
            # The original formal record still carries its own deadline. The
            # legacy mixed object remains intact until the present correction.
            projection.plans({name: old_plan}, first, source, self.at(12), adopted_at=self.at(12))
            projection.plans({name: new_plan}, second, source, self.at(), adopted_at=self.at(2),
                             fact_fields={'current_fact_version': {'generated': 'v1'}})
            active_id = 'recommendation:' + name + ':' + second['object_id']
            retired_id = 'recommendation:' + name + ':' + first['object_id']
            active = self.objects.get('PLAN', active_id)
            retired = self.objects.get('PLAN', retired_id)
            self.assertEqual(active['first_identified_ms'], millis(self.at(2)))
            self.assertEqual(active['observed_ms'], millis(self.at(2)))
            self.assertEqual(active['known_ms'], millis(self.at()))
            self.assertEqual(active['valid_until'], new_plan['validity']['until'])
            self.assertEqual(active['payload']['recommendation']['filled'], None)
            self.assertFalse(retired['active'])
            self.assertEqual(retired['valid_until'], old_plan['validity']['until'])
            self.assertEqual(retired['payload']['superseded_by'], active_id)
            self.assertEqual(self.db.blobs.get(legacy['digest']), legacy_bytes)
            self.assertTrue(self.objects.get('PLAN', legacy_id, as_of=self.at(0.5))['active'])
            self.assertFalse(self.objects.get('PLAN', legacy_id)['active'])
            self.assertEqual(self.objects.get('PLAN', legacy_id)['valid_until'], legacy['valid_until'])
            due_ids = [row['object_id'] for row in self.objects.due(scope='PERSONAL', limit=500)['objects']]
            self.assertIn(legacy_id, due_ids)
            self.assertIn(retired_id, due_ids)
            self.assertNotIn(active_id, due_ids)
            context = full_context(self.db, 'PERSONAL')
            self.assertIn(active_id, [row['object_id'] for row in context['objects']])
            self.assertNotIn(legacy_id, [row['object_id'] for row in context['objects']])
            self.assertIn(retired['entity_key'], [row['entity_key'] for row in context['requirements']])
            version = active['version']
            projection.plans({name: new_plan}, second, source, self.at(), adopted_at=self.at(2),
                             fact_fields={'current_fact_version': {'generated': 'v1'}})
            self.assertEqual(self.objects.get('PLAN', active_id)['version'], version)
            projection.plans({name: new_plan}, second, source, self.at(), adopted_at=self.at(2),
                             fact_fields={'current_fact_version': {'generated': 'v2'}}, due=self.at())
            self.assertEqual(self.objects.get('PLAN', active_id)['valid_until'], active['valid_until'])
            self.assertEqual(self.objects.get('PLAN', active_id)['object_id'], active_id)
            # A late immutable source cannot revive the retired plan or displace
            # the replacement, even when it was not imported previously.
            late = binding('late', 10)
            projection.plans({name: old_plan}, late, source, self.at(10), adopted_at=self.at(10))
            self.assertFalse(self.objects.get('PLAN', 'recommendation:' + name + ':' + late['object_id'])['active'])
            self.assertTrue(self.objects.get('PLAN', active_id)['active'])
            projection.plans({name: old_plan}, first, source, self.at(), adopted_at=self.at(12))
            self.assertFalse(self.objects.get('PLAN', retired_id)['active'])
            with self.assertRaises(Conflict):
                changed = {**new_plan, 'validity': {'until': self.at(-48)}}
                projection.plans({name: changed}, second, source, self.at(), adopted_at=self.at(2))

    def test_fact_scope_cannot_overwrite_independent_market_or_unknown_values(self):
        fact = self.spec('FACT_PROJECTION', scope='PERSONAL', payload={'cash': None, 'margin': None})
        with self.assertRaises(ValueError):
            self.objects.put(fact, expected_version=0)
        self.objects.put(fact, expected_version=0, trusted_projection=True)
        market = self.spec('DECISION')
        self.objects.put(market, expected_version=0)
        contaminated = deepcopy(market)
        contaminated['dependencies'] = [{'type': 'ENTITY', 'kind': 'FACT_PROJECTION', 'object_id': fact['object_id']}]
        with self.assertRaises(ValueError):
            self.objects.put(contaminated, expected_version=1)
        allocation = self.spec('ALLOCATION', scope='PERSONAL', dependencies=[{'type': 'ENTITY',
                               'kind': 'FACT_PROJECTION', 'object_id': fact['object_id']}])
        self.objects.put(allocation, expected_version=0)
        fact['payload']['cash'] = '1500'
        self.objects.put(fact, expected_version=1, trusted_projection=True)
        self.assertEqual(self.objects.get('DECISION', market['object_id'])['version'], 1)
        self.assertIsNone(self.objects.get('FACT_PROJECTION', fact['object_id'])['payload']['margin'])
        self.assertIn(allocation['object_id'], [row['object_id'] for row in self.objects.due(scope='PERSONAL')['objects']])
        frozen=self.objects.get('DECISION',market['object_id'])
        binding={key:frozen[key] for key in ('object_id','version','digest')}
        queries=[('object.get',{'scope':'PERSONAL','kind':'FACT_PROJECTION','object_id':fact['object_id']}),
                 ('object.history',{'kind':'FACT_PROJECTION','object_id':fact['object_id']}),
                 ('object.list',{'scope':'PERSONAL'}),('conditions.due',{'scope':'PERSONAL'})]
        for method,params in queries:
            with self.assertRaisesRegex(ValueError,'freeze independent'):
                invoke(self.db,method,params)
            self.assertIsNotNone(invoke(self.db,method,{**params,'market_binding':binding}))

    def test_historical_review_does_not_spawn_work_but_keeps_live_consumer_corrections(self):
        from btc_node.codec import identity
        from btc_node.execution import full_context
        hypothesis = self.spec('HYPOTHESIS', valid_until=self.at(1))
        self.objects.put(hypothesis, expected_version=0)
        review = self.spec('REVIEW', active=True, review_at=self.at(2), payload={
            'subject': {'kind':'HYPOTHESIS','object_id':hypothesis['object_id'],'version':1},
            'outcome':'EVIDENCE_LIMITED','explanation':'Original window still needs evidence.'},
            dependencies=[{'type':'ENTITY','kind':'HYPOTHESIS','object_id':hypothesis['object_id']}])
        self.objects.put(review, expected_version=0)
        historical = self.objects.get('REVIEW', review['object_id'])
        self.assertFalse(historical['active'])
        self.assertIsNone(historical['review_at'])
        plan = self.spec('PLAN', dependencies=[{'type':'ENTITY','kind':'REVIEW','object_id':review['object_id']}])
        self.objects.put(plan, expected_version=0)

        # A legacy active flag and its erroneous obligation remain readable,
        # without becoming fresh work in either API or normal-run context.
        self.db.execute('UPDATE entities SET active=1 WHERE entity_key=%s',(historical['entity_key'],))
        stale = identity(self.id, 'historical-review-obligation')
        self.db.execute('INSERT INTO review_requirements(requirement_key,entity_key,cause_key,cause_version,reason,opened_ms) '
                        'VALUES(%s,%s,%s,%s,%s,%s)', (stale,historical['entity_key'],
                        self.objects.get('HYPOTHESIS',hypothesis['object_id'])['entity_key'],1,'legacy',millis()))
        changed = deepcopy(hypothesis); changed.update(known_at=self.at(1), observed_at=self.at(1))
        changed['payload']['counterevidence']=['corrected source']
        self.objects.put(changed, expected_version=1)
        due = {item['object_id'] for item in self.objects.due(scope='PUBLIC',limit=500)['objects']}
        self.assertIn(hypothesis['object_id'],due)
        self.assertIn(plan['object_id'],due)
        self.assertNotIn(review['object_id'],due)
        context=full_context(self.db,'PUBLIC')
        self.assertNotIn(review['object_id'],[item['object_id'] for item in context['objects']])
        self.assertNotIn(stale,[item['requirement_key'] for item in context['requirements']])
        self.assertEqual(self.objects.get('REVIEW',review['object_id'])['digest'],historical['digest'])
        self.assertIsNotNone(self.db.one('SELECT * FROM review_requirements WHERE requirement_key=%s',(stale,)))
        self.assertIn(review['object_id'],[item['object_id'] for item in
                      self.objects.list(scope='PUBLIC',kind='REVIEW',active=None,limit=500)['objects']])

    def test_as_known_context_preserves_version_and_retirement_history(self):
        from btc_node.execution import full_context
        value=self.spec('CYCLE', observed_at=self.at(6),known_at=self.at(5),payload={'direction':'BULLISH'})
        self.objects.put(value,expected_version=0)
        original=self.objects.get('CYCLE',value['object_id'])
        changed=deepcopy(value);changed.update(observed_at=self.at(2),known_at=self.at(1),active=False)
        changed['payload']={'direction':'UNKNOWN','reason':'Original interpretation withdrawn by new evidence.'}
        self.objects.put(changed,expected_version=1)
        past=full_context(self.db,'PUBLIC',as_of=self.at(3))
        prior=next(v for v in past['objects'] if v['object_id']==value['object_id'])
        self.assertEqual(prior['version'],1)
        self.assertEqual(prior['digest'],original['digest'])
        self.assertNotIn(value['object_id'],{v['object_id'] for v in full_context(self.db,'PUBLIC')['objects']})
        self.assertEqual(self.db.value(original['digest'])['payload'],{'direction':'BULLISH'})
        self.assertEqual(past['role'],'HISTORICAL_JUDGEMENT_CONTEXT_NOT_NEW_EXTERNAL_EVIDENCE')

    def test_legacy_dynamic_context_preserves_bytes_and_recovers_omitted_history(self):
        from btc_node.execution import project_state
        value=self.spec('CYCLE',observed_at=self.at(6),known_at=self.at(5))
        self.objects.put(value,expected_version=0)
        retired=deepcopy(value);retired.update(observed_at=self.at(2),known_at=self.at(1),active=False)
        self.objects.put(retired,expected_version=1)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'run.json').write_bytes(encode({'created_at':self.at(3)}))
            legacy=encode({'scope':'PUBLIC','objects':[],'requirements':[]})
            (path/'continuous-public.json').write_bytes(legacy)
            with patch('btc_node.execution.Projection.refresh',return_value={}):
                result=project_state(self.db,{'project':folder},os.environ['BTC_TEST_SOURCE'],['--run',str(path/'run.json')])
            self.assertEqual((path/'continuous-public.json').read_bytes(),legacy)
            self.assertEqual(result['public_context_binding']['origin'],'LEGACY_RECONSTRUCTED_AS_KNOWN')
            bounded=json.loads(Path(result['public_context']).read_bytes())
            self.assertIn(value['object_id'],{v['object_id'] for v in bounded['objects']})

    def test_original_package_revision_reaches_consumer_through_historical_decision(self):
        product=self.id+'-product'
        self.prices.instrument(product,{'market':'SYNTHETIC','symbol':'TEST','product_type':'SPOT',
            'quote_unit':'TEST','source_identity':'generated-only'})
        original=self.db.put_blob(encode({'synthetic_raw':'first observation'}))
        row={'item_key':'bar','observed_at':self.at(6),'known_at':self.at(5),'payload':{'price':'100'}}
        self.prices.ingest(product,'quotes',[row],{'original_digest':original,'source':'generated-only'},interval_ms=3600000)
        decision=self.spec('DECISION',active=False,dependencies=[{'type':'BLOB','digest':original}])
        self.objects.put(decision,expected_version=0)
        frozen=self.objects.get('DECISION',decision['object_id'])
        cycle=self.spec('CYCLE',known_at=self.at(2),dependencies=[{'type':'ENTITY','kind':'DECISION','object_id':decision['object_id']}])
        self.objects.put(cycle,expected_version=0)
        unrelated=self.spec('FACTOR',object_id=self.id+'-unrelated')
        self.objects.put(unrelated,expected_version=0)
        newer=self.db.put_blob(encode({'synthetic_raw':'corrected observation'}))
        row.update(known_at=self.at(1),payload={'price':'90'})
        self.prices.ingest(product,'quotes',[row],{'original_digest':newer,'source':'generated-only'},interval_ms=3600000)
        due={v['object_id'] for v in self.objects.due(scope='PUBLIC',limit=500)['objects']}
        self.assertIn(cycle['object_id'],due)
        self.assertNotIn(unrelated['object_id'],due)
        self.assertNotIn(decision['object_id'],due)
        self.assertEqual(self.objects.get('DECISION',decision['object_id'])['digest'],frozen['digest'])
        self.assertEqual(self.db.value(original),{'synthetic_raw':'first observation'})

    def test_explicit_factor_and_horizon_exit_is_not_reversed_by_projection(self):
        from btc_node.projection import Projection
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.json';path.write_text('{"synthetic_source":true}')
            for kind in ('FACTOR','HYPOTHESIS'):
                value=self.spec(kind,observed_at=self.at(6),known_at=self.at(5))
                self.objects.put(value,expected_version=0)
                retired=deepcopy(value);retired.update(observed_at=self.at(2),known_at=self.at(1),active=False,
                    change_reason='Explicit evidence-bound withdrawal; omission alone is not a retirement.')
                self.objects.put(retired,expected_version=1)
                prior=self.objects.get(kind,value['object_id'])
                projected=Projection(self.db,'synthetic-method').put(kind,value['object_id'],value['payload'],path,self.at())
                self.assertFalse(projected['active'])
                self.assertEqual(projected['digest'],prior['digest'])
                self.assertEqual(len(self.objects.history(kind,value['object_id'])['versions']),2)

    def test_same_value_retrieval_remains_a_cited_revision_bridge_after_retention(self):
        product = self.id + '-reverified'
        self.prices.instrument(product, {'market': 'SYNTHETIC', 'symbol': 'TEST',
            'product_type': 'SPOT', 'quote_unit': 'TEST', 'source_identity': 'generated'})
        original_a = self.db.put_blob(encode({'synthetic_raw': 'A100'}))
        original_b = self.db.put_blob(encode({'synthetic_raw': 'B100 unchanged reacquisition'}))
        original_c = self.db.put_blob(encode({'synthetic_raw': 'C90'}))
        row = {'item_key': 'bar', 'observed_at': self.at(50*24),
               'known_at': self.at(49*24), 'payload': {'price': '100'}}
        self.assertEqual(self.prices.ingest(product, 'quotes', [row],
            {'original_digest': original_a, 'source': 'generated'}, interval_ms=3600000)['inserted'], 1)
        row['known_at'] = self.at(48*24)
        self.assertEqual(self.prices.ingest(product, 'quotes', [row],
            {'original_digest': original_b, 'source': 'generated'}, interval_ms=3600000),
            {'inserted': 0, 'unchanged': 1})
        decision = self.spec('DECISION', active=False, observed_at=self.at(47*24),
            known_at=self.at(47*24), dependencies=[{'type': 'BLOB', 'digest': original_b}])
        self.objects.put(decision, expected_version=0)
        cycle = self.spec('CYCLE', observed_at=self.at(47*24), known_at=self.at(47*24),
            dependencies=[{'type': 'ENTITY', 'kind': 'DECISION', 'object_id': decision['object_id']}])
        self.objects.put(cycle, expected_version=0)
        unrelated = self.spec('HYPOTHESIS')
        self.objects.put(unrelated, expected_version=0)
        original_bytes = self.db.blobs.get(original_b)
        self.db.connection.commit()  # Maintenance opens its own writer transaction.
        maintain(self.db, apply=True, now=self.at())
        self.assertIsNotNone(self.db.one('SELECT r.source_digest FROM series_retrievals r '
            'JOIN series_originals o USING(source_digest) WHERE o.original_digest=%s', (original_b,)))
        row.update(known_at=self.at(1), payload={'price': '90'})
        self.prices.ingest(product, 'quotes', [row],
            {'original_digest': original_c, 'source': 'generated'}, interval_ms=3600000)
        due = {v['object_id'] for v in self.objects.due(scope='PUBLIC', limit=500)['objects']}
        self.assertIn(cycle['object_id'], due)
        self.assertNotIn(decision['object_id'], due)
        self.assertNotIn(unrelated['object_id'], due)
        self.assertEqual(self.db.blobs.get(original_b), original_bytes)
        self.assertEqual(self.db.one('SELECT COUNT(*) AS n FROM observations '
            'WHERE product=%s AND series=%s', (product, 'quotes'))['n'], 2)

    def test_historical_context_masks_a_later_review_resolution(self):
        from btc_node.execution import full_context
        actual_millis = millis
        factor = self.spec('FACTOR', observed_at=self.at(6), known_at=self.at(5))
        self.objects.put(factor, expected_version=0)
        cycle = self.spec('CYCLE', observed_at=self.at(6), known_at=self.at(5),
            dependencies=[{'type': 'ENTITY', 'kind': 'FACTOR', 'object_id': factor['object_id']}])
        self.objects.put(cycle, expected_version=0)
        factor.update(observed_at=self.at(4), known_at=self.at(4))
        factor['payload']['revision'] = 'new observation'
        with patch('btc_node.entities.millis', side_effect=lambda value=None:
                   actual_millis(self.at(4) if value is None else value)):
            self.objects.put(factor, expected_version=1)
        owner = self.objects.get('CYCLE', cycle['object_id'])
        requirement = self.db.one('SELECT requirement_key FROM review_requirements '
            'WHERE entity_key=%s', (owner['entity_key'],))['requirement_key']
        review = self.spec('REVIEW', active=False, observed_at=self.at(2), known_at=self.at(1),
            payload={'subject': {'kind': 'CYCLE', 'object_id': cycle['object_id'], 'version': 1},
                'outcome': 'REVISE', 'explanation': 'Later generated review.',
                'requirements': [requirement]},
            dependencies=[{'type': 'ENTITY', 'kind': 'CYCLE', 'object_id': cycle['object_id']}])
        self.objects.put(review, expected_version=0)
        self.objects.resolve(requirement, review['object_id'])
        historical = next(v for v in full_context(self.db, 'PUBLIC', as_of=self.at(3))['requirements']
                          if v['requirement_key'] == requirement)
        self.assertIsNone(historical['resolved_ms'])
        self.assertIsNone(historical['resolution_ref'])
        current = self.db.one('SELECT resolved_ms,resolution_ref FROM review_requirements '
            'WHERE requirement_key=%s', (requirement,))
        self.assertIsNotNone(current['resolved_ms'])
        self.assertEqual(current['resolution_ref'], review['object_id'])
        self.assertNotIn(requirement, {v['requirement_key'] for v in
            full_context(self.db, 'PUBLIC')['requirements']})

    def test_unresolved_review_cannot_clear_subject_dependency(self):
        hypothesis=self.spec('HYPOTHESIS')
        self.objects.put(hypothesis,expected_version=0)
        plan=self.spec('PLAN',dependencies=[{'type':'ENTITY','kind':'HYPOTHESIS','object_id':hypothesis['object_id']}])
        self.objects.put(plan,expected_version=0)
        hypothesis['payload']['counterevidence']=['new evidence']
        self.objects.put(hypothesis,expected_version=1)
        owner=self.objects.get('PLAN',plan['object_id'])
        requirement=self.db.one('SELECT requirement_key FROM review_requirements WHERE entity_key=%s',
                                (owner['entity_key'],))['requirement_key']
        review=self.spec('REVIEW',known_at=datetime.now(timezone.utc).isoformat(),payload={
            'subject':{'kind':'PLAN','object_id':plan['object_id'],'version':1},
            'outcome':'PENDING','explanation':'Waiting for corroboration.','requirements':[requirement]},
            dependencies=[{'type':'ENTITY','kind':'PLAN','object_id':plan['object_id']}])
        for version,outcome in enumerate(('PENDING','EVIDENCE_LIMITED')):
            review['payload']['outcome']=outcome
            self.objects.put(review,expected_version=version)
            with self.assertRaisesRegex(ValueError,'cannot discharge'):
                self.objects.resolve(requirement,review['object_id'])
            self.assertIsNone(self.db.one('SELECT resolved_ms FROM review_requirements WHERE requirement_key=%s',
                                         (requirement,))['resolved_ms'])
        review['payload'].update(outcome='REVISE',explanation='Actual new evidence invalidates the old condition.')
        self.objects.put(review,expected_version=2)
        self.objects.resolve(requirement,review['object_id'])
        self.assertIsNotNone(self.db.one('SELECT resolved_ms FROM review_requirements WHERE requirement_key=%s',
                                        (requirement,))['resolved_ms'])

    def test_original_forward_window_requires_evidenced_outcome_and_collector_keeps_receipt_clock(self):
        hypothesis = self.spec('HYPOTHESIS', valid_until=self.at(2))
        self.objects.put(hypothesis, expected_version=0)
        subject = {'kind':'HYPOTHESIS','object_id':hypothesis['object_id'],'version':1}
        review = self.spec('REVIEW', observed_at=self.at(1), known_at=self.at(1),
            payload={'subject':subject,'outcome':'PENDING','explanation':'Window elapsed without enough evidence.'})
        self.objects.put(review, expected_version=0)
        revised = deepcopy(hypothesis)
        revised.update(known_at=self.at(), valid_until=self.at(-24), outcome_review=review['object_id'])
        with self.assertRaises(Conflict): self.objects.put(revised, expected_version=1)
        proof = self.db.put_blob(encode({'result':'generated actual outcome'}))
        review['payload'].update(outcome='FAILED',explanation='Observed result contradicted the original deadline.')
        review['dependencies']=[{'type':'BLOB','digest':proof}]
        self.objects.put(review, expected_version=1)
        self.objects.put(revised, expected_version=1)
        self.assertEqual(self.objects.get('HYPOTHESIS',hypothesis['object_id'],version=1)['valid_until'],self.at(2))

        from btc_node.collector_series import ingest
        known, observed = millis(self.at(1)), millis(self.at(4))
        row={'observed_ms':observed,'open_time':observed-3599999,'close_time':observed,
             'close':101,'retrieved_at_ms':millis(self.at(2)),
             'provenance':{'endpoint_url':'https://example.test/public/futures','unit':'USDT'}}
        package={'assets':{'GENERATED':{'transport':{'received_at':known/1000},'bundle_id':'first',
            'datasets':{'contract_1h':{'instrument':self.id,'unit':'USDT','relation':'proxy','rows':[row]}}}}}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'collector.json';path.write_bytes(encode(package))
            first=ingest(self.db,package,path)
            self.assertEqual(first['counts'][0]['inserted'],1)
            package['assets']['GENERATED']['transport']['received_at']=millis(self.at())/1000
            row['retrieved_at_ms']=millis(self.at())
            path.write_bytes(encode(package))
            self.assertEqual(ingest(self.db,package,path)['counts'][0]['unchanged'],1)
            products=[r for r in self.prices.products(limit=500)['products'] if r['identity']['symbol']==self.id]
            self.assertEqual(len(products),1)
            product=products[0]['product']
            query={'start':self.at(6),'end':self.at(),'limit':10}
            self.assertEqual(self.prices.query(product,'contract_1h',as_of=self.at(2),**query)['observations'],[])
            result=self.prices.query(product,'contract_1h',as_of=self.at(),**query)['observations'][0]
            self.assertEqual(result['known_ms'],known)
            self.assertEqual(result['provenance']['trust'],'UNTRUSTED_LEAD')
            self.assertEqual(result['provenance']['relation'],'proxy')

    def test_exactly_once_request_and_concurrent_versions(self):
        self.db.connection.rollback()
        request = {'method': 'object.put', 'operation_id': self.id,
                   'params': {'spec': self.spec(), 'expected_version': 0}}
        first = dispatch(self.settings, request)
        self.assertEqual(dispatch(self.settings, request), first)
        revised = deepcopy(request)
        revised['operation_id'] += '-revision'
        revised['params']['expected_version'] = 1
        revised['params']['spec']['payload']['state'] = 'FAILED'
        another = deepcopy(revised); another['operation_id'] += '-competing'
        def submit(req):
            try:
                return dispatch(self.settings, req)['version']
            except Conflict:
                return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, [revised, another]))
        self.assertCountEqual(results, [2, 'CONFLICT'])

    def test_seller_projection_retrievals_revisions_raw_refs_and_daily_retention(self):
        from btc_node.collector_series import ingest, SELLER
        sys.path.insert(0,str(Path(os.environ['BTC_COLLECTOR_CONTRACT_PATH']).parent))
        import seller_exhaustion as sec
        known = millis(self.at(5))
        last = (known // sec.DAY - 1) * sec.DAY
        rows=[]
        for i in range(70):
            day=last-(69-i)*sec.DAY
            values={'price':50000*math.exp(.02*math.sin(i/3)), 'profit':12000000+i*100, 'supply':20000000}
            rows.append({'observed_ms':day+sec.DAY-1,'period_start_ms':day,'period_end_ms':day+sec.DAY,
                'inputs':{kind:{'value':values[kind],'source_as_of_ms':day,'retrieved_at_ms':known,
                    'provenance':{'endpoint_url':sec.BASE+endpoint,'access_scope':'public','unit':unit}}
                    for kind,(endpoint,_,unit) in sec.INPUTS.items()}})
        item=sec.measurements(None,known)[SELLER]
        item.update(rows=sec.derive(rows),status='ok')
        dataset=sec.project(item,known)['datasets'][SELLER]
        package={'assets':{'BTC':{'transport':{'received_at':known/1000},'bundle_id':self.id,
                                 'datasets':{SELLER:dataset}}}}
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'collector.json'
            def save():
                source.write_bytes(encode(package))
                return ingest(self.db,package,source)
            node=Path(folder)/'container-node.json'
            node.write_text(json.dumps({'reader_code_dir':str(Path(os.environ['BTC_COLLECTOR_CONTRACT_PATH']).parent)}))
            # The daemon does not inherit the child CLI's BTC_NODE_CONFIG or
            # test-only contract override. Its persisted node owns the reader.
            with patch.dict(self.db.settings, {'container_node_config':str(node)}), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(sum(r['inserted'] for r in save()['counts']),40)
            product=next(r['product'] for r in self.prices.products(limit=500)['products']
                         if r['identity']['product_type']=='COLLECTOR_SERIES:'+SELLER)
            query={'start':self.at(24*80),'end':self.at(),'limit':100}
            original=self.prices.query(product,SELLER,as_of=self.at(4),**query)['observations']
            self.assertEqual(original[-1]['known_ms'],known)
            self.assertEqual(original[-1]['interval_ms'],sec.DAY)
            self.assertFalse(original[-1]['provenance']['analysis_eligible'])
            ref=original[-1]['provenance']['retrieval']['raw_ref']
            self.assertEqual(ref['value_hash'],'sha256:'+__import__('hashlib').sha256(encode(dataset)).hexdigest())
            self.assertEqual(self.db.value(original[-1]['provenance']['original_digest']),package)
            # Receipt projection stores a producer attachment, not the frozen
            # assets snapshot. Resolve the reference against its actual bytes.
            attachment = {'measurements': {SELLER: deepcopy(dataset)}}
            raw_source = Path(folder)/'attachment.json'
            raw_source.write_bytes(encode(attachment))
            self.assertEqual(sum(r['inserted'] for r in ingest(self.db,package,raw_source)['counts']),0)
            attachment_digest = self.db.put_blob(raw_source.read_bytes())
            retrieval = next(self.db.value(r['source_digest']) for r in self.db.rows(
                'SELECT source_digest FROM series_retrievals WHERE product=%s AND series=%s', (product,SELLER))
                if self.db.value(r['source_digest']).get('original_digest') == attachment_digest)
            ref = retrieval['retrieval']['raw_ref']
            self.assertEqual(ref['pointer'], '/measurements/' + SELLER)
            self.assertEqual(ref['value_hash'], 'sha256:'+__import__('hashlib').sha256(encode(attachment['measurements'][SELLER])).hexdigest())
            from btc_node.collector_series import seller_source_ref
            conflicting = deepcopy(attachment)
            conflicting['measurements'][SELLER]['rows'][-1]['value'] += 0.001
            with self.assertRaisesRegex(ValueError, 'differs from stored source'):
                seller_source_ref(attachment_digest, conflicting, [original[-1]['payload']])
            # Reproduce the already-persisted bad pointer in this isolated DB.
            legacy = deepcopy(retrieval)
            legacy['retrieval']['raw_ref'] = {**ref, 'pointer':'/assets/BTC/datasets/' + SELLER}
            legacy_digest = self.db.put_blob(encode(legacy))
            self.prices.preserve_original(legacy_digest, legacy)
            self.db.execute('UPDATE observations SET source_digest=%s WHERE observation_key=%s',
                            (legacy_digest, original[-1]['observation_key']))
            count = self.db.one('SELECT COUNT(*) AS n FROM observations')['n']
            with patch('btc_node.series.millis', return_value=millis(self.at(1))):
                repaired = self.prices.repair_seller_references(limit=500)
            self.assertGreaterEqual(repaired['repaired'], 1)
            old = self.prices.query(product,SELLER,as_of=self.at(2),**query)['observations'][-1]
            self.assertNotIn('reference_correction', old)
            corrected = self.prices.query(product,SELLER,as_of=self.at(),**query)['observations'][-1]
            self.assertEqual(corrected['provenance'], legacy)
            self.assertEqual(corrected['reference_correction']['corrected_ref'], ref)
            self.assertEqual(corrected['reference_correction']['original_ref'], legacy['retrieval']['raw_ref'])
            self.assertEqual(corrected['known_ms'], known)
            self.assertEqual(self.prices.repair_seller_references(limit=500)['repaired'], 0)
            self.assertEqual(self.db.one('SELECT COUNT(*) AS n FROM observations')['n'], count)
            self.assertEqual(raw_source.read_bytes(), encode(attachment))
            # Upgrade an already-projected frozen file, without rewriting it
            # or reprojecting unrelated datasets; absent old fields stay absent.
            from btc_node.history import project_history
            from types import SimpleNamespace
            sys.path.insert(0,str(Path(os.environ['BTC_TEST_SOURCE'])/'skill/btc-core-strategy/scripts'))
            root=Path(folder)/'strategy';(root/'v4'/self.id).mkdir(parents=True)
            frozen=root/'v4'/self.id/'collector.json'
            frozen.write_bytes(source.read_bytes()); original_bytes=frozen.read_bytes()
            Catalog(self.db).sync('strategy',root)
            document=self.db.one('SELECT document_key,digest FROM documents WHERE namespace=%s AND path=%s',
                                 ('strategy','v4/'+self.id+'/collector.json'))
            self.db.execute('REPLACE INTO projection_sources(document_key,digest,projected_ms,outcome) VALUES(%s,%s,%s,%s)',
                            (document['document_key'],document['digest'],known,'COLLECTOR'))
            project_history(SimpleNamespace(db=self.db),root/'v4')
            self.assertEqual(self.db.one('SELECT outcome FROM projection_sources WHERE document_key=%s',
                                        (document['document_key'],))['outcome'],'COLLECTOR_V2')
            self.assertEqual(project_history(SimpleNamespace(db=self.db),root/'v4')['sources'],{})
            self.assertEqual(frozen.read_bytes(),original_bytes)
            for row in dataset['rows']:
                for raw in row['inputs'].values():raw['retrieved_at_ms']=millis(self.at(3))
            dataset['calculated_at_ms']=millis(self.at(3))
            package['assets']['BTC']['transport']['received_at']=millis(self.at(3))/1000
            package['assets']['BTC']['bundle_id']=self.id+'-refresh'
            self.assertEqual(sum(r['unchanged'] for r in save()['counts']),40)
            # Moving the attachment start must not retract a previously valid
            # boundary value; raw warmup inputs remain in the frozen package.
            boundary = original[0]['item_key']
            dataset['rows'] = sec.derive(dataset['rows'][1:])
            self.assertEqual(sum(r['inserted'] for r in save()['counts']), 0)
            kept = self.prices.query(product,SELLER,as_of=self.at(),**query)['observations']
            self.assertIsNotNone(next(r['payload']['value'] for r in kept if r['item_key'] == boundary))
            dataset['rows'][-1]['inputs']['profit']['value']=None
            dataset['rows']=sec.derive(dataset['rows'])
            self.assertEqual(sum(r['inserted'] for r in save()['counts']),1)
            current=self.prices.query(product,SELLER,as_of=self.at(),**query)['observations']
            self.assertIsNone(current[-1]['payload']['value'])
            self.assertIsNotNone(self.prices.query(product,SELLER,as_of=self.at(4),**query)['observations'][-1]['payload']['value'])
            self.assertEqual(sum(r['inserted'] for r in save()['counts']),0)
            bad=dataset['rows'][-1]['inputs']['supply']['provenance']
            bad['endpoint_url']='https://unexpected.test/supply'
            self.assertIn('source_identity_mismatch',save()['gaps'][0]['reason'])
            with self.db.transaction():pass
            maintain(self.db,apply=True,now=self.now+timedelta(days=500))
            maintain(self.db,apply=True,now=self.now+timedelta(days=503))
            self.assertTrue(self.prices.query(product,SELLER,as_of=self.at(),**query)['observations'])
            self.assertTrue(self.db.value(original[-1]['provenance']['original_digest']))
            self.assertTrue(self.db.value(attachment_digest))

    def test_frozen_run_binding_is_returned_and_formal_binding_stays_separate(self):
        from btc_node.execution import project_state
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'run.json').write_bytes(encode({'created_at':self.at(1)}))
            frozen={'result_hash':self.id,'created_at':self.at(1),'outlook':{'generated':True}}
            (path/'market-result.json').write_bytes(encode(frozen))
            formal={'object_id':'old-formal','version':1,'digest':'old'}
            with patch('btc_node.execution.Projection.refresh',return_value={'market_binding':formal}):
                result=project_state(self.db,{'project':folder},os.environ['BTC_TEST_SOURCE'],['--run',str(path/'run.json')])
            personal=json.loads((path/'continuous-personal.json').read_bytes())
            self.assertEqual(result['market_binding'],personal['market_binding'])
            self.assertEqual(result['market_binding']['object_id'],self.id)
            self.assertEqual(result['formal_market_binding'],formal)

    def test_recycle_preserves_essential_evidence_and_deduplicates(self):
        with self.db.transaction():
            digest = self.db.put_blob(encode({'unique': self.id}))
            self.assertEqual(self.db.put_blob(encode({'unique': self.id})), digest)
            spec = self.spec(dependencies=[{'type': 'BLOB', 'digest': digest}])
            self.objects.put(spec, expected_version=0)
            unneeded = self.db.put_blob(encode({'unneeded': self.id}))
        maintain(self.db, apply=True, now=self.now)
        maintain(self.db, apply=True, now=self.now + timedelta(days=3))
        self.assertTrue(self.db.blobs.path(digest).exists())
        self.assertFalse(self.db.blobs.path(unneeded).exists())
        self.assertIsNone(self.db.one('SELECT digest FROM blobs WHERE digest=%s', (unneeded,)))

    def test_capacity_walk_allows_live_writes_and_rejects_incomplete_measurement(self):
        from btc_node.lifecycle import allocated
        written = []
        def measure(paths):
            other = Database(self.settings)
            try:
                with other.writer(timeout=0), other.transaction():
                    written.append(other.put_blob(encode({'concurrent': self.id})))
            finally:
                other.close()
            return allocated(paths)
        with patch('btc_node.lifecycle.allocated', side_effect=measure):
            maintain(self.db, apply=True)
        self.assertEqual(len(written), 1)
        self.assertEqual(self.db.value(written[0]), {'concurrent': self.id})
        self.assertIn('measurement_started_ms', self.db.metadata('capacity.daily')[-1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); child = root/'child'; child.mkdir()
            (child/'value').write_text(self.id)
            original = os.walk
            with patch('btc_node.lifecycle.os.walk', wraps=original) as walk:
                self.assertEqual(allocated([child, root, child]), (child/'value').stat().st_blocks * 512)
                self.assertEqual(walk.call_count, 1)
            def denied(path, *, followlinks, onerror):
                onerror(PermissionError('unreadable generated directory'))
            with patch('btc_node.lifecycle.os.walk', side_effect=denied):
                with self.assertRaises(PermissionError): allocated([root])
            with self.assertRaises(FileNotFoundError): allocated([root/'missing'])

    def test_daily_maintenance_records_growth_without_a_million_path_audit(self):
        from btc_node.lifecycle import daily, status
        self.db.settings = {**self.db.settings, 'maintenance_enabled': False}
        previous_daily = self.db.metadata('capacity.daily')
        previous_maintenance = self.db.metadata('maintenance.daily')
        try:
            with self.db.transaction():
                self.db.metadata('capacity.daily', [], write=True)
                self.db.metadata('maintenance.daily', {'day': '2000-01-01'}, write=True)
            with patch('btc_node.lifecycle.allocated', side_effect=AssertionError('daily full walk')), \
                    patch('btc_node.lifecycle.measure_capacity', side_effect=AssertionError('daily audit')):
                result = daily(self.db)
            self.assertIsNone(result['content_allocated_bytes'])
            sample = status(self.db)['daily'][-1]
            self.assertEqual(sample['allocated_file_bytes_status'], 'NOT_MEASURED')
            self.assertNotIn('allocated_file_bytes', sample)
            self.assertIn('unique_content_bytes', sample)
            self.assertTrue(sample['filesystem_headroom'])
            self.assertIn('available_bytes', sample['filesystem_headroom'][0])
            self.assertTrue(status(self.db)['live_filesystem_headroom']['volumes'])
            audit = subprocess.run([sys.executable, '-B', '-m', 'btc_node', '--config',
                                    os.environ['BTC_TEST_CONFIG'], 'capacity-audit'],
                                   capture_output=True, text=True, check=True)
            measured = json.loads(audit.stdout)
            self.assertEqual(measured['allocated_file_bytes_status'], 'MEASURED')
            self.assertIn('allocated_file_bytes', measured)
            self.assertLessEqual(measured['measurement_started_ms'], measured['physical_measured_at_ms'])
        finally:
            with self.db.transaction():
                self.db.metadata('capacity.daily', previous_daily, write=True)
                self.db.metadata('maintenance.daily', previous_maintenance, write=True)

    def test_capacity_health_detects_external_volume_loss_without_tree_walk(self):
        from btc_node.lifecycle import filesystem_headroom, record_capacity, status
        previous = self.db.metadata('capacity.daily')
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root/'same-volume').mkdir()
                blocks = type('Blocks', (), {'f_blocks':1000, 'f_bavail':400,
                    'f_bfree':500, 'f_frsize':4096})()
                with patch('btc_node.lifecycle.os.walk', side_effect=AssertionError('file walk')), \
                        patch('btc_node.lifecycle.os.statvfs', return_value=blocks) as probe:
                    sampled = filesystem_headroom([root, root/'same-volume'])
                self.assertEqual(probe.call_count, 1)
                self.assertEqual(len(sampled), 1)
                self.assertEqual(sampled[0]['available_bytes'], 400*4096)
            with self.db.transaction():
                self.db.metadata('capacity.daily', [], write=True)
            first = [{'device':'fixture', 'roots':['/fixture'], 'total_bytes':4096000,
                      'available_bytes':2000000, 'free_bytes':2100000}]
            later = [{**first[0], 'available_bytes':1200000, 'free_bytes':1300000}]
            with patch('btc_node.lifecycle.filesystem_headroom', side_effect=[first, later, later]):
                record_capacity(self.db, now=self.now)
                second = record_capacity(self.db, now=self.now+timedelta(days=1))
                live = status(self.db)['live_filesystem_headroom']['volumes']
            self.assertEqual(second['filesystem_headroom'][0]['available_change_bytes'], -800000)
            self.assertEqual(live[0]['available_bytes'], 1200000)
            self.assertEqual(second['allocated_file_bytes_status'], 'NOT_MEASURED')
        finally:
            with self.db.transaction():
                self.db.metadata('capacity.daily', previous, write=True)

    def test_capacity_spill_preserves_exact_hardlink_accounting(self):
        from btc_node.lifecycle import allocated
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, third = [root/name for name in ('first','second','third')]
            for path in (first, second, third):
                path.write_bytes((path.name*7).encode())
            nested = root/'nested'; nested.mkdir()
            os.link(first, nested/'same-inode')
            (nested/'symlink').symlink_to(second)
            expected = sum(path.stat().st_blocks * 512 for path in (first,second,third))
            with patch('btc_node.lifecycle.MAX_INODE_KEYS',2), \
                    patch('btc_node.lifecycle.sqlite3.connect',wraps=sqlite3.connect) as connect:
                self.assertEqual(allocated([nested,root,nested]),expected)
            self.assertEqual(connect.call_count,1)

    def test_unchanged_raw_views_share_content_without_changing_retention_clocks(self):
        with tempfile.TemporaryDirectory(dir=self.db.blobs.root) as folder:
            root = Path(folder)
            raw = b'{"source":"generated","value":17}\n'
            with self.db.transaction():
                digest = self.db.put_blob(raw)
            # CAS predates this run. Sharing must not transfer its old age to
            # the run document or alter an ordinary, writable collector export.
            os.utime(self.db.blobs.path(digest), (millis(self.now)/1000-20*86400,)*2)
            paths = [root/name/'evidence/raw'/(digest+'.json') for name in ('run-a','run-b')]
            for path in paths:
                path.parent.mkdir(parents=True);path.write_bytes(raw)
            export = root/'collector.json';export.write_bytes(raw)
            catalog = Catalog(self.db)
            with self.db.transaction():catalog.sync(self.id, root)
            before = self.db.rows('SELECT * FROM documents WHERE namespace=%s ORDER BY path',(self.id,))
            self.db.settings['require_shared_evidence'] = True
            try:
                for _ in range(2):
                    with self.db.transaction():
                        result = catalog.sync(self.id, root, hardlink=True)
                    self.assertEqual(result['changed'], [])
                    self.assertTrue(all(path.samefile(self.db.blobs.path(digest)) for path in paths))
                    self.assertFalse(export.samefile(self.db.blobs.path(digest)))
                    self.assertEqual(self.db.rows('SELECT * FROM documents WHERE namespace=%s ORDER BY path',(self.id,)), before)
                self.assertEqual(self.db.blobs.get(digest), raw)
                export.write_bytes(b'{"source":"generated","value":18}\n')
                self.assertEqual(self.db.blobs.get(digest), raw)
            finally:self.db.settings.pop('require_shared_evidence',None)

    def test_content_capacity_excludes_compatibility_files_but_total_allocation_keeps_them(self):
        directory=Path(self.db.settings['objects'])/'.strategy';directory.mkdir(exist_ok=True)
        marker=directory/(self.id+'.json');marker.write_bytes(b'generated compatibility material'*10000)
        try:
            before=sum(p.stat().st_blocks*512 for p in self.db.blobs.root.glob('*/*')
                         if p.is_file() and len(p.parent.name)==2 and len(p.name)==62)
            result=maintain(self.db,apply=False)
            expected=sum(p.stat().st_blocks*512 for p in self.db.blobs.root.glob('*/*')
                         if p.is_file() and len(p.parent.name)==2 and len(p.name)==62)
            self.assertEqual(result['content_allocated_bytes'],expected)
            self.assertTrue(marker.exists())
            sample=self.db.metadata('capacity.daily')[-1]
            self.assertGreaterEqual(sample['allocated_file_bytes'],before+marker.stat().st_blocks*512)
        finally:
            marker.unlink()

    def test_frozen_run_inputs_share_only_verified_bindings_and_keep_original_clocks(self):
        with tempfile.TemporaryDirectory(dir=self.db.settings['objects']) as folder:
            root = Path(folder)
            collector = root/'collector.json'; collector.write_bytes(encode({'generated':self.id}))
            market = root/'market.json'; market.write_bytes(encode({'generated_market':self.id}))
            evidence = root/'evidence'; evidence.mkdir()
            package = {'collector':{'source_id':'generated','snapshot_id':'sha256:'+fingerprint(encode(json.loads(collector.read_bytes())))},
                       'sources':[{'id':'generated','raw_hash':'sha256:'+fingerprint(collector.read_bytes())}]}
            package['evidence_id'] = 'sha256:'+fingerprint(encode(package))
            (evidence/'evidence.json').write_bytes(encode(package))
            run = {'schema':'BTC_RUN_MATERIAL_V1','collector':str(collector),'market':str(market),
                   'market_hash':'sha256:'+fingerprint(encode(json.loads(market.read_bytes()))),
                   'evidence':str(evidence),'evidence_id':package['evidence_id']}
            (root/'run.json').write_bytes(encode(run))
            catalog = Catalog(self.db)
            with self.db.transaction():
                catalog.sync(self.id,root)
            before = self.db.rows('SELECT * FROM documents WHERE namespace=%s ORDER BY path',(self.id,))
            for path in (collector,market):
                cas=self.db.blobs.path(fingerprint(path.read_bytes()))
                os.utime(cas,(self.now.timestamp()-20*86400,)*2)
            with self.db.transaction():
                self.assertEqual(catalog.sync(self.id,root,hardlink=True)['changed'],[])
                self.assertEqual(catalog.sync(self.id,root,hardlink=True)['changed'],[])
            self.assertEqual(self.db.rows('SELECT * FROM documents WHERE namespace=%s ORDER BY path',(self.id,)),before)
            for path in (collector,market):
                self.assertTrue(path.samefile(self.db.blobs.path(fingerprint(path.read_bytes()))))
            unrelated = root/'unbound'; unrelated.mkdir()
            (unrelated/'collector.json').write_bytes(collector.read_bytes())
            (unrelated/'run.json').write_bytes(encode({**run,'collector':str(unrelated/'collector.json'),
                                                       'evidence_id':'sha256:'+'0'*64}))
            with self.db.transaction():
                catalog.sync(self.id,root,hardlink=True)
            self.assertFalse((unrelated/'collector.json').samefile(collector))

    def test_required_shared_original_rejects_cross_mount_copy_and_preserves_original(self):
        with tempfile.TemporaryDirectory(dir=self.db.blobs.root) as folder:
            root = Path(folder);raw = b'{"generated":true}\n'
            path = root/'raw'/(fingerprint(raw)+'.json');path.parent.mkdir();path.write_bytes(raw)
            catalog = Catalog(self.db)
            with self.db.transaction():catalog.sync(self.id,root)
            before = self.db.rows('SELECT * FROM documents WHERE namespace=%s',(self.id,))
            self.db.settings['require_shared_evidence']=True
            try:
                with patch('btc_node.codec.os.link',side_effect=OSError(errno.EXDEV,'generated different mounts')):
                    with self.assertRaisesRegex(Conflict,'must share the content volume'):
                        with self.db.transaction():catalog.sync(self.id,root,hardlink=True)
                self.assertEqual(path.read_bytes(),raw)
                self.assertEqual(self.db.rows('SELECT * FROM documents WHERE namespace=%s',(self.id,)),before)
                self.assertEqual(list(path.parent.iterdir()),[path])
            finally:self.db.settings.pop('require_shared_evidence',None)

    def test_sharing_checks_actual_original_bytes_before_replacing_a_view(self):
        with tempfile.TemporaryDirectory(dir=self.db.blobs.root) as folder:
            root=Path(folder);raw=b'{"value":17}\n'
            path=root/'raw'/(fingerprint(raw)+'.json');path.parent.mkdir();path.write_bytes(raw)
            catalog=Catalog(self.db)
            with self.db.transaction():catalog.sync(self.id,root)
            path.write_bytes(b'{"value":18}\n')
            with self.assertRaisesRegex(Conflict,'immutable original changed'):
                with self.db.transaction():catalog.sync(self.id,root,hardlink=True)
            self.assertEqual(self.db.blobs.get(fingerprint(raw)),raw)
            self.assertEqual(path.read_bytes(),b'{"value":18}\n')

    def test_immutable_directory_reuse_preserves_clocks_and_completed_run_policy(self):
        with tempfile.TemporaryDirectory(dir=self.db.blobs.root) as directory:
            root=Path(directory); run=root/'run-generated'; raw=run/'raw'
            raw.mkdir(parents=True); (run/'run.json').write_text('{"status":"RUNNING"}')
            body=encode({'generated':self.id}); digest=fingerprint(body)
            source=raw/(digest+'.json'); source.write_bytes(body)
            mutable=run/'answers.json'; mutable.write_text('{"value":1}')
            catalog=Catalog(self.db)
            with patch.dict(self.db.settings,require_shared_evidence=True):
                catalog.sync(self.id,root,hardlink=True)
                before=self.db.one('SELECT * FROM documents WHERE namespace=%s AND path=%s',(self.id,str(source.relative_to(root))))
                self.assertEqual(before['retention_class'],'ACTIVE')
                mutable.write_text('{"value":2}')
                reads=[]; original=Path.lstat
                walked=[]; original_walk=os.walk
                def counted(path,*args,**kwargs):
                    reads.append(path);return original(path,*args,**kwargs)
                def tracked_walk(*args,**kwargs):
                    for item in original_walk(*args,**kwargs):
                        walked.append(item[0]);yield item
                with patch.object(Path,'lstat',counted), patch.object(Path,'rglob',side_effect=AssertionError('full run search')), \
                        patch('btc_node.catalog.os.walk',tracked_walk):
                    second=catalog.sync(self.id,root,hardlink=True)
                self.assertEqual(second['immutable_files_reused'],1)
                self.assertNotIn(source,reads)
                self.assertNotIn(str(raw),walked)
                self.assertIn(str(mutable.relative_to(root)),second['changed'])
                (run/'run.json').write_text('{"status":"COMPLETED"}')
                third=catalog.sync(self.id,root,hardlink=True)
                after=self.db.one('SELECT * FROM documents WHERE namespace=%s AND path=%s',(self.id,str(source.relative_to(root))))
                self.assertEqual(third['immutable_files_reused'],1)
                self.assertEqual(after['retention_class'],'RUN')
                self.assertEqual(after['expires_ms'],before['source_mtime_ns']//1000000+7*86400000)
                for field in ['digest','source_mtime_ns','recorded_ms']:
                    self.assertEqual(before[field],after[field])
                self.assertTrue(self.db.blobs.path(digest).samefile(source))

    def test_immutable_directory_changes_and_recovery_are_not_hidden(self):
        with tempfile.TemporaryDirectory(dir=self.db.blobs.root) as directory:
            root=Path(directory);raw=root/'raw';raw.mkdir()
            one=encode({'first':self.id});a=raw/(fingerprint(one)+'.json');a.write_bytes(one)
            catalog=Catalog(self.db)
            with patch.dict(self.db.settings,require_shared_evidence=True):
                catalog.sync(self.id,root,hardlink=True)
                two=encode({'second':self.id});b=raw/(fingerprint(two)+'.json');b.write_bytes(two)
                changed=catalog.sync(self.id,root,hardlink=True)
                self.assertIn(str(b.relative_to(root)),changed['changed'])
                self.assertTrue(self.db.blobs.path(fingerprint(two)).samefile(b))
                a.unlink()
                removed=catalog.sync(self.id,root,hardlink=True)
                self.assertIn(str(a.relative_to(root)),removed['removed'])
                b.unlink();b.write_bytes(two)
                restored=catalog.sync(self.id,root,hardlink=True)
                self.assertEqual(restored['immutable_files_reused'],0)
                self.assertTrue(self.db.blobs.path(fingerprint(two)).samefile(b))
                mutable=raw/'ordinary.json';mutable.write_text('{"mutable":1}')
                catalog.sync(self.id,root,hardlink=True)
                mutable.write_text('{"mutable":2}')
                result=catalog.sync(self.id,root,hardlink=True)
                self.assertIn('raw/ordinary.json',result['changed'])
                self.assertEqual(result['immutable_files_reused'],0)

    def test_catalog_roundtrip_preserves_bytes_sqlite_blobs_cursor_and_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            source, restored = Path(folder)/'source', Path(folder)/'restored'
            source.mkdir()
            raw = b'{ "state": "UNKNOWN", "original_spacing": true }\n'
            (source/'status.json').write_bytes(raw)
            (source/'current').symlink_to('status.json')
            with sqlite3.connect(source/'reader.sqlite3') as connection:
                connection.executescript('CREATE TABLE reader(id INTEGER PRIMARY KEY AUTOINCREMENT, cursor INTEGER, payload BLOB); PRAGMA user_version=3;')
                connection.execute('INSERT INTO reader(cursor,payload) VALUES(?,?)', (987654,b'original\x00bytes'))
                connection.execute('INSERT INTO reader(cursor,payload) VALUES(?,?)', (999999,b'deleted'))
                connection.execute('DELETE FROM reader WHERE id=2')
                connection.execute('CREATE TABLE history(payload BLOB)')
                connection.executemany('INSERT INTO history(rowid,payload) VALUES(?,?)',[(3,b'duplicate'),(19,b'duplicate')])
            catalog = Catalog(self.db)
            with self.db.transaction():
                first = catalog.sync(self.id, source)
                second = catalog.sync(self.id, source)
                self.assertEqual(second['changed'], [])
                self.assertFalse(second['sqlite'][0]['changed'])
                catalog.restore(self.id, restored)
                self.assertTrue(catalog.compare(self.id, restored)['equal'])
            self.assertEqual((restored/'status.json').read_bytes(), raw)
            self.assertEqual(os.readlink(restored/'current'), 'status.json')
            with sqlite3.connect(restored/'reader.sqlite3') as connection:
                self.assertEqual(connection.execute('SELECT cursor,payload FROM reader').fetchone(), (987654,b'original\x00bytes'))
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
                self.assertEqual(connection.execute('SELECT rowid,payload FROM history').fetchall(),[(3,b'duplicate'),(19,b'duplicate')])
                connection.execute('INSERT INTO reader(cursor) VALUES(1)')
                self.assertEqual(connection.execute('SELECT max(id) FROM reader').fetchone()[0], 3)
            with sqlite3.connect(source/'reader.sqlite3') as connection:
                connection.execute('DROP TABLE reader')
            with self.db.transaction():
                catalog.sync(self.id,source)
                catalog.restore(self.id,Path(folder)/'dropped-table')
                self.assertTrue(catalog.compare(self.id,Path(folder)/'dropped-table')['equal'])
            (source/'reader.sqlite3').unlink()
            with self.db.transaction():
                catalog.sync(self.id,source)
                catalog.restore(self.id,Path(folder)/'deleted-database')
            self.assertFalse((Path(folder)/'deleted-database/reader.sqlite3').exists())

    def test_dependencies_bind_only_the_versions_known_at_the_decision_time(self):
        region = self.spec(known_at=self.at(5), observed_at=self.at(6))
        self.objects.put(region, expected_version=0)
        revised = deepcopy(region)
        revised.update(known_at=self.at(1), observed_at=self.at(2))
        revised['payload']['state'] = 'FAILED'
        self.objects.put(revised, expected_version=1)
        plan = self.spec('PLAN', dependencies=[{'type':'ENTITY','kind':'REGION','object_id':region['object_id']}])
        self.objects.put(plan, expected_version=0)
        bound = self.objects.get('PLAN', plan['object_id'])['dependencies'][0]
        self.assertEqual(bound['version'], 1)
        plan['object_id'] += '-future'
        plan['dependencies'][0]['version'] = 2
        with self.assertRaisesRegex(ValueError, 'not known'):
            self.objects.put(plan, expected_version=0)

    def test_expired_files_are_removed_after_grace_without_resurrection_or_active_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'cache').mkdir(); (root/'raw').mkdir()
            unused = root/'cache'/'old.txt'; unused.write_text('rebuildable cache')
            database=root/'cache'/'old.sqlite3'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE obsolete(value TEXT)')
                connection.execute('INSERT INTO obsolete VALUES(?)',(self.id,))
            important = root/'raw'/'evidence.txt'; important.write_text(self.id)
            for path in (unused, important, database):
                old = (self.now - timedelta(days=45)).timestamp(); os.utime(path,(old,old))
            catalog = Catalog(self.db)
            with self.db.transaction():
                catalog.sync(self.id, root)
                digest = self.db.put_blob(important.read_bytes())
                self.objects.put(self.spec(dependencies=[{'type':'BLOB','digest':digest}]),expected_version=0)
            maintain(self.db,apply=True,now=self.now)
            self.assertTrue(unused.exists())
            maintain(self.db,apply=True,now=self.now+timedelta(days=3))
            self.assertFalse(unused.exists())
            self.assertFalse(database.exists())
            self.assertTrue(important.exists())
            with self.db.transaction():
                catalog.sync(self.id,root)
                self.assertFalse(self.db.one('SELECT document_key FROM documents WHERE namespace=%s AND path=%s',(self.id,'cache/old.txt')))
                self.assertFalse(self.db.one('SELECT database_key FROM legacy_databases WHERE namespace=%s',(self.id,)))

    def test_fault_closure_uses_actual_recovery_and_closure_clock_with_grace(self):
        from btc_node.codec import identity
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'runtime').mkdir()
            closed=root/'runtime/failure-receipt.json'; opened=root/'runtime/open-failure.json'
            raw=encode({'schema':'BTC_RUN_FAILURE_RECEIPT_V1','error':'generated fault','run_ref':self.id})
            for file in (closed,opened):
                file.write_bytes(raw); old=(self.now-timedelta(days=60)).timestamp();os.utime(file,(old,old))
            catalog=Catalog(self.db);key=identity(self.id,'runtime/failure-receipt.json')
            recovery=self.id+'-recovered'
            with self.db.transaction():
                catalog.sync(self.id,root)
                request=self.db.put_blob(encode({'argv':['generated-recovery'],'release':'generated-test'}))
                result=self.db.put_blob(encode({'returncode':0}))
                self.db.execute('INSERT INTO operations(operation_id,request_digest,status,started_ms,finished_ms,result_digest) VALUES(%s,%s,%s,%s,%s,%s)',
                    (recovery,request,'COMMITTED',millis(),millis(),result))
            maintain(self.db,apply=True,now=self.now+timedelta(days=3))
            self.assertTrue(opened.exists()); self.assertTrue(closed.exists())
            current=dispatch(self.settings,{'method':'failure.get','params':{'document_key':key}})
            self.assertEqual(current['state'],'OPEN'); self.assertEqual(current['document']['retention_class'],'ACTIVE')
            self.assertIn(key,{r['document_key'] for r in dispatch(self.settings,{'method':'failure.list','params':{}})['failures']})
            params={'document_key':key,'expected_digest':current['document']['digest'],'expected_version':0,
                    'resolution_operation_id':recovery,'reason':'Generated same-fault replay succeeded'}
            with self.assertRaises(ValueError):
                dispatch(self.settings,{'method':'failure.resolve','operation_id':self.id+'-missing','params':{**params,'resolution_operation_id':'missing'}})
            request={'method':'failure.resolve','operation_id':self.id+'-close','params':params}
            saved=dispatch(self.settings,request)
            self.assertEqual(saved,dispatch(self.settings,request))
            close=datetime.fromtimestamp(saved['resolution']['closed_ms']/1000,timezone.utc)
            self.assertEqual(saved['document']['expires_ms'],millis(close+timedelta(days=14)))
            self.db.connection.rollback()
            with self.db.transaction():catalog.sync(self.id,root)
            with self.assertRaises(Conflict):
                dispatch(self.settings,{'method':'failure.resolve','operation_id':self.id+'-reclose','params':{**params,'expected_version':1}})
            maintain(self.db,apply=True,now=close+timedelta(days=13));self.assertTrue(closed.exists())
            maintain(self.db,apply=True,now=close+timedelta(days=15));self.assertTrue(closed.exists())
            maintain(self.db,apply=True,now=close+timedelta(days=18));self.assertFalse(closed.exists());self.assertTrue(opened.exists())
            state=dispatch(self.settings,{'method':'failure.get','params':{'document_key':key}})
            self.assertEqual(state['state'],'CLOSED');self.assertIsNone(state['document'])
            self.assertEqual(self.db.blobs.get(params['expected_digest']),raw)

    def test_fault_same_path_new_original_cannot_inherit_old_closure(self):
        from btc_node.codec import identity
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);file=root/'step-failure.json';file.write_bytes(encode({'ok':False,'error':'first'}))
            key=identity(self.id,file.name);catalog=Catalog(self.db)
            with self.db.transaction():
                catalog.sync(self.id,root)
                first=invoke(self.db,'failure.get',{'document_key':key})
                old={'document_key':key,'digest':first['document']['digest'],'version':1,'closed_ms':millis(self.now-timedelta(days=40)),
                     'resolution_operation_id':'generated-old','reason':'historical generated recovery'}
                self.db.metadata('failure:'+key,old,write=True)
                catalog.sync(self.id,root)
                self.assertEqual(invoke(self.db,'failure.get',{'document_key':key})['state'],'CLOSED')
                file.write_bytes(encode({'ok':False,'error':'new failure'}));catalog.sync(self.id,root)
                current=invoke(self.db,'failure.get',{'document_key':key})
                self.assertEqual(current['state'],'OPEN');self.assertEqual(current['prior_resolution'],old)
                self.assertEqual(current['document']['retention_class'],'ACTIVE');self.assertIsNone(current['document']['expires_ms'])
                with self.assertRaises(Conflict):
                    invoke(self.db,'failure.resolve',{'document_key':key,'expected_digest':old['digest'],'expected_version':1,
                        'resolution_operation_id':'generated-old','reason':'late close must be rejected'})
            maintain(self.db,apply=True,now=self.now+timedelta(days=3));self.assertTrue(file.exists())

    def test_fact_views_require_current_files_and_preserve_unknown_and_targets(self):
        from btc_node.fact_views import project, NAMES
        from btc_node.codec import fingerprint
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in NAMES:(root/name).write_text('Confirmed target: 40%. Cash: UNKNOWN.\n')
            self.db.settings={**self.db.settings,'project':directory}
            version={name:'sha256:'+fingerprint((root/name).read_bytes()) for name in NAMES}
            item={'object_id':self.id,'category':'TARGETS','fields':{'target_fraction':'0.4','cash':None},
                  'source_refs':[{'file':NAMES[0],'line_start':1,'line_end':1,'quote':'Confirmed target: 40%. Cash: UNKNOWN.'}]}
            result=project(self.db,fact_version=version,items=[item])
            view=self.objects.get('PROFILE',result['objects'][0]['object_id'])
            self.assertIsNone(view['payload']['fields']['cash'])
            self.assertEqual(view['payload']['fields']['target_fraction'],'0.4')
            self.assertFalse(view['payload']['derived_values'])
            (root/NAMES[0]).write_text('Confirmed target: 50%.\n')
            with self.assertRaises(Conflict):project(self.db,fact_version=version,items=[item])

    def test_rollback_exports_current_mysql_protocol_not_the_old_sqlite_snapshot(self):
        from btc_node.market_store import FIELDS, export_sqlite
        with Connection(self.settings) as connection:
            connection.execute('INSERT INTO evidence VALUES(?,?,?,?,?)',(self.id,self.at(),1,'{"delivery":"UNKNOWN"}',self.at()))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'market.sqlite3'
            with sqlite3.connect(path) as target:
                for table,fields in FIELDS.items():
                    target.execute('CREATE TABLE '+table+' ('+','.join(f+' TEXT'+(' PRIMARY KEY' if i==0 else '') for i,f in enumerate(fields))+')')
                target.execute('INSERT INTO evidence(rowid,evidence_id,payload) VALUES(27,?,?)',(self.id,'older view'))
            export_sqlite(self.db,path)
            with sqlite3.connect(path) as target:
                self.assertEqual(target.execute('SELECT payload FROM evidence WHERE evidence_id=?',(self.id,)).fetchone()[0],'{"delivery":"UNKNOWN"}')
                self.assertEqual(target.execute('SELECT rowid FROM evidence WHERE evidence_id=?',(self.id,)).fetchone()[0],27)

    def test_backups_on_evidence_mount_share_content_and_survive_live_unlink(self):
        import shutil
        with self.db.transaction():
            digest=self.db.put_blob(encode({'generated_backup':self.id}))
        root=Path(self.settings['objects'])/'.backups'/self.id
        try:
            backup.create(self.db,root/'first')
            backup.create(self.db,root/'second')
            live=self.db.blobs.path(digest)
            first=root/'first/objects'/digest[:2]/digest[2:]
            second=root/'second/objects'/digest[:2]/digest[2:]
            self.assertTrue(live.samefile(first))
            self.assertTrue(first.samefile(second))
            original=live.read_bytes()
            live.unlink()
            self.assertEqual(first.read_bytes(),original)
            self.assertEqual(second.read_bytes(),original)
            self.assertEqual(self.db.put_blob(original),digest)
        finally:
            shutil.rmtree(root,ignore_errors=True)

    def test_backup_restores_latest_unknown_and_blocks_stale_overwrite(self):
        with self.db.transaction():
            self.db.metadata(self.id,{'delivery':'UNKNOWN','cursor':987654},write=True)
        digest=self.db.one('SELECT digest FROM node_metadata WHERE name=%s',(self.id,))['digest']
        with tempfile.TemporaryDirectory(prefix='btc-backup-contract-') as directory:
            target=Path(directory)/'snapshot'
            saved=backup.create(self.db,target)
            with self.assertRaises(Conflict):
                backup.restore(self.db,target)
            # This node is explicitly test_only. Simulate total DB loss and one
            # lost live evidence file, then recover from the independent backup.
            with self.db.writer():
                self.db.execute('SET FOREIGN_KEY_CHECKS=0')
                for row in self.db.rows('SHOW TABLES'):
                    self.db.execute('DROP TABLE '+backup.identifier(next(iter(row.values()))))
                self.db.execute('SET FOREIGN_KEY_CHECKS=1')
                self.db.connection.commit()
            self.db.blobs.path(digest).unlink()
            restored=backup.restore(self.db,target)
            self.assertEqual(saved['tables'],restored['tables'])
            self.assertEqual(self.db.metadata(self.id),{'delivery':'UNKNOWN','cursor':987654})
            self.assertTrue(self.db.blobs.path(digest).is_file())

    def test_backup_copy_allows_writes_and_gc_without_losing_snapshot_originals(self):
        import gzip
        from btc_node.lifecycle import _maintain
        with self.db.transaction():
            digest = self.db.put_blob(encode({'unreferenced_snapshot_original': self.id}))
            self.db.execute('UPDATE blobs SET unreferenced_ms=%s WHERE digest=%s',
                            (millis(self.now-timedelta(days=4)), digest))
            self.db.metadata(self.id, {'delivery':'UNKNOWN','cursor':17}, write=True)
        other = Database(self.settings)
        copied = []
        original = self.db.blobs.materialize
        def copy_with_concurrent_writer(source_digest, target):
            if not copied:
                with other.writer(timeout=0), other.transaction():
                    # The copying connection still owns its temporary lease;
                    # another writer can change live state, but not its view.
                    self.assertEqual(backup.reap_pins(other), [])
                    other.metadata(self.id, {'delivery':'DELIVERED','cursor':19}, write=True)
                with other.writer(timeout=0):
                    _maintain(other, apply=True, now=self.now)
                self.assertTrue(other.blobs.path(digest).is_file())
                self.assertIsNotNone(other.one('SELECT digest FROM blobs WHERE digest=%s', (digest,)))
            copied.append(source_digest)
            return original(source_digest, target)
        try:
            with tempfile.TemporaryDirectory(prefix='btc-backup-concurrency-') as directory:
                target=Path(directory)/'snapshot'
                with patch.object(self.db.blobs, 'materialize', side_effect=copy_with_concurrent_writer):
                    saved=backup.create(self.db, target)
                self.assertIn(digest, copied)
                with gzip.open(target/'database.jsonl.gz', 'rt') as stream:
                    records=[json.loads(line) for line in stream]
                record=next(r['row'] for r in records if r.get('table')=='node_metadata'
                            and r.get('row',{}).get('name')==self.id)
                archived=backup.Blobs(target/'objects')
                self.assertEqual(json.loads(archived.get(record['digest'])), {'delivery':'UNKNOWN','cursor':17})
                self.assertFalse(any(r.get('table')=='metadata_blobs' and
                                     r.get('row',{}).get('name','').startswith(backup.PIN_PREFIX) for r in records))
                self.db.connection.rollback()
                self.assertEqual(self.db.metadata(self.id), {'delivery':'DELIVERED','cursor':19})
                self.assertFalse(saved['pin_cleanup_deferred'])
                self.assertFalse(self.db.rows('SELECT name FROM metadata_blobs WHERE name LIKE %s',
                                              (backup.PIN_PREFIX+'%',)))
                self.assertGreaterEqual(saved['completed_ms'], saved['created_ms'])
        finally:
            other.close()

    def test_backup_object_copy_does_not_stream_while_hashing_files(self):
        original = Database.iter_rows
        def reject_slow_object_stream(snapshot, sql, args=()):
            if sql == 'SELECT digest FROM blobs':
                raise AssertionError('slow file copy holds a MySQL server cursor open')
            return original(snapshot, sql, args)
        with tempfile.TemporaryDirectory(prefix='btc-backup-cursor-') as directory:
            with patch.object(Database, 'iter_rows', reject_slow_object_stream):
                result = backup.create(self.db, Path(directory)/'snapshot')
            self.assertIn('blobs', result['tables'])

    def test_open_maintenance_fault_blocks_daily_before_any_backup(self):
        from btc_node import deep_gate
        from btc_node.__main__ import record_maintenance_failure
        from btc_node.lifecycle import daily
        previous = self.db.metadata(deep_gate.STATE_NAME)
        try:
            record_maintenance_failure(self.settings, RuntimeError('synthetic failure'))
            # This test connection had read the prior state in REPEATABLE READ;
            # a restarted maintenance process opens a new transaction.
            self.db.connection.rollback()
            blocked = deep_gate.check_scope(self.db, scope='btc/maintenance')
            self.assertFalse(blocked['allowed'])
            fault_id = blocked['faults'][0]['fault_id']
            with patch('btc_node.backup.create', side_effect=AssertionError('backup started')):
                with self.assertRaisesRegex(Conflict, 'known maintenance defect'):
                    daily(self.db)
            fault = deep_gate.status(self.db)['faults'][fault_id]
            with self.assertRaisesRegex(Conflict, 'changed repair'):
                with self.db.transaction():
                    deep_gate.resolve_fault(self.db, fault_id=fault_id,
                        repair_digest=fault['fault_digest'], verification_ref='synthetic check',
                        verification_status='PASS')
            with self.db.transaction():
                deep_gate.resolve_fault(self.db, fault_id=fault_id,
                    repair_digest='sha256:'+'3'*64, verification_ref='synthetic check',
                    verification_status='PASS')
            self.assertTrue(deep_gate.check_scope(self.db, scope='btc/maintenance')['allowed'])
            self.db.settings = {**self.db.settings, 'maintenance_enabled': False}
            with patch('btc_node.lifecycle.maintain', return_value={'synthetic': 'continued'}):
                self.assertEqual(daily(self.db), {'synthetic': 'continued'})
        finally:
            with self.db.transaction():
                self.db.metadata(deep_gate.STATE_NAME, previous, write=True)

    def test_paged_backup_rows_keep_the_original_snapshot_during_live_write(self):
        import gzip
        with self.db.transaction():
            self.db.metadata(self.id, {'cursor': 17, 'delivery': 'UNKNOWN'}, write=True)
        other = Database(self.settings)
        original = Database.rows
        changed = []
        def page_with_live_write(snapshot, sql, args=()):
            page = original(snapshot, sql, args)
            if sql.startswith('SELECT * FROM `node_metadata`') and not changed:
                with other.writer(timeout=0), other.transaction():
                    other.metadata(self.id, {'cursor': 19, 'delivery': 'DELIVERED'}, write=True)
                changed.append(True)
            return page
        try:
            with tempfile.TemporaryDirectory(prefix='btc-backup-stream-') as directory:
                target = Path(directory)/'snapshot'
                with patch.object(Database, 'rows', page_with_live_write):
                    backup.create(self.db, target)
                self.assertEqual(changed, [True])
                with gzip.open(target/'database.jsonl.gz', 'rt') as stream:
                    archived = [json.loads(line) for line in stream]
                row = next(r['row'] for r in archived if r.get('table') == 'node_metadata'
                           and r.get('row', {}).get('name') == self.id)
                self.assertEqual(json.loads(backup.Blobs(target/'objects').get(row['digest'])),
                                 {'cursor': 17, 'delivery': 'UNKNOWN'})
                self.db.connection.rollback()
                self.assertEqual(self.db.metadata(self.id), {'cursor': 19, 'delivery': 'DELIVERED'})
        finally:
            other.close()

    def test_interrupted_backup_leases_are_reaped_but_live_copy_is_protected(self):
        holder=Database(self.settings)
        live=backup.PIN_PREFIX+uuid.uuid4().hex
        orphan=backup.PIN_PREFIX+uuid.uuid4().hex
        try:
            self.assertEqual(holder.one('SELECT GET_LOCK(%s,0) AS acquired', (live,))['acquired'], 1)
            with self.db.writer(), self.db.transaction():
                digest=self.db.put_blob(encode({'backup_crash':self.id}))
                for name in (live, orphan):
                    self.db.execute('INSERT INTO metadata_blobs(name,digest) VALUES(%s,%s)', (name,digest))
                self.assertEqual(backup.reap_pins(self.db), [orphan])
                self.assertIsNotNone(self.db.one('SELECT name FROM metadata_blobs WHERE name=%s', (live,)))
            holder.close()  # Process/connection loss releases the named lease.
            with self.db.writer(), self.db.transaction():
                self.assertEqual(backup.reap_pins(self.db), [live])
            with tempfile.TemporaryDirectory(prefix='btc-backup-failure-') as directory:
                target=Path(directory)/'failed'
                with patch.object(self.db.blobs, 'materialize', side_effect=OSError('generated copy failure')):
                    with self.assertRaisesRegex(OSError, 'generated copy failure'):
                        backup.create(self.db, target)
                stages = list(Path(directory).iterdir())
                self.assertEqual(len(stages), 1)
                self.assertTrue(stages[0].name.startswith('.backup-'))
                self.assertTrue((stages[0]/'in-progress.json').is_file())
                self.assertFalse((stages[0]/'manifest.json').exists())
                self.db.connection.rollback()
                self.assertFalse(self.db.rows('SELECT name FROM metadata_blobs WHERE name LIKE %s',
                                              (backup.PIN_PREFIX+'%',)))
        finally:
            if holder.connection.open:
                holder.close()

    def test_elapsed_acceptance_window_does_not_pass_without_actual_consumption(self):
        from btc_node import acceptance
        with self.db.transaction():
            self.db.metadata('writer',{'owner':'DOCKER','changed_ms':millis()},write=True)
            self.db.metadata('acceptance',{'first_full_day':(self.now-timedelta(days=10)).date().isoformat(),
                'release':'generated-test','required_days':7,'days':{},'defects':[]},write=True)
        state=acceptance.status(self.db)
        self.assertEqual(state['scope'], 'LOCAL_NATURAL_ACCEPTANCE')
        self.assertNotIn('Still requires', state['overall_completion'])
        self.assertEqual(state['status'],'FOLLOWING')
        self.assertEqual(len(state['missing_or_invalid']),7)
        with self.db.transaction():
            proof=self.db.put_blob(encode({'source':'test execution proof','id':self.id}))
            self.db.metadata(self.id,{'retained_evidence':proof},write=True)
        maintain(self.db,apply=True,now=self.now)
        maintain(self.db,apply=True,now=self.now+timedelta(days=3))
        self.assertTrue(self.db.blobs.path(proof).exists())
        with self.db.transaction():
            acceptance.defect(self.db,reason='generated missing consumption',affected_days=[],criteria=['cli_mcp_runtime'])
        self.assertEqual(acceptance.status(self.db)['status'],'NEEDS_REPAIR')
        old=self.db.metadata('acceptance')['first_full_day']
        with self.db.transaction():
            repaired=acceptance.repair(self.db,defect_index=0,release='generated-fixed',
                reason='replayed and verified the consumer',evidence_refs=[{'type':'BLOB','digest':proof}])
        self.assertEqual(repaired['status'],'FOLLOWING')
        self.assertEqual(repaired['prior_windows'][0]['first_full_day'],old)
        self.assertEqual(repaired['required_days'][0],(datetime.now(acceptance.JST).date()+timedelta(days=1)).isoformat())
        self.assertEqual(repaired['open_defects'],[])
        before = self.db.metadata('acceptance')
        with self.db.transaction():
            acceptance.defect(self.db, reason='scoped asset risk correction', affected_days=[],
                              criteria=['personal_allocation', 'report_delivery'])
            acceptance.repair(self.db, defect_index=1, release='scoped-fixed', preserve_window=True,
                              reason='actual post-fix consumption reviewed', evidence_refs=[{'type':'BLOB','digest':proof}])
        after = self.db.metadata('acceptance')
        for key in ('first_full_day', 'required_days', 'release', 'days', 'prior_windows'):
            self.assertEqual(after[key], before[key])
        self.assertTrue(after['defects'][1]['repair']['preserve_window'])
        with self.assertRaises(Conflict):
            acceptance.repair(self.db,defect_index=0,release='later',reason='no moving target',
                evidence_refs=[{'type':'BLOB','digest':proof}])

    def test_scoped_acceptance_repair_retains_original_defect_and_future_gaps(self):
        from btc_node import acceptance
        with self.db.transaction():
            self.db.metadata('acceptance', {'first_full_day':'2026-10-12','required_days':7,
                'release':'original','days':{},'defects':[]}, write=True)
            proof = self.db.put_blob(encode({'source':'generated reviewed result','id':self.id}))
            acceptance.defect(self.db, reason='generated optional risk action correction',
                affected_days=[], criteria=['personal_allocation','report_delivery'])
        before = self.db.metadata('acceptance')
        params = {'defect_index':0,'release':'generated-fixed','reason':'reviewed real-input result and report',
            'evidence_refs':[{'type':'BLOB','digest':proof}], 'preserve_window':True,
            'verified_scope':['optional_action_interface','uncertainty_report'],
            'remaining_scope':['no_response','risk_response']}
        first = dispatch(self.settings, {'method':'acceptance.repair',
            'operation_id':self.id+'-repair-1','params':params})
        self.assertEqual(first['open_defects'],[0])
        self.assertEqual(first['partial_repairs'][0]['verified_scope'],params['verified_scope'])
        self.assertEqual(first['partial_repairs'][0]['remaining_scope'],params['remaining_scope'])
        self.assertEqual(first['partial_repairs'][0]['evidence_digests'],[proof])
        self.db.connection.commit()  # Refresh this connection after the API writer commits.
        state = self.db.metadata('acceptance')
        for key in ('first_full_day','required_days','release','days','prior_windows'):
            self.assertEqual(state.get(key),before.get(key))
        self.assertEqual(state['defects'][0]['reason'],before['defects'][0]['reason'])
        self.assertNotIn('repair',state['defects'][0])
        self.assertEqual(len(state['defects'][0]['repair_progress']['history']),1)
        with self.assertRaises(Conflict):
            acceptance.repair(self.db,**dict(params,verified_scope=['not_outstanding']))
        with self.assertRaises(ValueError):
            acceptance.repair(self.db,**dict(params,verified_scope=['no_response'],
                                              remaining_scope=['no_response','risk_response']))
        with self.db.transaction():
            second = acceptance.repair(self.db,**dict(params,verified_scope=['no_response'],
                remaining_scope=['risk_response'],reason='generated subsequent content review'))
        self.assertEqual(second['open_defects'],[0])
        self.assertEqual(second['partial_repairs'][0]['verified_scope'],
                         ['optional_action_interface','uncertainty_report','no_response'])
        with self.db.transaction():
            final = acceptance.repair(self.db,**dict(params,verified_scope=['risk_response'],
                remaining_scope=[],reason='generated final content review'))
        self.assertEqual(final['open_defects'],[])
        self.assertEqual(final['partial_repairs'],[])
        closed = self.db.metadata('acceptance')['defects'][0]
        self.assertNotIn('repair_progress',closed)
        self.assertEqual(len(closed['repair']['history']),3)
        with self.assertRaises(Conflict):
            acceptance.repair(self.db,**dict(params,verified_scope=['risk_response'],remaining_scope=[]))

    def test_reader_wal_reopen_checkpoints_exact_cursor_before_next_poll(self):
        with tempfile.TemporaryDirectory(prefix='btc-reader-checkpoint-') as directory:
            path=Path(directory)/'cursor.sqlite3'
            connection=sqlite3.connect(path)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE cursor (id INTEGER PRIMARY KEY, next_offset INTEGER)')
            connection.execute('INSERT INTO cursor VALUES(1,43)');connection.commit();connection.close()
            self.assertFalse(Path(str(path)+'-shm').exists())
            settings={**self.settings,'reader_data':directory}
            self.assertTrue(dispatch(settings,{'method':'reader.checkpoint'})['checkpointed'])
            stored=self.db.rows("SELECT digest FROM legacy_rows WHERE table_name='cursor'")
            self.assertEqual(self.db.value(stored[0]['digest'])['next_offset'],43)
            connection=sqlite3.connect(path)
            connection.execute('UPDATE cursor SET next_offset=47');connection.commit();connection.close()
            dispatch(settings,{'method':'reader.checkpoint'})
            self.db.connection.commit()
            stored=self.db.rows("SELECT digest FROM legacy_rows WHERE table_name='cursor'")
            self.assertEqual(len(stored),1)
            self.assertEqual(self.db.value(stored[0]['digest'])['next_offset'],47)
            self.assertFalse(dispatch(settings,{'method':'reader.checkpoint'})['databases'][0]['changed'])

    def test_backup_retention_covers_named_migration_copies_and_keeps_two(self):
        from btc_node.lifecycle import prune_backups,DAY
        with tempfile.TemporaryDirectory(prefix='btc-backup-retention-') as directory:
            root=Path(directory);now=millis(self.now)
            for name,age in [('pre-cutover',12),('recovery-check',10),('daily-old',9),('daily-new',1)]:
                path=root/name;path.mkdir()
                (path/'manifest.json').write_bytes(encode({'format':'BTC_MYSQL_BACKUP_V1','created_ms':now-age*DAY}))
            (root/'.backup-incomplete').mkdir()
            self.assertEqual(set(prune_backups(root,now_ms=now,keep_days=7)),{'pre-cutover','recovery-check'})
            self.assertEqual({p.name for p in root.iterdir()},{'daily-old','daily-new','.backup-incomplete'})

    def test_existing_observation_sql_uses_mysql_cas_and_preserves_interface(self):
        payload = '{ "datasets": {}, "original": true }'
        with Connection(self.settings) as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('INSERT INTO evidence VALUES(?,?,?,?,?)', (self.id, self.at(1), 1, payload, self.at()))
            connection.execute('INSERT OR IGNORE INTO retrieval VALUES(?,?,?,?)', (self.id, self.id, '{}', self.at()))
            connection.execute('INSERT OR IGNORE INTO retrieval VALUES(?,?,?,?)', (self.id, self.id, '{}', self.at()))
            connection.execute('INSERT INTO review VALUES(?,?,?,?,?,?,?)', (self.id, self.id, self.at(1), self.id, '{}', '{}', self.at()))
            connection.execute('INSERT OR REPLACE INTO baseline VALUES(?,?,?,NULL)', ('HOUR', self.id, self.at()))
        with Connection(self.settings, readonly=True) as connection:
            row = connection.execute('SELECT r.*, e.payload AS evidence_payload FROM review r JOIN evidence e USING(evidence_id) WHERE r.review_id=?', (self.id,)).fetchone()
            self.assertEqual(row['evidence_payload'], payload)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM retrieval WHERE retrieval_id=?', (self.id,)).fetchone()[0], 1)
            with self.assertRaises(ValueError):
                connection.execute('DELETE FROM evidence')
        with Connection(self.settings) as connection:
            connection.execute('DELETE FROM retrieval WHERE retrieved_at<? AND retrieval_id NOT IN (SELECT MIN(retrieval_id) FROM retrieval GROUP BY evidence_id)',(self.at(100),))
        stored = self.db.one('SELECT payload FROM mo_evidence WHERE evidence_id=%s', (self.id,))['payload']
        self.assertTrue(stored.startswith('@blob:'))
        self.assertEqual(self.db.blobs.get(stored[6:]).decode(), payload)

    def test_cli_and_mcp_read_the_same_committed_state(self):
        request = {'method': 'object.put', 'operation_id': self.id + '-interface',
                   'params': {'spec': self.spec(), 'expected_version': 0}}
        config = dict(self.settings, direct=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.json'; path.write_text(json.dumps(config))
            params = {'kind': 'REGION', 'object_id': self.id + 'REGION', 'scope': 'PUBLIC'}
            command = [sys.executable, '-m', 'btc_node', '--config', str(path)]
            subprocess.run(command + ['request'], input=json.dumps(request), text=True, capture_output=True, check=True)
            cli = subprocess.run(command + ['request'], input=json.dumps({'method': 'object.get', 'params': params}), text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(cli.stdout)['result']['version'], 1)
            mcp = subprocess.run(command + ['mcp'], input=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'object_get', 'arguments': {'params': params}}})+'\n', text=True, capture_output=True, check=True)
            response = json.loads(mcp.stdout)['result']
            self.assertFalse(response['isError'])
            self.assertEqual(json.loads(response['content'][0]['text'])['result']['version'], 1)
            # The real socket CLI and stdio MCP must report the same field
            # errors, then continue reading the original committed version.
            invalid = [
                ('object.get', {k:v for k,v in params.items() if k != 'kind'}, 'kind'),
                ('object.get', {k:v for k,v in params.items() if k != 'scope'}, 'scope'),
                ('object.get', dict(params, product='BTC'), 'product'),
                ('object.get', dict(params, scope='UNKNOWN'), 'scope'),
                ('object.get', dict(params, kind=[]), 'kind'),
                ('object.get', dict(params, version=True), 'version'),
                ('object.get', dict(params, market_binding={'object_id':'x','version':1}), 'market_binding.digest'),
                ('object.history', {'object_id':params['object_id']}, 'kind'),
                ('object.list', {'scope':'PUBLIC','limit':True}, 'limit'),
                ('conditions.due', {'scope':None}, 'scope'),
                ('context.personal', {'market_binding':'x'}, 'market_binding'),
            ]
            calls = [{'jsonrpc':'2.0','id':n,'method':'tools/call',
                      'params':{'name':method.replace('.', '_'),'arguments':{'params':values}}}
                     for n,(method,values,field) in enumerate(invalid, 10)]
            calls += [{'jsonrpc':'2.0','id':100,'method':'tools/list'},
                      {'jsonrpc':'2.0','id':101,'method':'tools/call',
                       'params':{'name':'object_get','arguments':{'params':params}}}]
            output = subprocess.run(command + ['mcp'],
                input=''.join(json.dumps(x)+'\n' for x in calls), text=True, capture_output=True, check=True)
            answers = [json.loads(line) for line in output.stdout.splitlines()]
            for (method,values,field),answer in zip(invalid,answers):
                result = answer['result']
                self.assertTrue(result['isError'])
                mcp_error = json.loads(result['content'][0]['text'])
                self.assertEqual(mcp_error['error'], 'ValueError')
                self.assertIn(method+'.params.'+field, mcp_error['message'])
                failed = subprocess.run(command + ['request'],
                    input=json.dumps({'method':method,'params':values}), text=True, capture_output=True)
                self.assertEqual(failed.returncode, 1)
                self.assertEqual(json.loads(failed.stdout), mcp_error)
            tools = {t['name']:t for t in answers[-2]['result']['tools']}
            self.assertIn('prices_repair_seller_references', tools)
            self.assertEqual(tools['object_get']['inputSchema']['properties']['params']['required'],
                             ['kind','object_id','scope'])
            self.assertEqual(json.loads(answers[-1]['result']['content'][0]['text'])['result']['version'], 1)


if __name__ == '__main__':
    unittest.main()
