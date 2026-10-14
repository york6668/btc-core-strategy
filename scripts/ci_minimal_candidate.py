#!/usr/bin/env python3
"""Materialize the pinned a157 resource repair in the existing CI checkout.

The patches are source-only artifacts. This never changes main, creates a Git
branch, or touches the installed BTC node. Git's temporary index supplies the
same tree identity that an immutable commit would have.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'a1577cc6a2c216d832959f5c0c09d84444484427'
EXPECTED_TREE = '1be83c7aea16f55ff9c438d6edfe517b7e68ec41'
PATCHES = (
    ('4689765c64aaaa7d692132a5a974a4988bc1728d',
     'test_support/minimal_candidate/01-request-trace.patch',
     '81fb170a9e6a0d960d2bedbde81426fad15c0f266bd47ebbb93caad19a1777c5'),
    ('8ead51d95d8ae0b66bcf966ba59d50247a94a5ea',
     'test_support/minimal_candidate/02-streaming-blob.patch',
     '9b19a88feddddecd808fc2ed90edd5fee2465a41286fe28b56ab1b2e4c4565d9'),
    ('28d0fbd72977a3d9b68c2ae3066225b3e6582bc7',
     'test_support/minimal_candidate/03-cgroup-breakdown.patch',
     '205a576668cee046e5a2be0bfbb9d690864ae90edd3e17e6d5e1df126174a6bf'),
)
PACKAGING_PATCH = ('test_support/minimal_candidate/04-export-trace.patch',
                   'e0590dc97a2bcca8f7bd02d9663544d65fdc1981d07623ab15cd0794af20c814')


def git(*args: str, env: dict[str, str]) -> str:
    return subprocess.check_output(['git', '-C', str(ROOT), *args], env=env,
                                   text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    manifest = args.manifest.resolve()
    if (output.exists() or manifest.exists() or output == ROOT or ROOT in output.parents
            or manifest == ROOT or ROOT in manifest.parents):
        parser.error('output and manifest must be fresh paths outside the source checkout')
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if output == manifest or output in manifest.parents:
        parser.error('manifest must be outside the candidate tree')

    base_env = os.environ.copy()
    if git('rev-parse', BASELINE + '^{commit}', env=base_env) != BASELINE:
        raise ValueError('the pinned baseline commit is unavailable')
    for source, relative, expected in PATCHES:
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('pinned patch bytes changed: ' + source)
    if hashlib.sha256((ROOT / PACKAGING_PATCH[0]).read_bytes()).hexdigest() != PACKAGING_PATCH[1]:
        raise ValueError('pinned release export patch changed')

    with tempfile.TemporaryDirectory(prefix='btc-minimal-index-', dir=output.parent) as scratch:
        env = base_env.copy()
        env['GIT_INDEX_FILE'] = str(Path(scratch) / 'index')
        git('read-tree', BASELINE, env=env)
        for _, relative, _ in PATCHES:
            git('apply', '--cached', str(ROOT / relative), env=env)
        git('apply', '--cached', str(ROOT / PACKAGING_PATCH[0]), env=env)
        tree = git('write-tree', env=env)
        if tree != EXPECTED_TREE:
            raise ValueError('minimal candidate source tree differs: ' + tree)
        output.mkdir()
        env['GIT_WORK_TREE'] = str(output)
        git('checkout-index', '-a', env=env)
        git('add', '-A', env=env)
        if git('write-tree', env=env) != EXPECTED_TREE:
            raise ValueError('materialized candidate differs from the pinned source tree')

    record = {'schema': 'BTC_MINIMAL_RESOURCE_CANDIDATE_V1',
              'baseline_commit': BASELINE, 'patches': [
                  {'source_commit': source, 'path': relative, 'sha256': digest}
                  for source, relative, digest in PATCHES],
              'source_tree': EXPECTED_TREE,
              'packaging_adjustment': {'path': PACKAGING_PATCH[0],
                  'sha256': PACKAGING_PATCH[1],
                  'reason': 'export the traced runtime module in the installed release'},
              'contains_scheduler_cutover': False,
              'contains_model_contract_change': False}
    manifest.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print('BTC_MINIMAL_CANDIDATE=' + json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    main()
