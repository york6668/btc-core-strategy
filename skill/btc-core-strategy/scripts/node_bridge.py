"""Native adapter: pinned source, local facts and native proof stay on this node."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def forward_if_enabled(argv, *, requested_release=None):
    if os.environ.get('BTC_IN_CONTAINER') == '1':
        return
    import node_config
    config = node_config.load()
    docker = config.get('docker_service')
    if not docker or not docker.get('enabled'):
        return
    sync_facts(Path(config['project_dir']), Path(docker['fact_bridge_dir']))
    release = Path(requested_release).resolve() if requested_release else Path(__file__).resolve().parents[3]
    operation = os.environ.get('BTC_OPERATION_ID') or 'core-' + uuid.uuid4().hex
    arguments = list(argv)
    inputs = {}
    for flag in ('--input', '--answers', '--template', '--market'):
        if flag in arguments:
            original = Path(arguments[arguments.index(flag) + 1]).resolve()
            if original.is_file():
                data = original.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                directory = Path(docker['fact_bridge_dir']).parent / 'native-inputs' / digest
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                target = directory / original.name
                if not target.exists():
                    with target.open('xb') as handle:
                        handle.write(data); handle.flush(); os.fsync(handle.fileno())
                elif target.read_bytes() != data:
                    raise ValueError('frozen command input changed')
                arguments[arguments.index(flag) + 1] = str(target)
                inputs[str(target)] = digest
    for flag in ('--rollout',):
        if flag in arguments:
            original = Path(arguments[arguments.index(flag) + 1]).resolve()
            directory = Path(docker['native_proof_dir'])
            copy_proof(original, directory)
            arguments[arguments.index(flag) + 1] = str(Path(docker['container_proof_dir']) / original.name)
    request = {'method': 'core.execute', 'operation_id': operation,
               'params': {'release': str(release), 'argv': arguments, 'input_hashes': inputs}}
    command = [docker['binary'], 'compose', '-p', docker['project'], '--env-file', docker['env_file'],
               '-f', docker['compose_file']]
    for extra in docker.get('override_files', []):
        command += ['-f', extra]
    command += ['exec', '-T', 'core', 'python', '-m', 'btc_node', 'request']
    process = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True)
    try:
        response = json.loads(process.stdout)
    except ValueError:
        print('Docker core unavailable. No host fallback. Recovery operation: ' + operation, file=sys.stderr)
        raise SystemExit(75)
    if not response.get('ok'):
        print(response.get('message', 'Docker core failed') + '\nRecovery operation: ' + operation, file=sys.stderr)
        raise SystemExit(75)
    result = response['result']
    if result.get('stdout'):
        try:
            value = json.loads(result['stdout'])
            if isinstance(value, dict) and result.get('continuity'):
                value['continuity'] = {k: v for k, v in result['continuity'].items() if k != 'objects'}
            print(json.dumps(value, ensure_ascii=False, indent=2))
        except ValueError:
            sys.stdout.write(result['stdout'])
    sys.stderr.write(result.get('stderr', ''))
    raise SystemExit(result['returncode'])


def sync_facts(project, directory):
    """Atomically expose a frozen generation; original files remain authority."""
    names = ('资产配置.md', '操作记录.md', '纠正记录.md')
    contents = {name: (project / name).read_bytes() for name in names}
    hashes = {name: hashlib.sha256(value).hexdigest() for name,value in contents.items()}
    generation = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    target = directory / generation
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, value in contents.items():
        path = target / name
        if path.exists() and path.read_bytes() != value:
            raise ValueError('confirmed fact generation changed')
        if not path.exists():
            with path.open('xb') as handle:
                handle.write(value); handle.flush(); os.fsync(handle.fileno())
    if any((project / name).read_bytes() != value for name,value in contents.items()):
        raise ValueError('confirmed files changed during native capture; retry')
    temporary = directory / ('.current-' + uuid.uuid4().hex)
    temporary.symlink_to(generation, target_is_directory=True)
    os.replace(temporary, directory / 'current')
    return hashes


def copy_proof(source, directory, seen=None):
    """Copy only an explicitly requested native transcript and its actual parents."""
    seen = set() if seen is None else seen
    if source in seen:
        raise ValueError('cyclic native proof history')
    seen.add(source)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    original = source.read_bytes()
    first = json.loads(original.splitlines()[0]).get('payload', {})
    target = directory / source.name
    temporary = directory / ('.' + source.name + '.tmp')
    temporary.write_bytes(original); temporary.chmod(0o600)
    os.replace(temporary, target)
    history = first.get('history_base') if first.get('history_mode') == 'paginated' else None
    if history:
        base = next((p for p in source.parents if p.name == 'sessions'), source.parent)
        matches = list(base.glob('**/rollout-*' + history['thread_id'] + '.jsonl'))
        if len(matches) != 1:
            raise ValueError('native parent proof missing or ambiguous')
        copy_proof(matches[0], directory, seen)
