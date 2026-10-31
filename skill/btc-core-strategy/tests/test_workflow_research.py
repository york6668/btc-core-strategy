"""Question research and main-only sensing, using synthetic facts and runtime traces."""
from copy import deepcopy
from pathlib import Path
from datetime import datetime
import json
import sys
import unittest
from unittest import mock


sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_recommendation_maintenance as support
import test_market_observation as fixtures
from v4 import evidence, research, sensing, storage
from v4.common import Invalid, create, digest, facts, read, replace


class ResearchWorkflowTests(unittest.TestCase):
    bind = support.RecommendationTests.bind
    commit = support.RecommendationTests.commit

    def setUp(self):
        support.RecommendationTests.setUp(self)
        support.RecommendationTests.upgrade(self)
        self.commit()
        replace(self.store / 'write-policy.json', {'minimum_assessment_revision': 3})
        self.db = self.project / '.strategy/market_observation.sqlite3'
        self.ref = {'source_id': 'hourly', 'pointer': '/datasets', 'value_hash': digest(self.raw['datasets'])}
        self.spec = {'issue_key': 'SPOT_DEMAND_PERSISTENCE', 'scope': ['/outlook'], 'question': 'Does the current raw sequence support persistent spot demand?',
            'decision_id': storage.current(self.store)['decision_id'], 'evidence_id': self.evidence['evidence_id'],
            'change': {'kind': 'INITIAL_QUESTION', 'reason': 'A specific unresolved causal question changes the core outlook.', 'refs': [self.ref], 'reopen_condition': None},
            'reopen_conditions': ['Independent spot flow reverses across closed windows.']}

    def request(self, spec=None):
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:00Z'):
            return research.request(self.store, self.project, spec or self.spec)

    def trace(self, req, turn, start, end, phase='independent', candidate=None, model='gpt-6-astra', include_input=True):
        path = self.base / 'agent.jsonl'
        values = [] if not path.exists() else [json.loads(l) for l in path.read_text().splitlines()]
        if not values:
            values.append({'type': 'session_meta', 'payload': {'id': 'SYNTHETIC_AGENT'}})
        values.append({'type': 'turn_context', 'payload': {'turn_id': turn, 'model': model, 'effort': 'max'}})
        if include_input:
            payload = research.read_input(self.store, req['request_id'], phase, candidate)
            payload['continuity'] = {'reader_receipts': {'status': 'PROJECTED', 'processed': 0}}
            values.extend([{'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': turn,
                'input': 'python strategy_v4.py research-read --request ' + req['request_id'] + ' --phase ' + phase}},
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': turn, 'output': [{'type': 'text', 'text': json.dumps(payload)}]}}])
        values.append({'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': turn,
            'started_at': datetime.fromisoformat(start.replace('Z', '+00:00')).timestamp(),
            'completed_at': datetime.fromisoformat(end.replace('Z', '+00:00')).timestamp(), 'last_agent_message': 'Bound synthetic research analysis.'}})
        path.write_text('\n'.join(json.dumps(v) for v in values) + '\n')
        return path

    def supplement(self, rewrite_source=False):
        spec = {k: deepcopy(v) for k, v in self.evidence.items() if k not in ('schema', 'evidence_id')}
        spec['parent_id'] = None
        spec['cutoff'] = '2030-03-01T13:03:04Z'
        raw = self.base / 'later-official.json'
        create(raw, {'result': 'Synthetic newly published official decision.'})
        files = {s['id']: self.evidence_dir / 'raw' / (s['raw_hash'][7:] + '.json') for s in spec['sources']}
        if rewrite_source:
            spec['sources'][0]['url'] += '/rewritten'
        spec['sources'].append({'id': 'later_official', 'origin': 'FED',
            'url': 'https://www.federalreserve.gov/newsevents/synthetic.htm', 'trust': 'PUBLIC_RAW',
            'published_at': '2030-03-01T13:03:00Z', 'retrieved_at': '2030-03-01T13:03:04Z',
            'start': '2030-03-01T13:03:00Z', 'end': '2030-03-01T13:03:00Z', 'raw_hash': digest(raw.read_bytes())})
        files['later_official'] = raw
        directory = self.base / ('rewritten-supplement' if rewrite_source else 'later-supplement')
        bundle = evidence.freeze(spec, files, directory)
        storage.archive_evidence(self.store, directory)
        return bundle, directory

    def finished(self, additional=None):
        req = self.request()
        path = self.trace(req, 'FIRST', '2030-03-01T13:03:01Z', '2030-03-01T13:03:03Z')
        independent = research.independent(self.store, req['request_id'], 'SYNTHETIC_AGENT', path, 'FIRST')
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:05Z'):
            candidate = research.reveal(self.store, req['request_id'], independent['independent_id'], {'hypothesis': 'Sol candidate to challenge.'}, additional=additional)
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:06Z'):
            self.trace(req, 'SECOND', '2030-03-01T13:03:06Z', '2030-03-01T13:03:09Z', 'candidate', candidate['candidate_id'])
        spec = {'request_id': req['request_id'], 'candidate_id': candidate['candidate_id'], 'agent_id': 'SYNTHETIC_AGENT', 'rollout_path': str(path), 'turn_id': 'SECOND',
            'coverage': ['Spot flow causal question'], 'counterevidence': ['Open interest alternatives'], 'unresolved': ['Participant identity is unknown'],
            'reopen_conditions': self.spec['reopen_conditions'], 'analysis': 'Bound synthetic research analysis.'}
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:10Z'):
            saved = research.result(self.store, spec)
        return req, saved, spec

    def test_supplement_keeps_request_but_updates_actual_final_evidence(self):
        bundle, directory = self.supplement()
        req, saved, spec = self.finished(additional=directory)
        self.assertEqual(saved['request_id'], req['request_id'])
        self.assertEqual(saved['evidence_id'], bundle['evidence_id'])
        self.assertEqual(saved['knowledge_cutoff'], bundle['cutoff'])
        self.assertEqual(saved['market_cutoff'], self.raw['cutoff'])
        self.assertEqual(saved['market_evidence_id'], self.raw['evidence_id'])
        self.assertNotEqual(req['knowledge_cutoff'], saved['knowledge_cutoff'])
        original = research.read_input(self.store, req['request_id'])['payload']
        self.assertNotIn('later_official', [s['source']['id'] for s in original['sources']])
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:11Z'):
            disclosed = research.read_input(self.store, req['request_id'], 'candidate', spec['candidate_id'])['payload']
        self.assertIn('later_official', [s['source']['id'] for s in disclosed['evidence_context']['sources']])
        self.assertEqual(len(list((self.store / 'research' / 'requests').glob('*.json'))), 1)
        # Final read and result cannot silently use subsequently altered bytes.
        source = disclosed['evidence_context']['sources'][-1]
        Path(source['path']).write_text('{"result":"tampered"}')
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:11Z'), self.assertRaisesRegex(Invalid, 'raw evidence'):
            research.read_input(self.store, req['request_id'], 'candidate', spec['candidate_id'])

    def test_supplement_cannot_rewrite_original_source_identity(self):
        _, directory = self.supplement(rewrite_source=True)
        with self.assertRaisesRegex(Invalid, 'rewrite an existing research source'):
            self.finished(additional=directory)

    def observe(self, raw=None, research_findings=None, finding='COUNTEREVIDENCE'):
        raw = raw or self.raw
        ctx = sensing.context(self.store, self.db, self.project, raw)
        candidate = fixtures.review(raw, {'context_id': ctx['context_id'], 'baselines': {}}, required=False)
        value = {'schema': 'BTC_SENSING_REVIEW_V4', 'revision': 3, 'context_id': ctx['context_id'], 'market_review': candidate,
            'recommendation_checks': [{'side': side, 'findings': [finding], 'refs': ['windows.4h.binance_spot_1h'], 'explanation': 'Main assesses relevance to its current recommendation.'} for side in ('upper_short', 'lower_spot')],
            'extra_findings': [], 'main_evaluation': {'required': True, 'reasons': ['Assess the current core conclusion.']}, 'research_findings': research_findings or []}
        return sensing.record(self.store, self.db, self.project, raw, value)

    def finding(self, kind='INITIAL_QUESTION', material=True, reopen=None):
        return {'issue_key': self.spec['issue_key'], 'scope': self.spec['scope'], 'question': self.spec['question'],
            'material_change': material, 'change_kind': kind, 'change_reason': 'Specific source-backed discriminator needs investigation.' if material else 'Previous investigation covers this observation.',
            'refs': ['windows.4h.binance_spot_1h'], 'reopen_condition': reopen}

    def test_touch_counterevidence_and_expiry_trigger_main_not_research(self):
        result = self.observe()
        self.assertTrue(result['main_eligible'])
        self.assertIn('upper_short:PRICE_TOUCH_REVIEW', result['triggers'])
        self.assertFalse(result['research_eligible'])
        self.assertFalse(result['deep_eligible'])
        later = self.observe(fixtures.evidence(1899291600))
        self.assertIn('upper_short:EXPIRY', later['triggers'])
        self.assertFalse(later['research_eligible'])

    def test_fact_change_does_not_automatically_call_astra(self):
        (self.project / '资产配置.md').write_text('Changed synthetic fact.\n')
        result = self.observe(finding='NO_MATERIAL_CHANGE')
        self.assertIn('FACTS_CHANGED', result['triggers'])
        self.assertTrue(result['main_eligible'])
        self.assertFalse(result['research_eligible'])

    def test_legacy_unknown_is_not_automatic_research_and_compact_uses_core(self):
        (self.store / 'deep-current.json').unlink()
        pinned = storage.current(self.store)
        pinned['assessment']['conclusion'] = {'business_meaning': {'value_state': 'CORE_CURRENT'}, 'narrative': 'Current main conclusion.'}
        with mock.patch('v4.storage.current', return_value=pinned):
            ctx = sensing.context(self.store, self.db, self.project, self.raw)
        self.assertEqual(ctx['current_conclusion']['business_meaning']['value_state'], 'CORE_CURRENT')
        self.assertEqual(ctx['research']['legacy_coverage'], 'UNKNOWN')
        self.assertEqual(ctx['research']['issues'], [])
        self.assertNotIn('samples', ctx['measurements'][0]['tiers'][0])
        self.assertIn('sample_count', ctx['measurements'][0]['tiers'][0])

    def test_broken_legacy_deep_cannot_block_revision3_context_or_gate(self):
        replace(self.store / 'deep-current.json', {'completion': 'deep/missing-legacy-completion.json'})
        ctx = sensing.context(self.store, self.db, self.project, self.raw)
        self.assertEqual(ctx['current_core']['decision_id'], storage.current(self.store)['decision_id'])
        self.assertIsNone(ctx['identity']['deep_completion_hash'])
        observed = self.observe()
        gated = sensing.gate(self.store, observed['observation_id'])
        self.assertTrue(gated['main_eligible'])
        self.assertFalse(gated['research_eligible'])
        # This fixture still has a revision 2 core; disabling the revision 3
        # policy restores its original strict legacy-completion requirement.
        (self.store / 'write-policy.json').unlink()
        with self.assertRaises(FileNotFoundError):
            sensing.context(self.store, self.db, self.project, self.raw)

    def test_issue_scope_or_hour_alias_cannot_reopen_same_evidence(self):
        first = self.request()
        second = deepcopy(self.spec)
        second['scope'].append('/recommendations/upper_short')
        second['change']['kind'] = 'NEW_KEY_EVIDENCE'
        with self.assertRaisesRegex(Invalid, 'already requested'):
            self.request(second)
        self.assertEqual(first['issue_id'], research.issue_identity(second['issue_key'], second['scope']))
        with self.assertRaisesRegex(Invalid, 'hourly suffix'):
            research.issue_identity('SPOT_DEMAND_20260907_1000Z', ['/outlook'])

    def test_labels_no_delta_and_unmatched_reopen_do_not_create_research(self):
        self.finished()
        quiet = self.observe(research_findings=[self.finding('NO_NEW_KEY_EVIDENCE', False)])
        self.assertFalse(quiet['research_eligible'])
        with self.assertRaisesRegex(Invalid, 'recorded discriminator'):
            self.observe(research_findings=[self.finding('REOPEN_CONDITION_MET', True, 'Unrelated clock tick.')])
        reopened = self.observe(research_findings=[self.finding('REOPEN_CONDITION_MET', True, self.spec['reopen_conditions'][0])])
        self.assertTrue(reopened['research_eligible'])

    def test_nested_candidate_data_rejected_before_request(self):
        with self.assertRaisesRegex(Invalid, 'candidate-bearing'):
            research._candidate_free({'raw': [{'nested': {'market_review': {'hypothesis': 'Sol'}}}]})
        self.assertFalse((self.store / 'research' / 'requests').exists())

    def test_actual_input_read_and_model_required(self):
        req = self.request()
        path = self.trace(req, 'FIRST', '2030-03-01T13:03:01Z', '2030-03-01T13:03:03Z', include_input=False)
        with self.assertRaisesRegex(Invalid, 'did not read'):
            research.independent(self.store, req['request_id'], 'SYNTHETIC_AGENT', path, 'FIRST')
        path.unlink()
        path = self.trace(req, 'FIRST', '2030-03-01T13:03:01Z', '2030-03-01T13:03:03Z', model='gpt-5.6-sol')
        with self.assertRaisesRegex(Invalid, 'actually run Astra'):
            research.independent(self.store, req['request_id'], 'SYNTHETIC_AGENT', path, 'FIRST')

    def test_same_agent_ordered_phases_and_result_never_publish_core(self):
        before = storage.current(self.store)['decision_id']
        _, saved, spec = self.finished()
        self.assertEqual(storage.current(self.store)['decision_id'], before)
        self.assertFalse((self.store / 'research' / 'reported-current.json').exists())
        bad = dict(spec, agent_id='ANOTHER_AGENT')
        with self.assertRaisesRegex(Invalid, 'same agent'):
            research.result(self.store, bad)
        self.assertEqual(research.context(self.store)['issues'][0]['latest_result']['result_id'], saved['result_id'])

    def test_node_continuity_does_not_hide_changed_read_contract(self):
        req = self.request()
        for field, value in [('input_id', 'sha256:' + '0' * 64), ('payload', {'question': 'Different frozen input.'}), ('unknown_field', True)]:
            with self.subTest(field=field):
                path = self.trace(req, 'FIRST', '2030-03-01T13:03:01Z', '2030-03-01T13:03:03Z')
                values = [json.loads(line) for line in path.read_text().splitlines()]
                output = next(v['payload']['output'] for v in values if v['payload'].get('type') == 'custom_tool_call_output')
                proof = json.loads(output[0]['text'])
                proof[field] = value
                output[0]['text'] = json.dumps(proof)
                path.write_text('\n'.join(json.dumps(v) for v in values) + '\n')
                with self.assertRaisesRegex(Invalid, 'did not read'):
                    research.independent(self.store, req['request_id'], 'SYNTHETIC_AGENT', path, 'FIRST')
                path.unlink()

    def test_context_exposes_existing_phase_and_same_agent_without_claiming_liveness(self):
        req = self.request()
        self.assertEqual(research.context(self.store)['issues'][0]['latest_request']['progress']['stage'], 'REQUESTED')
        path = self.trace(req, 'FIRST', '2030-03-01T13:03:01Z', '2030-03-01T13:03:03Z')
        phase = research.independent(self.store, req['request_id'], 'SYNTHETIC_AGENT', path, 'FIRST')
        progress = research.context(self.store)['issues'][0]['latest_request']['progress']
        self.assertEqual(progress['stage'], 'INDEPENDENT_COMPLETED')
        self.assertEqual(progress['independent_id'], phase['independent_id'])
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:05Z'):
            candidate = research.reveal(self.store, req['request_id'], phase['independent_id'], {'hypothesis': 'Already under review.'})
        ctx = sensing.context(self.store, self.db, self.project, self.raw)
        progress = ctx['research']['issues'][0]['latest_request']['progress']
        self.assertEqual(progress['stage'], 'CANDIDATE_REVEALED')
        self.assertEqual(progress['candidate_id'], candidate['candidate_id'])
        self.assertEqual(progress['agent_id'], 'SYNTHETIC_AGENT')
        self.assertEqual(progress['rollout_path'], str(path.resolve()))
        self.assertEqual(progress['runtime_status'], 'NOT_CHECKED')
        self.assertNotIn('candidate', progress)

    def test_prior_result_cannot_hide_reopened_request_or_revised_candidate(self):
        req, saved, spec = self.finished()
        issue = research.context(self.store)['issues'][0]
        self.assertEqual(issue['latest_request']['progress']['result_id'], saved['result_id'])
        self.assertEqual(issue['status'], 'RESEARCHED')
        previous = research._load(self.store, 'candidates', spec['candidate_id'])
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:11Z'):
            revised = research.reveal(self.store, req['request_id'], previous['independent_id'], {'hypothesis': 'Revised after review.'})
        issue = research.context(self.store)['issues'][0]
        self.assertEqual(issue['latest_request']['progress']['candidate_id'], revised['candidate_id'])
        self.assertEqual(issue['latest_request']['progress']['stage'], 'CANDIDATE_REVEALED')
        self.assertEqual(issue['status'], 'REQUESTED')
        self.assertEqual(issue['latest_result']['result_id'], saved['result_id'])

    def test_adopted_result_survives_later_unreviewed_candidate(self):
        req, saved, spec = self.finished()
        original = research._load(self.store, 'candidates', spec['candidate_id'])
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:11Z'):
            later = research.reveal(self.store, req['request_id'], original['independent_id'], {'hypothesis': 'Unadopted scratch revision.'})
        pinned = deepcopy(storage.current(self.store))
        pinned['assessment']['research_absorptions'] = [{'result_id': saved['result_id'], 'decision': 'ADOPT'}]
        with mock.patch('v4.storage.current', return_value=pinned):
            issue = research.context(self.store)['issues'][0]
        self.assertEqual(issue['status'], 'RESEARCHED')
        self.assertEqual(issue['latest_request']['progress']['candidate_id'], spec['candidate_id'])
        self.assertFalse(issue['latest_request']['progress']['resume_required'])
        self.assertEqual(issue['adopted_result']['result_id'], saved['result_id'])
        self.assertEqual(issue['completed_result']['result_id'], saved['result_id'])
        self.assertEqual([v['candidate_id'] for v in issue['pending_candidates']], [later['candidate_id']])

    def test_candidate_preflight_does_not_disclose_or_write(self):
        req, saved, spec = self.finished()
        original = research._load(self.store, 'candidates', spec['candidate_id'])
        before = {str(p): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:11Z'):
            result = research.reveal(self.store, req['request_id'], original['independent_id'], {'hypothesis': 'Read-only preview.'}, check_only=True)
        self.assertTrue(result['valid'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.store.rglob('*') if p.is_file()})

    def test_new_request_is_not_completed_by_previous_result(self):
        req, saved, spec = self.finished()
        bundle, _ = self.supplement()
        later = deepcopy(self.spec)
        later['evidence_id'] = bundle['evidence_id']
        later['change'] = {'kind': 'NEW_KEY_EVIDENCE', 'reason': 'New official raw result changes the question.',
                          'refs': [{'source_id': 'later_official', 'pointer': '/result',
                                    'value_hash': digest('Synthetic newly published official decision.')}],
                          'reopen_condition': None}
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:12Z'):
            reopened = research.request(self.store, self.project, later)
        issue = research.context(self.store)['issues'][0]
        self.assertEqual(issue['latest_request']['request_id'], reopened['request_id'])
        self.assertEqual(issue['latest_request']['progress']['stage'], 'REQUESTED')
        self.assertNotIn('agent_id', issue['latest_request']['progress'])
        self.assertEqual(issue['status'], 'REQUESTED')
        self.assertEqual(issue['latest_result']['result_id'], saved['result_id'])

    def test_result_cannot_use_same_turn_or_fabricate_agent_output(self):
        _, _, spec = self.finished()
        with self.assertRaisesRegex(Invalid, 'later independent turn'):
            research.result(self.store, dict(spec, turn_id='FIRST'))
        with self.assertRaisesRegex(Invalid, 'actual agent output'):
            research.result(self.store, dict(spec, analysis='Invented output.'))

    def test_absorption_scope_facts_and_knowledge_cutoff(self):
        req, saved, _ = self.finished()
        draft = deepcopy(self.draft)
        item = {'issue_id': saved['issue_id'], 'request_id': req['request_id'], 'result_id': saved['result_id'],
                'decision': 'PARTIAL', 'rationale': 'Sol retains the supported part and rejects unsupported identity attribution.', 'assessment_refs': ['/outlook']}
        draft['research_absorptions'] = [item]
        self.assertEqual(research.validate_absorptions(self.store, draft), [item])
        item['assessment_refs'] = ['/recommendations/upper_short']
        with self.assertRaisesRegex(Invalid, 'outside its question'):
            research.validate_absorptions(self.store, draft)
        item['assessment_refs'] = ['/outlook']
        draft['fact_version']['资产配置.md'] = digest('Changed synthetic facts')
        with self.assertRaisesRegex(Invalid, 'fresh main applicability'):
            research.validate_absorptions(self.store, draft)
        item['applicability'] = {'fact_version': draft['fact_version'], 'evidence_id': draft['evidence_id'], 'explanation': 'Sol rechecks this market mechanism against the changed synthetic exposure.'}
        research.validate_absorptions(self.store, draft)
        draft['temporal_scope']['knowledge_cutoff'] = '2030-03-01T13:01:00Z'
        with self.assertRaisesRegex(Invalid, 'knowledge cutoff'):
            research.validate_absorptions(self.store, draft)

    def test_research_cumulative_window_not_reset_by_main_updates(self):
        self.finished()
        later = fixtures.evidence(1898604000)
        ctx = sensing.context(self.store, self.db, self.project, later)
        issue = ctx['research']['issues'][0]
        self.assertEqual(issue['accumulation']['start'], self.raw['cutoff'])
        self.assertEqual(issue['accumulation']['windows']['binance_spot_1h']['actual_count'], 1)
        self.assertEqual(issue['latest_result']['unresolved'], ['Participant identity is unknown'])

    def test_completing_one_question_preserves_another_questions_own_window(self):
        # A starts from 13Z; B starts from 14Z while A is still outstanding.
        # A finishes at 14:30 without gaining evidence after its 13Z cutoff.
        with mock.patch('v4.evidence.now', return_value='2030-03-01T16:10:00Z'):
            question_a = self.request()
            raw_b = fixtures.evidence(1898604000)
            raw_path = self.base / 'question-b-raw.json'
            create(raw_path, raw_b)
            spec = {k: deepcopy(v) for k, v in self.evidence.items() if k not in ('schema', 'evidence_id')}
            spec.update(parent_id=None, cutoff='2030-03-01T14:02:00Z')
            files = {s['id']: str(self.evidence_dir / 'raw' / (s['raw_hash'][7:] + '.json')) for s in spec['sources']}
            hourly = next(s for s in spec['sources'] if s['id'] == 'hourly')
            hourly.update(published_at=raw_b['cutoff'], retrieved_at=next(iter(raw_b['retrievals'].values()))['retrieved_at'],
                          start=raw_b['datasets']['binance_spot_1h']['requested_start'], end=raw_b['cutoff'], raw_hash=digest(raw_path.read_bytes()))
            files['hourly'] = str(raw_path)
            directory = self.base / 'question-b-evidence'
            package = evidence.freeze(spec, files, directory)
            storage.archive_evidence(self.store, directory)
            request_b = deepcopy(self.spec)
            request_b.update(issue_key='LEVERAGE_CAUSAL_CONTRIBUTION', scope=['/robustness'],
                             question='Does the later leverage sequence undermine the independent spot explanation?', evidence_id=package['evidence_id'])
            request_b['change']['refs'] = [{'source_id': 'hourly', 'pointer': '/datasets', 'value_hash': digest(raw_b['datasets'])}]
            with mock.patch('v4.research.now', return_value='2030-03-01T14:03:00Z'):
                question_b = research.request(self.store, self.project, request_b)
            current = fixtures.evidence(1898607600)
            before = {v['issue_id']: v for v in sensing.context(self.store, self.db, self.project, current)['research']['issues']}
            self.assertEqual(len(before), 2)
            self.assertIsNone(before[question_b['issue_id']]['latest_result'])
            self.assertEqual(before[question_a['issue_id']]['accumulation']['windows']['binance_spot_1h']['actual_count'], 2)
            self.assertEqual(before[question_b['issue_id']]['accumulation']['windows']['binance_spot_1h']['actual_count'], 1)

            path = self.trace(question_a, 'A_FIRST', '2030-03-01T13:03:01Z', '2030-03-01T14:25:00Z')
            phase = research.independent(self.store, question_a['request_id'], 'SYNTHETIC_AGENT', path, 'A_FIRST')
            with mock.patch('v4.research.now', return_value='2030-03-01T14:26:00Z'):
                candidate = research.reveal(self.store, question_a['request_id'], phase['independent_id'], {'hypothesis': 'Only question A candidate.'})
            self.trace(question_a, 'A_SECOND', '2030-03-01T14:27:00Z', '2030-03-01T14:29:00Z', 'candidate', candidate['candidate_id'])
            result_spec = {'request_id': question_a['request_id'], 'candidate_id': candidate['candidate_id'], 'agent_id': 'SYNTHETIC_AGENT',
                          'rollout_path': str(path), 'turn_id': 'A_SECOND', 'coverage': ['Only the spot-demand question through 13Z'],
                          'counterevidence': [], 'unresolved': ['Participant identity remains unknown'],
                          'reopen_conditions': self.spec['reopen_conditions'], 'analysis': 'Bound synthetic research analysis.'}
            with mock.patch('v4.research.now', return_value='2030-03-01T14:30:00Z'):
                completed = research.result(self.store, result_spec)
            after = {v['issue_id']: v for v in sensing.context(self.store, self.db, self.project, current)['research']['issues']}
            self.assertEqual(after[question_a['issue_id']]['latest_result']['result_id'], completed['result_id'])
            self.assertEqual(after[question_a['issue_id']]['accumulation']['start'], question_a['market_cutoff'])
            self.assertEqual(after[question_a['issue_id']]['accumulation']['windows']['binance_spot_1h']['actual_count'], 2)
            self.assertEqual(after[question_b['issue_id']], before[question_b['issue_id']])
            self.assertEqual(after[question_b['issue_id']]['accumulation']['start'], question_b['market_cutoff'])
            self.assertEqual(after[question_b['issue_id']]['accumulation']['windows']['binance_spot_1h']['actual_count'], 1)

    def orchestration_trace(self, method='spawn_agent', call='SPAWN', returned='FINAL1', start='2030-03-01T13:03:01Z', end='2030-03-01T13:03:03Z', fork='none', author='/root/astra_sample'):
        path = self.base / 'parent-rollout.jsonl'
        values = [] if not path.exists() else [json.loads(line) for line in path.read_text().splitlines()]
        args = {'task_name': 'astra_sample', 'fork_turns': fork, 'model': 'gpt-6-astra', 'reasoning_effort': 'max', 'message': 'ENCRYPTED_PROMPT_NOT_INSPECTABLE'} if method == 'spawn_agent' else {'target': '/root/astra_sample', 'message': 'ENCRYPTED_CANDIDATE_PROMPT'}
        values.extend([
            {'timestamp': start, 'type': 'response_item', 'payload': {'type': 'function_call', 'namespace': 'collaboration', 'name': method, 'call_id': call, 'arguments': json.dumps(args)}},
            {'timestamp': start, 'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': call, 'output': json.dumps({'task_name': author})}},
            {'timestamp': end, 'type': 'response_item', 'payload': {'type': 'agent_message', 'id': returned, 'author': author, 'recipient': '/root',
                'content': [{'type': 'input_text', 'text': 'Message Type: FINAL_ANSWER\nTask name: /root\nSender: ' + author + '\nPayload:\nActual orchestration analysis ' + returned}]}}
        ])
        path.write_text('\n'.join(json.dumps(v) for v in values) + '\n')
        return path

    def main_input_review(self, req, identity, call):
        return {'reviewer_role': 'MAIN', 'request_id': req['request_id'], 'input_id': identity, 'dispatch_call_id': call,
            'material_review': 'Main reviewed the actual dispatched prompt and frozen material; no inner trace is available.',
            'phase_order_review': 'Main checked which material was disclosed during this phase.',
            'evidence_refs': ['Synthetic parent dispatch receipt and main-observed actual research-read output.']}

    def test_orchestration_actual_returns_supported_without_fake_native_proof(self):
        before = storage.current(self.store)['decision_id']
        supplement, directory = self.supplement()
        req = self.request()
        path = self.orchestration_trace()
        phase = research.orchestrated_independent(self.store, req['request_id'], path, 'SPAWN', 'FINAL1', self.main_input_review(req, req['independent_input_id'], 'SPAWN'))
        self.assertEqual(phase['summary'], 'Actual orchestration analysis FINAL1')
        self.assertNotIn('evidence_context', phase)
        self.assertEqual(phase, research.orchestrated_independent(self.store, req['request_id'], path,
            'SPAWN', 'FINAL1', self.main_input_review(req, req['independent_input_id'], 'SPAWN')))
        self.assertNotIn('turn_id', phase)
        self.assertFalse(phase['inner_tool_trace_verified'])
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:05Z'):
            candidate = research.reveal(self.store, req['request_id'], phase['independent_id'], {'hypothesis': 'Candidate after independent return.'}, additional=directory)
        self.orchestration_trace('followup_task', 'FOLLOWUP', 'FINAL2', '2030-03-01T13:03:06Z', '2030-03-01T13:03:09Z')
        spec = {'request_id': req['request_id'], 'candidate_id': candidate['candidate_id'], 'rollout_path': str(path),
            'dispatch_call_id': 'FOLLOWUP', 'return_message_id': 'FINAL2', 'input_review': self.main_input_review(req, candidate['candidate_id'], 'FOLLOWUP'),
            'coverage': ['Question scope'], 'counterevidence': [], 'unresolved': ['Remaining uncertainty'], 'reopen_conditions': self.spec['reopen_conditions']}
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:10Z'):
            result = research.orchestrated_result(self.store, spec)
        self.assertEqual(result['analysis'], 'Actual orchestration analysis FINAL2')
        self.assertEqual(result['knowledge_cutoff'], supplement['cutoff'])
        self.assertEqual(result['evidence_id'], supplement['evidence_id'])
        self.assertEqual(result['verification_scope'], research.ORCHESTRATED_SCOPE)
        self.assertFalse(result['os_input_isolation_verified'])
        self.assertEqual(storage.current(self.store)['decision_id'], before)
        with self.assertRaisesRegex(Invalid, 'invalid fields'):
            research.orchestrated_result(self.store, dict(spec, analysis='Handwritten replacement is forbidden.'))

    def test_orchestrated_supplement_consumes_cli_and_preserves_identity(self):
        import contextlib
        import io
        import strategy_v4
        bundle, directory = self.supplement()
        req = self.request()
        path = self.orchestration_trace(end='2030-03-01T13:03:06Z')
        review = self.main_input_review(req, req['independent_input_id'], 'SPAWN')
        review_path = self.base / 'main-review.json'
        create(review_path, review)
        arguments = ['--project', str(self.project), '--root', str(self.store), '--full',
            'research-orchestrated-independent', '--request', req['request_id'],
            '--rollout', str(path), '--dispatch', 'SPAWN', '--returned', 'FINAL1',
            '--input-review', str(review_path), '--additional', str(directory)]
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:10Z'), \
             mock.patch.object(research, '_public_result', wraps=research._public_result) as consumer:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(strategy_v4.main(arguments), 0)
            phase = json.loads(output.getvalue())
            consumer.assert_called_once_with(self.store.as_posix(), req,
                'Actual orchestration analysis FINAL1', str(directory))
            repeated = research.orchestrated_independent(self.store, req['request_id'], path,
                'SPAWN', 'FINAL1', review, additional=directory)
        self.assertEqual(phase, repeated)
        self.assertEqual(phase['request_id'], req['request_id'])
        self.assertEqual(phase['input_id'], req['independent_input_id'])
        self.assertEqual(phase['evidence_context']['evidence_id'], bundle['evidence_id'])
        self.assertEqual(phase['evidence_context']['knowledge_cutoff'], bundle['cutoff'])
        self.assertEqual(phase['verification_scope'], research.ORCHESTRATED_SCOPE)
        self.assertFalse(phase['inner_tool_trace_verified'])
        self.assertFalse(phase['os_input_isolation_verified'])
        self.assertEqual(research._load(self.store, 'requests', req['request_id']), req)
        self.assertEqual({s['source']['id'] for s in phase['evidence_context']['sources']},
            {s['id'] for s in self.evidence['sources']} | {'later_official'})

    def separated_orchestration_phases(self):
        req = self.request()
        public = self.orchestration_trace().rename(self.base / 'public-proof.jsonl')
        phase = research.orchestrated_independent(self.store, req['request_id'], public,
            'SPAWN', 'FINAL1', self.main_input_review(req, req['independent_input_id'], 'SPAWN'))
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:05Z'):
            candidate = research.reveal(self.store, req['request_id'], phase['independent_id'],
                                        {'hypothesis': 'Candidate after the frozen public phase.'})
        approval = self.orchestration_trace('followup_task', 'FOLLOWUP', 'FINAL2',
            '2030-03-01T13:03:06Z', '2030-03-01T13:03:09Z')
        spec = {'request_id': req['request_id'], 'candidate_id': candidate['candidate_id'],
            'rollout_path': str(approval), 'dispatch_call_id': 'FOLLOWUP', 'return_message_id': 'FINAL2',
            'input_review': self.main_input_review(req, candidate['candidate_id'], 'FOLLOWUP'),
            'coverage': ['Question scope'], 'counterevidence': [], 'unresolved': [],
            'reopen_conditions': self.spec['reopen_conditions']}
        return public, approval, phase, spec

    def test_orchestrated_result_consumes_each_frozen_phase_proof(self):
        public, approval, phase, spec = self.separated_orchestration_phases()
        before = public.read_bytes(), approval.read_bytes(), storage.current(self.store)
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:10Z'):
            result = research.orchestrated_result(self.store, spec)
        self.assertEqual(result['analysis'], 'Actual orchestration analysis FINAL2')
        self.assertEqual(result['independent_id'], phase['independent_id'])
        self.assertEqual(result['rollout_path'], str(approval))
        self.assertEqual((public.read_bytes(), approval.read_bytes(), storage.current(self.store)), before)

    def test_separate_approval_does_not_hide_changed_public_proof(self):
        public, approval, _, spec = self.separated_orchestration_phases()
        events = [json.loads(line) for line in public.read_text().splitlines()]
        events[-1]['payload']['content'][0]['text'] += ' changed after independent registration'
        public.write_text('\n'.join(json.dumps(item) for item in events) + '\n')
        approval_bytes = approval.read_bytes()
        with self.assertRaisesRegex(Invalid, 'independent orchestration proof changed'):
            research.orchestrated_result(self.store, spec)
        self.assertEqual(approval.read_bytes(), approval_bytes)
        self.assertEqual(list((self.store / 'research/results').glob('*.json')), [])

    def test_orchestrated_supplement_rejects_late_changed_and_omitted_sources(self):
        req = self.request()
        path = self.orchestration_trace()
        review = self.main_input_review(req, req['independent_input_id'], 'SPAWN')
        _, directory = self.supplement()
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:10Z'):
            with self.assertRaisesRegex(Invalid, 'after the actual completed answer'):
                research.orchestrated_independent(self.store, req['request_id'], path,
                    'SPAWN', 'FINAL1', review, additional=directory)
            (self.base / 'later-official.json').unlink()
            _, changed = self.supplement(rewrite_source=True)
            with self.assertRaisesRegex(Invalid, 'rewrite an existing'):
                research.orchestrated_independent(self.store, req['request_id'], path,
                    'SPAWN', 'FINAL1', review, additional=changed)
            spec = read(directory / 'evidence.json')
            spec = {k: v for k, v in spec.items() if k not in ('schema', 'evidence_id')}
            spec['sources'] = [s for s in spec['sources'] if s['id'] == 'later_official']
            files = {'later_official': directory / 'raw' / (spec['sources'][0]['raw_hash'][7:] + '.json')}
            omitted = self.base / 'omitted-source'
            evidence.freeze(spec, files, omitted)
            storage.archive_evidence(self.store, omitted)
            with self.assertRaisesRegex(Invalid, 'omit an existing'):
                research.orchestrated_independent(self.store, req['request_id'], path,
                    'SPAWN', 'FINAL1', review, additional=omitted)
        self.assertEqual(list((self.store / 'research/independent').glob('*.json')), [])

    def _result_after_verified_public_successor(self, source_phase):
        req=self.request();path=self.orchestration_trace()
        original=research.orchestrated_independent(self.store,req['request_id'],path,'SPAWN','FINAL1',
            self.main_input_review(req,req['independent_input_id'],'SPAWN'))
        continuation={k:deepcopy(v) for k,v in original.items() if k!='independent_id'}
        continuation.update(verification_scope=research.PUBLIC_ARTIFACT_SCOPES[source_phase],
            parent_independent_id=original['independent_id'],market_amendment={'source_phase':source_phase})
        phase=research._save(self.store,'independent','independent_id',continuation)
        # The manifest verifier is exercised with full generated author proofs
        # in test_system_amendment_clock. This boundary must always invoke it,
        # then retain the original spawn and actual candidate follow-up checks.
        with mock.patch.object(research,'_verify_market_amendment') as verified:
            with mock.patch('v4.research.now',return_value='2030-03-01T13:03:05Z'):
                candidate=research.reveal(self.store,req['request_id'],phase['independent_id'],{'hypothesis':'Exact disclosed input.'})
            self.orchestration_trace('followup_task','FOLLOWUP','FINAL2','2030-03-01T13:03:06Z','2030-03-01T13:03:09Z')
            spec={'request_id':req['request_id'],'candidate_id':candidate['candidate_id'],'rollout_path':str(path),
                  'dispatch_call_id':'FOLLOWUP','return_message_id':'FINAL2',
                  'input_review':self.main_input_review(req,candidate['candidate_id'],'FOLLOWUP'),
                  'coverage':['Actual disclosed input'],'counterevidence':[],'unresolved':[],
                  'reopen_conditions':self.spec['reopen_conditions']}
            with mock.patch('v4.research.now',return_value='2030-03-01T13:03:10Z'):
                result=research.orchestrated_result(self.store,spec)
            verified.assert_called_once_with(phase,self.store)
            self.assertEqual('Actual orchestration analysis FINAL2',result['analysis'])
            self.assertEqual(phase['independent_id'],result['independent_id'])
            self.assertFalse(result['inner_tool_trace_verified']);self.assertFalse(result['os_input_isolation_verified'])
            for invalid in ({'market_amendment':None},{'market_amendment':{'source_phase':'PUBLIC_CORRECTION'}},
                            {'verification_scope':'NATIVE_RUNTIME_PHASE_INPUT_READ'}):
                changed=dict(continuation,**invalid);bad=research._save(self.store,'independent','independent_id',changed)
                disclosed=dict(candidate,independent_id=bad['independent_id']);disclosed.pop('candidate_id')
                bad_candidate=research._save(self.store,'candidates','candidate_id',disclosed)
                rejected=dict(spec,candidate_id=bad_candidate['candidate_id'],
                    input_review=self.main_input_review(req,bad_candidate['candidate_id'],'FOLLOWUP'))
                with self.subTest(invalid=invalid),self.assertRaisesRegex(Invalid,'native research'):
                    research.orchestrated_result(self.store,rejected)

    def test_orchestrated_result_after_verified_portfolio_public_successor(self):
        self._result_after_verified_public_successor('PORTFOLIO')

    def test_orchestrated_result_after_verified_rejected_review_public_successor(self):
        self._result_after_verified_public_successor('APPROVAL')

    def test_orchestration_requires_fork_none_and_bound_main_input_review(self):
        req = self.request()
        path = self.orchestration_trace(fork='all')
        review = self.main_input_review(req, req['independent_input_id'], 'SPAWN')
        with self.assertRaisesRegex(Invalid, 'without inherited'):
            research.orchestrated_independent(self.store, req['request_id'], path, 'SPAWN', 'FINAL1', review)
        path.unlink()
        path = self.orchestration_trace()
        review['input_id'] = digest('Unrelated frozen material')
        with self.assertRaisesRegex(Invalid, 'not bound'):
            research.orchestrated_independent(self.store, req['request_id'], path, 'SPAWN', 'FINAL1', review)

    def test_orchestration_does_not_accept_wrong_final_author_or_missing_return(self):
        req = self.request()
        path = self.orchestration_trace(author='/root/another_agent')
        review = self.main_input_review(req, req['independent_input_id'], 'SPAWN')
        with self.assertRaisesRegex(Invalid, 'differs from'):
            research.orchestrated_independent(self.store, req['request_id'], path, 'SPAWN', 'FINAL1', review)
        with self.assertRaisesRegex(Invalid, 'unique actual dispatch'):
            research.orchestrated_independent(self.store, req['request_id'], path, 'SPAWN', 'MISSING', review)

    def test_orchestration_follows_real_paginated_history_boundaries(self):
        path=self.orchestration_trace()
        events=[json.loads(line) for line in path.read_text().splitlines()]
        parent_id='11111111-1111-1111-1111-111111111111'
        meta={'type':'session_meta','payload':{'id':'SAME_SESSION'}}
        parent=self.base/('rollout-old-'+parent_id+'.jsonl')
        original='\n'.join(json.dumps(v) for v in [meta]+events[:2])+'\n'
        parent.write_text(original)
        end=parent.stat().st_size
        # A later parent record is outside the child's declared history boundary.
        parent.write_text(original+json.dumps(events[2])+'\n')
        child_meta={'type':'session_meta','payload':{'id':'SAME_SESSION','history_mode':'paginated',
            'history_base':{'thread_id':parent_id,'end_byte_offset':end}}}
        path.write_text('\n'.join(json.dumps(v) for v in [child_meta,events[2]])+'\n')
        result=research._orchestration_turn(path,'SPAWN','FINAL1','spawn_agent')
        self.assertEqual(result['analysis'],'Actual orchestration analysis FINAL1')
        child_meta['payload']['id']='ANOTHER_SESSION'
        path.write_text('\n'.join(json.dumps(v) for v in [child_meta,events[2]])+'\n')
        with self.assertRaisesRegex(Invalid,'another session'):
            research._orchestration_turn(path,'SPAWN','FINAL1','spawn_agent')

    def test_orchestration_streams_without_bulk_reads_or_retaining_unrelated_events(self):
        import weakref
        path = self.orchestration_trace()
        original_open = Path.open
        class Stream:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def __getattr__(self, name): return getattr(self.stream, name)
            def read(self, *args): raise AssertionError('bulk trace read is forbidden')
        with mock.patch.object(Path, 'open', lambda p, *a, **kw: Stream(original_open(p, *a, **kw))), \
             mock.patch.object(Path, 'read_text', side_effect=AssertionError('whole trace read is forbidden')):
            proof = research._orchestration_turn(path, 'SPAWN', 'FINAL1', 'spawn_agent')
        events = list(research._orchestration_items(path))
        class Event(dict): pass
        def items(*args):
            yield events[0]
            refs = []
            for index in range(20):
                self.assertLessEqual(sum(r() is not None for r in refs), 2)
                value = Event(type='response_item', payload={'type': 'unrelated', 'index': index})
                refs.append(weakref.ref(value))
                yield value
            yield from events[1:]
        with mock.patch.object(research, '_orchestration_items', side_effect=items):
            streamed = research._orchestration_turn(path, 'SPAWN', 'FINAL1', 'spawn_agent')
        self.assertEqual(streamed['analysis'], proof['analysis'])
        self.assertEqual(streamed['dispatch_hash'], proof['dispatch_hash'])
        self.assertEqual(streamed['return_hash'], proof['return_hash'])

    def test_orchestration_stream_preserves_rejected_proofs(self):
        path = self.orchestration_trace()
        events = list(research._orchestration_items(path))
        variants = []
        def changed(index, **changes):
            values = deepcopy(events)
            values[index]['payload'].update(changes)
            return values
        args = json.loads(events[0]['payload']['arguments'])
        variants.append((changed(0, arguments=json.dumps(dict(args, model='gpt-6.1-sol'))), 'Astra/max'))
        variants.append((changed(0, arguments=json.dumps(dict(args, fork_turns='all'))), 'without inherited'))
        variants.append(([events[2], *events[:2]], 'predates dispatch'))
        variants.append(([*events, events[2]], 'unique actual dispatch'))
        variants.append(([events[0], events[1], events[1], events[2]], 'actual tool receipt'))
        variants.append((changed(1, output=json.dumps({'task_name': '/root/other'})), 'different agent'))
        variants.append((changed(1, output=json.dumps({'isError': True})), 'dispatch failed'))
        for values, error in variants:
            with self.subTest(error=error), mock.patch.object(research, '_orchestration_items', side_effect=lambda *a: iter(values)):
                with self.assertRaisesRegex(Invalid, error):
                    research._orchestration_turn(path, 'SPAWN', 'FINAL1', 'spawn_agent')

    def test_unfinished_phase_can_resume_but_completed_return_cannot_be_relabelled(self):
        path=self.orchestration_trace()
        events=[json.loads(line) for line in path.read_text().splitlines()]
        resumed={'timestamp':'2030-03-01T13:03:02Z','type':'response_item','payload':{
            'type':'function_call','namespace':'collaboration','name':'followup_task','call_id':'RESUME',
            'arguments':json.dumps({'target':'/root/astra_sample','message':'Continue the same unfinished public phase.'})}}
        path.write_text('\n'.join(json.dumps(v) for v in events[:2]+[resumed]+events[2:])+'\n')
        result=research._orchestration_turn(path,'SPAWN','FINAL1','spawn_agent')
        self.assertEqual(result['continuations'][0]['call_id'],'RESUME')
        self.orchestration_trace('followup_task','LATER','FINAL2','2030-03-01T13:03:06Z','2030-03-01T13:03:09Z')
        with self.assertRaisesRegex(Invalid,'earlier completed return'):
            research._orchestration_turn(path,'SPAWN','FINAL2','spawn_agent')


if __name__ == '__main__':
    unittest.main()
