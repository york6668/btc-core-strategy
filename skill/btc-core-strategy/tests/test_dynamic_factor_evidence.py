"""Synthetic factor contracts and mixed-frequency evidence; no live state or HTTP."""
from copy import deepcopy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import test_market_observation as market_fixture
from v4 import evidence, factors, storage
from v4.common import Invalid, create, digest, read


class DynamicFactorEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='btc-factor-evidence-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project = self.base / 'project'
        self.project.mkdir()
        self.store = self.project / '.strategy' / 'v4'
        clock = mock.patch('v4.evidence.now', return_value='2026-09-04T00:00:00Z')
        clock.start()
        self.addCleanup(clock.stop)
        self.cutoff = '2026-09-03T12:02:00Z'
        self.raw_market = self.base / 'market.json'
        create(self.raw_market, market_fixture.evidence())
        self.raw = {'period': '2026-07', 'published_at': '2026-08-25T17:00:00Z',
                    'vintage': '2026-08-25', 'unit': 'synthetic index', 'value': '108'}
        self.bundle, self.directory = self.freeze('base', 'historical_anchor', self.raw)
        self.ref = {'source_id': 'historical_anchor', 'pointer': '/value', 'value_hash': digest('108')}

    def freeze(self, name, sid, payload, url='https://www.federalreserve.gov/releases/h6/current/default.htm',
               origin=None, cutoff=None, trust='PUBLIC_RAW'):
        raw_path = self.base / (name + '-source.json')
        create(raw_path, payload)
        source = {'id': sid, 'origin': origin or evidence.canonical_origin(url), 'url': url, 'trust': trust,
                  'published_at': '2026-08-25T17:00:00Z', 'retrieved_at': '2026-08-25T17:01:00Z',
                  'start': '2026-07-01T00:00:00Z', 'end': '2026-07-31T23:59:59Z',
                  'raw_hash': digest(raw_path.read_bytes())}
        spec = {'parent_id': None, 'cutoff': cutoff or self.cutoff, 'sources': [source],
                'measurements': [], 'verified_facts': [], 'candidates': [], 'gaps': [],
                'handoff': {'previous_hour': None, 'successful_deep': None, 'changes_since_hour': [],
                            'changes_since_deep': [], 'active_hypotheses': [], 'open_questions': []}}
        directory = self.base / name
        return evidence.freeze(spec, {sid: raw_path}, directory), directory

    def assessment(self, refs=None, counter_refs=None):
        return {'items': [{'id': '自由的新因素/非预设目录', 'name': '合成尚未命名机制',
                           'assessment': '现有证据只支持候选机制；方向、持续性和作用仍需模型判断。',
                           'change_reason': '本轮发现此前解释没有覆盖的新现象。',
                           'refs': [self.ref] if refs is None else refs,
                           'counter_refs': [] if counter_refs is None else counter_refs}],
                'summary': '这是模型当前采用的解释，未规定指标数量或权重。'}

    def test_open_factor_names_and_empty_selection_are_valid_without_a_catalogue(self):
        value = self.assessment()
        self.assertIs(factors.validate(value, self.bundle, self.directory), value)
        empty = {'items': [], 'summary': '新增信息尚不改变解释，本轮没有选取需要展开的因素。'}
        self.assertIs(factors.validate(empty, self.bundle, self.directory), empty)
        missing = self.assessment(refs=[])
        missing['items'][0]['assessment'] = '关键原始数据缺失，模型将此因素保留为待核验假设。'
        self.assertIs(factors.validate(missing, self.bundle, self.directory), missing)

    def test_exit_and_changed_direction_are_assessments_not_programmed_signs(self):
        initial = self.assessment()
        reviewed = deepcopy(initial)
        reviewed['items'][0].update(assessment='反向证据改变了原解释；本轮退出原有驱动判断。',
                                    change_reason='原先预期的传导未出现，现象更符合其他机制。')
        self.assertIs(factors.validate(reviewed, self.bundle, self.directory), reviewed)
        self.assertNotEqual(initial, reviewed)

    def test_support_and_counterevidence_both_require_exact_raw_values(self):
        for field in ('refs', 'counter_refs'):
            value = self.assessment(refs=[], counter_refs=[])
            bad = dict(self.ref, value_hash=digest('invented value'))
            value['items'][0][field] = [bad]
            with self.subTest(field=field), self.assertRaisesRegex(Invalid, 'raw value reference mismatch'):
                factors.validate(value, self.bundle, self.directory)

    def test_missing_sources_and_duplicate_ids_fail_but_leads_remain_leads(self):
        value = self.assessment(refs=[dict(self.ref, source_id='not_frozen')])
        with self.assertRaisesRegex(Invalid, 'reference source absent'):
            factors.validate(value, self.bundle, self.directory)
        value = self.assessment()
        value['items'].append(deepcopy(value['items'][0]))
        with self.assertRaisesRegex(Invalid, 'duplicate factor id'):
            factors.validate(value, self.bundle, self.directory)
        bundle, directory = self.freeze('lead', 'historical_anchor', self.raw, trust='UNTRUSTED_LEAD')
        factors.validate(self.assessment(), bundle, directory)
        self.assertEqual(bundle['verified_facts'], [])
        with self.assertRaisesRegex(Invalid, 'untrusted lead'):
            evidence.resolve(bundle, directory, self.ref, verified=True)

    def test_context_exposes_missing_guide_without_accessing_account_files(self):
        from unittest import mock
        with mock.patch('node_config.factor_guide', return_value=self.project / 'missing-guide.md'):
            context = factors.context(self.store, self.project, self.directory)
        self.assertEqual(context['guide']['status'], 'MISSING')
        self.assertEqual(context['current']['status'], 'NO_CURRENT_ASSESSMENT')
        self.assertIsNone(context['current']['factor_assessment'])
        self.assertEqual(context['evidence']['evidence_id'], self.bundle['evidence_id'])
        indexed = context['evidence']['sources'][0]
        self.assertEqual(read(indexed['raw_path']), self.raw)
        self.assertEqual(indexed['retrieved_at'], '2026-08-25T17:01:00Z')
        self.assertEqual(list(self.project.iterdir()), [])

    def test_context_uses_pinned_current_and_never_fills_historical_assessment(self):
        guide = self.project / '价值因子.md'
        guide.write_text('开放的研究参考，不设固定名单。\n')
        archived = self.store / 'evidence' / self.bundle['evidence_id'][7:]
        shutil.copytree(self.directory, archived)
        prior = storage._publish(self.store, {'assessment': {'revision': 3, 'evidence_id': self.bundle['evidence_id']}}, {}, None)
        historical = factors.context(self.store, self.project)
        from node_config import factor_guide
        self.assertEqual(historical['guide']['hash'], digest(factor_guide().read_bytes()))
        self.assertNotEqual(historical['guide']['hash'], digest(guide.read_bytes()))
        self.assertEqual(historical['current']['status'], 'HISTORY_UNASSESSED')
        self.assertIsNone(historical['current']['factor_assessment'])
        current = storage._publish(self.store, {'assessment': {'revision': 4, 'evidence_id': self.bundle['evidence_id'],
                                                              'factor_assessment': self.assessment()}}, {}, prior['decision_id'])
        selected = factors.context(self.store, self.project)
        self.assertEqual(selected['current']['decision_id'], current['decision_id'])
        self.assertEqual(selected['current']['factor_assessment'], self.assessment())
        self.assertEqual(selected['evidence']['directory'], str(archived.resolve()))
        self.assertEqual(read(self.store / 'versions' / prior['decision_id'] / 'decision.json'), prior)

    def test_compact_root_merges_monthly_sources_and_keeps_original_vintage(self):
        extra_raw = dict(self.raw, value='109', vintage='2026-08-26')
        extra, additional = self.freeze('additional', 'new_monthly', extra_raw, url='https://www.eia.gov/outlooks/steo/')
        target = self.base / 'combined'
        bundle = evidence.compact_root(self.directory, self.raw_market, [self.ref], target, self.cutoff, additional)
        self.assertEqual(len(bundle['sources']), 3)
        self.assertIsNone(bundle['parent_id'])
        self.assertEqual(next(s for s in bundle['sources'] if s['id'] == 'new_monthly'), extra['sources'][0])
        original = target / 'raw' / (extra['sources'][0]['raw_hash'][7:] + '.json')
        self.assertEqual(read(original), extra_raw)
        self.assertEqual(evidence.resolve(bundle, target, self.ref), '108')
        self.assertEqual(read(self.directory / 'evidence.json'), self.bundle)
        self.assertEqual(bundle['verified_facts'], [])

    def test_published_calendar_retains_future_event_time_without_future_data(self):
        payload={'published_on':'2026-08-25','event_at':'2026-09-10T18:00:00Z','status':'scheduled','result':None}
        extra,directory=self.freeze('calendar','official_calendar',payload,
            url='https://www.federalreserve.gov/newsevents/calendar.htm')
        target=self.base/'with-calendar'
        bundle=evidence.compact_root(self.directory,self.raw_market,[self.ref],target,self.cutoff,directory)
        source=next(s for s in bundle['sources'] if s['id']=='official_calendar')
        self.assertEqual(source,extra['sources'][0])
        ref={'source_id':'official_calendar','pointer':'/event_at','value_hash':digest(payload['event_at'])}
        self.assertEqual(evidence.resolve(bundle,target,ref,verified=True),'2026-09-10T18:00:00Z')
        self.assertEqual(read(target/'raw'/(source['raw_hash'][7:]+'.json'))['result'],None)

    def test_identical_additional_source_deduplicates_but_collisions_fail_before_write(self):
        target = self.base / 'deduplicated'
        bundle = evidence.compact_root(self.directory, self.raw_market, [self.ref], target, self.cutoff, self.directory)
        self.assertEqual(len(bundle['sources']), 2)
        _, additional = self.freeze('collision', 'historical_anchor', dict(self.raw, value='109'))
        # An id cannot be reassigned even when its previous source was not selected.
        for refs, name in (([], 'collision-unretained'), ([self.ref], 'collision-retained')):
            destination = self.base / name
            with self.subTest(retained=bool(refs)), self.assertRaisesRegex(Invalid, 'source collision'):
                evidence.compact_root(self.directory, self.raw_market, refs, destination, self.cutoff, additional)
            self.assertFalse(destination.exists())

    def test_same_market_retrieved_twice_keeps_both_original_clocks(self):
        import market_observation as market
        first_dir=self.base/'first-market'
        first=evidence.compact_root(self.directory,self.raw_market,[],first_dir,self.cutoff)
        original=read(self.raw_market)
        newer=deepcopy(original)
        for item in newer['retrievals'].values():
            item['retrieved_at']=market.iso(market.timestamp(item['retrieved_at'])+30)
        newer['retrieval_id']=market.digest({'evidence_id':newer['evidence_id'],'retrievals':newer['retrievals']})
        newer_path=self.base/'newer-market.json';create(newer_path,newer)
        additional=self.base/'second-market'
        second=evidence.compact_root(self.directory,newer_path,[],additional,self.cutoff)
        source=first['sources'][0]
        ref={'source_id':source['id'],'pointer':'/datasets/binance_spot_1h',
             'value_hash':digest(original['datasets']['binance_spot_1h'])}
        target=self.base/'both-retrievals'
        merged=evidence.compact_root(first_dir,newer_path,[ref],target,self.cutoff,additional)
        self.assertEqual(len(merged['sources']),2)
        self.assertIn(source,merged['sources'])
        fresh=next(s for s in merged['sources'] if s['raw_hash']==second['sources'][0]['raw_hash'])
        self.assertNotEqual(fresh['id'],source['id'])
        self.assertEqual(fresh['retrieved_at'],second['sources'][0]['retrieved_at'])
        self.assertEqual(evidence.resolve(merged,target,ref),original['datasets']['binance_spot_1h'])
        self.assertEqual(read(first_dir/'evidence.json'),first)

    def test_retained_factor_lead_is_not_promoted_to_verified_evidence(self):
        _, source = self.freeze('historical-lead', 'historical_anchor', self.raw, trust='UNTRUSTED_LEAD')
        target = self.base / 'retained-lead'
        bundle = evidence.compact_root(source, self.raw_market, [self.ref], target, self.cutoff)
        retained = next(s for s in bundle['sources'] if s['id'] == 'historical_anchor')
        self.assertEqual(retained['trust'], 'UNTRUSTED_LEAD')
        factors.validate(self.assessment(), bundle, target)
        with self.assertRaisesRegex(Invalid, 'untrusted lead'):
            evidence.resolve(bundle, target, self.ref, verified=True)
        self.assertEqual(bundle['verified_facts'], [])

    def test_additional_future_knowledge_or_tampered_raw_is_rejected(self):
        _, additional = self.freeze('future', 'later_revision', self.raw, cutoff='2026-09-03T13:00:00Z')
        with self.assertRaisesRegex(Invalid, 'additional evidence exceeds new cutoff'):
            evidence.compact_root(self.directory, self.raw_market, [], self.base / 'bad-future', self.cutoff, additional)
        extra, additional = self.freeze('changed-raw', 'monthly', self.raw)
        (additional / 'raw' / (extra['sources'][0]['raw_hash'][7:] + '.json')).write_text('{}')
        with self.assertRaisesRegex(Invalid, 'changed raw evidence'):
            evidence.compact_root(self.directory, self.raw_market, [], self.base / 'bad-raw', self.cutoff, additional)

    def test_official_registration_preserves_historical_unknown_and_fred_independence(self):
        eia_url = 'https://www.eia.gov/outlooks/steo/'
        historical, directory = self.freeze('legacy-eia', 'eia', self.raw, eia_url, 'UNESTABLISHED:www.eia.gov')
        self.assertEqual(evidence.validate(historical, directory), historical)
        eia_ref = dict(self.ref, source_id='eia')
        self.assertEqual(evidence.origins(historical, [eia_ref]), set())
        current, directory = self.freeze('registered-eia', 'eia', self.raw, eia_url)
        self.assertEqual(evidence.origins(current, [eia_ref]), {'EIA'})
        fred, directory = self.freeze('fred', 'fred', self.raw, 'https://fred.stlouisfed.org/series/M2SL')
        self.assertEqual(evidence.origins(fred, [dict(self.ref, source_id='fred')]), set())
        altered = deepcopy(fred)
        altered['sources'][0]['origin'] = 'FED'
        altered['evidence_id'] = digest({k: v for k, v in altered.items() if k != 'evidence_id'})
        with self.assertRaisesRegex(Invalid, 'self-declare independence'):
            evidence.validate(altered, directory)


if __name__ == '__main__':
    unittest.main()
