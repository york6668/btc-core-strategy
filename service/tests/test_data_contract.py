"""Behavioral regression tests against an explicitly isolated MySQL 8.4 node.

All inputs are built at runtime. Set BTC_TEST_CONFIG to an isolated node config;
this suite never reads local account files, production state or saved fixtures.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import sqlite3
import subprocess
import sys
import unittest
import uuid

from btc_node.api import dispatch, invoke
from btc_node.codec import Conflict, encode, millis
from btc_node.database import Database
from btc_node.entities import Entities
from btc_node.lifecycle import maintain
from btc_node.series import Series
from btc_node.catalog import Catalog
from btc_node.market_store import Connection
from btc_node import backup


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

    def test_elapsed_acceptance_window_does_not_pass_without_actual_consumption(self):
        from btc_node import acceptance
        with self.db.transaction():
            self.db.metadata('writer',{'owner':'DOCKER','changed_ms':millis()},write=True)
            self.db.metadata('acceptance',{'first_full_day':(self.now-timedelta(days=10)).date().isoformat(),
                'release':'generated-test','required_days':7,'days':{},'defects':[]},write=True)
        state=acceptance.status(self.db)
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
        with self.assertRaises(Conflict):
            acceptance.repair(self.db,defect_index=0,release='later',reason='no moving target',
                evidence_refs=[{'type':'BLOB','digest':proof}])

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


if __name__ == '__main__':
    unittest.main()
