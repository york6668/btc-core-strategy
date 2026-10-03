#!/usr/bin/env python3
"""Prepare node-owned Docker configuration from its existing runtime config.

This command creates no source credentials, does not enable a writer, and does
not alter schedules or move data. Cutover follows verified import/recovery.
"""
import argparse
import json
from pathlib import Path
import secrets
import sys


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)


def prepare(node_path, data, docker, compose_project, source):
    node = json.loads(node_path.read_text())
    if node.get('schema') != 'BTC_NODE_CONFIG_V1':
        raise ValueError('existing BTC node configuration required')
    data = data.resolve(); data.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_dir = data / 'secrets'; secret_dir.mkdir(exist_ok=True, mode=0o700)
    for name in ('mysql', 'mysql-root'):
        path = secret_dir / name
        if not path.exists():
            path.write_text(secrets.token_urlsafe(40) + '\n'); path.chmod(0o600)
    settings_path = data / 'settings.json'
    if settings_path.exists():
        raise FileExistsError('node already prepared; edit its existing configuration explicitly')
    project = Path(node['project_dir'])
    sys.path.insert(0, str(source / 'skill/btc-core-strategy/scripts'))
    from node_bridge import sync_facts
    sync_facts(project, data / 'native-facts')
    native_project = data / 'native-project'
    native_project.mkdir(exist_ok=True)
    (data / 'reader-source/data').mkdir(parents=True, exist_ok=True)
    (data / 'reader').mkdir(exist_ok=True)
    for name in ('资产配置.md', '操作记录.md', '纠正记录.md'):
        target = native_project / name
        if not target.is_symlink():
            target.symlink_to(Path('/run/btc/facts/current') / name)
    state_link = native_project / '.strategy'
    state_link.mkdir(exist_ok=True)
    container_node = dict(node, python='/usr/local/bin/python', frozen_market_python='/usr/local/bin/python')
    container_node.pop('frozen_market_python_version', None)
    container_node.pop('docker_service', None)
    write(data / 'container-node.json', container_node)
    settings = {'mysql': {'host': 'mysql', 'user': 'btc', 'database': 'btc', 'password_file': '/run/secrets/mysql_password'},
                'objects': str(data / 'objects'), 'socket': '/run/btc/btc.sock', 'journal': str(data / 'journal'),
                'project': str(project), 'release_root': str(Path(node['release_root']) / 'releases'),
                'container_node_config': str(data / 'container-node.json'),
                'reader_data': node['reader_data_dir'],
                'observation_db': node['observation_db'], 'maintenance_enabled': False,
                'recycle_grace_hours': 48, 'compatibility_root': str(data / 'strategy'),
                'backup_directory': str(data / 'backups'), 'backup_retention_days': 7,
                'auxiliary_namespaces': {name:str(data/path) for name,path in
                    (('native_inputs','native-inputs'),('native_proofs','native-proofs'),('facts','native-facts'))},
                'capacity_paths': [str(data/name) for name in ('strategy','reader','backups','native-proofs','native-inputs','native-facts','journal')],
                'namespaces': {'strategy': str(project / '.strategy')}}
    write(settings_path, settings)
    environment = {'BTC_DATA_DIR': str(data), 'BTC_CONTAINER_DATA_DIR': str(data), 'BTC_SETTINGS_FILE': str(settings_path),
                   'BTC_MYSQL_PASSWORD_FILE': str(secret_dir / 'mysql'),
                   'BTC_MYSQL_ROOT_PASSWORD_FILE': str(secret_dir / 'mysql-root'),
                   'BTC_READER_DATA_DIR': str(data / 'reader'), 'BTC_IMAGE_TAG': 'candidate'}
    (data / 'compose.env').write_text('\n'.join(k+'='+v for k,v in environment.items())+'\n')
    volumes = [str(data) + ':' + str(data),
               node['release_root'] + ':' + node['release_root'] + ':ro',
               str(data / 'reader-source') + ':' + node['reader_code_dir'] + ':ro',
               # SQLite mode=ro still needs a writable directory for WAL's
               # shared-memory coordination when the previous reader closed.
               str(data / 'reader') + ':' + node['reader_data_dir'],
               str(native_project) + ':' + str(project),
               str(data / 'strategy') + ':' + str(project / '.strategy'),
               str(data / 'native-facts') + ':/run/btc/facts:ro']
    # Import runs on the native adapter through a localhost-only DB endpoint.
    # Containers receive source exports and fact generations, never broad home
    # directory access or the Keychain.
    write(data / 'compose.override.json', {'services': {'core': {'volumes': volumes},
         'maintenance': {'volumes': volumes}, 'mysql': {'ports': ['127.0.0.1::3306']}}})
    write(data / 'compose.active.json', {'services': {'core': {'volumes': volumes},
         'maintenance': {'volumes': volumes}}})
    bridge = {'enabled': False, 'binary': str(docker), 'project': compose_project,
              'env_file': str(data / 'compose.env'), 'compose_file': str(source / 'service/compose.yaml'),
              'override_files': [str(data / 'compose.active.json')],
              'native_proof_dir': str(data / 'native-proofs'), 'container_proof_dir': str(data / 'native-proofs')}
    bridge['fact_bridge_dir'] = str(data / 'native-facts')
    write(data / 'bridge.json', bridge)
    return {'data': str(data), 'settings': str(settings_path), 'writer_enabled': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True, type=Path)
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--docker', required=True, type=Path)
    parser.add_argument('--compose-project', default='btc-analysis')
    args = parser.parse_args()
    print(json.dumps(prepare(args.node, args.data, args.docker, args.compose_project, Path(__file__).resolve().parents[1])))
