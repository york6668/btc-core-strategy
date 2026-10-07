"""Run a role from a source-only image with explicitly mounted node settings."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('collector', 'reader'))
    parser.add_argument('--config', type=Path)
    # The wrapped role owns its CLI options, including reader --asset. Only
    # consume this wrapper's node configuration and retain the remaining order.
    args, role_arguments = parser.parse_known_args()
    environment = dict(os.environ)
    if args.role == 'reader':
        environment.setdefault('BTC_READER_DATA_DIR', '/data/reader')
        environment.setdefault('BTC_READER_EVIDENCE_PATH', '/data/策略证据.md')
        environment['BTC_READER_TOKEN_STDIN'] = '1'
        target = Path('/app/telegram_reader/reader.py')
        arguments = role_arguments or ['sync']
    else:
        if not args.config:
            parser.error('collector requires its existing node configuration')
        config = json.loads(args.config.read_text())
        environment.update(config.get('environment', {}))
        private = config.get('private_environment_file')
        if private:
            path=Path(private)
            if path.stat().st_mode & 0o077:
                raise ValueError('private environment must be owner only')
            credentials=json.loads(path.read_text())
            if not isinstance(credentials,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in credentials.items()):
                raise ValueError('private environment must contain strings')
            if set(credentials) & set(config.get('environment',{})):
                raise ValueError('private environment cannot override public node settings')
            environment.update(credentials)
        target = Path('/app/btc_push.py')
        arguments = role_arguments or config.get('arguments', [])
    os.execve(sys.executable, [sys.executable, str(target), *arguments], environment)


if __name__ == '__main__':
    main()
