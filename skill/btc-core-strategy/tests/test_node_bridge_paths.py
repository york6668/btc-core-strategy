"""Native path semantics survive a different Docker working directory."""
from pathlib import Path
import tempfile
import unittest
import node_bridge
from unittest import mock
import contextlib
import io
import json
import hashlib
import os
from types import SimpleNamespace
import strategy_v4
import runtime_schedule


class NativePaths(unittest.TestCase):
    def setUp(self):
        self.addCleanup(runtime_schedule.bind,None,None)

    def test_native_schedule_uses_existing_immutable_input_and_cli_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'run.json';run.write_text('{"run_ref":"SYNTHETIC_RUN"}')
            cfg={'project_dir':folder,'docker_service':{'enabled':True,'fact_bridge_dir':str(root/'facts'),
                 'binary':'SYNTHETIC_DOCKER','project':'SYNTHETIC_PROJECT','env_file':str(root/'env'),
                 'compose_file':str(root/'compose')}}
            timing={'schema':'BTC_EXECUTION_WINDOW_V1','status':'UNAVAILABLE','run_ref':'SYNTHETIC_RUN'}
            response=SimpleNamespace(returncode=0,stderr='',stdout=json.dumps({'ok':True,'result':{'returncode':0,'stdout':'{}'}}))
            with mock.patch('node_config.load',return_value=cfg), mock.patch.object(node_bridge,'sync_facts'), \
                 mock.patch('runtime_schedule.snapshot',return_value=timing) as lookup, \
                 mock.patch.object(node_bridge.subprocess,'run',return_value=response) as execute, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as done:
                    node_bridge.forward_if_enabled(['--project',folder,'--root',folder,'run-material','--run',str(run)])
                self.assertEqual(done.exception.code,0)
                request=json.loads(execute.call_args.kwargs['input']);params=request['params']
                self.assertEqual(request['method'],'core.execute')
                path=Path(params['argv'][params['argv'].index('--runtime-schedule')+1])
                self.assertEqual(json.loads(path.read_text()),timing)
                self.assertEqual(params['input_hashes'][str(path)],hashlib.sha256(path.read_bytes()).hexdigest())
                runtime_schedule.bind(path,'SYNTHETIC_RUN')
                self.assertEqual(runtime_schedule.current(),timing)
                lookup.assert_called_once_with(cfg,'SYNTHETIC_RUN')

    def test_timing_command_is_not_inferred_from_a_flag_value(self):
        self.assertEqual(node_bridge.subcommand(['--project','run-advance','--root','/r','run-value-ref']),'run-value-ref')
        self.assertEqual(node_bridge.subcommand(['--project','/p','--root','/r','--full','report-care']),'report-care')

    def test_read_set_path_keeps_native_caller_meaning_before_container_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            args = ['--project','project','--root','store','run-read','--run','work/run.json',
                    '--read-set','work/reading/market/read-set.json']
            actual = node_bridge.absolute_paths(args, folder)
            self.assertEqual(actual[actual.index('--read-set')+1], str(Path(folder)/'work/reading/market/read-set.json'))
            self.assertEqual(node_bridge.subcommand(actual), 'run-read')

    def test_reading_immutable_material_does_not_enter_service_writer_or_sync_facts(self):
        with mock.patch('node_config.load', side_effect=AssertionError('no service lookup')), \
             mock.patch.object(node_bridge,'sync_facts',side_effect=AssertionError('no fact sync')), \
             mock.patch.object(node_bridge.subprocess,'run',side_effect=AssertionError('no subprocess')):
            self.assertIsNone(node_bridge.forward_if_enabled(['--project','/p','--root','/p/.strategy/v4',
                'run-read','--run','/p/run/run.json','--read-set','/p/run/reading/view/read-set.json']))

    def test_delivery_receipt_remains_an_input_through_native_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);receipt=root/'receipt.json';receipt.write_text('{"synthetic":"INPUT_RECEIPT"}')
            original=receipt.read_bytes()
            cfg={'project_dir':folder,'docker_service':{'enabled':True,'fact_bridge_dir':str(root/'facts'),
                 'binary':'SYNTHETIC_DOCKER','project':'SYNTHETIC_PROJECT','env_file':str(root/'env'),
                 'compose_file':str(root/'compose')}}
            response=SimpleNamespace(returncode=0,stderr='',stdout=json.dumps({'ok':True,'result':{'returncode':0,'stdout':'{}'}}))
            args=['--project',folder,'--root',folder,'delivery','--identity','SYNTHETIC_DELIVERY',
                  '--action','CONFIRM','--receipt',str(receipt)]
            with mock.patch('node_config.load',return_value=cfg), mock.patch.object(node_bridge,'sync_facts'), \
                 mock.patch.object(node_bridge.subprocess,'run',return_value=response) as execute, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as done:
                    node_bridge.forward_if_enabled(args)
                self.assertEqual(done.exception.code,0)
                self.assertEqual(json.loads(execute.call_args.kwargs['input'])['params']['argv'],args)
            self.assertEqual(receipt.read_bytes(),original)

    def test_delivery_input_cannot_use_stage_filename_to_bypass_stdout_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt=Path(folder)/'market-stage-receipt.json';receipt.write_text('SYNTHETIC_INPUT')
            args=['--project',folder,'--root',folder,'delivery','--identity','SYNTHETIC_DELIVERY',
                  '--action','CONFIRM','--receipt',str(receipt)]
            with receipt.open('a') as stream:
                with self.assertRaisesRegex(ValueError,'stdout collides'):
                    node_bridge.guard_stdout(args,stream.fileno())

    def test_receipt_rejects_owned_result_before_open_or_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);result=root/'market-result.json'
            result.write_text('{"preserved":"SYNTHETIC_FROZEN_MARKET"}')
            run=root/'run.json';run.write_text('{"run_ref":"SYNTHETIC_RUN"}')
            args=['--project',folder,'--root',folder,'run-advance','--run',str(run),'--stage','market',
                  '--answers',str(root/'answers.json'),'--template',str(root/'template.json'),'--receipt',str(result)]
            with mock.patch('v4.run_flow.advance') as advance, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(strategy_v4.main(args),2)
                advance.assert_not_called()
            self.assertEqual(result.read_text(),'{"preserved":"SYNTHETIC_FROZEN_MARKET"}')

    def test_shell_collision_reports_precise_path_before_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);reserved=root/'market-result.json'
            args=['run-advance','--run',str(root/'run.json'),'--stage','market']
            with reserved.open('w') as stream:
                with self.assertRaisesRegex(ValueError,'stdout collides.*market-result.json'):
                    node_bridge.guard_stdout(args,stream.fileno())
            with (root/'market-stage-receipt.json').open('w') as stream:
                node_bridge.guard_stdout(args,stream.fileno())

    def test_receipt_alias_does_not_bypass_reserved_stdout_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);owned=root/'market-result.json';owned.write_text('SYNTHETIC')
            alias=root/'market-stage-receipt.json';os.link(owned,alias)
            with alias.open('a') as stream:
                # Either hard link can be the first collision; directory order
                # does not change the requirement to reject without dispatch.
                for paths in ([owned,alias], [alias,owned]):
                    with mock.patch.object(Path,'glob',return_value=paths):
                        with self.assertRaisesRegex(ValueError,'stdout collides') as rejected:
                            node_bridge.guard_stdout(['--run',str(root/'run.json')],stream.fileno())
                    self.assertIn(str(paths[0]),str(rejected.exception))
            self.assertEqual(owned.read_text(),'SYNTHETIC')

    def test_dedicated_receipt_writes_result_without_changing_market_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);market=root/'market-result.json';market.write_text('SYNTHETIC_FROZEN')
            run=root/'run.json';run.write_text('{"run_ref":"SYNTHETIC_RUN"}')
            receipt=root/'report-stage-receipt.json'
            args=['--project',folder,'--root',folder,'run-advance','--run',str(run),'--stage','report',
                  '--answers',str(root/'answers.json'),'--template',str(root/'template.json'),'--receipt',str(receipt)]
            result={'action':'EVIDENCE_LIMITED','review_id':'SYNTHETIC_REVIEW'}
            with mock.patch('v4.run_flow.advance',return_value=result), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(strategy_v4.main(args),0)
            self.assertEqual(json.loads(receipt.read_text()),result)
            self.assertEqual(market.read_text(),'SYNTHETIC_FROZEN')

    def test_relative_run_template_and_snapshot_keep_native_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            args=['--project','.', '--root','.strategy/v4', 'run-advance',
                  '--run','.strategy/v4/run/run.json','--template','.strategy/v4/run/template.json',
                  '--collector-snapshot','.strategy/v4/old/collector.json','--stage','market']
            actual=node_bridge.absolute_paths(args,root)
            for flag in ('--project','--root','--run','--template','--collector-snapshot'):
                index=args.index(flag)+1
                self.assertEqual(actual[index],str((root/args[index]).absolute()))
            self.assertEqual(args[args.index('--run')+1],'.strategy/v4/run/run.json')
            self.assertEqual(actual[-1],'market')
            identity=['delivery','--identity','sha256:original','--action','UNKNOWN']
            self.assertEqual(node_bridge.absolute_paths(identity,root),identity)
