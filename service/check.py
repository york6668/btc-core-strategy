#!/usr/bin/env python3
"""Run the real MySQL/CLI/MCP contracts in a disposable Compose project."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from validation_guard import GIB, ValidationGuard, cleanup_compose, install_interrupt_handlers


ROOT = Path(__file__).resolve().parents[1]


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
         'python:3.11-slim', '-c', code], check=True, timeout=90)


def run(docker, reader, reader_image=None, preserved_volume_manifest=None):
    # One local flock and one daemon-wide atomic claim cover separate shell
    # processes and separate clients of the same Docker VM.
    project = 'btc-test-' + uuid.uuid4().hex[:12]
    with ValidationGuard(docker, project=project, stack_limit_bytes=(11 * GIB) // 4,
                         preserved_volume_manifest=preserved_volume_manifest) as guard:
        restore = install_interrupt_handlers()
        try:
            return _run(docker, reader, reader_image, guard, project)
        finally:
            restore()


def _run(docker, reader, reader_image, guard, project):
    reader = Path(reader).resolve()
    if not (reader / 'seller_exhaustion.py').is_file():
        raise ValueError('supply --reader with the actual compatible role export')
    directory = Path(tempfile.mkdtemp(prefix='btc-data-contract-'))
    command = [docker, 'compose', '-p', project, '--env-file', str(directory/'compose.env'),
               '-f', str(ROOT/'service/compose.yaml'), '--profile', 'analysis']
    result = 1
    try:
        if reader_image:
            # Run the published role entrypoint, not a mocked exec call. The
            # source bind is read-only while its nested SQLite data is writable.
            (reader/'data').mkdir(exist_ok=True)
            reader_data=directory/'reader';reader_data.mkdir()
            launch=[docker,'run','--rm','--read-only','--tmpfs','/tmp',
                    '--memory','512m','--cpus','1','--restart','no',
                    '-v',str(reader)+':/app/telegram_reader:ro',
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
        verify_disposable_limits(effective_test, app_user, evidence, alias)
        if not any(mount['target']=='/var/lib/mysql' and mount['source']==str(directory/'mysql-data')
                   for mount in effective_test['mysql']['volumes']):
            raise AssertionError('disposable MySQL must use its own bind mount')
        guard.project_started = True
        subprocess.run(command+['up','-d','--wait','mysql','core'], check=True, timeout=600)
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
        reader_copy = directory/'reader-export'
        shutil.copytree(reader,reader_copy,symlinks=False)
        subprocess.run([docker,'cp',str(reader_copy),container+':/tmp/reader'],check=True)
        subprocess.run(command+['exec','-T','core','mkdir','-p','/tmp/source/scripts'],check=True)
        for relative in ('skill','project-methods','scripts/build_synthetic_fixtures.py'):
            subprocess.run([docker,'cp',str(ROOT/relative),container+':/tmp/source/'+relative],check=True)
        subprocess.run(command+['exec','-T','-e','BTC_TEST_CONFIG=/run/btc/settings.json','-e','PYTHONPATH=/opt/btc:/tmp/reader',
                       '-e','BTC_TEST_SOURCE=/tmp/source','-e','BTC_COLLECTOR_CONTRACT_PATH=/tmp/reader/btc_evidence.py',
                       'core','python','-m','unittest','discover','-s','/tmp/tests','-v'],check=True,timeout=900)
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
        def fingerprint_state():
            code = ('import json,hashlib; from btc_node.database import Database; '
                    'd=Database(json.load(open("/run/btc/settings.json"))); '
                    'tables=[next(iter(r.values())) for r in d.rows("SHOW TABLES")]; '
                    'state={t:sorted([json.dumps(r,sort_keys=True) for r in d.rows("SELECT * FROM `"+t+"`")]) for t in tables}; '
                    '[d.blobs.get(r["digest"]) for r in d.rows("SELECT digest FROM blobs")]; '
                    'print(hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest())')
            return subprocess.check_output(command+['exec','-T','core','python','-c',code])
        original_state = fingerprint_state()
        # A real restart must preserve both business state and protocol access.
        before = subprocess.check_output(command+['exec','-T','core','python','-m','btc_node','request'],
                                         input=b'{"method":"status"}\n')
        subprocess.run(command+['restart','mysql','core'],check=True)
        subprocess.run(command+['up','-d','--wait','mysql','core'],check=True)
        after = subprocess.check_output(command+['exec','-T','core','python','-m','btc_node','request'],
                                        input=b'{"method":"status"}\n')
        if json.loads(before)['result']['objects'] != json.loads(after)['result']['objects']:
            raise AssertionError('object state changed after container/database restart')
        if fingerprint_state() != original_state:
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
        if fingerprint_state() != original_state:
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
    args=parser.parse_args()
    if not args.docker:
        parser.error('Docker client and daemon are required; this validation cannot be skipped')
    raise SystemExit(run(args.docker, args.reader, args.reader_image,
                         args.preserved_volume_manifest))
