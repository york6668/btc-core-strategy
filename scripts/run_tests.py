#!/usr/bin/env python3
"""Run every core test using synthetic facts and pinned reader test dependencies."""
from __future__ import annotations
import argparse
import fnmatch
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

# Frozen revision1..8 contract regressions. Adding a new test never silently
# opts it out of the current revision9 write and ownership constraints.
HISTORICAL_CONTRACT_MODULES = ('test_authored_report', 'test_brief_report', 'test_closed_loop', 'test_collector_observation', 'test_collector_provenance', 'test_collector_v2_pipeline', 'test_cycle_workflow', 'test_decision_report', 'test_deep_report', 'test_delivery_obligations', 'test_dynamic_factor_evidence', 'test_dynamic_factor_main', 'test_dynamic_factor_sensing', 'test_event_contract', 'test_evidence_numbers', 'test_evidence_reasoning', 'test_fact_sync_comparison', 'test_investment', 'test_investment_feedback', 'test_market_independence', 'test_market_observation', 'test_market_reporting', 'test_market_state', 'test_material_reads', 'test_mobile_report', 'test_multi_asset_pipeline', 'test_node_bridge_paths', 'test_pattern_integration', 'test_portfolio_risk', 'test_primary_thesis_continuity', 'test_production_strategy', 'test_recommendation_maintenance', 'test_report_care', 'test_revision2_report_bridge', 'test_run_flow', 'test_run_usage', 'test_runtime_schedule', 'test_strategy_contract', 'test_v4', 'test_workflow_delivery', 'test_workflow_event_review', 'test_workflow_main', 'test_workflow_research')
# These four existing modules construct SYNTHETIC_MAIN agent traces in their
# fixtures and do not claim a real node admission. Their business assertions
# are separate from the node gate. The real node and registration boundary
# is exercised by test_deep_gate and test_model_guard_registration separately.
SYNTHETIC_RESEARCH_MODULES = ('test_closed_loop', 'test_cycle_workflow', 'test_deep_report',
    'test_evidence_reasoning', 'test_investment', 'test_market_independence', 'test_market_state',
    'test_portfolio_risk', 'test_system_amendment_clock', 'test_system_research_transport',
    'test_workflow_research')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reader', type=Path, default=ROOT / '.local/reader')
    parser.add_argument('--pattern', default='test_*.py')
    args = parser.parse_args()
    reader = args.reader.resolve()
    if not (reader / 'reader.py').is_file():
        parser.error('reader missing: run scripts/prepare_reader.py or supply --reader')
    skill = ROOT / 'skill/btc-core-strategy'
    with tempfile.TemporaryDirectory(prefix='btc-core-tests-') as directory:
        node = Path(directory)
        for name in ('资产配置.md', '操作记录.md', '纠正记录.md'):
            (node / name).write_text('# SYNTHETIC TEST FACTS\nNo real account or position.\n', encoding='utf-8')
        config = node / 'node.json'
        config.write_text(json.dumps({
            'schema': 'BTC_NODE_CONFIG_V1', 'project_dir': str(node),
            'legacy_revision2_dir': str(node / 'fixtures'),
            'python': sys.executable,
            'frozen_market_python': sys.executable,
        }), encoding='utf-8')
        # Subprocesses inherit only this isolated configuration, never node secrets.
        os.environ['BTC_NODE_CONFIG'] = str(config)
        os.environ['BTC_COLLECTOR_CONTRACT_PATH'] = str(reader / 'btc_evidence.py')
        for key in ('BOT_TOKEN', 'TELEGRAM_BOT_TOKEN', 'CHAT_ID', 'BTC_READER_DATA_DIR',
                    'BTC_READER_EVIDENCE_PATH', 'BTC_FROZEN_MARKET_PYTHON'):
            os.environ.pop(key, None)
        os.environ['BTC_TEST_FIXTURE_DIR'] = str(node / 'fixtures')
        os.environ['BTC_READER_TEST_FIXTURE_DIR'] = str(node / 'reader-fixtures')
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_synthetic_fixtures.py'),
                        '--output', str(node / 'fixtures')], check=True)
        sys.path[:0] = [str(skill / 'scripts'), str(skill / 'tests'),
                        str(reader), str(reader / 'tests'),
                        str(ROOT / 'service'), str(ROOT / 'service/tests')]
        os.environ['BTC_TEST_SOURCE'] = str(ROOT)
        from build_reader_fixtures import build
        build(node / 'reader-fixtures')
        def deny_network(*args, **kwargs):
            raise AssertionError('offline tests cannot open network connections')
        socket.socket.connect = deny_network
        suite = unittest.defaultTestLoader.discover(str(skill / 'tests'), pattern=args.pattern)
        packaging = unittest.TestLoader().discover(str(ROOT / 'tests'), pattern=args.pattern)
        suite.addTests(packaging)
        # The installed worker and its node journal are an explicit offline
        # part of this release; service's live-DB suites remain separate.
        for filename in ('test_scheduler.py', 'test_tasks.py', 'test_worker_recovery.py', 'test_operation_recovery_offline.py'):
            if fnmatch.fnmatch(filename, args.pattern):
                suite.addTests(unittest.TestLoader().discover(
                    str(ROOT / 'service/tests'), pattern=filename))
        # Historical contract cases deliberately construct old publications.
        # They exercise their original field/runtime contracts in isolation;
        # current lifecycle entry tests always retain the real revision9 floor.
        # This is a test-only mock, never an environment/runtime escape hatch.
        class ContractCase(unittest.TestSuite):
            def __init__(self, case): super().__init__([case]); self.case = case
            def run(self, result, debug=False):
                module = self.case.__class__.__module__
                with ExitStack() as stack:
                    if module in HISTORICAL_CONTRACT_MODULES:
                        stack.enter_context(mock.patch('v4.storage.NEW_WRITE_REVISION',1))
                    if module in SYNTHETIC_RESEARCH_MODULES:
                        # These generated model traces test business/provenance
                        # contracts. The real dispatch gate is exercised by
                        # test_model_guard_registration without this patch.
                        stack.enter_context(mock.patch('v4.research._guard_completed'))
                    return super().run(result, debug)
        def cases(item):
            for child in item:
                if isinstance(child, unittest.TestSuite): yield from cases(child)
                else: yield child
        classified = unittest.TestSuite(ContractCase(case) for case in cases(suite))
        result = unittest.TextTestRunner(verbosity=1).run(classified)
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
