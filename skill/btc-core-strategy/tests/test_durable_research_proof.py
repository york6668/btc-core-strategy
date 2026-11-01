"""Synthetic official-source projections; no captured user data is committed."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import node_bridge
from v4 import durable_proof, main_flow, research, run_flow
from v4.common import Invalid, digest
from test_workflow_research import ResearchWorkflowTests


def sources(method='spawn_agent', call='START', returned='FINAL', turn='PUBLIC',
            start='2030-03-01T13:03:01Z', end='2030-03-01T13:03:03Z', child='child'):
    author = '/root/astra_sample'
    activity = {'type': 'subAgentActivity', 'id': call,
                'kind': 'started' if method == 'spawn_agent' else 'interacted',
                'agentThreadId': child, 'agentPath': author}
    done = dict(activity, id='subagent-completed-' + turn, kind='completed')
    parent = {'schemaVersion': 1, 'thread': {'kind': 'codex', 'id': 'parent', 'hostId': 'durable'},
              'turns': [{'id': 'PARENT-' + turn, 'items': [activity, done]}]}
    output = {'schemaVersion': 1, 'thread': {'kind': 'codex', 'id': child, 'hostId': 'durable'},
              'turns': [{'id': turn, 'status': 'completed', 'error': None,
                         'startedAt': datetime.fromisoformat(start.replace('Z', '+00:00')).timestamp(),
                         'completedAt': datetime.fromisoformat(end.replace('Z', '+00:00')).timestamp(),
                         'items': [{'type': 'agentMessage', 'id': returned, 'phase': 'final_answer',
                                    'text': 'Actual synthetic output ' + returned}]}]}
    return parent, output


def write_proof(directory, parent, child):
    manifest = {'schema': durable_proof.SCHEMA, 'provider': 'mcp__codex_app__read_thread',
                'parent_turn_id': parent['turns'][0]['id'], 'child_turn_id': child['turns'][0]['id']}
    for key, source in [('parent', parent), ('child', child)]:
        raw = json.dumps(source).encode()
        identity = digest(raw)
        name = identity[7:] + '.json'
        (directory / name).write_bytes(raw)
        manifest[key] = {'path': name, 'sha256': identity}
    raw = json.dumps(manifest, indent=2).encode()
    path = directory / ('proof-' + digest(raw)[7:] + '.json')
    path.write_bytes(raw)
    return path


class DurableProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_actual_output_and_unknown_runtime_fields_stay_distinct(self):
        path = write_proof(self.directory, *sources())
        proof = research._orchestration_turn(path, 'START', 'FINAL', 'spawn_agent')
        self.assertEqual(proof['analysis'], 'Actual synthetic output FINAL')
        self.assertEqual(proof['verification_scope'], durable_proof.SCOPE)
        self.assertIsNone(proof['dispatch_line'])
        self.assertIsNone(proof['source_verification']['runtime_model'])
        self.assertFalse(proof['source_verification']['runtime_configuration_verified'])
        self.assertFalse(proof['source_verification']['inbound_messages_verified'])
        self.assertEqual(proof['source_verification']['return_pointer'], '/turns/0/items/0')

    def test_incomplete_failed_ambiguous_and_wrong_identity_rejected(self):
        mutations = [
            ('failed', lambda p, c: c['turns'][0].update(status='failed')),
            ('error', lambda p, c: c['turns'][0].update(error={'message': 'interrupted'})),
            ('missing clock', lambda p, c: c['turns'][0].update(startedAt=None)),
            ('reverse clock', lambda p, c: c['turns'][0].update(completedAt=1)),
            ('not final', lambda p, c: c['turns'][0]['items'][0].update(phase='commentary')),
            ('truncated', lambda p, c: c['turns'][0]['items'][0].update(truncated=True)),
            ('duplicate final', lambda p, c: c['turns'][0]['items'].append(deepcopy(c['turns'][0]['items'][0]))),
            ('wrong author', lambda p, c: p['turns'][0]['items'][1].update(agentPath='/root/other')),
            ('wrong child', lambda p, c: p['turns'][0]['items'][0].update(agentThreadId='other')),
            ('wrong host', lambda p, c: c['thread'].update(hostId='local')),
            ('completion first', lambda p, c: p['turns'][0]['items'].reverse()),
            ('earlier completed', lambda p, c: p['turns'][0]['items'].insert(1,
                 dict(p['turns'][0]['items'][1], id='subagent-completed-OLD'))),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                parent, child = sources()
                mutate(parent, child)
                path = write_proof(self.directory, parent, child)
                with self.assertRaises(Invalid):
                    research._orchestration_turn(path, 'START', 'FINAL', 'spawn_agent')

    def test_original_sources_are_rechecked_and_bridged_exactly(self):
        path = write_proof(self.directory, *sources())
        target = self.directory / 'bridge'
        node_bridge.copy_proof(path, target)
        self.assertEqual(research._orchestration_turn(path, 'START', 'FINAL', 'spawn_agent'),
                         research._orchestration_turn(target / path.name, 'START', 'FINAL', 'spawn_agent'))
        node_bridge.copy_proof(path, target)  # Immutable duplicate copy is harmless.
        manifest = json.loads(path.read_bytes())
        raw = self.directory / manifest['child']['path']
        raw.write_bytes(raw.read_bytes() + b' ')
        with self.assertRaisesRegex(Invalid, 'source bytes changed'):
            research._orchestration_turn(path, 'START', 'FINAL', 'spawn_agent')
        with self.assertRaisesRegex(Invalid, 'source bytes changed'):
            node_bridge.copy_proof(path, target)

    def test_reference_escape_and_unbound_main_assertion_rejected(self):
        path = write_proof(self.directory, *sources())
        manifest = json.loads(path.read_bytes())
        manifest['parent']['path'] = '../parent.json'
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(Invalid, 'content-addressed sibling'):
            research._orchestration_turn(path, 'START', 'FINAL', 'spawn_agent')
        path.write_text(json.dumps({'schema': durable_proof.SCHEMA, 'author': '/root/astra_sample', 'completed': True}))
        with self.assertRaises(Invalid):
            research._orchestration_turn(path, 'START', 'FINAL', 'spawn_agent')


class DurableMainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.runtime = self.directory / 'runtime'
        patch = mock.patch('node_config.load', return_value={'release_root': str(self.runtime)})
        patch.start()
        self.addCleanup(patch.stop)

    def identity(self, change=None):
        source = {'schemaVersion': 1,
                  'thread': {'kind': 'codex', 'hostId': 'durable', 'id': 'main-thread'},
                  'turns': [{'id': 'main-turn', 'status': 'inProgress', 'error': None,
                    'items': [{'id': 'entry', 'type': 'commandExecution', 'status': 'completed', 'exitCode': 0,
                               'command': str(self.runtime / 'bin/btc-core') + ' enter --run-id run-a',
                               'output': {'truncated': False, 'text': json.dumps({
                                   'run_id': 'run-a', 'status': 'ACTIVE', 'entry_action': 'START_NEW_RUN',
                                   'commit': 'a' * 40, 'release': str(self.runtime / 'releases' / ('a' * 40)),
                                   'node_config': str(self.runtime / 'node.json')})}}]}]}
        if change:
            change(source)
        raw = json.dumps(source).encode()
        reference = {'path': digest(raw)[7:] + '.json', 'sha256': digest(raw)}
        (self.directory / reference['path']).write_bytes(raw)
        manifest = {'schema': durable_proof.MAIN_SCHEMA, 'provider': 'mcp__codex_app__read_thread',
                    'thread': reference, 'thread_id': 'main-thread', 'turn_id': 'main-turn',
                    'entry_item_id': 'entry', 'run_ref': 'run-a'}
        path = self.directory / 'main-proof.json'
        path.write_text(json.dumps(manifest))
        return {'role': 'MAIN', 'model': None, 'effort': None, 'agent_id': 'main-thread', 'run_ref': 'run-a',
                'configuration_evidence': {'scope': durable_proof.MAIN_SCOPE, 'proof_path': str(path),
                    'proof_hash': digest(path.read_bytes()), 'runtime_configuration_verified': False}}

    def test_active_main_unknown_configuration_and_history_keep_the_source(self):
        value = self.identity()
        result = main_flow.validate_provenance(value)
        self.assertEqual(result['thread_id'], value['agent_id'])
        self.assertFalse(result['runtime_configuration_verified'])
        self.assertEqual(result, main_flow.validate_provenance(value, historical=True))
        run = {'agent_id': value['agent_id'], 'run_ref': value['run_ref'], 'main_provenance': value}
        self.assertEqual(run_flow.identity(run), value)
        with self.assertRaises(Invalid):
            run_flow.identity(dict(run, run_ref='other'))
        target = self.directory / 'bridge'
        node_bridge.copy_proof(Path(value['configuration_evidence']['proof_path']), target)
        self.assertEqual(len(list(target.iterdir())), 2)

    def test_unknown_main_requires_exact_run_author_source_and_no_known_conflict(self):
        value = self.identity()
        for key, wrong in [('agent_id', 'other'), ('run_ref', 'other'), ('model', 'gpt-6.1-sol'), ('effort', 'high')]:
            with self.subTest(key=key), self.assertRaises(Invalid):
                main_flow.validate_provenance(dict(value, **{key: wrong}))
        with self.assertRaises(Invalid):
            main_flow.validate_provenance({k:v for k,v in value.items() if k != 'configuration_evidence'})
        mutations = [
            lambda s: s['thread'].update(id='other'),
            lambda s: s['turns'][0].update(id='other'),
            lambda s: s['turns'][0].update(status='failed'),
            lambda s: s['thread'].update(model='wrong-known-model'),
            lambda s: s['turns'][0].update(reasoningEffort='low'),
            lambda s: s['turns'][0]['items'][0].update(exitCode=1),
            lambda s: s['turns'][0]['items'][0].update(command='echo enter --run-id run-a'),
            lambda s: s['turns'][0]['items'][0].update(command=str(self.runtime / 'bin/btc-core') + ' enter --run-id other'),
            lambda s: s['turns'][0]['items'][0].update(command='/tmp/unrelated/bin/btc-core enter --run-id run-a'),
            lambda s: s['turns'][0]['items'][0].update(command='/bin/zsh -lc ' + __import__('shlex').quote(str(self.runtime / 'bin/btc-core') + ' enter --run-id run-a\ntrue')),
            lambda s: s['turns'][0]['items'][0]['output'].update(truncated=True),
            lambda s: s['turns'][0]['items'][0]['output'].update(text=json.dumps({'run_id': 'other', 'status': 'ACTIVE'})),
        ]
        for change in mutations:
            with self.subTest(change=change), self.assertRaises(Invalid):
                main_flow.validate_provenance(self.identity(change))
        value = self.identity()
        manifest = json.loads(Path(value['configuration_evidence']['proof_path']).read_bytes())
        source = self.directory / manifest['thread']['path']
        source.write_bytes(source.read_bytes() + b' ')
        with self.assertRaisesRegex(Invalid, 'source bytes changed'):
            main_flow.validate_provenance(value)


class DurableWorkflowTests(ResearchWorkflowTests):
    # Reuse the existing request/evidence fixtures without rerunning inherited
    # cases in this focused class. Full native tests retain their own class.
    def test_durable_public_and_same_author_approval_keep_provenance_limits(self):
        req = self.request()
        public = write_proof(self.base, *sources())
        phase = research.orchestrated_independent(self.store, req['request_id'], public, 'START', 'FINAL',
                    self.main_input_review(req, req['independent_input_id'], 'START'))
        self.assertEqual(phase['verification_scope'], durable_proof.SCOPE)
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:05Z'):
            candidate = research.reveal(self.store, req['request_id'], phase['independent_id'], {'hypothesis': 'Candidate.'})
        approval = write_proof(self.base, *sources('followup_task', 'FOLLOW', 'APPROVED', 'APPROVAL',
                        '2030-03-01T13:03:06Z', '2030-03-01T13:03:09Z'))
        spec = {'request_id': req['request_id'], 'candidate_id': candidate['candidate_id'], 'rollout_path': str(approval),
                'dispatch_call_id': 'FOLLOW', 'return_message_id': 'APPROVED',
                'input_review': self.main_input_review(req, candidate['candidate_id'], 'FOLLOW'),
                'coverage': ['Current question'], 'counterevidence': [], 'unresolved': ['Runtime configuration not exposed'],
                'reopen_conditions': self.spec['reopen_conditions']}
        saved = research.orchestrated_result(self.store, spec)
        self.assertEqual(saved['verification_scope'], durable_proof.SCOPE)
        self.assertEqual(saved['source_verification']['author_thread_id'], 'child')
        wrong = write_proof(self.base, *sources('followup_task', 'FOLLOW', 'APPROVED', 'APPROVAL',
                        '2030-03-01T13:03:06Z', '2030-03-01T13:03:09Z', child='different-thread'))
        with self.assertRaisesRegex(Invalid, 'original author thread'):
            research.orchestrated_result(self.store, dict(spec, rollout_path=str(wrong)))
        manifest = json.loads(public.read_bytes())
        original = self.base / manifest['parent']['path']
        original.write_bytes(original.read_bytes() + b' ')
        with self.assertRaisesRegex(Invalid, 'source bytes changed'):
            research.orchestrated_result(self.store, spec)


def load_tests(loader, tests, pattern):
    suite = loader.loadTestsFromTestCase(DurableProofTests)
    suite.addTests(loader.loadTestsFromTestCase(DurableMainTests))
    suite.addTest(DurableWorkflowTests('test_durable_public_and_same_author_approval_keep_provenance_limits'))
    return suite


if __name__ == '__main__':
    unittest.main()
