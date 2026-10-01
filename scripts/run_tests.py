#!/usr/bin/env python3
"""Run every core test using synthetic facts and pinned reader test dependencies."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


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
            'legacy_revision2_dir': str(node / 'legacy'),
            'python': sys.executable,
            'frozen_market_python': sys.executable,
        }), encoding='utf-8')
        # Subprocesses inherit only this isolated configuration, never node secrets.
        os.environ['BTC_NODE_CONFIG'] = str(config)
        subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/build_synthetic_fixtures.py'),
                        '--output-dir', str(node / 'legacy')], check=True)
        os.environ['BTC_COLLECTOR_CONTRACT_PATH'] = str(reader / 'btc_evidence.py')
        for key in ('BOT_TOKEN', 'TELEGRAM_BOT_TOKEN', 'CHAT_ID', 'BTC_READER_DATA_DIR',
                    'BTC_READER_EVIDENCE_PATH', 'BTC_FROZEN_MARKET_PYTHON'):
            os.environ.pop(key, None)
        sys.path[:0] = [str(skill / 'scripts'), str(skill / 'tests'),
                        str(reader), str(reader / 'tests')]
        def deny_network(*args, **kwargs):
            raise AssertionError('offline tests cannot open network connections')
        socket.socket.connect = deny_network
        suite = unittest.defaultTestLoader.discover(str(skill / 'tests'), pattern=args.pattern)
        packaging = unittest.TestLoader().discover(str(ROOT / 'tests'), pattern=args.pattern)
        suite.addTests(packaging)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
