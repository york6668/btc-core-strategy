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


ROOT = Path(__file__).resolve().parents[1]


def run(docker, reader):
    reader = Path(reader).resolve()
    if not (reader / 'seller_exhaustion.py').is_file():
        raise ValueError('supply --reader with the actual compatible role export')
    directory = Path(tempfile.mkdtemp(prefix='btc-data-contract-'))
    project = 'btc-test-' + uuid.uuid4().hex[:12]
    command = [docker, 'compose', '-p', project, '--env-file', str(directory/'compose.env'),
               '-f', str(ROOT/'service/compose.yaml'), '--profile', 'analysis']
    result = 1
    try:
        for name in ('mysql', 'mysql-root'):
            (directory/name).write_text(secrets.token_urlsafe(40)+'\n'); (directory/name).chmod(0o600)
        settings = {'mysql': {'host':'mysql','user':'btc','database':'btc','password_file':'/run/secrets/mysql_password'},
                    'objects':'/data/objects','socket':'/run/btc/btc.sock','test_only':True,
                    'maintenance_enabled':False,'recycle_grace_hours':48}
        (directory/'settings.json').write_text(json.dumps(settings))
        (directory/'compose.env').write_text('\n'.join(['BTC_DATA_DIR='+str(directory),
            'BTC_SETTINGS_FILE='+str(directory/'settings.json'), 'BTC_MYSQL_PASSWORD_FILE='+str(directory/'mysql'),
            'BTC_MYSQL_ROOT_PASSWORD_FILE='+str(directory/'mysql-root'), 'BTC_IMAGE_TAG=contract'])+'\n')
        subprocess.run(command+['up','-d','--build','--wait','mysql','core'], check=True, timeout=600)
        container = subprocess.check_output(command+['ps','-q','core'], text=True).strip()
        subprocess.run([docker,'cp',str(ROOT/'service/tests'),container+':/tmp/tests'],check=True)
        subprocess.run([docker,'cp',str(reader),container+':/tmp/reader'],check=True)
        subprocess.run(command+['exec','-T','core','mkdir','-p','/tmp/source/scripts'],check=True)
        for relative in ('skill','project-methods','scripts/build_synthetic_fixtures.py'):
            subprocess.run([docker,'cp',str(ROOT/relative),container+':/tmp/source/'+relative],check=True)
        subprocess.run(command+['exec','-T','-e','BTC_TEST_CONFIG=/run/btc/settings.json','-e','PYTHONPATH=/opt/btc',
                       '-e','BTC_TEST_SOURCE=/tmp/source','-e','BTC_COLLECTOR_CONTRACT_PATH=/tmp/reader/btc_evidence.py',
                       'core','python','-m','unittest','discover','-s','/tmp/tests','-v'],check=True)
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
        print('BTC_DATA_SERVICE_RESULT='+json.dumps({'mysql':True,'cli_mcp':True,'restart':True,'outage_recovery':True}))
        result = 0
    finally:
        stopped = subprocess.run(command+['down','--volumes','--remove-orphans'], timeout=90)
        if stopped.returncode == 0:
            # Desktop file sharing can release bind-mount handles just after
            # Compose returns. Retry this test-owned directory only.
            for attempt in range(4):
                try:
                    shutil.rmtree(directory);break
                except PermissionError:
                    if attempt==3:
                        print('Temporary test files retained for cleanup: '+str(directory),file=sys.stderr)
                    else:time.sleep(0.25)
        else:
            print('Temporary test node retained for cleanup: '+str(directory),file=sys.stderr)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker',default=shutil.which('docker'))
    parser.add_argument('--reader', default=str(ROOT/'telegram_reader'))
    args=parser.parse_args()
    if not args.docker:
        parser.error('Docker client and daemon are required; this validation cannot be skipped')
    raise SystemExit(run(args.docker, args.reader))
