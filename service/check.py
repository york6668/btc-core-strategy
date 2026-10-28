#!/usr/bin/env python3
"""Run the real MySQL/CLI/MCP contracts in a disposable Compose project."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from validation_guard import GIB, ValidationGuard, cleanup_compose, install_interrupt_handlers
from python_image import python_image


ROOT = Path(__file__).resolve().parents[1]
CORE_SOURCE_ROOT = '/opt/btc'
RESOURCE_SERVICES = frozenset(('core', 'mysql', 'scheduler', 'maintenance'))
RESOURCE_UNITS = {'memory.*': 'bytes', 'stat.*': 'bytes', 'vm.*': 'KiB'}
RESOURCE_FIELDS = frozenset((
    'memory.current', 'memory.max', 'memory.peak', 'memory.swap.current',
    'stat.anon', 'stat.file', 'stat.inactive_file', 'stat.kernel',
    'vm.MemTotal', 'vm.MemAvailable', 'vm.SwapTotal', 'vm.SwapFree'))
RESOURCE_PROBE = ('for f in memory.current memory.max memory.peak memory.swap.current; do '
                  'printf "%s=" "$f"; cat "/sys/fs/cgroup/$f"; done; '
                  'awk \'/^(anon|file|inactive_file|kernel) / {print "stat." $1 "=" $2}\' '
                  '/sys/fs/cgroup/memory.stat; '
                  'awk \'/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ '
                  '{name=$1; sub(/:$/, "", name); print "vm." name "=" $2}\' /proc/meminfo')

# Source-only reader contracts: preserve previously reviewed exports and add
# the exact role revision used by reader.lock.json. Match the complete Python
# tree as well as its entrypoint; no version is inferred from a mutable branch.
READER_COMMON = {
    'seller_exhaustion.py': '048bcfa068123a8457ae4a1b813453a08ec43a4f6fde96b2ae77605c100d3621',
    'btc_evidence.py': '24a139cfa3613c4979f586a69a619e1ac284c4ae6c5ef006c5526bd70846a0cc',
    'tests/test_evidence_v2.py': '38080f32a8c5bce9ea53a60ca216f13d0163642b0596afa48a1ab908a224965d',
}
READER_VARIANTS = {
    'role_9b47d1e': '27ed27b18cd2375ec022cadbb1456e80840e7fcfd556e1675b8511740c25a378',
    'ci_snapshot_c58': '8b05d8b041697efc96d67cb0439761b40a47b0f4337b2c92b339459f2d4414ba',
    'role_34c': '27ed27b18cd2375ec022cadbb1456e80840e7fcfd556e1675b8511740c25a378',
}
READER_TREE_VARIANTS = {
    'role_9b47d1e': '5b43ce165d7ce1a299c23b7bc64f543e7dab18c0a6bd6d7a12b0d56962779c0e',
    'ci_snapshot_c58': 'c22368605c9cd1e9c99da13e320727bbd92631df76c778bb3ac918e264b0d903',
    'role_34c': '671106a8a5b25cd3c21199f58299d7017dfedbafc259d88877b5f053ec08aa6c',
}


def reader_source_files(reader):
    reader = Path(reader).resolve()
    owner_root = reader.parent.parent.resolve()
    for source in reader.rglob('*.py'):
        relative = source.relative_to(reader)
        if (source.is_file() and source.resolve().is_relative_to(owner_root) and
                not any(part.startswith('.') or part in ('data', '__pycache__')
                        for part in relative.parts)):
            yield relative, source


def reader_tree_digest(reader):
    rows = ((relative.as_posix(), hashlib.sha256(source.read_bytes()).hexdigest())
            for relative, source in reader_source_files(reader))
    canonical = ''.join(name+'\0'+digest+'\n' for name, digest in sorted(rows))
    return hashlib.sha256(canonical.encode()).hexdigest()


def reader_source_variant(reader, *, require_actual=False):
    """Reject missing fixtures, unknown versions, and CI reader in a field run."""
    reader = Path(reader).resolve()
    required = dict(READER_COMMON)
    required['btc_evidence_reader.py'] = None
    missing = [str(reader/name) for name in required if not (reader/name).is_file()]
    if missing:
        raise ValueError('test preflight missing source/export: '+', '.join(missing))
    for name, digest in READER_COMMON.items():
        if hashlib.sha256((reader/name).read_bytes()).hexdigest() != digest:
            raise ValueError('reader compatibility version mismatch: '+name)
    actual = hashlib.sha256((reader/'btc_evidence_reader.py').read_bytes()).hexdigest()
    tree = reader_tree_digest(reader)
    variant = next((name for name, digest in READER_VARIANTS.items()
                    if digest == actual and READER_TREE_VARIANTS[name] == tree), None)
    if variant is None or (require_actual and not variant.startswith('role_')):
        raise ValueError('reader compatibility version mismatch: btc_evidence_reader.py')
    return variant


def probe_actual_reader_contract(reader):
    """Exercise the real role reader with only a synthetic in-memory store."""
    script = '''import json
from btc_evidence_reader import EvidenceReader
from btc_evidence import encode_bundle
from test_evidence_v2 import generated
r = EvidenceReader(":memory:", -1001, clock=lambda: 1000000)
bundle, body = generated("BTC")
raw = encode_bundle(bundle)
posts = ({"message_id": 100, "text": body},
         {"message_id": 101, "reply_to_message": {"message_id": 100},
          "document": {"file_id": "synthetic", "file_name": "BTC.json", "file_size": len(raw)}})
for uid, post in enumerate(posts, 1):
    result = r.ingest_update({"update_id": uid, "channel_post": dict(post, date=1000000+uid,
                              chat={"id": -1001})})
    assert result["status"] == "stored", result
assert r.process_pending(lambda _: (_ for _ in ()).throw(TimeoutError("private://secret")))[0]["status"] == "pending_download"
failure = r.db.execute("SELECT detail FROM collector_download_failures").fetchone()
assert failure and json.loads(failure[0]) == {"classes": ["TimeoutError"]}
r.db.execute("UPDATE collector_attachments SET download_retry_at=0")
assert r.process_pending(lambda _: raw)[0]["role"] == "auxiliary_observation"
transport = r.observations(asset="BTC")[0]["transport"]
assert transport["raw_available_at"] == 1000000 and transport["download_attempts"] == 1
r.close()
'''
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(reader), str(reader/'tests'))),
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-B', '-c', script], env=env,
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('actual reader synthetic compatibility probe failed: '+result.stderr[-2000:])


def preflight_test_sources(root, reader, *, require_actual=False):
    """Check the source/export inputs before requesting a resource window."""
    root, reader = Path(root).resolve(), Path(reader).resolve()
    required = (
        root/'service/Dockerfile', root/'service/compose.yaml',
        root/'service/btc_node/__main__.py',
        root/'service/tests/test_data_contract.py',
        root/'service/tests/test_domain_runtime.py',
        root/'scripts/build_synthetic_fixtures.py',
        root/'skill/btc-core-strategy/scripts/strategy_v4.py',
        root/'skill/btc-core-strategy/tests/test_run_flow.py',
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError('test preflight missing source/export: '+', '.join(missing))
    variant = reader_source_variant(reader, require_actual=require_actual)
    dockerfile = (root/'service/Dockerfile').read_text()
    if (f'WORKDIR {CORE_SOURCE_ROOT}' not in dockerfile or
            f'COPY service/btc_node {CORE_SOURCE_ROOT}/btc_node' not in dockerfile):
        raise ValueError('test preflight requires the core package at /opt/btc')
    if variant.startswith('role_'):
        probe_actual_reader_contract(reader)
    return variant


def export_reader_sources(reader, destination):
    """Stage Python source only; never copy a role's database, cache or secret."""
    reader, destination = Path(reader).resolve(), Path(destination)
    destination.mkdir(mode=0o700)
    for relative, source in reader_source_files(reader):
        target = destination/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def stage_reader_source_mount(reader, destination):
    """Provide an empty target for the nested writable data bind, without state."""
    export_reader_sources(reader, destination)
    destination = Path(destination)
    (destination / 'data').mkdir(mode=0o755)
    return destination


def run_contract_tests(command, label, *, run=subprocess.run):
    """Keep one private, exclusive log per attempt even when tests fail."""
    if label not in ('standalone-domain', 'complete-suite'):
        raise ValueError('invalid contract test label')
    log_dir = Path(tempfile.mkdtemp(prefix='btc-check-evidence-'))
    log = log_dir/(label+'-'+uuid.uuid4().hex+'.log')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(log, flags, 0o600), 'w') as stream:
        print('BTC_CONTRACT_TEST_LOG='+str(log), file=sys.stderr, flush=True)
        try:
            result = run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=900)
        except BaseException:
            stream.flush()
            raise
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, output='log='+str(log))
    return log


def core_python_script_command(compose_command, script):
    """Run a staged script with the core image's package root on sys.path.

    Python puts the script's directory, not the container working directory,
    on sys.path[0]. A script copied to /tmp therefore needs this explicit path.
    """
    if not script.startswith('/tmp/') or not script.endswith('.py'):
        raise ValueError('core validation script must be a staged /tmp Python file')
    return compose_command + ['exec', '-T', '-e',
                              'PYTHONPATH=' + CORE_SOURCE_ROOT,
                              'core', 'python', script]


def fingerprint_disposable_state(compose_command, docker, *, root=ROOT,
                                 run=subprocess.run, check_output=subprocess.check_output):
    """Fingerprint the current disposable core, including after a restart."""
    current = check_output(compose_command + ['ps', '-q', 'core'], text=True).strip()
    if not current:
        raise AssertionError('disposable core unavailable for state fingerprint')
    run([docker, 'cp', str(root / 'service/check_fingerprint.py'),
         current + ':/tmp/check_fingerprint.py'], check=True)
    return check_output(core_python_script_command(
        compose_command, '/tmp/check_fingerprint.py'))


def verify_core_service_contract(services, socket_path):
    """Inspect rendered Compose before building or starting the test stack."""
    core = services.get('core', {})
    command = core.get('command')
    if command != ['serve']:
        raise AssertionError('test core must start the Unix socket service')
    health = core.get('healthcheck', {}).get('test', [])
    if socket_path not in ' '.join(map(str, health)):
        raise AssertionError('test core healthcheck must probe the configured Unix socket')
    environment = core.get('environment', {})
    if isinstance(environment, list):
        environment = dict(item.split('=', 1) for item in environment if '=' in item)
    if environment.get('BTC_SERVICE_CONFIG') != '/run/btc/settings.json':
        raise AssertionError('test core must read the mounted service config')
    if not any(mount.get('target') == '/run/btc/settings.json' and mount.get('read_only')
               for mount in core.get('volumes', [])):
        raise AssertionError('test core config must be mounted read-only')


def capture_validation_resources(docker, project, *, run=subprocess.run):
    """Sample only this disposable project; no application files or logs."""
    if not re.fullmatch(r'btc-(?:test|adoption)-[a-z0-9]+', project):
        raise ValueError('invalid disposable project identity')

    def call(args):
        result = run(args, capture_output=True, text=True, timeout=5)
        if result.returncode or len(result.stdout) > 4096:
            raise RuntimeError('bounded Docker resource probe unavailable')
        return result.stdout

    listing = call([docker, 'ps', '--filter',
                    'label=com.docker.compose.project=' + project, '--format',
                    '{{.Label "com.docker.compose.service"}}|{{.ID}}'])
    active = {}
    for line in listing.splitlines():
        service, separator, identity = line.partition('|')
        if not separator or service not in RESOURCE_SERVICES or not re.fullmatch(r'[0-9a-f]{12,64}', identity):
            raise ValueError('unexpected disposable resource identity')
        if service in active:
            raise ValueError('duplicate disposable service')
        active[service] = identity

    def probe(item):
        service, identity = item
        try:
            raw = call([docker, 'exec', identity, 'sh', '-c', RESOURCE_PROBE])
            fields = {}
            for line in raw.splitlines():
                key, separator, value = line.partition('=')
                if not separator or key in fields or not re.fullmatch(
                        r'(?:memory\.(?:current|max|peak|swap\.current)|stat\.(?:anon|file|inactive_file|kernel)|vm\.(?:MemTotal|MemAvailable|SwapTotal|SwapFree))', key):
                    raise ValueError('invalid resource probe field')
                fields[key] = value if key == 'memory.max' and value == 'max' else int(value)
            if set(fields) != RESOURCE_FIELDS:
                raise ValueError('resource probe incomplete')
            return service, {'status': 'OK', 'fields': fields}
        except Exception as exc:
            return service, {'status': 'UNAVAILABLE', 'reason': type(exc).__name__}

    with ThreadPoolExecutor(max_workers=2) as pool:
        services = dict(pool.map(probe, sorted(active.items())))
    return {'schema': 'BTC_VALIDATION_RESOURCE_V1', 'units': RESOURCE_UNITS,
            'status': ('NO_RUNNING_SERVICES' if not services else
                       'OK' if all(row['status'] == 'OK' for row in services.values()) else 'PARTIAL'),
            'services': services}


def record_validation_resources(docker, project, stage, *, run=subprocess.run, stream=None):
    """Best-effort evidence; sampler failure never changes the test verdict."""
    try:
        evidence = capture_validation_resources(docker, project, run=run)
    except Exception as exc:
        evidence = {'schema': 'BTC_VALIDATION_RESOURCE_V1', 'units': RESOURCE_UNITS,
                    'status': 'UNAVAILABLE',
                    'reason': type(exc).__name__, 'services': {}}
    evidence['stage'] = stage
    try:
        print('BTC_VALIDATION_RESOURCE=' + json.dumps(evidence, sort_keys=True),
              file=stream or sys.stderr, flush=True)
    except Exception:
        pass
    return evidence


def verify_disposable_limits(services, app_user, evidence_source, alias):
    """Require exact limits and one identity/mount across disposable services."""
    for service, limit in (('core', GIB), ('mysql', GIB),
                           ('scheduler', GIB//2), ('maintenance', GIB//4)):
        effective = services[service]
        if effective['restart'] != 'no' or str(effective['mem_limit']) != str(limit):
            raise AssertionError('disposable stack restart/memory override missing: '+service)
    for service in ('core', 'scheduler', 'maintenance'):
        if services[service].get('user') != app_user:
            raise AssertionError('disposable stack app user differs: '+service)
        if not any(mount.get('source') == str(evidence_source) and mount.get('target') == alias
                   for mount in services[service].get('volumes', [])):
            raise AssertionError('disposable stack evidence mount differs: '+service)


def cleanup_disposable_mysql_bind(docker, directory, *, run=subprocess.run):
    """Remove only this test's MySQL bind after its Compose project is gone."""
    mysql_data = directory / 'mysql-data'
    if (directory.is_symlink() or directory.parent.resolve() != Path(tempfile.gettempdir()).resolve()
            or not directory.name.startswith('btc-data-contract-') or mysql_data.is_symlink()
            or not mysql_data.is_dir()):
        raise RuntimeError('disposable MySQL bind identity changed')
    code = 'import shutil; shutil.rmtree("/fixture/mysql-data")'
    run([docker, 'run', '--rm', '--network', 'none', '--pull', 'never',
         '--memory', '128m', '--cpus', '0.25', '--restart', 'no',
         '-v', str(directory) + ':/fixture', '--entrypoint', 'python',
         python_image(), '-c', code], check=True, timeout=90)


def run(docker, reader, reader_image=None, preserved_volume_manifest=None,
        *, require_actual_reader=False, contract_only_domain=False):
    preflight_test_sources(ROOT, reader, require_actual=require_actual_reader)
    if require_actual_reader and (not reader_image or not re.fullmatch(
            r'(?:sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64})', reader_image)):
        raise ValueError('actual reader validation requires an immutable image ID/digest')
    # One local flock and one daemon-wide atomic claim cover separate shell
    # processes and separate clients of the same Docker VM.
    project = 'btc-test-' + uuid.uuid4().hex[:12]
    with ValidationGuard(docker, project=project, stack_limit_bytes=(11 * GIB) // 4,
                         preserved_volume_manifest=preserved_volume_manifest) as guard:
        restore = install_interrupt_handlers()
        try:
            return _run(docker, reader, reader_image, guard, project,
                        contract_only_domain=contract_only_domain)
        finally:
            restore()


def _run(docker, reader, reader_image, guard, project, *, contract_only_domain=False):
    reader = Path(reader).resolve()
    if not (reader / 'seller_exhaustion.py').is_file():
        raise ValueError('supply --reader with the actual compatible role export')
    directory = Path(tempfile.mkdtemp(prefix='btc-data-contract-'))
    # The reader image and read-only source bind both need an existing target
    # before Docker can attach the separately writable SQLite data mount.
    # Only the empty directory is staged; no reader state enters the source.
    reader_copy = stage_reader_source_mount(reader, directory/'reader-export')
    command = [docker, 'compose', '-p', project, '--env-file', str(directory/'compose.env'),
               '-f', str(ROOT/'service/compose.yaml'), '--profile', 'analysis']
    result = 1
    try:
        if reader_image:
            # Run the published role entrypoint, not a mocked exec call. The
            # source bind is read-only while its nested SQLite data is writable.
            reader_data=directory/'reader';reader_data.mkdir()
            launch=[docker,'run','--rm','--read-only','--tmpfs','/tmp',
                    '--memory','512m','--cpus','1','--restart','no',
                    '-v',str(reader_copy)+':/app/telegram_reader:ro',
                    '-v',str(reader_data)+':/app/telegram_reader/data',
                    '-e','BTC_READER_DATA_DIR=/app/telegram_reader/data']
            probe=('import sqlite3; from pathlib import Path; '
                   'p=Path("/app/telegram_reader/data/probe.sqlite"); '
                   'c=sqlite3.connect(p); assert c.execute("PRAGMA journal_mode=WAL").fetchone()[0]=="wal"; '
                   'c.execute("CREATE TABLE probe(value)"); c.commit(); c.close(); '
                   '\ntry: Path("/app/telegram_reader/prohibited").write_text("x")'
                   '\nexcept OSError: pass'
                   '\nelse: raise AssertionError("source must be read-only")')
            subprocess.run(launch+['--entrypoint','python',reader_image,'-c',probe],check=True)
            response=subprocess.check_output(launch+[reader_image,'reader','collector-observations','--asset','all'],text=True)
            if not isinstance(json.loads(response),list):
                raise AssertionError('real reader CLI must return the observation list')
        for name in ('mysql', 'mysql-root'):
            (directory/name).write_text(secrets.token_urlsafe(40)+'\n'); (directory/name).chmod(0o600)
        evidence = directory/'objects'; evidence.mkdir()
        release = evidence/'releases'/'test-release'; release.mkdir(parents=True)
        (release/'RELEASE.json').write_text('{}')
        (evidence/'current').symlink_to('releases/test-release', target_is_directory=True)
        legacy = directory/'strategy'; legacy.mkdir()
        marker = legacy/'fixed-run-path.json'
        marker.write_text('{"generated":true}')
        legacy_inode = marker.stat().st_ino
        legacy.rename(evidence/'.strategy')
        legacy.symlink_to(evidence/'.strategy', target_is_directory=True)
        if (legacy/marker.name).stat().st_ino != legacy_inode:
            raise AssertionError('compatibility move changed the original file')
        alias = '/fixture/project'
        settings = {'mysql': {'host':'mysql','user':'btc','database':'btc','password_file':'/run/secrets/mysql_password'},
                    'objects':alias,'project':alias,'compatibility_root':alias+'/.strategy',
                    'release_root':alias+'/releases',
                    'require_shared_evidence':True,
                    'socket':'/run/btc/btc.sock','test_only':True,
                    'maintenance_enabled':False,'recycle_grace_hours':48}
        (directory/'settings.json').write_text(json.dumps(settings))
        scheduler_settings = directory/'settings-scheduler.json'
        scheduler_settings.write_text(json.dumps(dict(settings,
            scheduler_mode='ACTIVE',scheduler_poll_ms=60_000,direct=True)))
        (directory/'compose.env').write_text('\n'.join(['BTC_DATA_DIR='+str(directory),
            'BTC_SETTINGS_FILE='+str(directory/'settings.json'), 'BTC_MYSQL_PASSWORD_FILE='+str(directory/'mysql'),
            'BTC_MYSQL_ROOT_PASSWORD_FILE='+str(directory/'mysql-root'), 'BTC_IMAGE_TAG=contract'])+'\n')
        effective = json.loads(subprocess.check_output(command+['config','--format','json'], text=True))
        verify_core_service_contract(effective['services'], settings['socket'])
        if effective['services']['maintenance']['restart'] != 'unless-stopped':
            raise AssertionError('normal host restart must restore guarded maintenance')
        subprocess.run(command+['build','core'], check=True, timeout=600)
        # Match the owner seen through this daemon's bind mount. Linux retains
        # the runner UID; Desktop file sharing can map it differently. This
        # keeps the private 0600 test secrets readable without DAC bypass, and
        # makes chmod(0) an actual PermissionError for the service and CLI.
        owner_code = ('import json; from pathlib import Path; '
                      's=Path("/fixture").stat(); print(json.dumps([s.st_uid,s.st_gid]))')
        uid, gid = json.loads(subprocess.check_output([docker,'run','--rm',
            '--memory','128m','--cpus','0.25','--restart','no',
            '--entrypoint','python','-v',str(directory)+':/fixture:ro',
            'btc-node:contract','-c',owner_code], text=True))
        override = directory/'compose.test.json'
        # Scheduler and maintenance write 0600 blobs into the same test CAS.
        app_user = str(uid)+':'+str(gid)
        override.write_text(json.dumps({'services':{'core':{'volumes':[str(evidence)+':'+alias],
            'user':app_user,
            'restart':'no', 'mem_limit':'1g', 'cpus':1,
            'tmpfs':['/run/btc:uid='+str(uid)+',gid='+str(gid)+',mode=0700'],
            'cap_drop':['DAC_OVERRIDE','DAC_READ_SEARCH']},
            'mysql':{'volumes':[str(directory/'mysql-data')+':/var/lib/mysql'],
                     'restart':'no','mem_limit':'1g','cpus':1},
            'scheduler':{'volumes':[str(evidence)+':'+alias,
                str(scheduler_settings)+':/run/btc/settings.json:ro'],
                'user':app_user,'restart':'no','mem_limit':'512m','cpus':0.5},
            'maintenance':{'volumes':[str(evidence)+':'+alias],
                'user':app_user,'restart':'no','mem_limit':'256m','cpus':0.5}}}))
        (directory/'mysql-data').mkdir()
        command += ['-f',str(override)]
        effective_test = json.loads(subprocess.check_output(command+[
            '--profile','node-scheduler','config','--format','json'],text=True))['services']
        verify_core_service_contract(effective_test, settings['socket'])
        verify_disposable_limits(effective_test, app_user, evidence, alias)
        if not any(mount['target']=='/var/lib/mysql' and mount['source']==str(directory/'mysql-data')
                   for mount in effective_test['mysql']['volumes']):
            raise AssertionError('disposable MySQL must use its own bind mount')
        guard.project_started = True
        subprocess.run(command+['up','-d','--wait','mysql','core'], check=True, timeout=600)
        record_validation_resources(docker, project, 'core-started')
        container = subprocess.check_output(command+['ps','-q','core'], text=True).strip()
        probe = ('import json,os; from pathlib import Path; '
                 'root=Path("/fixture/project"); assert (root/".strategy").resolve()==root/".strategy"; '
                 'assert json.loads((root/".strategy/fixed-run-path.json").read_text())=={"generated":True}; '
                 'source=root/"mount-probe"; target=root/".strategy/mount-probe"; '
                 'source.write_bytes(b"generated mount check"); os.link(source,target); '
                 'assert source.samefile(target); target.unlink(); source.unlink()')
        subprocess.run(command+['exec','-T','core','python','-c',probe],check=True)
        subprocess.run([docker,'cp',str(ROOT/'service/tests'),container+':/tmp/tests'],check=True)
        # The compatible role export contains relative links to its owner
        # repository. Materialize them in a disposable copy because Docker cp
        # preserves nested links that otherwise point outside the container.
        subprocess.run([docker,'cp',str(reader_copy),container+':/tmp/reader'],check=True)
        subprocess.run(command+['exec','-T','core','mkdir','-p','/tmp/source/scripts'],check=True)
        for relative in ('skill','project-methods','scripts/build_synthetic_fixtures.py'):
            subprocess.run([docker,'cp',str(ROOT/relative),container+':/tmp/source/'+relative],check=True)
        test_command = command+['exec','-T','-e','BTC_TEST_CONFIG=/run/btc/settings.json',
                                '-e','PYTHONPATH=/opt/btc:/tmp/reader:/tmp/tests',
                                '-e','BTC_TEST_SOURCE=/tmp/source',
                                '-e','BTC_COLLECTOR_CONTRACT_PATH=/tmp/reader/btc_evidence.py',
                                'core','python','-m','unittest']
        if contract_only_domain:
            test_command += ['test_domain_runtime.DomainRuntime.'
                             'test_actual_prepare_observe_and_interrupted_result_recovery', '-v']
        else:
            test_command += ['discover','-s','/tmp/tests','-v']
        run_contract_tests(test_command,
                           'standalone-domain' if contract_only_domain else 'complete-suite')
        record_validation_resources(docker, project, 'core-tests-complete')
        if contract_only_domain:
            result = 0
            return result
        # The Docker node clock must tick on its own, survive a container
        # restart and retain task identities without a Codex CLI in its image.
        scheduler_command = command+['--profile','node-scheduler']
        effective_scheduler = json.loads(subprocess.check_output(
            scheduler_command+['config','--format','json'],text=True))['services']['scheduler']
        if not any(mount.get('target') == '/run/btc/settings.json'
                   and mount.get('source') == str(scheduler_settings)
                   for mount in effective_scheduler['volumes']):
            raise AssertionError('isolated scheduler settings were not mounted')
        subprocess.run(scheduler_command+['up','-d','--wait','scheduler'],check=True,timeout=120)
        no_cli = subprocess.check_output(scheduler_command+['exec','-T','scheduler',
            'python','-c','import shutil; print(shutil.which("codex") is None)'],text=True).strip()
        if no_cli != 'True':
            raise AssertionError('test node clock unexpectedly has a Codex CLI')
        def scheduler_request(method):
            response = json.loads(subprocess.check_output(scheduler_command+[
                'exec','-T','scheduler','python','-m','btc_node','request'],
                input=json.dumps({'method':method,'params':{}}).encode()))
            if not response.get('ok'):
                raise AssertionError('isolated scheduler request failed')
            return response['result']
        deadline = time.monotonic()+12
        while True:
            window = scheduler_request('schedule.window')
            if window['status'] == 'AVAILABLE':
                break
            if time.monotonic() >= deadline:
                raise AssertionError('Docker node clock did not tick independently')
            time.sleep(0.2)
        before_tick = scheduler_request('schedule.status')['last_tick_ms']
        before_tasks = {task['task_id'] for task in scheduler_request('task.status')['tasks']}
        subprocess.run(scheduler_command+['restart','scheduler'],check=True,timeout=120)
        deadline = time.monotonic()+12
        while scheduler_request('schedule.status')['last_tick_ms'] <= before_tick:
            if time.monotonic() >= deadline:
                raise AssertionError('Docker node clock did not resume after restart')
            time.sleep(0.2)
        if {task['task_id'] for task in scheduler_request('task.status')['tasks']} != before_tasks:
            raise AssertionError('node restart duplicated or changed scheduled task identities')
        record_validation_resources(docker, project, 'scheduler-before-stop')
        subprocess.run(scheduler_command+['stop','scheduler'],check=True,timeout=120)
        # Recreate a known failure across two real maintenance process starts.
        # The service must stay idle until a reviewed fault is resolved.
        fault = {'method':'model.defect.open','operation_id':'synthetic-maintenance-lockout',
                 'params':{'scope':'btc/maintenance','kind':'UNKNOWN',
                           'signature':'sha256:'+'d'*64,'fault_digest':'sha256:'+'d'*64,
                           'evidence_ref':'synthetic Docker restart check'}}
        opened = json.loads(subprocess.check_output(command+['exec','-T','core','python','-m','btc_node','request'],
            input=json.dumps(fault).encode()))
        if not opened.get('ok'):
            raise AssertionError('maintenance fault could not be recorded')
        def maintenance_state():
            container = subprocess.check_output(command+['ps','-a','-q','maintenance'],text=True).strip()
            if not container:
                raise AssertionError('maintenance container was not created')
            inspected = json.loads(subprocess.check_output(
                [docker,'inspect','--format','{{json .}}',container],text=True))
            return {**inspected['State'], 'restart_count': inspected['RestartCount'],
                    'restart_policy': inspected['HostConfig']['RestartPolicy']['Name'],
                    'user': inspected['Config']['User']}
        for attempt in range(2):
            subprocess.run(command+(['up','-d','maintenance'] if attempt == 0 else ['restart','maintenance']),check=True)
            time.sleep(0.5)
            state = maintenance_state()
            if state['Status'] != 'running' or state['OOMKilled']:
                raise AssertionError('guarded maintenance did not remain idle and running')
            time.sleep(0.5)
            after_state = maintenance_state()
            if after_state['Pid'] != state['Pid']:
                fields = ('Status', 'Pid', 'ExitCode', 'OOMKilled', 'restart_count', 'restart_policy', 'user')
                print('BTC_MAINTENANCE_PID_DRIFT='+json.dumps({'attempt':attempt,
                    'before':{key:state.get(key) for key in fields},
                    'after':{key:after_state.get(key) for key in fields}}),file=sys.stderr)
                raise AssertionError('known maintenance fault caused process restart')
        fault_check = json.loads(subprocess.check_output(command+['exec','-T','core','python','-m','btc_node','request'],
            input=b'{"method":"model.defect.check","params":{"scope":"btc/maintenance"}}'))
        if fault_check['result']['allowed'] or len(fault_check['result']['faults']) != 1:
            raise AssertionError('restart changed maintenance fault state')
        record_validation_resources(docker, project, 'maintenance-checked')
        original_state = fingerprint_disposable_state(command, docker)
        # A real restart must preserve both business state and protocol access.
        before = subprocess.check_output(command+['exec','-T','core','python','-m','btc_node','request'],
                                         input=b'{"method":"status"}\n')
        subprocess.run(command+['restart','mysql','core'],check=True)
        subprocess.run(command+['up','-d','--wait','mysql','core'],check=True)
        after = subprocess.check_output(command+['exec','-T','core','python','-m','btc_node','request'],
                                        input=b'{"method":"status"}\n')
        if json.loads(before)['result']['objects'] != json.loads(after)['result']['objects']:
            raise AssertionError('object state changed after container/database restart')
        if fingerprint_disposable_state(command, docker) != original_state:
            raise AssertionError('business values, versions, references, cursors or delivery state changed after restart')
        # The service must fail closed while MySQL is unavailable.
        subprocess.run(command+['stop','mysql'],check=True)
        interrupted = subprocess.run(command+['exec','-T','core','python','-m','btc_node','request'],
            input=b'{"method":"status"}\n',capture_output=True)
        if interrupted.returncode == 0:
            raise AssertionError('database outage was silently treated as success')
        subprocess.run(command+['up','-d','--wait','mysql'],check=True)
        recovered = subprocess.check_output(command+['exec','-T','core','python','-m','btc_node','request'],
                                            input=b'{"method":"status"}\n')
        if json.loads(before)['result']['objects'] != json.loads(recovered)['result']['objects']:
            raise AssertionError('object state changed after outage recovery')
        if fingerprint_disposable_state(command, docker) != original_state:
            raise AssertionError('business content changed after database outage recovery')
        print('BTC_DATA_SERVICE_RESULT='+json.dumps({'mysql':True,'cli_mcp':True,'restart':True,'outage_recovery':True,
              'node_scheduler_restart':True,'node_scheduler_without_codex_cli':True,
              'node_task_identities_stable_after_restart':True,
              'project_evidence_single_mount_and_legacy_path':True,
              'reader_cli_readonly_source_writable_data':True if reader_image else 'NOT_RUN'}))
        result = 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, AssertionError):
        # Capture startup evidence before cleanup, scoped to this disposable
        # test project. Never dump settings, environment or credential files.
        for diagnostic in (['ps','--all'], ['logs','--tail','60','core'],
                           ['logs','--tail','60','maintenance']):
            try:
                subprocess.run(command+diagnostic, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass  # Preserve the original test failure.
        raise
    finally:
        if guard.project_started:
            record_validation_resources(docker, project, 'before-cleanup')
            try:
                cleanup_compose(command+['--profile','node-scheduler'], project, guard)
            except Exception as exc:
                print('Temporary test node and files retained for review: '+str(directory)+' ('+
                      str(exc)+')',file=sys.stderr)
                raise
        else:
            guard.cleaned = True
        try:
            try:
                shutil.rmtree(directory)
            except PermissionError:
                cleanup_disposable_mysql_bind(docker, directory)
                shutil.rmtree(directory)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
            guard.cleaned = False
            raise RuntimeError('Temporary test files retained for review: '+str(directory)) from exc
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker',default=shutil.which('docker'))
    parser.add_argument('--reader', default=str(ROOT/'telegram_reader'))
    parser.add_argument('--reader-image', help='Built analysis-role image for the actual reader entrypoint contract')
    parser.add_argument('--preserved-volume-manifest',
                        help='Exact, previously reviewed retained-volume identities; never bypasses resource admission')
    parser.add_argument('--preflight-only', action='store_true',
                        help='check source and reader fixtures without Docker')
    parser.add_argument('--require-actual-reader', action='store_true',
                        help='require the reviewed role checkout, not the older CI snapshot')
    parser.add_argument('--contract-only-domain', action='store_true',
                        help='run the standalone domain method in a fresh guarded disposable stack')
    args=parser.parse_args()
    if args.preflight_only:
        variant = preflight_test_sources(ROOT, args.reader,
                                         require_actual=args.require_actual_reader)
        print('BTC_TEST_PREFLIGHT=SOURCE_AND_READER_OK reader='+variant)
        raise SystemExit(0)
    if not args.docker:
        parser.error('Docker client and daemon are required; this validation cannot be skipped')
    raise SystemExit(run(args.docker, args.reader, args.reader_image,
                         args.preserved_volume_manifest,
                         require_actual_reader=args.require_actual_reader,
                         contract_only_domain=args.contract_only_domain))
