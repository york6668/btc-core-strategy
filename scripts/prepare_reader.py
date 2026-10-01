#!/usr/bin/env python3
"""Fetch an exact reader revision, then export source without data or core recursion."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.local/reader')
    args = parser.parse_args()
    destination = args.output_dir.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        parser.error('output already exists; use a new directory and pass it to run_tests.py --reader')
    locked = json.loads((ROOT / 'test_support/reader.lock.json').read_text())
    revision = locked['commit']
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('reader lock must identify an exact Git commit')
    with tempfile.TemporaryDirectory(prefix='btc-reader-source-') as directory:
        checkout = Path(directory)
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        git = ['git', '-C', str(checkout)]
        subprocess.run(git + ['remote', 'add', 'origin', locked['repository']], check=True)
        subprocess.run(git + ['fetch', '--depth=1', 'origin', revision], check=True)
        subprocess.run(git + ['checkout', '--detach', '-q', 'FETCH_HEAD'], check=True)
        actual = subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip()
        if actual != revision:
            raise ValueError('reader checkout does not match the lock')
        # The reader needs collector sources only. Do not recurse back into core.
        subprocess.run(git + ['submodule', 'update', '--init', 'core/market-collector'], check=True)
        subprocess.run([sys.executable, '-B', str(checkout / 'roles/export_role.py'),
                        'reader', str(destination)], check=True)
    print('Pinned reader ready: ' + str(destination))


if __name__ == '__main__':
    main()
