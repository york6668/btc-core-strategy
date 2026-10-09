"""Synthetic revision 6 ownership, update, expression and recovery scenarios."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import json
import unittest
from unittest import mock

import test_market_state as old
import test_run_flow as adapter
import test_report_care as care_fixture
import test_authored_report as report_fixture
from v4 import closed_loop as loop, market_state, run_flow, sensing, reporting, research, cycle
from v4 import recommendations, report_care, storage, authored_report, delivery
from v4.common import Invalid, create, digest, facts, instant, read, replace


def forward(stamp):
    end = (instant(stamp) + timedelta(days=2)).isoformat()
    return {'observed_window': {'start': stamp, 'end': stamp},
            'judgement_window': {'start': stamp, 'end': end},
            'realization_window': {'start': stamp, 'end': end},
            'timing_basis': 'Synthetic mechanism may resolve over the stated window.',
            'conditions': ['Independent demand persists.'], 'invalidations': ['Demand exits.'],
            'prior_check': {'outcome': 'INITIAL', 'explanation': 'Initial dated synthetic thesis; no previous prediction is a fact.', 'refs': []}}


def market(stamp, refs=()):
    value = old.assessment(stamp, refs)
    for item in value['markets']:
        for horizon in item['horizons']:
            horizon['forward'] = forward(stamp)
    return value


def allocation(core, stamp):
    return {'status': 'PARTIAL', 'summary': '降低非核心风险；保护是否保留待覆盖核验。',
            'actions': [
                {'id': 'risk', 'scope': 'Non-core crypto exposure', 'status': 'DECIDED',
                 'decision': 'REDUCE', 'arrangement': '减少非核心现货；数量待现金和持仓核验。',
                 'gap_impact': 'QUANTITY_ONLY', 'execution': {'status': 'LIMITED', 'limits': ['Quantity unknown.']},
                 'reentry_conditions': ['Demand recovery confirmed by independent sources.']},
                {'id': 'hedge', 'scope': 'Combined protective derivatives', 'status': 'UNDETERMINED',
                 'decision': 'UNDETERMINED', 'arrangement': '现有保护开平无法决定，需核对总覆盖。',
                 'gap_impact': 'ACTION', 'execution': {'status': 'UNDETERMINED', 'limits': ['Coverage unknown.']},
                 'reentry_conditions': []}],
            'execution_limits': ['No size can be inferred from missing balances.'],
            'market_result_hash': loop.public_hash(core), 'fact_version': core['fact_version'],
            'decided_at': stamp, 'valid_until': (instant(stamp) + timedelta(days=2)).isoformat(),
            'review_conditions': ['Reassess reduced exposure and protection together when facts change.']}


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.stamp = '2030-03-01T13:00:00Z'
        self.core = {'outlook': {'market_assessment': market(self.stamp), 'dispute_zones': []},
                     'factor_assessment': {'items': [], 'summary': 'Resolved synthetic evidence.'},
                     'fact_version': {'synthetic': digest('facts')}}
        self.value = allocation(self.core, self.stamp)

    def validate(self, value=None):
        loop.validate_allocation(value or self.value, self.stamp, loop.public_hash(self.core), self.core['fact_version'])

    def test_quantity_gap_preserves_reduce_and_hedge_gap_remains_undecided(self):
        self.validate()
        for key, val in [('decision', 'HOLD'), ('gap_impact', 'QUANTITY_ONLY'), ('status', 'DECIDED')]:
            bad = deepcopy(self.value); bad['actions'][1][key] = val
            with self.assertRaises(Invalid): self.validate(bad)

    def test_version_bindings_and_return_to_participation_are_required(self):
        for mutation in (lambda v: v.update(market_result_hash=digest('other')),
                         lambda v: v.update(fact_version={'synthetic': digest('changed')}),
                         lambda v: v['actions'][0].update(reentry_conditions=[])):
            bad = deepcopy(self.value); mutation(bad)
            with self.assertRaises(Invalid): self.validate(bad)

    def test_expired_forward_window_requires_outcome_even_when_new_dates_move(self):
        old_value = forward(self.stamp)
        new = forward('2030-03-05T13:00:00Z')
        with self.assertRaisesRegex(Invalid, 'actual outcome'):
            loop.validate_forward(new, '2030-03-05T13:00:00Z', lambda r: None, {'forward': old_value})
        new['prior_check']['outcome'] = 'PENDING'
        with self.assertRaisesRegex(Invalid, 'expired forward'):
            loop.validate_forward(new, '2030-03-05T13:00:00Z', lambda r: None, {'forward': old_value})
        new['prior_check'].update(outcome='FAILED', explanation='Cumulative contrary demand observations refuted the original expected path.')
        loop.validate_forward(new, '2030-03-05T13:00:00Z', lambda r: None, {'forward': old_value})

    def test_history_changes_do_not_create_public_facts(self):
        ctx = {'revision': 6, 'identity': {}, 'factor_context': {'current': {'assessment_revision': 6, 'factor_assessment': self.core['factor_assessment']}, 'evidence': {'sources': []}},
               'market_baseline': self.core['outlook'], 'accumulation': {'windows': {}}, 'cycle_baseline': {}}
        before = market_state.market_projection(ctx)
        ctx['market_baseline']['dispute_zones'] = [old.zone(self.stamp)]
        ctx['market_baseline']['dispute_zones'][0]['market'] = 'SPX'
        ctx['market_baseline']['market_assessment']['summary'] = 'A changed historical prediction.'
        after = market_state.market_projection(ctx)
        for key in ('public_evidence', 'factor_context', 'accumulation', 'report_subjects'):
            self.assertEqual(before[key], after[key])
        self.assertNotEqual(before['historical_hypotheses'], after['historical_hypotheses'])

    def test_area_requires_evidence_kind_and_formation_not_just_high_low(self):
        with self.assertRaisesRegex(Invalid, 'evidence kind'):
            market_state.validate_zones([old.zone(self.stamp)], lambda r: None, revision=6)


class HourlyClosedLoopTests(unittest.TestCase):
    def setUp(self):
        self.f = adapter.RunFlowTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.f.store
        policy = read(self.root / 'write-policy.json')
        replace(self.root / 'write-policy.json', dict(policy, minimum_assessment_revision=5))

    def prepare(self, directory=None):
        return run_flow.prepare(self.root, self.f.f.project, 'SYNTHETIC_R6', directory or self.f.d,
            self.f.raw_file, agent_id='SYNTHETIC_MAIN', workflow_revision=6)

    def public(self, prepared):
        run = read(prepared['run']); raw = read(run['market'])
        value = self.f.authored(prepared)
        value = {k: value[k] for k in market_state.MARKET_FIELDS if k in value}
        value.update(market_assessment=market(raw['cutoff']), dispute_zones=[])
        return value

    def freeze(self):
        p = self.prepare(); public = self.public(p)
        return p, run_flow.advance(p['run'], 'market', self.f.file(public)), public

    def personal(self, frozen):
        value = read(frozen['portfolio_context'])
        form = {'recommendation_checks': self.f.authored({'observation_template': frozen['observation_template']})['recommendation_checks'],
                'main_evaluation': {'required': False, 'reasons': []},
                'allocation_review': {'market_result_hash': frozen['market_result_hash'], 'allocation_decision_hash': None,
                    'fact_version': value['fact_version'], 'outcome': 'EVIDENCE_LIMITED',
                    'explanation': 'Legacy portfolio prose is not a formally assessed allocation decision.',
                    'limitations': ['Allocation requires actual assessment.'], 'material': False}}
        return form

    def test_account_edits_do_not_change_public_material_or_frozen_result(self):
        p, frozen, public = self.freeze()
        before = read(p['context']); old_hash = frozen['market_result_hash']
        with (self.f.f.project / '资产配置.md').open('a') as stream:
            stream.write('\nSynthetic changed cost, target and holdings.\n')
        after = self.prepare(self.f.f.base / 'changed-facts')
        self.assertEqual(before, read(after['context']))
        refreshed = run_flow.advance(p['run'], 'market', self.f.file(public))
        self.assertEqual(old_hash, refreshed['market_result_hash'])
        self.assertNotEqual(read(frozen['portfolio_context'])['fact_version'], read(refreshed['portfolio_context'])['fact_version'])
        # Facts-only refresh commits observation without rebuilding its public phase.
        result = run_flow.advance(p['run'], 'observe', template_path=refreshed['observation_template'], answers_path=self.f.file(self.personal(refreshed)))
        self.assertEqual(sensing.observation(self.root, result['observation_id'])['review']['market_result_hash'], old_hash)

    def test_invalid_market_shape_fails_before_freeze_and_portfolio_disclosure(self):
        p = self.prepare(); public = self.public(p)
        public['market_review']['deep_review']['required'] = 'not a bool'
        with self.assertRaises(ValueError): run_flow.advance(p['run'], 'market', self.f.file(public))
        self.assertFalse((self.f.d / 'market-result.json').exists())
        self.assertFalse(list(self.f.d.glob('portfolio-context*')))

    def test_actual_public_candidate_is_readable_before_freeze_and_preserves_reviewed_scope(self):
        p = self.prepare(); public = self.public(p)
        ambiguous = '甲所与乙所价格回升；主动成交差额为负，持仓量下降。'
        scoped = '甲所与乙所价格回升；甲所现货与永续主动成交差额为负，甲所永续持仓量下降。'
        public['market_review']['changes_since_deep'] = [ambiguous]
        owners = {str(f): f.read_bytes() for f in self.root.rglob('*') if f.is_file()}
        fact_version = facts(self.f.f.project)
        candidate = run_flow.advance(p['run'], 'market', template_path=p['market_template'],
            answers_path=self.f.file(public), check_only=True)
        # The program exposes the real ambiguity; it does not claim to judge it.
        self.assertEqual(candidate['scope'], 'structure_and_bindings_only')
        original = Path(candidate['market_candidate']).read_bytes()
        self.assertEqual(read(candidate['market_candidate'])['payload'], public)
        material = read(candidate['review_material'])
        self.assertEqual(material['market_candidate'], read(candidate['market_candidate']))
        self.assertIn(market_state.PUBLIC_RESULT_REVIEW, material['instructions'])
        view = read(candidate['read_set']['market_review'])
        parts = ''.join(Path(part).read_text() for item in view['required'] for part in item['parts'])
        self.assertIn(ambiguous, parts)
        self.assertFalse((self.f.d / 'market-result.json').exists())
        self.assertFalse(list(self.f.d.glob('portfolio-*')))
        self.assertEqual(owners, {str(f): f.read_bytes() for f in self.root.rglob('*') if f.is_file()})
        self.assertEqual(fact_version, facts(self.f.f.project))
        # A model correction creates a new candidate, retaining the original.
        public['market_review']['changes_since_deep'] = [scoped]
        reviewed = run_flow.advance(p['run'], 'market', self.f.file(public), check_only=True)
        frozen = run_flow.advance(p['run'], 'market', reviewed['market_candidate'])
        self.assertEqual(Path(frozen['market_result']).read_bytes(), Path(reviewed['market_candidate']).read_bytes())
        self.assertEqual(original, Path(candidate['market_candidate']).read_bytes())
        result = run_flow.advance(p['run'], 'observe', template_path=frozen['observation_template'],
            answers_path=self.f.file(self.personal(frozen)))
        saved = sensing.observation(self.root, result['observation_id'])['review']
        self.assertEqual(saved['market_review']['changes_since_deep'], [scoped])
        following = self.prepare(self.f.f.base / 'following')
        history = read(following['context'])['historical_hypotheses']['previous_market_observation']
        self.assertEqual(history['market_review']['changes_since_deep'], [scoped])

    def test_candidate_damage_and_stale_public_context_cannot_freeze(self):
        p = self.prepare(); public = self.public(p)
        result = run_flow.advance(p['run'], 'market', self.f.file(public), check_only=True)
        candidate = read(result['market_candidate'])
        changed = deepcopy(candidate)
        changed['payload']['market_review']['assessment_summary'] = 'Changed after review.'
        with self.assertRaisesRegex(Invalid, 'candidate changed'):
            run_flow.advance(p['run'], 'market', self.f.file(changed))
        changed['result_hash'] = digest(changed['payload'])
        with self.assertRaisesRegex(Invalid, 'actual prechecked'):
            run_flow.advance(p['run'], 'market', self.f.file(changed))
        changed = dict(candidate, market_context_id=digest('different public context'))
        with self.assertRaisesRegex(Invalid, 'candidate context changed'):
            run_flow.advance(p['run'], 'market', self.f.file(changed))
        latest = sensing.context(self.root, self.f.f.project / '.strategy/market_observation.sqlite3',
            self.f.f.project, read(read(p['run'])['market']), evidence_dir=read(p['run'])['evidence'], workflow_revision=6)
        latest['accumulation'] = {'changed': 'New public observation after candidate review.'}
        with mock.patch('v4.sensing.context', return_value=latest):
            with self.assertRaisesRegex(Invalid, 'public market inputs changed'):
                run_flow.advance(p['run'], 'market', result['market_candidate'])
        self.assertFalse((self.f.d / 'market-result.json').exists())
        self.assertFalse(list(self.f.d.glob('portfolio-context*')))

    def test_market_check_only_cli_exposes_candidate_and_other_stages_keep_their_boundaries(self):
        import strategy_v4
        p = self.prepare(); public = self.public(p)
        receipt = self.f.d.resolve() / 'market-stage-receipt.json'
        with mock.patch('sys.stdout'):
            code = strategy_v4.main(['--root', str(self.root), '--project', str(self.f.f.project),
                'run-advance', '--run', p['run'], '--stage', 'market', '--template', p['market_template'],
                '--answers', str(self.f.file(public)), '--check-only', '--receipt', str(receipt)])
        self.assertEqual(code, 0)
        self.assertEqual(read(receipt)['status'], 'MARKET_CANDIDATE_READY')
        self.assertFalse((self.f.d / 'market-result.json').exists())
        for stage in ('observe', 'report'):
            with self.assertRaisesRegex(Invalid, 'market or main stage'):
                run_flow.advance(p['run'], stage, check_only=True)

    def test_completed_report_material_contains_results_not_internal_factor_and_module_prose(self):
        p, frozen, public = self.freeze()
        result = run_flow.advance(p['run'], 'observe', template_path=frozen['observation_template'], answers_path=self.f.file(self.personal(frozen)))
        brief = read(result['report_context'])
        self.assertEqual(brief['market_result']['market_assessment'], public['market_assessment'])
        self.assertIsNone(brief['allocation_decision'])
        self.assertEqual(brief['applicability']['portfolio']['outcome'], 'EVIDENCE_LIMITED')
        for key in ('current_strategy', 'observation', 'collector_observations'):
            self.assertNotIn(key, brief)
        self.assertFalse(read(result['read_set']['report']).get('base_read_set'))
        self.assertEqual(storage.current(self.root)['assessment']['revision'], 3)

    def test_cycle_result_survives_short_horizon_order_and_hourly_direction_changes(self):
        original = storage.current(self.root)
        for number, direction in enumerate(('BULLISH', 'BEARISH', 'UNKNOWN')):
            with self.subTest(hourly_direction=direction):
                self.f.d = self.f.f.base / ('cycle-' + str(number))
                prepared = self.prepare()
                public = self.public(prepared)
                # The model's investment horizon is not the first array item.
                # These independent model outputs are not a program classifier.
                public['market_assessment']['summary'] = '主要投资周期仍为修复中的震荡，日内变化未改变价值依据；周期证据缺口保留。'
                for assessed in public['market_assessment']['markets']:
                    hourly = assessed['horizons'][0]
                    primary = deepcopy(hourly)
                    primary.update(id=assessed['id'] + '_CYCLE', direction='NEUTRAL',
                                   period='Investment horizon selected from the synthetic mechanism.',
                                   structure='Recovery within the assessed cycle, without a confirmed switch.')
                    primary['forward']['judgement_window']['end'] = '2030-04-01T13:00:00Z'
                    primary['forward']['realization_window'] = None
                    primary['forward']['timing_basis'] = 'The investment mechanism has no precise realization date.'
                    hourly.update(direction=direction, period='Hourly verification only.')
                    if number:
                        hourly['forward']['prior_check'].update(outcome='REVISE',
                            explanation='Hourly evidence changes direction within the existing window.')
                        primary['forward']['prior_check'].update(outcome='PENDING',
                            explanation='The cycle mechanism remains supported despite hourly changes.')
                    assessed['horizons'] = [hourly, primary]
                    for relation in ('resonance', 'reversal'):
                        assessed[relation]['periods'] = [hourly['id'], primary['id']]
                    assessed['resonance']['status'] = 'UNKNOWN' if direction == 'UNKNOWN' else 'CONFLICTED'
                expected = deepcopy(public['market_assessment'])
                frozen = run_flow.advance(prepared['run'], 'market', self.f.file(public))
                portfolio = read(frozen['portfolio_context'])
                self.assertEqual(portfolio['market_result']['payload']['market_assessment'], expected)
                result = run_flow.advance(prepared['run'], 'observe',
                    template_path=frozen['observation_template'], answers_path=self.f.file(self.personal(frozen)))
                report = read(result['report_context'])
                self.assertEqual(report['market_result']['market_assessment'], expected)
                self.assertEqual(report['result_instructions'], loop.REPORT_INSTRUCTIONS)
                self.assertNotIn(market_state.CYCLE_FOCUS_REVIEW, report['result_instructions'])
                self.assertEqual(storage.current(self.root), original)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.old = old.RevisionFivePublicationTests()
        self.old.setUp(); self.addCleanup(self.old.doCleanups)
        self.f, self.c = self.old.f, self.old.c
        policy = read(self.f.store / 'write-policy.json')
        replace(self.f.store / 'write-policy.json', dict(policy, minimum_assessment_revision=6))
        self.f.draft['revision'] = 6
        self.f.draft['outlook']['market_assessment'] = market(self.f.draft['temporal_scope']['knowledge_cutoff'], self.f.draft['outlook']['refs'])
        self.f.draft['outlook']['dispute_zones'] = []
        self.f.draft['recommendations']['portfolio']['allocation_decision'] = allocation(self.f.draft, self.f.draft['temporal_scope']['knowledge_cutoff'])
        self.public = {'outlook': deepcopy(self.f.draft['outlook']), 'factor_assessment': deepcopy(self.f.draft['factor_assessment']), 'market_report': self.old.public['market_report']}
        self.c.d.bind()

    def test_revision_six_actual_research_mapping_publish_and_read(self):
        req, saved, _ = self.c.study(authored=True, independent_output=json.dumps(self.public))
        published = self.c.d.commit()
        self.assertEqual(published['assessment']['revision'], 6)
        self.assertEqual(storage.current(self.f.store)['decision_id'], published['decision_id'])
        self.assertEqual(loop.allocation(published['assessment']), loop.allocation(self.f.draft))
        personal = research.read_input(self.f.store, req['request_id'], 'portfolio', independent_id=saved['independent_id'])['payload']
        self.assertEqual(personal['market_result'], self.public)
        # Quantity insufficiency is an executable-tier limitation, not a negative direction.
        rec = deepcopy(self.f.draft['recommendations'])
        rec['lower_spot'].update(status='EXECUTION_LIMITED', tiers=[])
        rec['portfolio']['capital_envelope_u'] = None
        seq = recommendations.validate(rec, self.f.draft['temporal_scope']['knowledge_cutoff'], lambda r: None, 6)
        self.assertIsNone(seq['spot_only'][0]['spot_capital_u'])
        self.assertIsNone(seq['spot_only'][0]['cumulative_capital_u'])
        self.assertEqual(rec['portfolio']['allocation_decision']['actions'][0]['decision'], 'REDUCE')

    def test_bad_final_market_binding_cannot_publish(self):
        bad = cycle.candidate(self.f.draft)
        bad['assessment']['recommendations']['portfolio']['allocation_decision']['market_result_hash'] = digest('changed')
        with self.assertRaisesRegex(Invalid, 'different frozen market'):
            cycle.validate_candidate(bad)

    def test_native_public_amendment_keeps_revision_six_zones_and_provenance(self):
        req, saved, _ = self.c.study(authored=True, independent_output=json.dumps(self.public))
        original = research._load(self.f.store, 'independent', saved['independent_id'])
        amended = deepcopy(self.public)
        stamp = self.f.draft['temporal_scope']['knowledge_cutoff']
        zone = old.zone(stamp)
        zone['area'].update(lower=None, upper=None)
        side = {'formation': 'UNKNOWN', 'validation': 'UNKNOWN',
                'evidence_window': {'start': stamp, 'end': stamp},
                'identified_at': stamp, 'tested_at': None, 'horizon': 'Synthetic near term',
                'basis': 'No verified boundary formation yet.', 'refs': [],
                'next_check': 'Verify actual price response.'}
        zone.update(role='MAIN_DISPUTE', evidence_kind='PRICE_RESPONSE',
                    limitations='No volume-at-price or order-book evidence.',
                    boundary_review={'revision': 2, 'lower': deepcopy(side), 'upper': deepcopy(side),
                                     'prior_zone_assessment': 'New synthetic candidate; no historical backfill.'})
        amended['outlook']['dispute_zones'] = [zone]
        path = Path(original['rollout_path'])
        def append_turn(identity, value, offset):
            with path.open('a') as stream:
                stream.write(json.dumps({'type': 'turn_context', 'payload': {
                    'turn_id': identity, 'model': 'gpt-6-astra', 'effort': 'max'}}) + '\n')
                stream.write(json.dumps({'type': 'event_msg', 'payload': {
                    'type': 'task_complete', 'turn_id': identity,
                    'started_at': original['completed_at'] + offset,
                    'completed_at': original['completed_at'] + offset + 10,
                    'last_agent_message': json.dumps(value)}}) + '\n')
        append_turn('PUBLIC_AMENDMENT', amended, 20)
        revised = research.amend_market(self.f.store, original['independent_id'], path,
                                       turn_id='PUBLIC_AMENDMENT')
        research._verify_market_amendment(revised)
        self.assertEqual(revised['market_result'], amended)
        self.assertEqual(revised['parent_independent_id'], original['independent_id'])
        self.assertEqual(revised['turn_id'], original['turn_id'])
        self.assertEqual(research._load(self.f.store, 'independent', original['independent_id']), original)
        personal = research.read_input(self.f.store, req['request_id'], 'portfolio',
                                       independent_id=revised['independent_id'])['payload']
        self.assertEqual(personal['market_result'], amended)
        self.f.draft['outlook'] = deepcopy(amended['outlook'])
        loop.allocation(self.f.draft)['market_result_hash'] = loop.public_hash(self.f.draft)
        candidate = cycle.candidate(self.f.draft)
        with mock.patch('v4.research.now', return_value='2030-03-01T13:10:00Z'):
            preview = research.reveal(self.f.store, req['request_id'], revised['independent_id'],
                                      candidate, check_only=True)
            self.assertTrue(preview['valid'])
            disclosed = research.reveal(self.f.store, req['request_id'], revised['independent_id'], candidate)
        self.assertEqual(disclosed['candidate'], candidate)
        self.assertEqual(disclosed['candidate_hash'], preview['candidate_hash'])
        invalid = deepcopy(amended['outlook']['dispute_zones'])
        invalid[0].pop('evidence_kind')
        with self.assertRaisesRegex(Invalid, 'price area evidence kind required'):
            market_state.validate_zones(invalid, lambda ref: None, revision=6)
        # Revision-aware validation still rejects disappearing active zones.
        append_turn('DROPPED_ZONE', self.public, 40)
        with self.assertRaisesRegex(Invalid, 'disappeared without retirement'):
            research.amend_market(self.f.store, revised['independent_id'], path, turn_id='DROPPED_ZONE')

    def test_delayed_publication_does_not_renew_an_expired_allocation(self):
        self.c.study(authored=True, independent_output=json.dumps(self.public))
        with mock.patch('v4.cycle.now', return_value='2030-03-04T13:00:00Z'):
            with self.assertRaisesRegex(Invalid, 'allocation decision expired'):
                self.c.d.commit()

    def test_portfolio_fact_change_reuses_research_and_frozen_market_without_new_study(self):
        req, saved, spec = self.c.study(authored=True, independent_output=json.dumps(self.public))
        first = self.c.d.commit()
        original_input = read(self.f.store / req['independent_input_path'])
        (self.f.project / '资产配置.md').write_text('Synthetic user-confirmed preference change; no trade.\n')
        self.f.draft['fact_version'] = facts(self.f.project)
        account = self.f.draft['domain']['account_facts']
        account['fact_owner_fingerprints']['ASSET_CONFIGURATION'] = self.f.draft['fact_version']['资产配置.md']
        account.update(account_fact_changed_known=False,
            known_change_evidence=[{'confirmation_type': 'USER_CONFIRMED_PREFERENCE_CHANGE', 'owner_id': 'ASSET_CONFIGURATION'}],
            local_record_status='PREFERENCE_RECORDED')
        self.f.draft['domain']['user_input_requirement'] = 'NONE'
        loop.allocation(self.f.draft)['fact_version'] = self.f.draft['fact_version']
        loop.allocation(self.f.draft)['actions'][0]['arrangement'] = '减少非核心现货；确认新约束后仍保持该方向，数量受限。'
        for section in ('outlook', 'factor_assessment'):
            self.f.draft['section_provenance'][section].update(mode='REUSED', decision_id=first['decision_id'])
        with mock.patch('v4.research.now', return_value='2030-03-01T13:04:00Z'):
            disclosed = research.reveal(self.f.store, req['request_id'], saved['independent_id'], cycle.candidate(self.f.draft))
        output = json.loads(saved['analysis']); output['candidate_hash'] = disclosed['candidate_hash']
        output['report_narrative'] = self.f.draft['conclusion']['narrative']
        proof = research.read_input(self.f.store, req['request_id'], 'candidate', disclosed['candidate_id'])
        events = [
            {'type': 'turn_context', 'payload': {'turn_id': 'PORTFOLIO_ONLY', 'model': 'gpt-6-astra', 'effort': 'max'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': 'PORTFOLIO_ONLY', 'input': 'python strategy_v4.py research-read --request ' + req['request_id'] + ' --phase candidate'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'PORTFOLIO_ONLY', 'output': json.dumps(proof)}},
            {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'PORTFOLIO_ONLY',
                'started_at': datetime.fromisoformat('2030-03-01T13:04:01+00:00').timestamp(),
                'completed_at': datetime.fromisoformat('2030-03-01T13:04:03+00:00').timestamp(),
                'last_agent_message': json.dumps(output)}}]
        with Path(saved['rollout_path']).open('a') as stream:
            stream.writelines(json.dumps(event) + '\n' for event in events)
        spec.update(candidate_id=disclosed['candidate_id'], turn_id='PORTFOLIO_ONLY', analysis=json.dumps(output))
        with mock.patch('v4.research.now', return_value='2030-03-01T13:04:04Z'):
            updated = research.result(self.f.store, spec)
        self.f.draft['research_absorptions'][0]['result_id'] = updated['result_id']
        self.c.d.bind()
        second = self.c.d.commit()
        self.assertNotEqual(first['assessment']['fact_version'], second['assessment']['fact_version'])
        self.assertEqual(loop.public_hash(first['assessment']), loop.public_hash(second['assessment']))
        self.assertEqual(original_input, read(self.f.store / req['independent_input_path']))
        self.assertEqual(len(list((self.f.store / 'research/requests').glob('*.json'))), 1)
        self.assertEqual(len(list((self.f.store / 'research/independent').glob('*.json'))), 1)


class ScopedCareTests(unittest.TestCase):
    def setUp(self):
        self.f = care_fixture.ReportCareTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.ctx = self.f.context(self.f.root); self.ctx['market_revision'] = 6

    def spec(self):
        value = self.f.spec('SILENT')
        value['maintenance']['retired'] = []
        value['maintenance']['triggers'][0]['scope'] = 'MARKET'
        value['maintenance']['checked'].update(outcome='EVIDENCE_LIMITED', outcomes={'MARKET': 'VALID', 'PORTFOLIO': 'EVIDENCE_LIMITED'})
        return value

    def test_personal_gap_does_not_turn_market_unknown_or_assume_hold(self):
        value = self.spec()
        report_care.validate(self.f.root, value, self.ctx)
        self.assertEqual(value['maintenance']['checked']['outcomes']['MARKET'], 'VALID')
        self.assertEqual(value['maintenance']['checked']['outcome'], 'EVIDENCE_LIMITED')

    def test_important_undecided_allocation_cannot_hide_as_silence(self):
        self.ctx['observation'] = {'review': {'allocation_review': {'material': True}}}
        with self.assertRaisesRegex(Invalid, 'important portfolio'):
            report_care.validate(self.f.root, self.spec(), self.ctx)

    def test_personal_reference_cannot_be_a_market_condition(self):
        value = self.spec()
        value['maintenance']['triggers'][0]['refs'] = ['/assessment/recommendations/portfolio/narrative']
        with self.assertRaisesRegex(Invalid, 'private account'):
            report_care.validate(self.f.root, value, self.ctx)

    def test_recheck_conditions_can_bind_final_market_and_allocation_inputs(self):
        core = deepcopy(storage.current(self.f.root)['assessment'])
        stamp = core['temporal_scope']['knowledge_cutoff']
        core['outlook'].update(market_assessment=market(stamp), dispute_zones=[])
        core['factor_assessment'] = {'items': [], 'summary': 'Synthetic public evidence.'}
        core['recommendations']['portfolio']['allocation_decision'] = allocation(core, stamp)
        self.ctx.update(current_strategy=core)
        self.ctx['identity']['fact_version'] = core['fact_version']
        value = self.spec()
        value['maintenance']['triggers'][0]['refs'] = ['/market_result/market_assessment/markets/0/horizons/0/forward/invalidations/0']
        value['maintenance']['triggers'].append({'id': 'RETURN', 'scope': 'PORTFOLIO',
            'condition': 'Review restoration of participation after the recorded conditions.',
            'reason': 'Reduced participation retains its own explicit return condition.',
            'refs': ['/allocation_decision/review_conditions/0']})
        report_care.validate(self.f.root, value, self.ctx)


class FinalReportTests(unittest.TestCase):
    def setUp(self):
        self.f = report_fixture.AuthoredReportTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.base_context = self.f.f.context
        self.core = self.f.f.f.decision['assessment']
        self.stamp = self.core['temporal_scope']['knowledge_cutoff']
        self.core.update(revision=6)
        self.core['factor_assessment'] = {'items': [], 'summary': 'Synthetic resolved public interpretation.'}
        self.core['outlook'].update(market_assessment=market(self.stamp), dispute_zones=[])
        self.core['recommendations']['portfolio']['allocation_decision'] = allocation(self.core, self.stamp)
        current = self.f.f.f.decision
        current['decision_id'] = digest({k: v for k, v in current.items() if k != 'decision_id'})[7:]
        create(self.root / 'versions' / current['decision_id'] / 'decision.json', current)
        (self.root / 'current').unlink()
        (self.root / 'current').symlink_to('versions/' + current['decision_id'])
        self.f.f.context = self.context
        mock.patch('v4.reporting.context', side_effect=self.context).start()

    def context(self, root):
        ctx = self.base_context(root)
        ctx.update(market_revision=6, evaluated_at=self.stamp, current_strategy=self.core)
        ctx['identity']['fact_version'] = self.core['fact_version']
        ctx['observation'] = {'baseline_decision_id': ctx['identity']['decision_id'], 'review': {'allocation_review': {
            'outcome': 'EVIDENCE_LIMITED', 'allocation_decision_hash': digest(loop.allocation(self.core)),
            'limitations': ['Protective coverage remains unknown.']}}}
        return ctx

    def test_report_inputs_require_observation_decision_identity(self):
        ctx = self.context(self.root)
        review = ctx['observation']['review']
        review['market_assessment'] = market(self.stamp)
        review['market_assessment']['summary'] = 'Synthetic later observation of the current decision.'
        review['dispute_zones'] = []
        review['applicability_review'] = {'outcome': 'VALID', 'limitations': 'Synthetic current thesis check.'}
        matched = loop.report_inputs(ctx)
        self.assertEqual(matched['market_result']['market_assessment'], review['market_assessment'])
        self.assertEqual(matched['applicability']['market'], review['applicability_review'])
        self.assertEqual(matched['applicability']['portfolio'], review['allocation_review'])
        expected = {key: self.core['outlook'][key] for key in ('market_assessment', 'dispute_zones')}
        decision_id = ctx['identity']['decision_id']
        for label, observation in (
                ('observation_absent', None), ('observation_empty', {}),
                ('baseline_absent', {'review': review}),
                ('baseline_mismatch', {'baseline_decision_id': digest('previous decision'), 'review': review}),
                ('matching_review_absent', {'baseline_decision_id': decision_id}),
                ('matching_review_null', {'baseline_decision_id': decision_id, 'review': None}),
                ('matching_review_empty', {'baseline_decision_id': decision_id, 'review': {}})):
            with self.subTest(observation=label):
                changed = dict(ctx, observation=observation)
                before = deepcopy(changed)
                result = loop.report_inputs(changed)
                self.assertEqual(result['market_result'], expected)
                self.assertEqual(result['allocation_decision'], loop.allocation(self.core))
                self.assertEqual(result['applicability']['market']['outcome'], 'EVIDENCE_LIMITED')
                self.assertEqual(result['applicability']['portfolio']['outcome'], 'EVIDENCE_LIMITED')
                self.assertEqual(changed, before)

    def test_missing_decision_identity_never_authorizes_observation(self):
        for missing, value in (('absent', None), ('null', None), ('empty', ''),
                               ('integer', 1), ('boolean', True), ('list', ['synthetic']),
                               ('object', {'decision': 'synthetic'})):
            with self.subTest(decision_id=missing):
                ctx = self.context(self.root)
                if missing == 'absent':
                    ctx['identity'].pop('decision_id')
                else:
                    ctx['identity']['decision_id'] = value
                ctx['observation']['baseline_decision_id'] = value
                review = ctx['observation']['review']
                review['market_assessment'] = market(self.stamp)
                review['market_assessment']['summary'] = 'Unbound observation must not replace the formal result.'
                review['applicability_review'] = {'outcome': 'VALID'}
                review['allocation_review']['outcome'] = 'VALID'
                before = deepcopy(ctx)
                result = loop.report_inputs(ctx)
                self.assertEqual(result['market_result'],
                    {key: self.core['outlook'][key] for key in ('market_assessment', 'dispute_zones')})
                self.assertEqual(result['allocation_decision'], loop.allocation(self.core))
                self.assertEqual(result['applicability']['market']['outcome'], 'EVIDENCE_LIMITED')
                self.assertEqual(result['applicability']['portfolio']['outcome'], 'EVIDENCE_LIMITED')
                self.assertEqual(ctx, before)

    def spec(self):
        value = self.f.spec()
        market_text = '市场结论：短期偏空，较长周期尚未确认；卖流与修复力度存在冲突。'
        personal = loop.allocation(self.core)
        lines = [a['arrangement'] for a in personal['actions']]
        value['layout']['body'] = market_text + '\n\n配置决定：' + ''.join(lines)
        value['information'] = {'market': market_text, 'allocation': ''.join(lines)}
        value['layout']['bindings'] = [
            {'text': market_text, 'scope': 'market', 'refs': ['/market_result/market_assessment/summary']},
            *[{'text': line, 'scope': 'personal', 'refs': ['/allocation_decision/actions/%d/arrangement' % i]}
              for i, line in enumerate(lines)]]
        value['result_binding'] = loop.report_inputs(self.context(self.root))['result_binding']
        return value

    def test_final_arrangements_survive_unknown_send_recovery_and_real_readback(self):
        spec = self.spec()
        identity, source = self.f.queue(spec)
        self.assertEqual(source['body'], spec['layout']['body'])
        self.f.begin(identity, source)
        delivery.transition(self.root, identity, 'UNKNOWN')
        self.assertFalse(self.f.begin(identity, source)['ready_to_send'])
        status = delivery.transition(self.root, identity, 'CONFIRM', self.f.receipt(identity))
        self.assertEqual((status['state'], status['attempts']), ('DELIVERED', 1))
        self.assertEqual(reporting.reported(self.root)['display'], spec['layout']['body'])
        with self.assertRaisesRegex(Invalid, 'same mobile meaning'):
            self.f.source(self.spec())

    def test_internal_analysis_and_changed_results_cannot_replace_final_arrangements(self):
        spec = self.spec()
        spec['layout']['bindings'][1]['refs'] = ['/assessment/portfolio_analysis/core_spot']
        with self.assertRaisesRegex(Invalid, 'only assessed risk'):
            self.f.source(spec)
        spec = self.spec()
        spec['result_binding']['allocation_decision_hash'] = digest('unreviewed')
        with self.assertRaisesRegex(Invalid, 'final market, allocation'):
            self.f.source(spec)

    def test_before_send_rechecks_applicability_without_rewriting_body(self):
        identity, source = self.f.queue(self.spec())
        original = self.core['recommendations']['portfolio']['allocation_decision']['valid_until']
        self.core['recommendations']['portfolio']['allocation_decision']['valid_until'] = self.stamp
        self.assertFalse(reporting.due(self.root, source))
        self.assertEqual(read(self.root / 'outbox' / identity / 'artifact.json')['body'], source['body'])
        self.core['recommendations']['portfolio']['allocation_decision']['valid_until'] = original

    def test_displayed_execution_prices_keep_their_formal_consensus_identity(self):
        spec = self.spec()
        line = '已核验的保护参考为 82,000 USDT。'
        spec['layout']['body'] += '\n\n' + line
        spec['layout']['bindings'].append({'text': line, 'scope': 'personal',
            'refs': ['/execution_plans/upper_short/tiers/0/price_low_usd']})
        identity, source = self.f.queue(spec); self.f.begin(identity, source)
        delivery.transition(self.root, identity, 'CONFIRM', self.f.receipt(identity))
        self.assertEqual(reporting.reported(self.root)['consensus_prices']['upper_short']['tiers'],
                         [{'price_low_usd': '82000', 'price_high_usd': '82000'}])


if __name__ == '__main__':
    unittest.main()
