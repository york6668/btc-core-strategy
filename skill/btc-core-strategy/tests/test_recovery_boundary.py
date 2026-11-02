"""Recovery disclosure follows the actual isolated run's market transition."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
import subprocess
import sys
from unittest.mock import patch

import test_closed_loop as fixture
import strategy_v4
from v4 import run_flow, storage
from v4.common import Invalid, read, replace


class RecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.policy = patch.object(storage, 'NEW_WRITE_REVISION', 1)
        self.policy.start(); self.addCleanup(self.policy.stop)
        self.f = fixture.HourlyClosedLoopTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root, self.project = self.f.root, self.f.f.f.project
        self.pending = patch('v4.reporting.pending', return_value=[{
            'review_id': 'synthetic-review', 'reason': 'PRIVATE_RECOVERY_REASON',
            'decision_id': 'synthetic-decision', 'deliveries': []}])
        self.pending.start(); self.addCleanup(self.pending.stop)

    def cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = strategy_v4.main(['--project', str(self.project), '--root', str(self.root), *args])
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_default_recovery_does_not_expose_internal_inventory(self):
        self.assertIn('PRIVATE_RECOVERY_REASON', json.dumps(storage.recover(self.root)))
        code, public = self.cli('recover')
        self.assertEqual(code, 0)
        self.assertNotIn('PRIVATE_RECOVERY_REASON', json.dumps(public))
        self.assertNotIn('report_care', public)
        self.assertEqual(public['scope'], 'PUBLIC')

    def test_real_cli_default_and_report_care_only_return_public_contracts(self):
        script = Path(strategy_v4.__file__)
        for command in ('recover', 'report-care'):
            child = subprocess.run([sys.executable, '-B', str(script), '--project', str(self.project),
                '--root', str(self.root), command], text=True, capture_output=True)
            self.assertEqual(0, child.returncode, child.stderr)
            value = json.loads(child.stdout)
            self.assertEqual('PUBLIC', value['scope'])
            self.assertNotIn('active_maintenance', value)
            self.assertNotIn('outbox', value)

    def test_candidate_is_not_a_freeze_and_only_own_run_unlocks_recovery(self):
        p = self.f.prepare()
        for path in (self.project / 'not-prepared.json', Path(p['run'])):
            code, _ = self.cli('recover', '--run', str(path), '--run-ref', 'SYNTHETIC_R6')
            self.assertNotEqual(code, 0)
        candidate = run_flow.advance(p['run'], 'market', self.f.f.file(self.f.public(p)), check_only=True)
        code, _ = self.cli('recover', '--run', p['run'], '--run-ref', 'SYNTHETIC_R6')
        self.assertNotEqual(code, 0)
        # Copying the public candidate to the result filename must not grant access.
        result_path = Path(p['run']).parent / 'market-result.json'
        result_path.write_bytes(Path(candidate['market_candidate']).read_bytes())
        code, _ = self.cli('recover', '--run', p['run'], '--run-ref', 'SYNTHETIC_R6')
        self.assertNotEqual(code, 0)
        result_path.unlink()
        frozen = run_flow.advance(p['run'], 'market', candidate['market_candidate'])
        code, complete = self.cli('recover', '--run', p['run'], '--run-ref', 'SYNTHETIC_R6')
        self.assertEqual(code, 0)
        self.assertIn('PRIVATE_RECOVERY_REASON', json.dumps(complete))
        code, care = self.cli('report-care', '--run', p['run'], '--run-ref', 'SYNTHETIC_R6')
        self.assertEqual(code, 0)
        self.assertNotEqual('PUBLIC', care.get('scope'))
        run_flow.advance(p['run'], 'observe', template_path=frozen['observation_template'],
                         answers_path=self.f.f.file(self.f.personal(frozen)))
        code, report = self.cli('report-context', '--run', p['run'], '--run-ref', 'SYNTHETIC_R6')
        self.assertEqual(code, 0)
        self.assertIn('report_care', report)
        code, _ = self.cli('recover', '--run', p['run'], '--run-ref', 'OTHER_RUN')
        self.assertNotEqual(code, 0)
        original = read(p['run'])
        damaged = deepcopy(original); damaged['market_hash'] = 'sha256:' + '0' * 64
        replace(p['run'], damaged)
        code, _ = self.cli('recover', '--run', p['run'], '--run-ref', 'SYNTHETIC_R6')
        self.assertNotEqual(code, 0)
        replace(p['run'], original)
        with self.assertRaises(Invalid):
            run_flow.read_run(p['run'], self.root.parent, self.project)

    def test_original_precontract_freeze_and_delivery_identity_remain_readable(self):
        p, frozen, _ = self.f.freeze()
        run = read(p['run']); run.pop('recovery_contract_revision')
        replace(p['run'], run)
        (Path(p['run']).parent / 'market-freeze-binding.json').unlink()
        original = Path(frozen['market_result']).read_bytes()
        with patch('v4.storage.recover', return_value={'outbox':[
            {'delivery_id':'original-delivery','state':'UNKNOWN','attempt_id':'original-attempt'}]}):
            code, result = self.cli('recover', '--run', p['run'], '--run-ref', 'SYNTHETIC_R6')
        self.assertEqual(0, code)
        self.assertEqual('original-attempt', result['outbox'][0]['attempt_id'])
        self.assertEqual('UNKNOWN', result['outbox'][0]['state'])
        self.assertEqual(original, Path(frozen['market_result']).read_bytes())

    def test_actual_node_recovery_requires_own_freeze_and_returns_exact_old_effect(self):
        from btc_node import api, execution
        from btc_node.codec import encode
        from test_recovery_boundary_offline import CommandJournal
        source = Path(strategy_v4.__file__).resolve().parents[3]
        settings = {'project':str(self.project),'release_root':str(source.parent),'journal':str(self.project/'journals')}
        db = CommandJournal()
        original = {'returncode':0,'stdout':'PRIVATE_ORIGINAL_OPERATION',
                    'continuity':{'personal_context':'PRIVATE_OLD_CONTEXT'}}
        original_digest = db.put_blob(encode(original))
        db.operations['old-operation'] = {'status':'COMMITTED','result_digest':original_digest,'release_id':source.name}
        p = self.f.prepare()
        params = {'operation_id':'old-operation','run':p['run'],'run_ref':'SYNTHETIC_R6'}
        with patch.object(execution,'Database',return_value=db):
            public = api.dispatch(settings,{'method':'core.recover','params':{'operation_id':'old-operation'}})
            self.assertNotIn('PRIVATE_',json.dumps(public))
            with self.assertRaises(Invalid):
                api.dispatch(settings,{'method':'core.recover','params':params})
            self.f.f.freeze_market(p['run'], self.f.public(p))
            self.assertEqual(original,api.dispatch(settings,{'method':'core.recover','params':params}))
            with self.assertRaises(Invalid):
                api.dispatch(settings,{'method':'core.recover','params':dict(params,run_ref='another-run')})
        self.assertEqual(original,db.value(original_digest))

    def test_report_material_cannot_bypass_the_public_stage(self):
        p = self.f.prepare()
        for stage in ('main', 'report', 'report-body'):
            with self.subTest(stage=stage), self.assertRaises(Invalid):
                run_flow.phase_material(read(p['run']), 'early', stage)


if __name__ == '__main__':
    unittest.main()
