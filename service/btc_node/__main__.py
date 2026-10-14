"""Node daemon and client. No model, native messenger or trading credentials inside."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import sys
import time
import traceback

from .api import dispatch
from .codec import Conflict, encode, millis
from .database import Database


MAX_REQUEST = 36 * 1024 * 1024


def record_maintenance_failure(settings, error):
    """Bind a failed maintenance attempt to the existing node defect gate."""
    from .deep_gate import open_fault
    signature = 'sha256:' + hashlib.sha256(
        (type(error).__name__ + '\0' + str(error)).encode()).hexdigest()
    reference = ('maintenance-loop/stderr/'
                 + datetime.now(timezone.utc).isoformat(timespec='seconds'))
    db = Database(settings)
    try:
        with db.writer(), db.transaction():
            attempt = db.metadata('maintenance.attempt') or {}
            if attempt.get('state') == 'RUNNING':
                attempt['state'] = 'FAILED'
                attempt['failed_ms'] = millis()
                db.metadata('maintenance.attempt', attempt, write=True)
            open_fault(db, scope='btc/maintenance', kind='UNKNOWN',
                       signature=signature, fault_digest=signature,
                       evidence_ref=reference)
    finally:
        db.close()


def record_scheduler_failure(settings, error):
    """Latch a failed node clock until the original defect is reviewed."""
    from .deep_gate import open_fault
    signature = 'sha256:' + hashlib.sha256(
        (type(error).__name__ + '\0' + str(error)).encode()).hexdigest()
    reference = 'scheduler-loop/stderr/' + datetime.now(timezone.utc).isoformat(timespec='seconds')
    db = Database(settings)
    try:
        with db.writer(), db.transaction():
            open_fault(db, scope='btc/scheduler', kind='UNKNOWN',
                       signature=signature, fault_digest=signature,
                       evidence_ref=reference)
    finally:
        db.close()


def scheduler_pulse(settings, database_class=Database):
    """Register due work inside the node writer without a per-minute operation log."""
    from . import scheduler
    from .deep_gate import check_scope
    db = database_class(settings)
    try:
        with db.writer(), db.transaction():
            if scheduler.mode(db) == 'OFF':
                return {'action': 'DISABLED'}
            if not check_scope(db, scope='btc/scheduler')['allowed']:
                return {'action': 'BLOCKED_SCHEDULER_FAULT'}
            return scheduler.tick(db)
    finally:
        db.close()


def scheduler_loop(settings):
    poll_ms = settings.get('scheduler_poll_ms', 60_000)
    if type(poll_ms) is not int or not 60_000 <= poll_ms <= 3_600_000:
        raise ValueError('node scheduler poll must be between one minute and one hour')
    while True:
        try:
            result = scheduler_pulse(settings)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as error:
            try: record_scheduler_failure(settings, error)
            except Exception as latch_error: traceback.print_exception(latch_error)
            traceback.print_exception(error)
        time.sleep(poll_ms / 1000)


def answer(settings, request):
    try:
        return {'ok': True, 'result': dispatch(settings, request)}
    except (ValueError, KeyError, TypeError) as exc:
        return {'ok': False, 'error': type(exc).__name__, 'message': str(exc)}
    except Exception as exc:
        # Never serialize DB connection settings or a driver's credential-bearing repr.
        return {'ok': False, 'error': type(exc).__name__,
                'message': 'service operation failed; retry the same operation_id after recovery'}


def call(path, request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(600)
        connection.connect(str(path))
        connection.sendall(encode(request))
        stream = connection.makefile('rb')
        response = stream.readline(MAX_REQUEST + 1)
        if not response or len(response) > MAX_REQUEST:
            raise Conflict('service disconnected or response exceeded limit; retry the same operation_id')
        return json.loads(response)


def serve(settings):
    path = Path(settings['socket'])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            call(path, {'method': 'status'})
        except (OSError, ValueError, Conflict):
            path.unlink()
        else:
            raise Conflict('service already listening')

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            raw = self.rfile.readline(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST or not raw.endswith(b'\n'):
                return
            try:
                request = json.loads(raw)
                response = answer(settings, request)
            except ValueError:
                response = {'ok': False, 'error': 'invalid JSON'}
            self.wfile.write(encode(response))

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    with Server(str(path), Handler) as server:
        path.chmod(0o660)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=os.environ.get('BTC_SERVICE_CONFIG'))
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'serve', 'request', 'mcp', 'maintenance', 'maintenance-loop', 'scheduler-loop', 'capacity-audit'):
        sub.add_parser(name)
    for name in ('import', 'compare', 'restore'):
        p = sub.add_parser(name)
        p.add_argument('--namespace', required=True)
        p.add_argument('--root', required=True)
    p = sub.add_parser('writer'); p.add_argument('owner', choices=('HOST', 'DOCKER'))
    p = sub.add_parser('project'); p.add_argument('--release', required=True)
    for name in ('backup','restore-database'):
        p = sub.add_parser(name); p.add_argument('--directory',required=True)
    args = parser.parse_args()
    if not args.config:
        parser.error('node-owned --config is required')
    settings = json.loads(Path(args.config).read_text())
    settings['_path'] = str(Path(args.config).resolve())
    if args.command in {'import', 'compare', 'restore', 'writer', 'project', 'backup', 'restore-database'}:
        from .admin import run
        print(json.dumps(run(settings, args.command, args), ensure_ascii=False))
        return 0
    if args.command == 'request':
        request = json.load(sys.stdin)
        response = answer(settings, request) if settings.get('direct') else call(settings['socket'], request)
        sys.stdout.buffer.write(encode(response))
        return 0 if response['ok'] else 1
    if args.command == 'mcp':
        from .mcp import run
        run(settings)
        return 0
    database = Database(settings)
    try:
        if args.command in {'init', 'serve'}:
            with database.writer():
                database.initialize()
    finally:
        database.close()
    if args.command == 'serve':
        if settings.get('compatibility_root'):
            compatibility = Path(settings['project']) / '.strategy'
            if not compatibility.exists() and not compatibility.is_symlink():
                compatibility.parent.mkdir(parents=True, exist_ok=True)
                compatibility.symlink_to(settings['compatibility_root'], target_is_directory=True)
        serve(settings)
    elif args.command == 'scheduler-loop':
        scheduler_loop(settings)
    elif args.command == 'capacity-audit':
        from .lifecycle import measure_capacity
        db = Database(settings)
        try:
            print(json.dumps(measure_capacity(db), ensure_ascii=False))
        finally:
            db.close()
    elif args.command.startswith('maintenance'):
        from .lifecycle import daily, loop_daily
        while True:
            db = None
            try:
                db = Database(settings)
                result = loop_daily(db) if args.command == 'maintenance-loop' else daily(db)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as error:
                if args.command != 'maintenance-loop':
                    raise
                if db is not None:
                    try: db.close()
                    except Exception: pass
                    db = None
                if not (isinstance(error, Conflict) and str(error) in {
                        'known maintenance defect blocks daily work',
                        'interrupted maintenance attempt requires fault review'}):
                    try: record_maintenance_failure(settings, error)
                    except Exception: pass
                traceback.print_exception(error)
                # Stay idle until a reviewed repair and explicit process
                # restart. Docker may revive this service after host reboot;
                # the persistent attempt/fault gate prevents backup replay.
                while True: time.sleep(3600)
            finally:
                if db is not None:
                    db.close()
            if args.command != 'maintenance-loop':
                break
            time.sleep(3600)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
