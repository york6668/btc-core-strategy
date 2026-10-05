"""Cycle ownership scenarios. All assets, prices and runtime traces are synthetic."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_dynamic_factor_main as main_fixture
import test_market_observation as market_fixture
import test_mobile_report as mobile_fixture
import market_observation as market
from v4 import cycle, research, sensing, storage, reporting, run_flow, main_flow, evidence
from v4.common import Invalid, digest, read, replace, facts


class CycleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.d = main_fixture.DynamicMainTests()
        self.d.setUp(); self.addCleanup(self.d.doCleanups)
        self.d.upgrade(); self.initial = self.d.commit(); self.f = self.d.f
        replace(self.f.store / 'write-policy.json', {'minimum_assessment_revision': 4, 'cycle_review_required': True})
        self.d.bind()

    def study(self, approved=True, coverage=None, authored=False, wrapper=None, before_reveal=None, user_reference=None, independent_output=None):
        f = self.f
        spec = {'issue_key': cycle.ISSUE_KEY, 'scope': cycle.SCOPE,
            'question': 'Synthetic complete cycle study, with downside and portfolio scenarios.',
            'decision_id': storage.current(f.store)['decision_id'], 'evidence_id': f.evidence['evidence_id'],
            'change': {'kind': 'INITIAL_QUESTION', 'reason': 'First complete cycle baseline is absent.',
                'refs': [{'source_id': 'hourly', 'pointer': '/datasets', 'value_hash': digest(f.raw['datasets'])}],
                'reopen_condition': None}, 'reopen_conditions': ['Synthetic persistent financing deterioration.']}
        if user_reference:
            spec['change']['kind'] = 'USER_REQUESTED_REVIEW'
            spec['user_request'] = {'reference': user_reference, 'reason': 'Synthetic explicit full review request.'}
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:00Z'):
            req = research.request(f.store, f.project, spec)
        path = f.base / 'synthetic-cycle-trace.jsonl'
        def turn(identity, phase, output, candidate_id=None):
            proof = research.read_input(f.store, req['request_id'], phase, candidate_id)
            start = '2030-03-01T13:03:01Z' if phase == 'independent' else '2030-03-01T13:03:06Z'
            end = '2030-03-01T13:03:03Z' if phase == 'independent' else '2030-03-01T13:03:09Z'
            events = [{'type': 'turn_context', 'payload': {'turn_id': identity, 'model': 'gpt-6-astra', 'effort': 'max'}},
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': identity,
                    'input': 'python strategy_v4.py research-read --request ' + req['request_id'] + ' --phase ' + phase}},
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': identity, 'output': json.dumps(proof)}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': identity,
                    'started_at': datetime.fromisoformat(start.replace('Z', '+00:00')).timestamp(),
                    'completed_at': datetime.fromisoformat(end.replace('Z', '+00:00')).timestamp(), 'last_agent_message': output}}]
            with path.open('a') as stream:
                for event in events: stream.write(json.dumps(event) + '\n')
        path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'SYNTHETIC_CYCLE_AGENT'}}) + '\n')
        turn('FIRST', 'independent', independent_output or 'Synthetic independent cycle, downside, price and portfolio assessment.')
        first = research.independent(f.store, req['request_id'], 'SYNTHETIC_CYCLE_AGENT', path, 'FIRST')
        if before_reveal:
            before_reveal()
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:05Z'):
            disclosed = research.reveal(f.store, req['request_id'], first['independent_id'], cycle.candidate(f.draft))
        output = {'candidate_hash': disclosed['candidate_hash'], 'approved': approved,
            'coverage': coverage if coverage is not None else {k: 'Synthetic reviewed ' + k + ' and its limits.' for k in cycle.COVERAGE},
            'counterevidence': ['Synthetic competing liquidity explanation.'], 'unresolved': ['Unknown actual fees remain unknown.'],
            'reopen_conditions': spec['reopen_conditions'], 'recheck_at': '2030-03-02T13:00:00Z'}
        if authored:
            output['report_narrative'] = f.draft['conclusion']['narrative']
        analysis = json.dumps(output)
        if wrapper:
            analysis = wrapper(analysis)
        turn('SECOND', 'candidate', analysis, disclosed['candidate_id'])
        result_spec = {'request_id': req['request_id'], 'candidate_id': disclosed['candidate_id'],
            'agent_id': 'SYNTHETIC_CYCLE_AGENT', 'rollout_path': str(path), 'turn_id': 'SECOND',
            'coverage': list(output['coverage'].values()), 'counterevidence': output['counterevidence'],
            'unresolved': output['unresolved'], 'reopen_conditions': output['reopen_conditions'], 'analysis': analysis}
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:10Z'):
            saved = research.result(f.store, result_spec)
        f.draft['research_absorptions'] = [{'issue_id': saved['issue_id'], 'request_id': req['request_id'],
            'result_id': saved['result_id'], 'decision': 'ADOPT', 'rationale': 'All mapped synthetic cycle sections match the actual final review.',
            'assessment_refs': cycle.SCOPE}]
        self.d.bind()
        return req, saved, result_spec

    def observe(self, finding='NO_MATERIAL_CHANGE', required=False, extra=None, applicability='VALID'):
        f = self.f
        ctx = sensing.context(f.store, f.project / '.strategy/market_observation.sqlite3', f.project, f.raw, evidence_dir=f.evidence_dir)
        review = {'schema': 'BTC_SENSING_REVIEW_V4', 'revision': 4, 'context_id': ctx['context_id'],
            'market_review': market_fixture.review(f.raw, {'context_id': ctx['context_id'], 'baselines': {}}, required=required),
            'recommendation_checks': [{'side': side, 'findings': [finding], 'refs': ['windows.4h.binance_spot_1h'],
                'explanation': 'Synthetic verification of the adopted downside conditions.'} for side in ('upper_short','lower_spot')],
            'extra_findings': extra or [], 'main_evaluation': {'required': False, 'reasons': []}, 'research_findings': [],
            'factor_checks': {'items': [], 'summary': 'Synthetic observations, cycle judgment retained.'},
            'applicability_review': {'basis_result_id': ctx['cycle_baseline']['result_id'],
                'outcome': applicability if ctx['cycle_baseline']['result_id'] else 'REVISE',
                'explanation': 'Synthetic explicit assessment of the full thesis and cumulative evidence.',
                'refs': [{'source_id': 'hourly', 'pointer': '/datasets', 'value_hash': digest(f.raw['datasets'])}],
                'limitations': 'Synthetic account uncertainty is unchanged; no fill or account safety inferred.'}}
        return sensing.record(f.store, f.project / '.strategy/market_observation.sqlite3', f.project, f.raw, review, evidence_dir=f.evidence_dir)

    def test_initial_missing_baseline_requests_deep_and_preserves_legacy_current(self):
        observed = self.observe()
        self.assertTrue(observed['deep_eligible'])
        self.assertEqual(storage.current(self.f.store)['decision_id'], self.initial['decision_id'])
        self.f.draft['conclusion']['narrative'] = 'Unreviewed new cycle interpretation.'
        self.d.bind()
        with self.assertRaisesRegex(Invalid, 'adopted actual Astra'):
            self.d.commit()

    def test_actual_compact_review_binds_frozen_body_and_publishes_complete_report(self):
        def compact(raw):
            value = json.loads(raw)
            value['report_hash'] = digest(value.pop('report_narrative'))
            return json.dumps(value)
        _, saved, _ = self.study(authored=True, wrapper=compact)
        narrative = self.f.draft['conclusion']['narrative']
        self.assertNotIn('report_narrative', json.loads(saved['analysis']))
        self.assertEqual(saved['cycle_review']['report_narrative'], narrative)
        published = self.d.commit()
        baseline = cycle.baseline(self.f.store, published['assessment'])
        self.assertEqual(baseline['status'], 'CURRENT')
        self.assertEqual(baseline['report_narrative'], narrative)
        self.assertEqual(baseline['report_hash'], digest(narrative))

    def test_compact_review_rejects_different_report_hash(self):
        def different(raw):
            value = json.loads(raw)
            value.pop('report_narrative')
            value['report_hash'] = digest('A different unreviewed report.')
            return json.dumps(value)
        with self.assertRaisesRegex(Invalid, 'report hash differs'):
            self.study(authored=True, wrapper=different)

    def test_review_cannot_supply_both_report_text_and_hash(self):
        def both(raw):
            value = json.loads(raw)
            value['report_hash'] = digest(value['report_narrative'])
            return json.dumps(value)
        with self.assertRaisesRegex(Invalid, 'choose report text or hash'):
            self.study(authored=True, wrapper=both)

    def test_changed_preferences_are_reviewed_within_same_study_and_old_input_retained(self):
        def update_preferences():
            (self.f.project/'资产配置.md').write_text('Synthetic updated three-income preferences; no fill.\n')
            self.f.draft['fact_version'] = facts(self.f.project)
            owners = self.f.draft['domain']['account_facts']['fact_owner_fingerprints']
            owners['ASSET_CONFIGURATION'] = self.f.draft['fact_version']['资产配置.md']
            self.f.draft['domain']['account_facts'].update(
                account_fact_changed_known=False,
                known_change_evidence=[{'confirmation_type': 'USER_CONFIRMED_PREFERENCE_CHANGE',
                                        'owner_id': 'ASSET_CONFIGURATION'}],
                local_record_status='PREFERENCE_RECORDED')
            self.f.draft['domain']['user_input_requirement'] = 'NONE'
            self.f.draft['conclusion']['narrative'] = 'Synthetic report on equity earnings, crypto cycles and repricing with changed preferences.'
        req, saved, _ = self.study(authored=True, before_reveal=update_preferences)
        self.assertNotEqual(req['fact_version'], saved['fact_version'])
        self.assertEqual(saved['fact_version'], facts(self.f.project))
        self.assertEqual(len(list((self.f.store/'research/requests').glob('*.json'))), 1)
        proof = research.read_input(self.f.store, req['request_id'], 'candidate', saved['candidate_id'])
        self.assertIn('no fill', proof['payload']['fact_context']['files']['资产配置.md']['text'])
        cycle.validate_publication(self.f.store, self.initial, self.f.draft)
        self.assertEqual(cycle.baseline(self.f.store, self.f.draft)['status'], 'CURRENT')
        published = self.d.commit()
        self.assertEqual(published['assessment']['fact_version'], facts(self.f.project))
        self.assertEqual(published['assessment']['domain']['user_input_requirement'], 'NONE')
        old = read(self.f.store/req['independent_input_path'])
        self.assertEqual(old['fact_files']['资产配置.md']['hash'], req['fact_version']['资产配置.md'])
        (self.f.project/'资产配置.md').write_text('Another synthetic preference change after approval.\n')
        self.assertEqual(research.read_input(self.f.store, req['request_id'], 'candidate', saved['candidate_id']), proof)
        changed = deepcopy(self.f.draft)
        changed['fact_version'] = facts(self.f.project)
        with self.assertRaisesRegex(Invalid, 'current confirmed facts'):
            cycle.validate_publication(self.f.store, self.initial, changed)

    def test_final_fact_version_cannot_be_invented(self):
        def forged_version():
            self.f.draft['fact_version']['资产配置.md'] = digest('Not the actual local content')
        with self.assertRaisesRegex(Invalid, 'facts changed before final review'):
            self.study(before_reveal=forged_version)

    def test_later_cycle_candidate_requires_bound_supplementary_evidence(self):
        req, saved, _ = self.study()
        value = cycle.candidate(self.f.draft)
        value['assessment']['temporal_scope']['knowledge_cutoff'] = '2030-03-01T13:03:00Z'
        with mock.patch('v4.research.now', return_value='2030-03-01T13:04:00Z'):
            with self.assertRaisesRegex(Invalid, 'requires --additional'):
                research.reveal(self.f.store, req['request_id'], saved['independent_id'], value)
            with self.assertRaisesRegex(Invalid, 'clocks must match'):
                research.reveal(self.f.store, req['request_id'], saved['independent_id'], value,
                               additional=self.f.evidence_dir)

    def test_candidate_rejects_recommendation_start_after_its_knowledge_clock_before_review(self):
        req, saved, _ = self.study()
        value = cycle.candidate(self.f.draft)
        value['assessment']['recommendations']['lower_spot']['validity']['from'] = '2030-03-01T13:03:30Z'
        count = len(list((self.f.store / 'research' / 'candidates').glob('*.json')))
        # This is already in the past at disclosure, but is still later than
        # the frozen knowledge cutoff. Publication time is a separate clock.
        with mock.patch('v4.research.now', return_value='2030-03-01T13:04:00Z'):
            with self.assertRaisesRegex(Invalid, 'future-start recommendation'):
                research.reveal(self.f.store, req['request_id'], saved['independent_id'], value)
        self.assertEqual(len(list((self.f.store / 'research' / 'candidates').glob('*.json'))), count)
        value['assessment']['recommendations']['lower_spot']['validity']['from'] = value['assessment']['temporal_scope']['knowledge_cutoff']
        cycle.validate_candidate(value)

    def test_final_deadline_must_follow_supplement_not_only_original_request(self):
        req, saved, spec = self.study()
        disclosed = research._load(self.f.store, 'candidates', spec['candidate_id'])
        disclosed['evidence_context'] = {'knowledge_cutoff': '2030-03-03T13:00:00Z'}
        with self.assertRaisesRegex(Invalid, 'deadline must follow'):
            cycle.review_output(req, disclosed, spec['analysis'], spec)

    def test_malformed_analysis_is_rejected_before_candidate_review(self):
        req, saved, _ = self.study()
        original = cycle.candidate(self.f.draft)
        count = len(list((self.f.store/'research/candidates').glob('*.json')))
        bad_values = []
        bad = deepcopy(original)
        bad['assessment']['outlook']['semantic']['next_evidence'].append('24H_NEW_HOUR')
        bad_values.append(bad)
        bad = deepcopy(original)
        conditions = bad['assessment']['robustness']['semantic']['adjustment_conditions']
        conditions.append(conditions[0])
        bad_values.append(bad)
        bad = deepcopy(original)
        del bad['assessment']['portfolio_analysis']['new_spot']
        bad_values.append(bad)
        for value in bad_values:
            with self.subTest(value=value):
                with mock.patch('v4.research.now', return_value='2030-03-01T13:04:00Z'):
                    with self.assertRaises(Invalid):
                        research.reveal(self.f.store, req['request_id'], saved['independent_id'], value)
        self.assertEqual(len(list((self.f.store/'research/candidates').glob('*.json'))), count)
        cycle.validate_candidate(original)

    def test_only_real_ordered_review_can_publish_the_approved_mapping(self):
        _, saved, spec = self.study()
        value = self.d.commit()
        basis = cycle.baseline(self.f.store, value['assessment'])
        self.assertEqual(basis['result_id'], saved['result_id'])
        self.assertFalse((self.f.store / 'deep-current.json').exists())
        self.assertEqual(value['assessment']['provenance']['model'], 'gpt-6.1-sol')
        with self.assertRaisesRegex(Invalid, 'actual agent output'):
            research.result(self.f.store, dict(spec, analysis='Forged approval.'))

    def test_domain_adapter_requires_the_actual_approved_unchanged_mapping(self):
        initial = main_flow.domain_validation_baseline(self.f.store, storage.current(self.f.store), self.f.draft)
        self.assertNotIn('_cycle_recheck_at', initial)
        unreviewed = deepcopy(self.f.draft)
        unreviewed['conclusion']['narrative'] += ' An unreviewed change.'
        with self.assertRaisesRegex(Invalid, 'adopted actual Astra'):
            main_flow.domain_validation_baseline(self.f.store, storage.current(self.f.store), unreviewed)
        _, saved, _ = self.study()
        value = main_flow.domain_validation_baseline(self.f.store, storage.current(self.f.store), self.f.draft)
        self.assertEqual(value['_cycle_recheck_at'], saved['cycle_review']['recheck_at'])
        changed = deepcopy(self.f.draft)
        changed['conclusion']['narrative'] += ' An unreviewed change.'
        with self.assertRaisesRegex(Invalid, 'differs from the candidate'):
            main_flow.domain_validation_baseline(self.f.store, storage.current(self.f.store), changed)

    def test_price_touch_and_support_weakening_stay_in_baseline(self):
        self.study(); self.d.commit()
        before = cycle.baseline(self.f.store, storage.current(self.f.store)['assessment'])
        observed = self.observe('SUPPORT_WEAKENED')
        self.assertIn('upper_short:PRICE_TOUCH_REVIEW', observed['triggers'])
        self.assertFalse(observed['deep_eligible'])
        ctx = self.d.bind()
        self.assertEqual(ctx['accumulation']['start'], before['market_cutoff'])
        self.assertEqual(ctx['accumulation']['baseline_role'], 'ADOPTED_ASTRA_CYCLE')
        self.d.commit()
        self.assertEqual(cycle.baseline(self.f.store, storage.current(self.f.store)['assessment'])['completed_at'], before['completed_at'])

    def test_flat_price_material_counterevidence_reopens_cycle(self):
        self.study(); self.d.commit()
        self.assertTrue(self.observe('COUNTEREVIDENCE')['deep_eligible'])
        self.assertEqual(reporting.context(self.f.store)['cycle_baseline']['status'], 'REASSESSMENT_PENDING')

    def test_quiet_next_observation_cannot_clear_unresolved_material_finding(self):
        self.study(); self.d.commit()
        self.observe('COUNTEREVIDENCE')
        later = self.observe()
        self.assertTrue(later['deep_eligible'])
        basis = reporting.context(self.f.store)['cycle_baseline']
        self.assertEqual(basis['status'], 'REASSESSMENT_PENDING')
        self.assertIn('upper_short:MATERIAL_REASSESSMENT', basis['pending_reasons'])

    def test_confirmed_spot_reduction_keeps_hedge_size_review_pending(self):
        self.study(); self.d.commit(); self.observe()
        before = deepcopy(storage.current(self.f.store))
        record = self.f.project/'操作记录.md'
        record.write_text(record.read_text() + 'Synthetic user-confirmed spot reduction; protective short unchanged; exact net exposure and costs unknown.\n')
        review = self.observe('AMOUNT_BASIS_CHANGED')
        self.assertIn('FACTS_CHANGED', review['triggers'])
        self.assertTrue(review['deep_eligible'])
        self.assertEqual(storage.current(self.f.store), before)
        self.assertTrue(self.observe()['deep_eligible'])
        basis = reporting.context(self.f.store)['cycle_baseline']
        self.assertEqual(basis['status'], 'FACTS_CHANGED')
        self.assertNotEqual(facts(self.f.project), before['assessment']['fact_version'])

    def test_uncovered_factor_routes_cycle_with_unchanged_market_and_amounts(self):
        self.study(); self.d.commit(); quiet=self.observe()
        f=self.f;before=storage.current(f.store)['decision_id']
        ctx=sensing.context(f.store,f.project/'.strategy/market_observation.sqlite3',f.project,f.raw,evidence_dir=f.evidence_dir)
        review=deepcopy(sensing.observation(f.store,quiet['observation_id'])['review'])
        review['context_id']=ctx['context_id']
        review['market_review']['context_id']=ctx['context_id']
        review['research_findings']=[{'issue_key':cycle.ISSUE_KEY,'scope':cycle.SCOPE,
            'question':'Does a newly identified coverage gap change the approved future-value assumptions?',
            'material_change':True,'change_kind':'NEW_KEY_EVIDENCE','change_reason':'The model identified a material omission without claiming a price direction.',
            'refs':[{'source_id':'hourly','pointer':'/windows','value_hash':digest(f.raw['windows'])}],
            'reopen_condition':None}]
        value=sensing.record(f.store,f.project/'.strategy/market_observation.sqlite3',f.project,f.raw,review,evidence_dir=f.evidence_dir)
        self.assertTrue(value['deep_eligible'])
        self.assertEqual(storage.current(f.store)['decision_id'],before)
        self.assertIn('RESEARCH_FINDING',sensing.gate(f.store,value['observation_id'])['gate']['cycle_review']['reasons'])

    def test_request_before_observation_remains_pending_until_adopted(self):
        req, saved, _ = self.study(); self.d.commit(); self.observe()
        spec = {k: deepcopy(req[k]) for k in ('issue_key','scope','question','decision_id','evidence_id','change','reopen_conditions')}
        spec['decision_id'] = storage.current(self.f.store)['decision_id']
        spec['change'].update(kind='NEW_KEY_EVIDENCE', refs=[{
            'source_id': 'hourly', 'pointer': '/windows', 'value_hash': digest(self.f.raw['windows'])}])
        with mock.patch('v4.research.now', return_value='2030-03-01T13:04:00Z'):
            pending = research.request(self.f.store, self.f.project, spec)
        basis = cycle.baseline(self.f.store, storage.current(self.f.store)['assessment'])
        self.assertEqual(basis['status'], 'REASSESSMENT_PENDING')
        self.assertIn('UNADOPTED_CYCLE_REQUEST', basis['pending_reasons'])
        self.assertTrue(self.observe()['deep_eligible'])
        self.assertEqual(cycle.pending_reassessment(self.f.store,
            dict(saved, request_id=pending['request_id'], result_id='sha256:' + 'f' * 64)), [])

    def test_equity_counterevidence_persists_without_crypto_price_change(self):
        self.study(); self.d.commit(); quiet = self.observe()
        f = self.f; before = deepcopy(storage.current(f.store))
        spec = {k:deepcopy(v) for k,v in f.evidence.items() if k not in ('schema','evidence_id')}
        spec['parent_id'] = None
        files = {s['id']:str(f.evidence_dir/'raw'/(s['raw_hash'][7:]+'.json')) for s in spec['sources']}
        release = f.base/'synthetic-equity-release.json'
        payload = {'asset':'NASDAQ100', 'period':'SYNTHETIC_QUARTER', 'earnings_revision':'DOWNWARD'}
        replace(release, payload)
        source = dict(spec['sources'][0], id='synthetic-equity-release', trust='PUBLIC_RAW',
            origin=evidence.canonical_origin('https://www.nasdaq.com/synthetic-release'),
            url='https://www.nasdaq.com/synthetic-release', raw_hash=digest(release.read_bytes()))
        spec['sources'].append(source); files[source['id']] = str(release)
        directory = f.base/'equity-evidence'
        evidence.freeze(spec, files, directory)
        ref = {'source_id':source['id'], 'pointer':'/earnings_revision', 'value_hash':digest(payload['earnings_revision'])}
        ctx = sensing.context(f.store,f.project/'.strategy/market_observation.sqlite3',f.project,f.raw,evidence_dir=directory)
        review = deepcopy(sensing.observation(f.store,quiet['observation_id'])['review'])
        review['context_id'] = review['market_review']['context_id'] = ctx['context_id']
        review['factor_checks'] = {'items':[{'id':'EQUITY_EARNINGS', 'name':'合成美股盈利反证',
            'assessment':'美股原盈利依据受损，已有持仓减仓与恢复投入需重评；不能据此改变BTC价位。',
            'change_reason':'新的美股原始证据，BTC行情包未变。', 'refs':[ref], 'counter_refs':[]}],
            'summary':'按美股自身证据发现原核心报告的重要反证。'}
        review['research_findings'] = [{'issue_key':cycle.ISSUE_KEY,'scope':cycle.SCOPE,
            'question':'Reassess equity reduction and reinvestment conditions after synthetic earnings counterevidence.',
            'material_change':True,'change_kind':'NEW_KEY_EVIDENCE','change_reason':'Equity thesis changed with unchanged BTC prices.',
            'refs':[ref],'reopen_condition':None}]
        observed = sensing.record(f.store,f.project/'.strategy/market_observation.sqlite3',f.project,f.raw,review,evidence_dir=directory)
        self.assertTrue(observed['deep_eligible'])
        self.assertEqual(storage.current(f.store), before)
        self.assertEqual(sensing.observation(f.store,observed['observation_id'])['review']['factor_checks']['items'][0]['refs'], [ref])
        f.evidence_dir = directory
        self.assertTrue(self.observe()['deep_eligible'])
        self.assertEqual(reporting.context(f.store)['cycle_baseline']['status'], 'REASSESSMENT_PENDING')

    def test_old_observation_cannot_invalidate_different_adopted_result(self):
        _, saved, _ = self.study(); self.d.commit(); self.observe('COUNTEREVIDENCE')
        self.assertEqual(cycle.pending_reassessment(self.f.store,
            dict(saved, result_id='sha256:' + 'f' * 64)), [])

    def test_independent_input_carries_factor_coverage_without_sol_findings(self):
        req, result, _ = self.study()
        packet = read(self.f.store / req['independent_input_path'])
        old_assessment = storage.current(self.f.store)['assessment']
        self.assertEqual(packet['historical_anchor']['temporal_scope'],old_assessment['temporal_scope'])
        for section,record in packet['historical_anchor']['section_provenance'].items():
            self.assertNotIn('explanation',record)
            for key,value in record.items():
                self.assertEqual(value,old_assessment['section_provenance'][section][key])
        self.assertEqual(set(packet['cycle_assignment']['required_coverage']), set(cycle.COVERAGE))
        self.assertIn(cycle.market_state.CYCLE_FOCUS_REVIEW, packet['cycle_assignment']['instructions'])
        self.assertFalse(set(cycle.SECTIONS) & set(packet['historical_anchor']))
        self.assertEqual(packet['cycle_assignment']['method_references'], cycle.method_references('research', self.f.project))
        disclosed = research._load(self.f.store, 'candidates', result['candidate_id'])
        self.assertEqual(disclosed['review_assignment']['method_references'], cycle.method_references('mapping'))
        for stage in ('research', 'mapping', 'observe', 'report'):
            for path in cycle.method_references(stage).values():
                self.assertTrue(Path(path.split('#')[0]).is_file(), path)
        self.assertNotIn('factor_checks', packet)
        self.assertNotIn('research_findings', packet)

    def manual_spec(self, original):
        spec = {k: deepcopy(original[k]) for k in
            ('issue_key', 'scope', 'question', 'decision_id', 'evidence_id', 'change', 'reopen_conditions')}
        spec['decision_id'] = storage.current(self.f.store)['decision_id']
        spec['change'].update(kind='USER_REQUESTED_REVIEW', reason='Synthetic explicit method upgrade review.')
        spec['user_request'] = {'reference': 'synthetic-user-message:method-upgrade',
                                'reason': 'The user explicitly requested a complete new review.'}
        return spec

    def test_manual_review_allows_existing_evidence_and_retries_after_publication(self):
        old, _, _ = self.study(); self.d.commit()
        spec = self.manual_spec(old)
        req = research.request(self.f.store, self.f.project, spec)
        self.assertNotEqual(req['request_id'], old['request_id'])
        stale = dict(spec, decision_id=old['decision_id'])
        repeated = research.request(self.f.store, self.f.project, stale)
        self.assertTrue(repeated['reused'])
        self.assertEqual(repeated['request_id'], req['request_id'])
        self.assertEqual(len(list((self.f.store / 'research/requests').glob('*.json'))), 2)
        # Recover if the process stopped after saving the request but before
        # recording its user-message binding.
        next((self.f.store / 'research/user_requests').glob('*.json')).unlink()
        self.assertEqual(research.request(self.f.store, self.f.project, stale)['request_id'], req['request_id'])

    def test_manual_request_resumes_unadopted_study_and_keeps_binding_after_adoption(self):
        old, _, _ = self.study()
        spec = self.manual_spec(old)
        resumed = research.request(self.f.store, self.f.project, spec)
        self.assertEqual(resumed['request_id'], old['request_id'])
        self.assertEqual(resumed['progress']['stage'], 'RESULT_RECORDED')
        self.d.commit()
        repeated = research.request(self.f.store, self.f.project, spec)
        self.assertEqual(repeated['request_id'], old['request_id'])
        self.assertEqual(repeated['next_action'], 'READ_ORIGINAL_STUDY_AND_CURRENT_APPLICABILITY')
        self.assertEqual(len(list((self.f.store / 'research/requests').glob('*.json'))), 1)

    def test_manual_review_requires_explicit_reference_without_changing_normal_requests(self):
        old, _, _ = self.study()
        valid = self.manual_spec(old)
        for edit in ('missing', 'blank', 'ordinary'):
            spec = deepcopy(valid)
            if edit == 'missing':
                spec.pop('user_request')
            elif edit == 'blank':
                spec['user_request']['reference'] = ' '
            else:
                spec['change']['kind'] = 'NEW_KEY_EVIDENCE'
            with self.subTest(edit=edit), self.assertRaises(Invalid):
                research.request(self.f.store, self.f.project, spec)

    def test_manual_request_resumes_rejected_candidate_with_original_astra(self):
        old, _, _ = self.study(approved=False)
        resumed = research.request(self.f.store, self.f.project, self.manual_spec(old))
        self.assertEqual(resumed['request_id'], old['request_id'])
        self.assertFalse(resumed['progress']['cycle_approved'])
        self.assertEqual(resumed['next_action'], 'RESUME_ORIGINAL_ASTRA_CANDIDATE_REVISION')
        self.assertEqual(len(list((self.f.store / 'research/requests').glob('*.json'))), 1)

    def test_manual_evidence_cannot_be_reopened_as_new_hourly_evidence(self):
        old, _, _ = self.study(user_reference='synthetic-user:initial-review'); self.d.commit()
        spec = self.manual_spec(old)
        automatic = deepcopy(spec)
        automatic.pop('user_request')
        automatic['change']['kind'] = 'NEW_KEY_EVIDENCE'
        with self.assertRaisesRegex(Invalid, 'same issue and key evidence'):
            research.request(self.f.store, self.f.project, automatic)
        # A different explicit user request remains an allowed review reason.
        self.assertNotEqual(research.request(self.f.store, self.f.project, spec)['request_id'], old['request_id'])

    def test_hourly_findings_cannot_invent_an_explicit_user_request(self):
        self.study()
        finding = {'issue_key': cycle.ISSUE_KEY, 'scope': cycle.SCOPE,
            'question': 'Synthetic hourly finding.', 'material_change': True,
            'change_kind': 'USER_REQUESTED_REVIEW', 'change_reason': 'An hour elapsed.',
            'refs': ['synthetic-raw'], 'reopen_condition': None}
        with self.assertRaisesRegex(Invalid, 'specific discriminating change'):
            research.findings(self.f.store, [finding], {'synthetic-raw'})

    def test_approved_result_cannot_be_reused_for_different_levels_or_prose(self):
        self.study(); self.d.commit(); self.d.bind()
        self.f.draft['conclusion']['narrative'] = 'A different downside target hidden in prose.'
        with self.assertRaisesRegex(Invalid, 'candidate actually approved'):
            self.d.commit()

    def test_rejected_or_incomplete_deep_result_cannot_publish(self):
        self.study(approved=False)
        with self.assertRaisesRegex(Invalid, 'approved actual Astra'):
            self.d.commit()

    def test_missing_coverage_cannot_be_completed_by_sol(self):
        with self.assertRaisesRegex(Invalid, 'cycle research coverage'):
            self.study(coverage={'cycle_structure': 'Incomplete synthetic cycle study.'})

    def test_actual_fact_change_requires_portfolio_impact_review(self):
        self.study(); self.d.commit()
        (self.f.project / '资产配置.md').write_text('A new user-confirmed synthetic asset fact.\n')
        self.assertTrue(self.observe()['deep_eligible'])

    def test_fact_change_can_report_limits_before_new_deep_publication(self):
        self.study(); self.d.commit()
        before = storage.current(self.f.store)['decision_id']
        (self.f.project / '资产配置.md').write_text('A new user-confirmed synthetic asset fact.\n')
        self.observe()
        ctx = reporting.context(self.f.store)
        self.assertEqual(ctx['cycle_baseline']['status'], 'FACTS_CHANGED')
        spec = {'schema': 'BTC_REPORT_REVIEW_V1', 'context_id': ctx['context_id'], 'action': 'REPORT',
            'purposes': ['MARKET_SUMMARY'], 'reason': '已确认事实有变化，需及时说明周期方案的适用性限制。',
            'next_observation': '补齐深评对新事实的组合影响，完成后再更新周期建议。',
            'baseline_review': '已核验历史周期结论，新的事实影响尚未完成深评。',
            'applicability_review': '保留历史结构参考，并明确旧建议尚未适配新的事实。',
            'information': {'fact_limitations': '资产记录已变化，旧建议的组合影响待深评核验。'},
            'provenance': self.f.draft['provenance'],
            'layout': {'format': 'MARKET_MOBILE_V1', 'lead': '周期方案正在复核，暂缓采用旧的新增建议。',
                'blocks': [{'kind': 'limitation', 'text': '已确认的资产记录发生变化，需要重新核验回撤承受和资金占用。'}],
                'include_recommendations': False,
                'quality_review': {k: '核对历史周期依据、已确认风险和事实变化的实际限制，保留补查条件。'
                    for k in cycle.REPORT_CHECKS | {'decision_and_advice','reasoning','necessary_numbers','material_risk','phone_readability'}}}}
        result = reporting.record(self.f.store, spec)
        self.assertIn('暂缓采用', reporting.source(self.f.store, result['review_id'])['body'])
        self.assertEqual(storage.current(self.f.store)['decision_id'], before)

    def test_pending_fact_review_can_record_limits_without_forcing_display(self):
        self.study();self.d.commit()
        before=storage.current(self.f.store)
        (self.f.project/'资产配置.md').write_text('Synthetic user-confirmed definition change; no new trade.\n')
        self.observe();ctx=reporting.context(self.f.store)
        self.assertEqual(ctx['cycle_baseline']['status'],'FACTS_CHANGED')
        spec={'schema':'BTC_REPORT_REVIEW_V1','context_id':ctx['context_id'],'action':'EVIDENCE_LIMITED',
              'purposes':[],'reason':'文件定义修订的绑定差异正在原研究审阅，本轮没有尚未展示的重要风险。',
              'next_observation':'接续同一研究完成组合适配，重要市场风险仍独立报告。',
              'baseline_review':'真实正文已包含当前风险，不以内部记录代替已展示依据。',
              'applicability_review':'实际核对为定义修订而非新成交，正式文件绑定仍待本轮深评采纳。',
              'information':{},'layout':None,'provenance':self.f.draft['provenance']}
        result=reporting.record(self.f.store,spec)
        self.assertEqual(result['action'],'EVIDENCE_LIMITED')
        self.assertEqual(storage.current(self.f.store),before)
        self.assertEqual(reporting.context(self.f.store)['cycle_baseline']['status'],'FACTS_CHANGED')
        self.assertFalse((self.f.store/'reported-current.json').exists())
        with self.assertRaisesRegex(Invalid,'did not request a report'):
            reporting.source(self.f.store,result['review_id'])
        spec['action']='SILENT'
        with self.assertRaisesRegex(Invalid,'rather than confirmed market silence'):
            reporting.record(self.f.store,spec)

    def test_deep_deadline_changes_report_binding_without_new_hour(self):
        self.study(); self.d.commit(); self.observe()
        before = reporting.context(self.f.store)
        with mock.patch('v4.reporting.now', return_value='2030-03-02T13:01:00Z'):
            after = reporting.context(self.f.store)
        self.assertNotEqual(before['context_id'], after['context_id'])
        self.assertEqual(after['cycle_baseline']['status'], 'REVIEW_DUE')

    def test_cycle_report_binds_downside_levels_and_purpose_to_approved_analysis(self):
        # Deliberately synthetic levels, unrelated to current market prices.
        self.f.draft['conclusion']['business_meaning']['downside'] = '若100 USDT支撑失守且承接未恢复，下一回撤观察区为90—95 USDT；它是风险区间，触及后仍需核验。'
        self.study(); self.d.commit(); self.observe()
        ctx = reporting.context(self.f.store)
        spec = {'schema': 'BTC_REPORT_REVIEW_V1', 'context_id': ctx['context_id'], 'action': 'REPORT',
            'purposes': ['MARKET_SUMMARY'], 'reason': '本轮周期下行区间与其成立条件需要向读者明确说明。',
            'next_observation': '继续核验周期支撑及现货承接，条件受损时重开深评。',
            'baseline_review': '已经读取已采纳深评，所述下行区间和条件与批准结果一致。',
            'applicability_review': '当前证据仍在批准情景覆盖内，点位只作为条件风险分析。',
            'information': {'cycle': '周期修复候选仍待确认，继续验证回撤风险。'},
            'provenance': self.f.draft['provenance'],
            'layout': {'format': 'MARKET_MOBILE_V1', 'lead': '周期修复仍待确认，继续保留现金应对回撤。',
                'blocks': [{'kind': 'condition', 'text': '{{/assessment/conclusion/business_meaning/downside}}'}],
                'include_recommendations': False,
                'quality_review': {k: '核对周期来源、下行区间及其用途，价格和条件与已批准分析一致。'
                    for k in cycle.REPORT_CHECKS | {'decision_and_advice','reasoning','necessary_numbers','material_risk','phone_readability'}}}}
        result = reporting.record(self.f.store, spec)
        body = reporting.source(self.f.store, result['review_id'])['body']
        self.assertIn('90—95 USDT', body)
        self.assertIn('风险区间，触及后仍需核验', body)

    def test_due_cycle_review_verifies_without_reopening_or_rolling_deadline(self):
        req, saved, _ = self.study(); self.d.commit()
        changed = {k: deepcopy(req[k]) for k in ('issue_key','scope','question','decision_id','evidence_id','change','reopen_conditions')}
        changed['decision_id'] = storage.current(self.f.store)['decision_id']
        changed['change'].update(kind='REOPEN_CONDITION_MET', reopen_condition='RECHECK_AT:' + saved['cycle_review']['recheck_at'])
        with mock.patch('v4.cycle.now', return_value='2030-03-02T13:01:00Z'):
            for _ in range(3):
                result = self.observe()
                self.assertFalse(result['deep_eligible'])
                self.assertFalse(result['main_eligible'])
            with self.assertRaisesRegex(Invalid, 'applicability verification'):
                research.request(self.f.store, self.f.project, changed)
        self.assertEqual(len(list((self.f.store/'research/requests').glob('*.json'))), 1)
        self.assertEqual(cycle.adopted(self.f.store, storage.current(self.f.store)['assessment'])[0]['cycle_review']['recheck_at'], saved['cycle_review']['recheck_at'])

    def test_due_material_counterevidence_still_requires_research(self):
        self.study(); self.d.commit()
        with mock.patch('v4.cycle.now', return_value='2030-03-02T13:01:00Z'):
            self.assertTrue(self.observe(finding='COUNTEREVIDENCE', required=True)['deep_eligible'])

    def test_multiple_due_hours_unpublished_data_then_material_event_or_omission(self):
        self.study(); self.d.commit()
        for hour in ('14','15','16'):
            with mock.patch('v4.cycle.now', return_value='2030-03-02T'+hour+':00:00Z'):
                observed = self.observe(extra=[{'summary':'Scheduled source remains unpublished; no new material evidence.',
                    'refs':['windows.4h.binance_spot_1h'], 'material':False}])
                self.assertFalse(observed['deep_eligible'])
                self.assertFalse(observed['main_eligible'])
        for reason in ('New material event changes the value thesis.', 'Previously omitted critical evidence affects downside.'):
            with self.subTest(reason=reason):
                observed = self.observe(extra=[{'summary':reason,'refs':['windows.4h.binance_spot_1h'],'material':True}])
                self.assertTrue(observed['deep_eligible'])
        self.assertEqual(len(list((self.f.store/'research/requests').glob('*.json'))),1)

    def test_due_verification_preserves_author_and_real_advice_expiry(self):
        _, saved, _ = self.study(authored=True); self.d.commit()
        before = deepcopy(storage.current(self.f.store))
        stamp='2030-03-02T13:01:00Z'
        with mock.patch('v4.cycle.now', return_value=stamp), mock.patch('v4.sensing.now', return_value=stamp):
            self.assertEqual(cycle.baseline(self.f.store, before['assessment'])['status'], 'REVIEW_DUE')
            self.observe()
            basis = cycle.baseline(self.f.store, before['assessment'])
            self.assertEqual(basis['status'], 'REVIEW_DUE')  # Old raw data cannot refresh applicability.
            fresh = deepcopy(self.f.raw)
            for retrieval in fresh['retrievals'].values():
                retrieval['retrieved_at'] = stamp
            with mock.patch('v4.reporting.raw_for_observation', return_value=fresh):
                basis = cycle.baseline(self.f.store, before['assessment'])
            self.assertEqual(basis['status'], 'CURRENT')
            self.assertIsNotNone(basis['applicability_verification'])
            self.assertEqual(basis['completed_at'], saved['completed_at'])
        self.assertEqual(storage.current(self.f.store), before)

    def test_unadopted_study_resumes_the_existing_candidate_instead_of_new_request(self):
        req, saved, _ = self.study()
        spec={k:deepcopy(req[k]) for k in ('issue_key','scope','question','decision_id','evidence_id','change','reopen_conditions')}
        result=research.request(self.f.store,self.f.project,spec)
        self.assertTrue(result['reused'])
        self.assertEqual(result['request_id'], req['request_id'])
        self.assertEqual(result['progress']['candidate_id'], saved['candidate_id'])
        self.assertEqual(len(list((self.f.store/'research/requests').glob('*.json'))),1)

    def test_missing_explicit_review_cannot_renew_applicability(self):
        _, saved, _ = self.study(); self.d.commit()
        ctx = {'cycle_baseline': {'status': 'CURRENT', 'result_id': saved['result_id']}}
        review = {'market_review': {'deep_review': {'required': False}},
                  'recommendation_checks': [], 'extra_findings': []}
        gate = cycle.review_gate(self.f.store, ctx, review, [])
        self.assertFalse(gate['required'])
        self.assertFalse(gate['verification_completed'])
        observed = self.observe()
        authored = deepcopy(sensing.observation(self.f.store, observed['observation_id'])['review'])
        del authored['applicability_review']
        new_ctx = sensing.context(self.f.store, self.f.project/'.strategy/market_observation.sqlite3',
                                  self.f.project, self.f.raw, evidence_dir=self.f.evidence_dir)
        authored['context_id'] = authored['market_review']['context_id'] = new_ctx['context_id']
        with self.assertRaisesRegex(Invalid, 'applicability_review'):
            sensing.record(self.f.store, self.f.project/'.strategy/market_observation.sqlite3',
                           self.f.project, self.f.raw, authored, evidence_dir=self.f.evidence_dir)

    def test_limited_evidence_is_not_valid_or_automatic_full_research(self):
        self.study(authored=True); self.d.commit()
        first = self.observe(applicability='EVIDENCE_LIMITED')
        gate = sensing.observation(self.f.store, first['observation_id'])['gate']['cycle_review']
        self.assertFalse(gate['required'])
        self.assertFalse(gate['verification_completed'])
        self.assertEqual(cycle.baseline(self.f.store, storage.current(self.f.store)['assessment'])['status'], 'EVIDENCE_LIMITED')
        second = self.observe(applicability='EVIDENCE_LIMITED')
        self.assertFalse(second['deep_eligible'])
        self.observe(applicability='VALID')
        self.assertEqual(cycle.baseline(self.f.store, storage.current(self.f.store)['assessment'])['status'], 'CURRENT')

    def test_explicit_revision_persists_even_when_next_hour_is_calm(self):
        self.study(); self.d.commit()
        first = self.observe(applicability='REVISE')
        self.assertTrue(first['deep_eligible'])
        self.assertIn('APPLICABILITY_REVISION_REQUIRED', sensing.observation(self.f.store, first['observation_id'])['gate']['cycle_review']['reasons'])
        second = self.observe()
        self.assertTrue(second['deep_eligible'])
        self.assertEqual(cycle.baseline(self.f.store, storage.current(self.f.store)['assessment'])['status'], 'REASSESSMENT_PENDING')

    def test_legacy_implicit_verification_does_not_renew_current_report(self):
        self.study(); self.d.commit()
        observed = self.observe()
        legacy = deepcopy(sensing.observation(self.f.store, observed['observation_id']))
        legacy['gate']['cycle_review']['applicability_revision'] = 2
        legacy['review'].pop('applicability_review')
        stamp = '2030-03-02T13:01:00Z'
        legacy['created_at'] = stamp
        fresh = deepcopy(self.f.raw)
        for retrieval in fresh['retrievals'].values(): retrieval['retrieved_at'] = stamp
        with mock.patch('v4.cycle.now', return_value=stamp), mock.patch('v4.sensing.observation', return_value=legacy), mock.patch('v4.reporting.raw_for_observation', return_value=fresh):
            basis = cycle.baseline(self.f.store, storage.current(self.f.store)['assessment'])
        self.assertEqual(basis['status'], 'REVIEW_DUE')
        self.assertIsNone(basis['applicability_verification'])


class CycleEvidenceTests(unittest.TestCase):
    def test_normal_collection_contains_closed_cycle_history(self):
        raw = market_fixture.evidence()
        self.assertEqual(len(raw['datasets']['binance_spot_1d']['rows']), 90)
        self.assertEqual(len(raw['datasets']['binance_spot_1w']['rows']), 52)
        metrics = reporting.metrics(raw, None)
        self.assertIn('closed_history', metrics['binance_spot_1w'])
        self.assertNotIn('today_utc', metrics['binance_spot_1w'])

    def test_cache_reuses_original_retrieval_and_refreshes_new_closed_day(self):
        cutoff = market_fixture.CUTOFF
        raw = market_fixture.evidence(cutoff)
        client = market_fixture.PublicFixtureClient(cutoff + market.HOUR)
        with mock.patch.object(client, 'get', wraps=client.get) as get:
            later = market.collect(market.iso(cutoff + market.HOUR), client=client, cycle_cache=raw)
        market.validate_evidence(later)
        self.assertEqual(later['retrievals']['binance_spot_1w'], raw['retrievals']['binance_spot_1w'])
        self.assertFalse(any(c.args[1].get('interval') in ('1d','1w') for c in get.call_args_list))
        next_day = int(market.timestamp(raw['datasets']['binance_spot_1d']['requested_end'])) + 86400
        client = market_fixture.PublicFixtureClient(next_day)
        with mock.patch.object(client, 'get', wraps=client.get) as get:
            market.collect(market.iso(next_day), client=client, cycle_cache=raw)
        self.assertTrue(any(c.args[1].get('interval') == '1d' for c in get.call_args_list))

    def test_forced_refresh_checks_revisions_without_claiming_deep_completion(self):
        raw = market_fixture.evidence()
        client = market_fixture.PublicFixtureClient()
        with mock.patch.object(client, 'get', wraps=client.get) as get:
            market.collect(market.iso(market_fixture.CUTOFF), deep=True, client=client, cycle_cache=raw)
        self.assertTrue(any(c.args[1].get('interval') == '1w' for c in get.call_args_list))


class CycleReportTests(unittest.TestCase):
    def test_risk_report_remains_available_before_deep_and_cannot_activate_old_advice(self):
        f = mobile_fixture.MobileReportTests(); f.setUp(); self.addCleanup(f.doCleanups)
        root = f.f.root
        policy = read(root / 'write-policy.json') if (root / 'write-policy.json').exists() else {}
        replace(root / 'write-policy.json', dict(policy, cycle_review_required=True))
        original_context = f.f.context
        def cycle_context(store):
            ctx = original_context(store)
            ctx.update(cycle_policy_revision=1, cycle_baseline={'status': 'INITIAL_REVIEW_PENDING'},
                       current_strategy={'outlook': f.f.f.decision['assessment']['outlook']})
            return ctx
        f.f.context = cycle_context
        with_context = mock.patch('v4.reporting.context', side_effect=cycle_context)
        with_context.start(); self.addCleanup(with_context.stop)
        spec = f.spec()
        spec['layout']['lead'] = '周期判断正在复核，现有下行风险需要继续防范。'
        spec['layout']['quality_review'].update({k: '明确引用原周期基线，并说明风险变化与下行位置的用途和限制。' for k in cycle.REPORT_CHECKS})
        source = f.source(spec)
        self.assertIn('周期判断正在复核', source['body'])
        ctx = reporting.context(root)
        brief = run_flow.report_brief(ctx)
        self.assertIn('outlook', brief['current_strategy'])
        spec = f.spec(); spec['layout']['include_recommendations'] = True
        spec['layout']['quality_review'].update({k: '明确引用原周期基线，并说明风险变化与下行位置的用途和限制。' for k in cycle.REPORT_CHECKS})
        with self.assertRaisesRegex(Invalid, 'pending cycle review'):
            f.source(spec)


if __name__ == '__main__':
    unittest.main()
