#!/usr/bin/env python3
"""Install immutable code releases and pin execution/recovery to one version.

All mutable records belong to the configured node. This tool never modifies
strategy state, facts, cursors, research, outboxes, credentials or schedules.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False, encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(str(temporary), str(path))


def node(path):
    config = read(path)
    if config.get('schema') != 'BTC_NODE_CONFIG_V1':
        raise ValueError('expected BTC_NODE_CONFIG_V1')
    for key in ('project_dir', 'release_root', 'python'):
        if not Path(config[key]).is_absolute():
            raise ValueError(key + ' must be an absolute node path')
    config['_path'] = str(Path(path).resolve())
    return config


@contextmanager
def locked(config):
    root = Path(config['release_root'])
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.release.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield root


def identity(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def record_path(root, kind, value):
    return root / kind / (identity(value) + '.json')


def version_dir(root, revision):
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('release must identify an exact Git commit')
    return root / 'releases' / revision


def check_release(path):
    metadata = read(path / 'RELEASE.json')
    if metadata['commit'] != path.name or metadata['data_schema'] != 'BTC_V4_UNCHANGED':
        raise ValueError('release identity or data compatibility mismatch')
    return metadata


def current(root):
    target = (root / 'current').resolve(strict=True)
    if target.parent != (root / 'releases').resolve():
        raise ValueError('current must point to an installed release')
    check_release(target)
    return target


def activate(config, revision):
    with locked(config) as root:
        target = version_dir(root, revision)
        check_release(target)
        active = [read(p) for p in (root / 'runs').glob('*.json') if read(p).get('status') == 'ACTIVE']
        if active:
            write(root / 'pending.json', {'commit': revision, 'status': 'WAITING_FOR_RUN_GAP'})
            return {'status': 'WAITING_FOR_RUN_GAP', 'commit': revision, 'active_runs': len(active)}
        previous = current(root).name if (root / 'current').exists() else None
        link = root / ('.current-' + revision)
        if link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        os.replace(str(link), str(root / 'current'))
        write(root / 'activation.json', {'commit': revision, 'previous': previous,
              'activated_at': datetime.now(timezone.utc).isoformat()})
        if (root / 'pending.json').exists():
            (root / 'pending.json').unlink()
        return {'status': 'ACTIVE', 'commit': revision, 'previous': previous, 'release': str(target)}


def install(config, source, ref='HEAD', *, activate_after=True):
    source = Path(source).resolve()
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', ref + '^{commit}'], text=True).strip()
    with locked(config) as root:
        destination = version_dir(root, revision)
        if destination.exists():
            check_release(destination)
        else:
            releases = root / 'releases'; releases.mkdir(exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix='.install-', dir=releases)).resolve()
            try:
                archive = stage / 'source.tar'
                with archive.open('wb') as output:
                    subprocess.run(['git', '-C', str(source), 'archive', revision], stdout=output, check=True)
                with tarfile.open(archive) as package:
                    for member in package.getmembers():
                        relative = Path(member.name)
                        if relative.is_absolute() or '..' in relative.parts or member.issym() or member.islnk():
                            raise ValueError('release archive must contain only relative regular sources')
                    package.extractall(stage)
                archive.unlink()
                required = read(stage / 'export-manifest.json')
                for filename in required['files']:
                    path = stage / filename
                    if not path.is_file() or path.is_symlink() or stage not in path.resolve().parents:
                        raise ValueError('missing or unsafe release source: ' + filename)
                reader = Path(config['reader_code_dir']).expanduser().resolve()
                if not (reader / 'reader.py').is_file():
                    raise ValueError('configured reader_code_dir must contain the installed reader')
                subprocess.run([config['python'], str(stage / 'scripts/run_tests.py'),
                                '--reader', str(reader)], cwd=stage, check=True)
                write(stage / 'RELEASE.json', {'commit': revision, 'data_schema': 'BTC_V4_UNCHANGED',
                      'tested_python': config['python'], 'repository': 'york6668/btc-core-strategy'})
                os.rename(str(stage), str(destination))
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
    return activate(config, revision) if activate_after else {'status': 'STAGED', 'commit': revision}


def enter(config, run_id, context=None):
    with locked(config) as root:
        run_file = record_path(root, 'runs', run_id)
        if run_file.exists():
            run = read(run_file)
            if run['run_id'] != run_id:
                raise ValueError('run identity mismatch')
            if context and run.get('resume_context') != context:
                raise ValueError('a pinned run cannot change recovery context')
        else:
            if context:
                bound = read(record_path(root, 'contexts', context))
                if bound['context'] != context:
                    raise ValueError('recovery context mismatch')
                release = version_dir(root, bound['commit'])
                check_release(release)
            else:
                release = current(root)
            run = {'run_id': run_id, 'commit': release.name, 'resume_context': context,
                   'started_at': datetime.now(timezone.utc).isoformat()}
        release = version_dir(root, run['commit'])
        check_release(release)
        run.update(status='ACTIVE', release=str(release), node_config=config['_path'],
                   skill=str(release / 'skill/btc-core-strategy/SKILL.md'),
                   methods=str(release / 'project-methods'), project_dir=config['project_dir'],
                   python=config['python'], environment={
                       'BTC_RELEASE': str(release), 'BTC_PROJECT': config['project_dir'],
                       'BTC_NODE_CONFIG': config['_path'], 'BTC_PYTHON': config['python'],
                   })
        write(run_file, run)
        return run


def bind(config, run_id, context):
    with locked(config) as root:
        run = read(record_path(root, 'runs', run_id))
        if run.get('status') != 'ACTIVE':
            raise ValueError('context binding requires an active pinned run')
        path = record_path(root, 'contexts', context)
        value = {'context': context, 'commit': run['commit'], 'origin_run': run_id}
        if path.exists() and read(path)['commit'] != run['commit']:
            raise ValueError('existing recovery context belongs to a different version')
        if not path.exists():
            write(path, value)
        return read(path)


def finish(config, run_id):
    with locked(config) as root:
        path = record_path(root, 'runs', run_id)
        run = read(path)
        run['status'] = 'FINISHED'
        write(path, run)
        return {'run_id': run_id, 'status': 'FINISHED', 'commit': run['commit']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-config', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('install'); p.add_argument('--source', required=True); p.add_argument('--ref', default='HEAD'); p.add_argument('--stage-only', action='store_true')
    p = sub.add_parser('activate'); p.add_argument('commit')
    p = sub.add_parser('enter'); p.add_argument('--run-id', required=True); p.add_argument('--resume-context')
    p = sub.add_parser('bind-context'); p.add_argument('--run-id', required=True); p.add_argument('--context', required=True)
    p = sub.add_parser('finish'); p.add_argument('--run-id', required=True)
    sub.add_parser('status')
    args = parser.parse_args(); config = node(args.node_config)
    if args.command == 'install':
        result = install(config, args.source, args.ref, activate_after=not args.stage_only)
    elif args.command == 'activate':
        result = activate(config, args.commit)
    elif args.command == 'enter':
        result = enter(config, args.run_id, args.resume_context)
    elif args.command == 'bind-context':
        result = bind(config, args.run_id, args.context)
    elif args.command == 'finish':
        result = finish(config, args.run_id)
    else:
        with locked(config) as root:
            result = {'current': str(current(root)), 'pending': read(root / 'pending.json') if (root / 'pending.json').exists() else None,
                      'project_dir': config['project_dir'], 'node_config': config['_path']}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
