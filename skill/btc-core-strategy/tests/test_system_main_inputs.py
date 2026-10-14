"""Constructed mechanical contract tests, not actual-market/actor acceptance.

All response bytes, EvidenceBundles and facts are generated in a temporary
directory. Assembly mocks only existing-owner/context seams; it never adopts
a candidate, records an approval or accesses a real personal source.
"""
import base64
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlencode

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import market_observation as market
import test_market_observation as collected
from v4 import cycle, evidence, main_flow, material_reads, run_flow, sensing
from v4.common import Invalid, digest, encoded


class MainCurrentInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-main-input-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.cutoff = 1788436800
        self.stamp = market.iso(self.cutoff)
        self.rows = [[t*1000, '100', '103', '99', '102', '10', (t+3600)*1000-1,
                      '1000', 30, '4', '400', '0'] for t in (self.cutoff-7200, self.cutoff-3600, self.cutoff)]
        self.body = encoded(self.rows)
        self.input = {'source_id': 'CONSTRUCTED_CURRENT_RAW',
            'url': 'https://data-api.binance.vision/api/v3/klines?' + urlencode(
                {'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 3, 'endTime': (self.cutoff+3600)*1000-1}),
            'retrieved_at': market.iso(self.cutoff+60), 'raw_hash': digest(self.body),
            'bytes_base64': base64.b64encode(self.body).decode()}
        self.raw = market.normalize_locked(self.stamp, {'binance_spot_1h': self.input})
        self.ref = {'source_id': self.input['source_id'], 'pointer': '/1/4', 'value_hash': digest(self.rows[1][4])}

    def package(self, raw=None, changes=None, extra=None):
        raw = self.raw if raw is None else raw
        directory = self.base / ('evidence-' + str(len(list(self.base.glob('evidence-*')))))
        rawfile = self.base / ('response-' + str(len(list(self.base.glob('response-*')))) + '.json')
        rawfile.write_bytes(self.body)
        container = self.base / ('market-' + str(len(list(self.base.glob('market-*')))) + '.json')
        container.write_bytes(encoded(raw))
        source = {'id': self.input['source_id'], 'origin': evidence.canonical_origin(self.input['url']), 'url': self.input['url'],
            'trust': 'PUBLIC_RAW', 'published_at': None, 'retrieved_at': self.input['retrieved_at'],
            'start': market.iso(self.cutoff-7200), 'end': self.stamp}
        source.update(changes or {})
        aggregate = {'id': 'CONSTRUCTED_MARKET', 'origin': 'BINANCE',
            'url': 'https://data-api.binance.vision/api/v3/klines',
            'trust': 'UNTRUSTED_LEAD' if 'normalization' in raw else 'PUBLIC_RAW',
            'published_at': raw['cutoff'], 'retrieved_at': max(r['retrieved_at'] for r in raw['retrievals'].values()),
            'start': min(d['requested_start'] for d in raw['datasets'].values()), 'end': raw['cutoff']}
        sources = [source, aggregate] + (extra or [])
        for s in sources:
            s['raw_hash'] = digest(container.read_bytes() if s is aggregate else rawfile.read_bytes())
        files = {s['id']: str(rawfile) for s in sources}
        files[aggregate['id']] = str(container)
        spec = {'parent_id':None, 'cutoff': max(s['retrieved_at'] for s in sources), 'sources': sources,
            'measurements': [], 'verified_facts': [], 'candidates': [], 'gaps': [],
            'handoff': {'previous_hour': None, 'successful_deep': None, 'changes_since_hour': [],
                        'changes_since_deep': [], 'active_hypotheses': [], 'open_questions': []}}
        return evidence.freeze(spec, files, directory), directory

    def test_explicit_original_close_is_preserved_without_trust_upgrade(self):
        package, directory = self.package()
        refs = main_flow.current_market_references(package, directory, self.raw, [self.ref])
        self.assertEqual(refs, [self.ref])
        self.assertIsNot(refs[0], self.ref)
        self.assertEqual(evidence.resolve(package, directory, refs[0], verified=True), '102')
        self.assertEqual(next(s['trust'] for s in package['sources'] if s['id'] == 'CONSTRUCTED_MARKET'), 'UNTRUSTED_LEAD')

    def test_full_assembly_retains_the_explicit_candidate_refs(self):
        package, directory = self.package()
        project = self.base / 'project'; project.mkdir()
        for name in ('资产配置.md', '操作记录.md', '纠正记录.md'):
            (project/name).write_text('CONSTRUCTED TEST FACTS ONLY\n')
        prior = {key: {} for key in main_flow.sections_for_revision(9)}
        prior.update(revision=9, research_absorptions=[], evidence_bindings={},
                     temporal_scope={'knowledge_cutoff': self.stamp}, evidence_id=package['evidence_id'])
        components = {'updates': {}, 'section_reviews': dict.fromkeys(main_flow.sections_for_revision(9), 'Constructed applicability finding'),
            'evidence_bindings': {}, 'domain_market_cutoff': self.stamp, 'stop_reason': 'INSUFFICIENT_CONSERVATIVE_COMPLETE',
            'current_market_refs': [self.ref]}
        with mock.patch('v4.storage.current', return_value={'decision_id': 'CONSTRUCTED_UNIT_OWNER', 'assessment': prior}), \
             mock.patch('v4.sensing.context', return_value={'context_id': 'CONSTRUCTED_UNIT_CONTEXT'}):
            draft, _ = main_flow.assemble(self.base/'store', project, components, directory, self.raw,
                {'role':'MAIN','model':'gpt-5.6-sol','effort':'high','agent_id':'CONSTRUCTED_UNIT_AGENT','run_ref':'CONSTRUCTED_UNIT_RUN'},
                {'checks':dict.fromkeys(main_flow.CHECKS.split(), 'Constructed mechanical finding'), 'unresolved_issues':[], 'material_new_outlook':False}, workflow_revision=9)
        self.assertEqual(cycle.candidate(draft)['assessment']['temporal_scope']['current_market_refs'], [self.ref])

    def test_original_bound_template_answers_can_supply_refs_without_new_runtime_fields(self):
        core = {'revision':9, 'evidence_bindings':{}, 'temporal_scope':{'domain_market_cutoff':self.stamp},
                'stop_reason':'INSUFFICIENT_CONSERVATIVE_COMPLETE'}
        template = run_flow.main_template(core)
        result = material_reads.compose(template, {'components': {'current_market_refs':[self.ref]}})
        self.assertEqual(result['components']['current_market_refs'], [self.ref])
        self.assertNotIn('current_market_refs', template['components'])

    def test_missing_explicit_refs_cannot_fallback_to_normalized_container(self):
        package, directory = self.package()
        with self.assertRaisesRegex(Invalid, 'requires explicit'):
            main_flow.current_market_references(package, directory, self.raw)

    def test_untrusted_quote_wrong_hash_and_self_reported_value_are_rejected(self):
        package, directory = self.package(changes={'trust':'UNTRUSTED_LEAD'})
        with self.assertRaisesRegex(Invalid, 'untrusted lead'):
            main_flow.current_market_references(package, directory, self.raw, [self.ref])
        package, directory = self.package()
        for ref, error in ((dict(self.ref, value_hash='sha256:'+'0'*64), 'raw value reference mismatch'),
                           (dict(self.ref, value='102'), 'raw reference: invalid fields')):
            with self.subTest(error=error), self.assertRaisesRegex(Invalid, error):
                main_flow.current_market_references(package, directory, self.raw, [ref])

    def test_stale_source_and_stale_or_unclosed_row_are_rejected(self):
        package, directory = self.package(changes={'end':market.iso(self.cutoff-3600)})
        with self.assertRaisesRegex(Invalid, 'different cutoff'):
            main_flow.current_market_references(package, directory, self.raw, [self.ref])
        package, directory = self.package()
        for index in (0, 2):
            ref = dict(self.ref, pointer=f'/{index}/4', value_hash=digest(self.rows[index][4]))
            with self.subTest(index=index), self.assertRaisesRegex(Invalid, 'does not reach'):
                main_flow.current_market_references(package, directory, self.raw, [ref])

    def test_same_bytes_under_unrelated_source_id_do_not_become_current(self):
        unrelated = {'id':'CONSTRUCTED_UNRELATED', 'origin':'BINANCE', 'url':self.input['url'],
            'trust':'PUBLIC_RAW', 'published_at':None, 'retrieved_at':self.input['retrieved_at'],
            'start':market.iso(self.cutoff-7200), 'end':self.stamp}
        package, directory = self.package(extra=[unrelated])
        with self.assertRaisesRegex(Invalid, 'not an original source'):
            main_flow.current_market_references(package, directory, self.raw, [dict(self.ref,source_id=unrelated['id'])])

    def test_original_source_url_or_retrieval_must_match_locked_identity(self):
        for changes in ({'url':self.input['url']+'&startTime=1'}, {'retrieved_at':market.iso(self.cutoff+120)}):
            package, directory = self.package(changes=changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(Invalid, 'identity differs'):
                main_flow.current_market_references(package, directory, self.raw, [self.ref])

    def test_changed_original_bytes_cannot_be_bound_under_the_locked_source_id(self):
        changed = deepcopy(self.rows); changed[0][4] = '101'
        self.body = encoded(changed)
        package, directory = self.package()
        with self.assertRaisesRegex(Invalid, 'identity differs'):
            main_flow.current_market_references(package, directory, self.raw, [self.ref])

    def test_changed_archived_bytes_are_rejected_even_when_selected_price_is_unchanged(self):
        package, directory = self.package()
        source = next(s for s in package['sources'] if s['id'] == self.ref['source_id'])
        changed = deepcopy(self.rows); changed[0][4] = '101'
        (directory/'raw'/(source['raw_hash'][7:]+'.json')).write_bytes(encoded(changed))
        with self.assertRaisesRegex(Invalid, 'missing or changed raw evidence'):
            main_flow.current_market_references(package, directory, self.raw, [self.ref])

    def test_trusted_label_cannot_turn_normalization_container_into_original(self):
        package, directory = self.package()
        next(s for s in package['sources'] if s['id']=='CONSTRUCTED_MARKET')['trust'] = 'PUBLIC_RAW'
        package['evidence_id'] = digest({k:v for k,v in package.items() if k!='evidence_id'})
        with self.assertRaisesRegex(Invalid, 'requires explicit'):
            main_flow.current_market_references(package, directory, self.raw)
        ref = {'source_id':'CONSTRUCTED_MARKET','pointer':'/datasets','value_hash':digest(self.raw['datasets'])}
        with self.assertRaisesRegex(Invalid, 'not an original source'):
            main_flow.current_market_references(package, directory, self.raw, [ref])

    def test_invalid_original_row_omitted_from_current_input_cannot_be_quoted(self):
        self.rows[1][2] = '1'
        self.body = encoded(self.rows); self.input['raw_hash'] = digest(self.body)
        self.input['bytes_base64'] = base64.b64encode(self.body).decode()
        self.raw = market.normalize_locked(self.stamp, {'binance_spot_1h':self.input})
        package, directory = self.package()
        with self.assertRaisesRegex(Invalid, 'absent from the validated'):
            main_flow.current_market_references(package, directory, self.raw, [self.ref])

    def test_other_current_market_identity_cannot_replace_the_bound_complete_input(self):
        package, directory = self.package()
        inputs = deepcopy(self.raw['normalization']['inputs'])
        inputs['binance_spot_1h']['source_id'] = 'CONSTRUCTED_OTHER_INPUT'
        other = market.normalize_locked(self.stamp, inputs)
        with self.assertRaisesRegex(Invalid, 'exact complete current market package'):
            main_flow.current_market_references(package, directory, other, [self.ref])

    def test_other_registered_original_provider_grammars_preserve_current_refs(self):
        cases = [
            ('coinbase_spot_1h', 'https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600',
             [[self.cutoff-3600,99,103,100,102,10]], '/0/4', 102),
            ('kraken_spot_1h', 'https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60',
             {'error':[], 'result':{'XXBTZUSD':[[self.cutoff-3600,'100','103','99','102','101','10',30]], 'last':self.cutoff}}, '/result/XXBTZUSD/0/4', '102'),
            ('binance_oi_1h', 'https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h',
             [{'symbol':'BTCUSDT','timestamp':self.cutoff*1000,'sumOpenInterest':'10','sumOpenInterestValue':'1020'}], '/0/sumOpenInterest', '10'),
            ('binance_funding', 'https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT',
             [{'symbol':'BTCUSDT','fundingTime':self.cutoff*1000,'fundingRate':'0.0001'}], '/0/fundingRate', '0.0001'),
        ]
        for key, url, payload, location, value in cases:
            with self.subTest(dataset=key):
                self.body = encoded(payload)
                self.input.update(url=url, raw_hash=digest(self.body), bytes_base64=base64.b64encode(self.body).decode())
                self.raw = market.normalize_locked(self.stamp, {key:self.input})
                package, directory = self.package()
                ref = {'source_id':self.input['source_id'],'pointer':location,'value_hash':digest(value)}
                self.assertEqual(main_flow.current_market_references(package,directory,self.raw,[ref]), [ref])

    def test_reused_domain_replay_uses_immutable_original_publication_clock(self):
        import json
        origin = {'decision_id':'CONSTRUCTED_ORIGIN', 'parent_id':'CONSTRUCTED_PARENT',
            'created_at':'2026-09-03T16:00:00Z',
            'assessment':{'temporal_scope':{'knowledge_cutoff':'2026-09-03T15:00:00Z'}}}
        parent = {'decision_id':'CONSTRUCTED_PARENT', 'canonical_domain':{}}
        for value in (origin, parent):
            path = self.base/'versions'/value['decision_id']/'decision.json'
            path.parent.mkdir(parents=True); path.write_text(json.dumps(value))
        domain = {'event':{'cycle':{'final_expiry_event_id':'CONSTRUCTED_FINAL',
             'events':[{'event_id':'CONSTRUCTED_FINAL','due_at':'2099-01-01T00:00:00Z'}]}}}
        draft = {'domain':domain, 'temporal_scope':{'knowledge_cutoff':'2026-09-03T15:00:00Z'},
                 'section_provenance':{'domain':{'mode':'REUSED','decision_id':origin['decision_id']}}}
        with mock.patch('v4.main_flow.validate_publication'), \
             mock.patch('v4.storage.domain_baseline', return_value={'decision_id':'CONSTRUCTED_PARENT','canonical_domain':{}}):
            baseline = main_flow.domain_validation_baseline(self.base, parent, draft)
        self.assertEqual(baseline['_verified_domain_replay_clock'].isoformat(), '2026-09-03T16:00:00+00:00')

    def test_empty_refs_and_container_as_explicit_original_are_rejected(self):
        package, directory = self.package()
        for refs, error in (([], 'contain actual'), ([{'source_id':'CONSTRUCTED_MARKET','pointer':'/datasets','value_hash':digest(self.raw['datasets'])}], 'untrusted lead')):
            with self.subTest(refs=refs), self.assertRaisesRegex(Invalid, error):
                main_flow.current_market_references(package, directory, self.raw, refs)

    def test_normal_public_collected_package_keeps_legacy_path_and_rejects_stale_row(self):
        raw = collected.evidence()
        package, directory = self.package(raw=raw)
        refs = main_flow.current_market_references(package, directory, raw)
        self.assertEqual(refs, [{'source_id':'CONSTRUCTED_MARKET','pointer':'/datasets','value_hash':digest(raw['datasets'])}])
        self.assertEqual(evidence.resolve(package, directory, refs[0], verified=True), raw['datasets'])
        row = raw['datasets']['binance_spot_1h']['rows'][0]
        stale = {'source_id':'CONSTRUCTED_MARKET','pointer':'/datasets/binance_spot_1h/rows/0/close','value_hash':digest(row['close'])}
        with self.assertRaisesRegex(Invalid, 'does not reach'):
            main_flow.current_market_references(package, directory, raw, [stale])

    def ref_assessment(self, refs=None, cutoff=None):
        return {'temporal_scope': {'current_market_refs': [self.ref] if refs is None else refs,
                                   'market_cutoff': cutoff or self.stamp}}

    def without_source(self, package, directory, source_id):
        spec = {k: deepcopy(v) for k, v in package.items() if k not in ('schema', 'evidence_id')}
        spec['sources'] = [s for s in spec['sources'] if s['id'] != source_id]
        files = {s['id']: str(directory/'raw'/(s['raw_hash'][7:]+'.json')) for s in spec['sources']}
        destination = self.base/('evidence-' + str(len(list(self.base.glob('evidence-*')))))
        return evidence.freeze(spec, files, destination), destination

    def sensing_context(self, directory, raw=None):
        project = self.base/'synthetic-project'
        project.mkdir(exist_ok=True)
        for name in ('资产配置.md', '操作记录.md', '纠正记录.md'):
            (project/name).write_text('SYNTHETIC_ENGINEERING_TEST ONLY\n')
        return sensing.context(project/'.strategy/v4', self.base/'absent.sqlite3', project,
                               self.raw if raw is None else raw, evidence_dir=directory,
                               frozen_review=True, workflow_revision=9)

    def test_real_sensing_context_accepts_raw_owned_replay_without_mocking_context(self):
        package, directory = self.package()
        ctx = self.sensing_context(directory)
        self.assertEqual(ctx['identity']['factor_evidence_id'], package['evidence_id'])
        self.assertEqual(ctx['identity']['market_evidence_id'], self.raw['evidence_id'])
        self.assertEqual(next(s['trust'] for s in package['sources'] if s['id']=='CONSTRUCTED_MARKET'), 'UNTRUSTED_LEAD')
        self.assertFalse((self.base/'synthetic-project/.strategy/v4/current').exists())

    def test_real_sensing_context_rejects_missing_untrusted_or_changed_input(self):
        package, directory = self.package()
        _, missing = self.without_source(package, directory, self.input['source_id'])
        with self.assertRaisesRegex(Invalid, 'current original source is absent'):
            self.sensing_context(missing)
        for changes, error in (({'trust':'UNTRUSTED_LEAD'}, 'untrusted lead'),
                               ({'url':self.input['url']+'&startTime=1'}, 'identity differs')):
            _, directory = self.package(changes=changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(Invalid, error):
                self.sensing_context(directory)

    def test_raw_market_finds_owned_wrapper_for_native_array_ref(self):
        package, directory = self.package()
        self.assertEqual(sensing.raw_market(directory, self.ref_assessment()), self.raw)
        self.assertEqual(next(s['trust'] for s in package['sources'] if s['id']=='CONSTRUCTED_MARKET'), 'UNTRUSTED_LEAD')

    def test_raw_market_keeps_direct_public_collector_path(self):
        raw = collected.evidence()
        package, directory = self.package(raw=raw)
        refs = main_flow.current_market_references(package, directory, raw)
        self.assertEqual(sensing.raw_market(directory, self.ref_assessment(refs, raw['cutoff'])), raw)

    def test_raw_market_rejects_missing_raw_wrong_input_and_tampered_ref(self):
        package, directory = self.package()
        _, missing = self.without_source(package, directory, self.input['source_id'])
        with self.assertRaises(Invalid):
            sensing.raw_market(missing, self.ref_assessment())
        for changes in ({'url':self.input['url']+'&startTime=1'}, {'retrieved_at':market.iso(self.cutoff+120)}):
            _, directory = self.package(changes=changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(Invalid, 'identity differs'):
                sensing.raw_market(directory, self.ref_assessment())
        _, directory = self.package()
        with self.assertRaisesRegex(Invalid, 'raw value reference mismatch'):
            sensing.raw_market(directory, self.ref_assessment([dict(self.ref, value_hash='sha256:'+'0'*64)]))
        stale = dict(self.ref, pointer='/0/4', value_hash=digest(self.rows[0][4]))
        with self.assertRaisesRegex(Invalid, 'does not reach'):
            sensing.raw_market(directory, self.ref_assessment([stale]))

    def test_raw_market_does_not_substitute_another_cutoff_or_fabricate_wrapper(self):
        package, directory = self.package()
        self.assertIsNone(sensing.raw_market(directory, self.ref_assessment(cutoff=market.iso(self.cutoff+3600))))
        _, missing = self.without_source(package, directory, 'CONSTRUCTED_MARKET')
        self.assertIsNone(sensing.raw_market(missing, self.ref_assessment()))

    def test_raw_market_rejects_two_different_complete_owned_inputs(self):
        package, directory = self.package()
        body = encoded([{'symbol':'BTCUSDT', 'timestamp':self.cutoff*1000,
                         'sumOpenInterest':'12', 'sumOpenInterestValue':'1224'}])
        extra = {'source_id':'CONSTRUCTED_OI_RAW', 'url':'https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h',
                 'retrieved_at':self.input['retrieved_at'], 'raw_hash':digest(body),
                 'bytes_base64':base64.b64encode(body).decode()}
        other = market.normalize_locked(self.stamp, {'binance_spot_1h':self.input, 'binance_oi_1h':extra})
        otherfile=self.base/'other-complete.json'; otherfile.write_bytes(encoded(other))
        inputfile=self.base/'other-original.json'; inputfile.write_bytes(body)
        spec = {k:deepcopy(v) for k,v in package.items() if k not in ('schema','evidence_id')}
        spec['sources'].extend([
            {'id':extra['source_id'], 'origin':'BINANCE', 'url':extra['url'], 'trust':'PUBLIC_RAW', 'published_at':None,
             'retrieved_at':extra['retrieved_at'], 'start':self.stamp,'end':self.stamp,'raw_hash':digest(body)},
            dict(next(s for s in package['sources'] if s['id']=='CONSTRUCTED_MARKET'), id='CONSTRUCTED_OTHER_MARKET', raw_hash=digest(otherfile.read_bytes()))])
        files={s['id']:str(directory/'raw'/(s['raw_hash'][7:]+'.json')) for s in package['sources']}
        files.update({extra['source_id']:str(inputfile), 'CONSTRUCTED_OTHER_MARKET':str(otherfile)})
        destination=self.base/'ambiguous-evidence'; evidence.freeze(spec,files,destination)
        with self.assertRaisesRegex(Invalid, 'ambiguous complete market inputs'):
            sensing.raw_market(destination,self.ref_assessment())

    def test_compact_replay_preserves_exact_existing_wrapper_and_original_bytes(self):
        package, directory = self.package()
        rawfile=self.base/'current-replay.json'; rawfile.write_bytes(encoded(self.raw))
        compact=self.base/'compact'
        result=evidence.compact_root(directory,rawfile,[self.ref],compact,package['cutoff'])
        self.assertEqual(result['sources'], sorted(package['sources'],key=lambda s:s['id']))
        self.assertFalse(any(s['id'].startswith('market_') for s in result['sources']))
        for source in result['sources']:
            filename=source['raw_hash'][7:]+'.json'
            self.assertEqual((compact/'raw'/filename).read_bytes(),(directory/'raw'/filename).read_bytes())
        self.assertEqual(sensing.raw_market(compact,self.ref_assessment()),self.raw)

    def test_compact_replay_can_retain_unreferenced_wrapper_from_additional(self):
        package, directory = self.package()
        _, base = self.without_source(package,directory,'CONSTRUCTED_MARKET')
        rawfile=self.base/'current-additional.json'; rawfile.write_bytes(encoded(self.raw))
        compact=self.base/'compact-additional'
        result=evidence.compact_root(base,rawfile,[self.ref],compact,package['cutoff'],additional=directory)
        self.assertEqual({s['id']:s for s in result['sources']},{s['id']:s for s in package['sources']})
        self.assertEqual(sensing.raw_market(compact,self.ref_assessment()),self.raw)

    def test_compact_replay_rejects_missing_wrapper_missing_raw_or_changed_input(self):
        package, directory = self.package()
        rawfile=self.base/'current-negative.json'; rawfile.write_bytes(encoded(self.raw))
        for source_id,error in (('CONSTRUCTED_MARKET','UNTRUSTED_LEAD wrapper owner'),
                                (self.input['source_id'],'current original source is absent')):
            _, missing=self.without_source(package,directory,source_id)
            with self.subTest(source=source_id), self.assertRaisesRegex(Invalid,error):
                evidence.compact_root(missing,rawfile,[],self.base/('compact-missing-'+source_id),package['cutoff'])
        _, wrong=self.package(changes={'url':self.input['url']+'&startTime=1'})
        with self.assertRaisesRegex(Invalid,'identity differs'):
            evidence.compact_root(wrong,rawfile,[],self.base/'compact-wrong',package['cutoff'])

    def test_compact_normal_collector_keeps_public_raw_behavior(self):
        raw=collected.evidence()
        package,directory=self.package(raw=raw)
        rawfile=self.base/'current-collector.json'; rawfile.write_bytes(encoded(raw))
        refs=main_flow.current_market_references(package,directory,raw)
        result=evidence.compact_root(directory,rawfile,refs,self.base/'compact-collector',package['cutoff'])
        current=next(s for s in result['sources'] if s['id'].startswith('market_'))
        self.assertEqual(current['trust'],'PUBLIC_RAW')
        self.assertEqual(current['raw_hash'],digest(rawfile.read_bytes()))


if __name__ == '__main__':
    unittest.main()
