#!/usr/bin/env python3
"""Verify the source-only CI reader dependency and run real Docker contracts."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def read_lock(root=ROOT):
    lock = json.loads((root / 'test_support/reader.lock.json').read_text())
    snapshot = lock.get('ci_snapshot') or {}
    for label, value in (('reader commit', lock.get('commit')),
                         ('snapshot tree', snapshot.get('tree'))):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
            raise ValueError(label + ' must be an exact Git SHA')
    path = PurePosixPath(snapshot['path'])
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('reader snapshot must be inside the source checkout')
    return lock


def object_id(kind, raw):
    return hashlib.sha1(kind + b' ' + str(len(raw)).encode() + b'\0' + raw).digest()


def source_tree(directory, files):
    """Reconstruct the Git tree from regular file bytes/modes, without Git access."""
    root = {}
    for relative in files:
        path = directory / relative
        mode = b'100755' if path.stat().st_mode & 0o111 else b'100644'
        node = root
        parts = relative.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (mode, object_id(b'blob', path.read_bytes()))

    def tree(node):
        entries = []
        for name, value in node.items():
            name = name.encode()
            if isinstance(value, dict):
                mode, oid, key = b'40000', tree(value), name + b'/'
            else:
                mode, oid = value
                key = name
            entries.append((key, mode + b' ' + name + b'\0' + oid))
        return object_id(b'tree', b''.join(item[1] for item in sorted(entries)))
    return tree(root).hex()


def verify_snapshot(snapshot, lock):
    snapshot = Path(snapshot).resolve()
    manifest = json.loads((snapshot / 'SNAPSHOT.json').read_text())
    if (manifest.get('schema') != 1
            or manifest.get('source_repository') != lock['repository']
            or manifest.get('source_commit') != lock['commit']):
        raise ValueError('reader snapshot upstream identity does not match the lock')
    expected = {'SNAPSHOT.json', '.dockerignore'}
    for item in manifest['files']:
        relative = PurePosixPath(item['path'])
        if relative.is_absolute() or '..' in relative.parts or str(relative) in expected:
            raise ValueError('invalid or duplicate reader snapshot path')
        expected.add(str(relative))
        path = snapshot / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError('reader snapshot source must be a regular file')
        raw = path.read_bytes()
        mode = '100755' if path.stat().st_mode & 0o111 else '100644'
        if (mode != item['mode']
                or hashlib.sha256(raw).hexdigest() != item['sha256']
                or object_id(b'blob', raw).hex() != item['git_blob_sha']):
            raise ValueError('reader snapshot source changed: ' + str(relative))
    actual = set()
    for path in snapshot.rglob('*'):
        if path.is_symlink():
            raise ValueError('reader snapshot must not contain symlinks')
        if path.is_file():
            actual.add(path.relative_to(snapshot).as_posix())
    if actual != expected:
        raise ValueError('reader snapshot has missing or extra files')
    if (snapshot / '.dockerignore').read_bytes() != b'.git\n':
        raise ValueError('reader build must exclude Git metadata')
    if source_tree(snapshot, actual) != lock['ci_snapshot']['tree']:
        raise ValueError('reader snapshot tree does not match the lock')
    required = {'telegram_reader/reader.py', 'telegram_reader/seller_exhaustion.py',
                'requirements.txt', 'roles/containers/Dockerfile',
                'roles/containers/entrypoint.py'}
    if not required <= actual:
        raise ValueError('reader snapshot lacks required build or runtime source')
    return snapshot


def commands(root, snapshot, docker, image):
    return [
        [docker, 'build', '--tag', image, '--file',
         str(snapshot / 'roles/containers/Dockerfile'), str(snapshot)],
        [sys.executable, '-B', str(root / 'service/check.py'),
         '--docker', docker, '--reader', str(snapshot / 'telegram_reader'),
         '--reader-image', image],
    ]


def stage_build_context(snapshot, destination):
    """Keep the verified source bytes and add only the empty nested mountpoint."""
    staged = Path(destination) / 'source'
    shutil.copytree(snapshot, staged)
    mountpoint = staged / 'telegram_reader' / 'data'
    mountpoint.mkdir(mode=0o755, exist_ok=True)
    if any(mountpoint.iterdir()):
        raise ValueError('reader build mountpoint must be empty')
    return staged


def execute(plan, docker, image):
    try:
        with tempfile.TemporaryDirectory(prefix='btc-reader-build-') as temporary:
            context = stage_build_context(Path(plan[0][-1]), temporary)
            build = list(plan[0])
            build[build.index('--file') + 1] = str(context / 'roles/containers/Dockerfile')
            build[-1] = str(context)
            subprocess.run(build, check=True)
            for command in plan[1:]:
                subprocess.run(command, check=True)
    finally:
        subprocess.run([docker, 'image', 'rm', image], check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'run'))
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--docker', default='docker')
    args = parser.parse_args()
    lock = read_lock()
    snapshot = verify_snapshot(args.snapshot or ROOT / lock['ci_snapshot']['path'], lock)
    image = 'btc-reader-ci:' + uuid.uuid4().hex
    plan = commands(ROOT, snapshot, args.docker, image)
    if args.command == 'plan':
        print(json.dumps({'status': 'VERIFIED_INPUTS_DOCKER_NOT_RUN', 'commands': plan,
                          'build_context_preparation': 'copy verified snapshot and add an empty telegram_reader/data mountpoint',
                          'source_commit': lock['commit'],
                          'snapshot_tree': lock['ci_snapshot']['tree']}, indent=2))
    else:
        execute(plan, args.docker, image)


if __name__ == '__main__':
    main()
