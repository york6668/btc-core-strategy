"""Native adapter: pinned source, local facts and native proof stay on this node."""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import stat
import tempfile

# Paths keep the native caller's meaning when the command crosses into Docker.
PATH_OPTIONS = ('--project', '--root', '--run', '--directory', '--input', '--answers',
                '--template', '--market', '--additional', '--evidence', '--base',
                '--references', '--collector-observations', '--collector-snapshot',
                '--retained-source-bindings', '--main-provenance', '--draft', '--review', '--spec', '--prepared-context', '--file', '--read-set', '--receipt', '--runtime-schedule', '--input-review', '--author-source', '--rollout')
IDENTITY_OPTIONS = ('--agent', '--run-ref')

# These host entries prepare/refresh model-facing material or advance the
# protected workflow. Standalone report/research paths must not bypass the
# run-material/run-advance admission. They do not themselves invoke a model.
USAGE_GUARDED_COMMANDS = frozenset((
    'run-prepare', 'run-material', 'run-advance', 'context', 'observe',
    'research-independent', 'research-orchestrated-independent',
    'report-context', 'report-review', 'report-preview', 'research-read',
))


class NodeTransportError(ValueError):
    """Safe evidence about this client invocation, never a remote retry decision."""

    def __init__(self, reason, *, operation_id=None, process=None, error=None, stage=None):
        diagnostic = {'stage': stage or ('PROCESS_START' if error is not None else 'CLIENT_RESPONSE'),
                      'operation_id': operation_id, 'effect': 'UNKNOWN', 'reason': reason}
        if error is not None:
            diagnostic.update(error_type=type(error).__name__, errno=getattr(error, 'errno', None))
        if process is not None:
            diagnostic['child_exit_code'] = process.returncode
            for name in ('stdout', 'stderr'):
                value = getattr(process, name, '') or ''
                data = value.encode('utf-8') if isinstance(value, str) else value
                diagnostic[name + '_bytes'] = len(data)
                diagnostic[name + '_sha256'] = hashlib.sha256(data).hexdigest()
            stderr = process.stderr or ''
            if isinstance(stderr, bytes):
                stderr = stderr.decode('utf-8', errors='replace')
            diagnostic['stderr_class'] = (
                'DOCKER_UNIX_SOCKET_PERMISSION_DIAGNOSTIC'
                if 'permission denied while trying to connect to the docker API at unix://' in stderr
                else 'CLIENT_DIAGNOSTIC_PRESENT' if stderr else 'NO_CLIENT_DIAGNOSTIC')
        self.diagnostic = diagnostic
        # Do not copy stderr, argv, environment or payload into errors: they may
        # contain credentials. A CLI permission message is not proof of its cause.
        super().__init__('node transport outcome unknown: ' + json.dumps(diagnostic, sort_keys=True))


def decode_node_response(process, operation_id=None):
    """Require both a successful client exit and an explicit node success."""
    try:
        response = json.loads(process.stdout)
    except (TypeError, ValueError) as exc:
        raise NodeTransportError('INVALID_JSON', operation_id=operation_id, process=process) from None
    if not isinstance(response, dict) or type(response.get('ok')) is not bool:
        raise NodeTransportError('INVALID_ENVELOPE', operation_id=operation_id, process=process)
    if response['ok']:
        if type(process.returncode) is not int or process.returncode != 0:
            raise NodeTransportError('CLIENT_EXIT_NOT_SUCCESSFUL', operation_id=operation_id, process=process)
        if 'result' not in response:
            raise NodeTransportError('MISSING_RESULT', operation_id=operation_id, process=process)
    # An explicit application rejection remains a rejection even if its CLI
    # exits nonzero. It cannot become a success or authorize another operation.
    return response


def receipt_path(argv, output):
    if '--run' not in argv or '--stage' not in argv:
        raise ValueError('a stage receipt requires --run and --stage')
    directory = Path(argv[argv.index('--run') + 1]).absolute().parent
    stage = argv[argv.index('--stage') + 1]
    target = Path(output).absolute()
    if target != directory / (stage + '-stage-receipt.json') or target.is_symlink():
        raise ValueError('receipt must use the dedicated ' + str(directory / (stage + '-stage-receipt.json')) + '; program-owned result/input paths are reserved')
    return target


def guard_stdout(argv, descriptor=1):
    """Catch caller redirection before dispatch. A shell may already truncate it.

    --receipt validates before opening and is the supported safe alternative.
    Never repair a possibly truncated business artifact by guessing its contents.
    """
    output = os.fstat(descriptor)
    if not stat.S_ISREG(output.st_mode):
        return
    paths = []
    for flag in PATH_OPTIONS:
        if flag in argv:
            paths.append(Path(argv[argv.index(flag) + 1]))
    if '--run' in argv:
        paths.extend(Path(argv[argv.index('--run') + 1]).parent.glob('*'))
    stage_output = None
    if subcommand(argv) == 'run-advance' and '--run' in argv and '--stage' in argv:
        stage_output = Path(argv[argv.index('--run') + 1]).absolute().parent / (argv[argv.index('--stage') + 1] + '-stage-receipt.json')
    for path in paths:
        try:
            source = path.stat()
        except OSError:
            continue
        if (source.st_dev, source.st_ino) != (output.st_dev, output.st_ino):
            continue
        if path.absolute() == stage_output and not path.is_symlink() and path not in [Path(argv[i + 1]) for i, flag in enumerate(argv[:-1]) if flag in PATH_OPTIONS and flag != '--receipt']:
            continue  # Also inspect aliases of this inode for a reserved path.
        raise ValueError('stdout collides with program-owned/input file ' + str(path) + '; use --receipt with a dedicated stage receipt. Shell redirection may already have truncated the target; preserve and inspect it before resuming.')


def absolute_paths(argv, directory):
    result = []
    seen = set()
    # Other commands consume a review file; this command consumes the already
    # registered immutable review identity. A Docker cwd must not alter it.
    review_identity = subcommand(argv) == 'market-report-source'
    def absolute(value):
        path = Path(value).expanduser()
        # Logical symlinks are part of the template identity.
        return os.path.abspath(path if path.is_absolute() else Path(directory) / path)
    index = 0
    options = PATH_OPTIONS + IDENTITY_OPTIONS
    while index < len(argv):
        item = argv[index]
        flag, equal, value = item.partition('=')
        if flag.startswith('--') and flag not in options and any(name.startswith(flag) for name in options):
            raise ValueError('use the full option spelling: ' + flag)
        if flag in options:
            if flag in seen:
                raise ValueError('one value required for ' + flag)
            seen.add(flag)
        if flag not in options or (flag == '--review' and review_identity):
            result.append(item)
            index += 1
            continue
        convert = absolute if flag in PATH_OPTIONS else lambda value: value
        if equal:
            result.extend((flag, convert(value)))
            index += 1
        elif index + 1 < len(argv):
            result.extend((flag, convert(argv[index + 1])))
            index += 2
        else:
            result.append(item)
            index += 1
    return result


def subcommand(argv):
    # Match the public parser's normal --name=value and unambiguous option
    # abbreviations too. A spelling accepted by argparse cannot skip admission.
    parser = argparse.ArgumentParser(add_help=False)
    for flag in ('--project', '--root', '--runtime-schedule'):
        parser.add_argument(flag)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('command', nargs='?')
    return parser.parse_known_args(argv)[0].command


def forward_if_enabled(argv, *, requested_release=None):
    if '--help' in argv or '-h' in argv:
        # argparse help is local; it must not synchronize facts or open a writer.
        return
    if os.environ.get('BTC_IN_CONTAINER') == '1':
        return
    # Normalize supported path spellings before every safety/transport
    # consumer. --spec=value and --spec value must bind the same input.
    argv = absolute_paths(argv, Path.cwd())
    if subcommand(argv) == 'run-read':
        # Reading already materialized immutable files stays on the native
        # node. No facts sync, service writer admission or projection rebuild.
        return
    import node_config
    config = node_config.load()
    if subcommand(argv) in USAGE_GUARDED_COMMANDS:
        from native_usage import require_admission
        require_admission(config)
    docker = config.get('docker_service')
    if not docker or not docker.get('enabled'):
        return
    guard_stdout(argv)
    if '--runtime-schedule' in argv:
        raise ValueError('--runtime-schedule is supplied by the native adapter, not authored input')
    if subcommand(argv) == 'run-advance' and '--receipt' in argv:
        receipt_path(argv, argv[argv.index('--receipt') + 1])
    sync_facts(Path(config['project_dir']), Path(docker['fact_bridge_dir']))
    release = Path(requested_release).resolve() if requested_release else Path(__file__).resolve().parents[3]
    operation = os.environ.get('BTC_OPERATION_ID') or 'core-' + uuid.uuid4().hex
    arguments = list(argv)
    inputs = {}
    # Reuse the existing immutable native-input bridge; no scheduler or service
    # process is added and older service images pass these CLI arguments through.
    import runtime_schedule
    if subcommand(arguments) in runtime_schedule.COMMANDS:
        timing = runtime_schedule.snapshot(config, runtime_schedule.run_reference(arguments))
        data = json.dumps(timing, sort_keys=True, ensure_ascii=False).encode()
        digest = hashlib.sha256(data).hexdigest()
        directory = Path(docker['fact_bridge_dir']).parent / 'native-inputs' / digest
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / 'execution-window.json'
        if not target.exists():
            with target.open('xb') as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
        elif target.read_bytes() != data:
            raise ValueError('frozen execution window changed')
        inputs[str(target)] = digest
        arguments = ['--runtime-schedule', str(target), *arguments]
    for flag in ('--input', '--answers', '--template', '--market', '--collector-observations',
                 '--collector-snapshot', '--retained-source-bindings', '--input-review'):
        if flag in arguments:
            if arguments.count(flag) != 1:
                raise ValueError('one value required for ' + flag)
            original = Path(arguments[arguments.index(flag) + 1]).resolve()
            if original.is_file():
                data = original.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                if flag == '--template':
                    # Run templates have an exact directory binding. Preserve
                    # that logical path while checking its original bytes.
                    inputs[str(Path(arguments[arguments.index(flag)+1]).absolute())]=digest
                    continue
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
    research_inputs = ('--spec',) if subcommand(arguments) in ('research-result', 'research-orchestrated-result') else ()
    if subcommand(arguments) == 'research-market-amendment':
        research_inputs = ('--author-source',)
    for flag in research_inputs:
        if flag not in arguments:
            continue
        if arguments.count(flag) != 1:
            raise ValueError('one value required for ' + flag)
        original = Path(arguments[arguments.index(flag) + 1])
        if not original.is_file() or original.is_symlink():
            raise ValueError('research source input must be an ordinary file')
        original = original.resolve()
        value = bridge_research_source(json.loads(original.read_bytes()), docker)
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        digest_value = hashlib.sha256(data).hexdigest()
        directory = Path(docker['fact_bridge_dir']).parent / 'native-inputs' / digest_value
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / original.name
        if target.exists():
            if target.is_symlink() or target.read_bytes() != data:
                raise ValueError('immutable research source input changed')
        else:
            with target.open('xb') as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
        arguments[arguments.index(flag) + 1] = str(target)
        inputs[str(target)] = digest_value
    provenance = None
    if subcommand(arguments) == 'run-prepare' and '--main-provenance' not in arguments:
        directory = Path(arguments[arguments.index('--directory') + 1])
        existing = next((directory / name for name in ('run.json', 'preparation.json')
                         if (directory / name).is_file()), None)
        if existing:
            provenance = json.loads(existing.read_bytes()).get('main_provenance')
        else:
            from model_identity import native_runtime_profile
            agent = arguments[arguments.index('--agent') + 1]
            observed = native_runtime_profile(config, agent)
            provenance = {'role': 'MAIN', 'agent_id': agent,
                          'run_ref': arguments[arguments.index('--run-ref') + 1],
                          'model': observed['name'], 'effort': observed['reasoning_effort']}
    if '--main-provenance' in arguments or provenance is not None:
        # Durable MAIN proof has nested immutable source files. Bridge both the
        # manifest and its bound official thread response, then rewrite only
        # the container-visible path in a staged provenance input.
        if '--main-provenance' in arguments:
            original = Path(arguments[arguments.index('--main-provenance') + 1]).resolve()
            if not original.is_file() or original.is_symlink() or original.stat().st_size > 16384:
                raise ValueError('MAIN provenance input is missing, linked, or oversized')
            provenance = json.loads(original.read_bytes())
        from v4.main_flow import validate_preview_provenance
        validate_preview_provenance(provenance)
        if 'execution_evidence' in provenance:
            raise ValueError('preview execution evidence is bound to the local validation code; use its isolated local consumer, not business forwarding')
        evidence = provenance.get('configuration_evidence')
        if evidence:
            proof_path = Path(evidence['proof_path'])
            proof_directory = Path(docker.get('native_proof_dir', ''))
            container_proof_directory = docker.get('container_proof_dir')
            if not proof_directory.is_absolute() or not container_proof_directory:
                raise ValueError('durable MAIN proof bridge is not configured for this node')
            copy_proof(proof_path, proof_directory)
            evidence['proof_path'] = str(Path(container_proof_directory) / proof_path.name)
        data = json.dumps(provenance, ensure_ascii=False, sort_keys=True).encode('utf-8')
        digest_value = hashlib.sha256(data).hexdigest()
        directory = Path(docker['fact_bridge_dir']).parent / 'native-inputs' / digest_value
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / 'main-provenance.json'
        if not target.exists():
            with target.open('xb') as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
            target.chmod(0o600)
        elif target.is_symlink() or target.read_bytes() != data:
            raise ValueError('immutable MAIN provenance bridge collision')
        if '--main-provenance' in arguments:
            arguments[arguments.index('--main-provenance') + 1] = str(target)
        else:
            arguments += ['--main-provenance', str(target)]
        inputs[str(target)] = digest_value
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
    try:
        try:
            process = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True)
        except OSError as exc:
            raise NodeTransportError('CLIENT_START_FAILED', operation_id=operation, error=exc) from None
        except UnicodeError as exc:
            raise NodeTransportError('CLIENT_OUTPUT_DECODE_FAILED', operation_id=operation,
                                     error=exc, stage='CLIENT_RESPONSE') from None
        response = decode_node_response(process, operation)
        if response['ok']:
            result = response['result']
            if (not isinstance(result, dict) or type(result.get('returncode')) is not int
                    or not isinstance(result.get('stdout', ''), str)
                    or not isinstance(result.get('stderr', ''), str)):
                raise NodeTransportError('INVALID_CORE_RESULT', operation_id=operation, process=process)
    except NodeTransportError as exc:
        print('Docker core unavailable. No host fallback. Recovery operation: ' + operation
              + '\n' + str(exc), file=sys.stderr)
        raise SystemExit(75) from None
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


def sync_facts(project, directory, *, allow_missing=False):
    """Atomically expose a frozen generation; original files remain authority."""
    names = ('资产配置.md', '操作记录.md', '纠正记录.md')
    def current_file(name):
        try:
            return (project / name).read_bytes()
        except FileNotFoundError:
            if not allow_missing:
                raise
            return None

    contents = {name: current_file(name) for name in names}
    contents = {name: value for name, value in contents.items() if value is not None}
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
    if any(current_file(name) != contents.get(name) for name in names):
        raise ValueError('confirmed files changed during native capture; retry')
    temporary = directory / ('.current-' + uuid.uuid4().hex)
    temporary.symlink_to(generation, target_is_directory=True)
    os.replace(temporary, directory / 'current')
    return hashes


def bridge_research_source(value, docker):
    """Transport only explicit proof references, preserving their source bytes."""
    value = dict(value)
    if 'rollout_path' in value:
        source = Path(value['rollout_path'])
        if not source.is_file() or source.is_symlink():
            raise ValueError('research proof source must be an ordinary file')
        source = source.resolve()
        identity = hashlib.sha256(source.read_bytes()).hexdigest()
        directory = Path(docker['native_proof_dir']) / identity
        copy_proof(source, directory)
        value['rollout_path'] = str(Path(docker['container_proof_dir']) / identity / source.name)
    if 'author_source' in value:
        value['author_source'] = bridge_research_source(value['author_source'], docker)
    return value


def copy_proof(source, directory, seen=None):
    """Copy an explicit native transcript or durable proof and its bound sources."""
    seen = set() if seen is None else seen
    if source in seen:
        raise ValueError('cyclic native proof history')
    seen.add(source)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    original = source.read_bytes()
    from v4 import durable_proof
    if durable_proof.is_manifest(source):
        for raw_source in durable_proof.source_files(source):
            raw = raw_source.read_bytes()
            target = directory / raw_source.name
            if target.exists():
                if target.is_symlink() or target.read_bytes() != raw:
                    raise ValueError('immutable durable source bridge collision')
            else:
                with target.open('xb') as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                target.chmod(0o600)
        target = directory / source.name
        if target.exists():
            if target.is_symlink() or target.read_bytes() != original:
                raise ValueError('immutable durable proof bridge collision')
        else:
            with target.open('xb') as stream:
                stream.write(original); stream.flush(); os.fsync(stream.fileno())
            target.chmod(0o600)
        return
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


def archive_proof_location(source, directory):
    """Transport one existing durable proof closure, preserving its old path.

    This is file availability, never approval. Publication is immutable and
    the location marker is written last; retries can complete a safe prefix.
    No research records, source bytes or author identities are changed.
    """
    from v4 import durable_proof
    source, directory = Path(source), Path(directory)
    original, key = durable_proof.location_key(source)
    if source.name == 'location.json':
        raise ValueError('native proof filename is reserved for its location record')
    if (not source.is_file() or source.is_symlink() or source.stat().st_size > 16384
            or not durable_proof.is_manifest(source)):
        raise ValueError('original durable proof manifest required')
    if not directory.is_absolute():
        raise ValueError('native proof archive must be absolute')
    parent = directory / 'locations' / key
    if directory.is_symlink():
        raise ValueError('native proof archive must not be linked')
    directory.mkdir(exist_ok=True, parents=True, mode=0o700)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        root_fd = os.open(directory, flags); descriptors.append(root_fd)
        def child(fd, name):
            try:
                os.mkdir(name, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            result = os.open(name, flags, dir_fd=fd)
            descriptors.append(result)
            return result
        locations_fd = child(root_fd, 'locations')
        parent_fd = child(locations_fd, key)

        def same_entry(fd, name, expected_fd):
            actual = os.stat(name, dir_fd=fd, follow_symlinks=False)
            expected = os.fstat(expected_fd)
            if (not stat.S_ISDIR(actual.st_mode) or
                    (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)):
                raise ValueError('native proof archive directory changed during publication')

        def check_location():
            same_entry(None, directory, root_fd)
            same_entry(root_fd, 'locations', locations_fd)
            same_entry(locations_fd, key, parent_fd)

        def publish(name, raw):
            temporary = '.proof-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent_fd)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                try:
                    os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                            follow_symlinks=False)
                except FileExistsError:
                    existing = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                       dir_fd=parent_fd)
                    with os.fdopen(existing, 'rb') as stream:
                        if (not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or
                                stream.read(len(raw) + 1) != raw):
                            raise ValueError('immutable native proof location collision')
            finally:
                os.unlink(temporary, dir_fd=parent_fd)

        # Staging is private and separate from the configured publication path.
        # All final reads/writes are anchored to opened no-follow directories.
        with tempfile.TemporaryDirectory(prefix='.btc-proof-stage-') as folder:
            staged = Path(folder)
            copy_proof(source, staged)
            manifest = staged / source.name
            files = durable_proof.source_files(manifest)
            check_location()
            for item in [*files, manifest]:
                publish(item.name, item.read_bytes())
            marker = {'schema':'BTC_NATIVE_PROOF_LOCATION_V1', 'original_path':original,
                      'proof_name':source.name,
                      'proof_hash':'sha256:' + hashlib.sha256(manifest.read_bytes()).hexdigest()}
            check_location()
            publish('location.json', json.dumps(marker, sort_keys=True).encode())
            os.fsync(parent_fd)
            check_location()
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
    return parent / source.name
