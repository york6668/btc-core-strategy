"""Fail-closed admission for disposable BTC Docker validation projects.

The host flock prevents concurrent local callers.  An unstarted Docker
container with a fixed name provides an atomic claim across hosts sharing the
same daemon.  A crashed owner leaves that claim in place for manual review.
"""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import subprocess


GIB = 1024 ** 3
LOCK_NAME = 'btc-validation-admission'
LOCK_PATH = Path('/tmp/btc-validation-admission.lock')
RESERVE_BYTES = 2 * GIB
MIN_SWAP_FREE_FRACTION = 0.75
PROJECT_PREFIXES = ('btc-adoption-', 'btc-test-')
PRESERVED_VOLUME_SCHEMA = 'BTC_PRESERVED_VALIDATION_VOLUMES_V1'
DESKTOP_VM_LOG = Path.home() / 'Library/Containers/com.docker.docker/Data/log/vm'


class AdmissionError(RuntimeError):
    pass


def _command(args, *, timeout=20, run=subprocess.run):
    try:
        result = run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdmissionError('Docker admission command unavailable or timed out') from exc
    if result.returncode:
        raise AdmissionError('Docker admission command failed: ' + ' '.join(args[:3]) +
                             ' (' + str(result.returncode) + ')')
    return result.stdout.strip()


def parse_meminfo(value):
    values = {}
    for line in value.splitlines():
        if ':' not in line:
            continue
        key, tail = line.split(':', 1)
        bits = tail.split()
        if bits and bits[0].isdigit():
            values[key] = int(bits[0]) * 1024
    required = {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}
    if not required <= values.keys():
        raise AdmissionError('Docker VM memory evidence is incomplete')
    return values


def check_budget(info, memory, *, stack_limit_bytes):
    if int(info.get('MemTotal') or 0) != memory['MemTotal']:
        # Docker info and /proc/meminfo use slightly different rounding.
        if abs(int(info.get('MemTotal') or 0) - memory['MemTotal']) > 64 * 1024 ** 2:
            raise AdmissionError('Docker VM memory evidence disagrees')
    if int(info.get('NCPU') or 0) < 2:
        raise AdmissionError('Docker VM has fewer than two CPUs')
    required = stack_limit_bytes + RESERVE_BYTES
    if memory['MemAvailable'] < required:
        raise AdmissionError('Docker VM memory admission denied: available=' +
                             str(memory['MemAvailable']) + ', required=' + str(required))
    if memory['SwapTotal'] and memory['SwapFree'] < int(memory['SwapTotal'] * MIN_SWAP_FREE_FRACTION):
        raise AdmissionError('Docker VM swap is still under pressure')


def conflicting_stacks(names):
    return sorted(name for name in names.splitlines()
                  if name.startswith(PROJECT_PREFIXES))


def load_preserved_volumes(path):
    """Require an explicit, exact Docker-volume identity list; no wildcard grants."""
    if path is None:
        return None
    try:
        source = Path(path)
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 32 * 1024:
            raise AdmissionError('preserved-volume manifest is not a bounded regular file')
        record = json.loads(source.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise AdmissionError('preserved-volume manifest cannot be read') from exc
    if (not isinstance(record, dict) or set(record) != {'schema', 'daemon_id', 'volumes'}
            or record['schema'] != PRESERVED_VOLUME_SCHEMA
            or not isinstance(record['daemon_id'], str) or not record['daemon_id']
            or not isinstance(record['volumes'], list) or not 1 <= len(record['volumes']) <= 32):
        raise AdmissionError('invalid preserved-volume manifest')
    names = set()
    for entry in record['volumes']:
        if not isinstance(entry, dict) or set(entry) != {
                'name', 'project', 'driver', 'created_at', 'mountpoint', 'state'}:
            raise AdmissionError('invalid preserved-volume entry')
        project = entry['project']
        if (not isinstance(project, str) or not project.startswith(PROJECT_PREFIXES)
                or not re.fullmatch(r'btc-(?:adoption|test)-[a-z0-9]+', project)
                or not isinstance(entry['name'], str)
                or entry['name'] != project + '_mysql' or entry['name'] in names
                or entry['state'] != 'PRESERVED'
                or entry['driver'] != 'local'
                or not isinstance(entry['created_at'], str) or not entry['created_at']
                or not isinstance(entry['mountpoint'], str)
                or entry['mountpoint'] != '/var/lib/docker/volumes/' + entry['name'] + '/_data'):
            raise AdmissionError('invalid preserved-volume identity')
        names.add(entry['name'])
    return record


def recent_vm_global_oom(log_dir, since):
    """Read Docker Desktop's Linux kernel record; events alone miss process OOM."""
    log_dir = Path(log_dir)
    files = sorted(log_dir.glob('init.log*'))
    if not files:
        raise AdmissionError('Docker VM kernel logs unavailable for OOM admission')
    seen = False
    for path in files:
        try:
            if path.stat().st_mtime < since.timestamp():
                continue
            with path.open(errors='ignore') as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    timestamp = record.get('time')
                    if not timestamp:
                        continue
                    try:
                        when = dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    except ValueError:
                        continue
                    if when >= since:
                        seen = True
                        if 'oom-kill:constraint=' in record.get('msg', '') and 'global_oom' in record['msg']:
                            return when
        except OSError as exc:
            raise AdmissionError('Docker VM kernel logs cannot be read') from exc
    if not seen:
        raise AdmissionError('Docker VM kernel logs have no recent coverage')
    return None


def cleanup_compose(command, project, admission, *, run=subprocess.run):
    """Stop only this validation project; retain its data on any uncertainty."""
    try:
        result = run(command + ['down', '--remove-orphans'], capture_output=True,
                     text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdmissionError('validation Compose cleanup timed out or unavailable: ' + project) from exc
    if result.returncode:
        raise AdmissionError('validation Compose cleanup failed: ' + project)
    admission.verify_project_gone(project)


class ValidationGuard:
    def __init__(self, docker, *, stack_limit_bytes, project=None, run=subprocess.run,
                 lock_path=LOCK_PATH, now=None, platform_name=None, vm_log_dir=DESKTOP_VM_LOG,
                 preserved_volume_manifest=None):
        self.docker = docker
        self.stack_limit_bytes = stack_limit_bytes
        self.run = run
        self.lock_path = Path(lock_path)
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.platform_name = platform_name or platform.system()
        self.vm_log_dir = vm_log_dir
        self.preserved_volumes = load_preserved_volumes(preserved_volume_manifest)
        if project is not None and not project.startswith(PROJECT_PREFIXES):
            raise ValueError('invalid BTC validation project')
        self.project = project
        self.handle = None
        self.claimed = False
        self.claim_id = None
        self.project_started = False
        self.cleaned = False

    def _call(self, *parts, timeout=20):
        return _command([self.docker, *parts], timeout=timeout, run=self.run)

    def _unlock_local(self):
        if self.handle is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None

    def _remove_claim(self):
        self._call('rm', self.claim_id)
        self.claimed = False
        self.claim_id = None

    def _verify_preserved_volumes(self, info, actual_names):
        record = self.preserved_volumes
        if record is None:
            return False
        if not info.get('ID') or info['ID'] != record['daemon_id']:
            raise AdmissionError('preserved-volume Docker daemon identity changed')
        expected = {entry['name'] for entry in record['volumes']}
        if set(actual_names) != expected:
            raise AdmissionError('preserved-volume inventory changed or is incomplete')
        for entry in record['volumes']:
            name, project = entry['name'], entry['project']
            try:
                volume = json.loads(self._call('volume', 'inspect', '--format', '{{json .}}', name))
            except (ValueError, TypeError) as exc:
                raise AdmissionError('preserved-volume inspection failed: ' + name) from exc
            if not isinstance(volume, dict):
                raise AdmissionError('preserved-volume inspection failed: ' + name)
            labels = volume.get('Labels') or {}
            if not isinstance(labels, dict):
                raise AdmissionError('preserved-volume inspection failed: ' + name)
            if (volume.get('Name') != name or volume.get('Driver') != entry['driver']
                    or volume.get('CreatedAt') != entry['created_at']
                    or volume.get('Mountpoint') != entry['mountpoint']
                    or labels.get('com.docker.compose.project') != project
                    or labels.get('com.docker.compose.volume') != 'mysql'):
                raise AdmissionError('preserved-volume identity changed: ' + name)
            if (self._call('ps', '-a', '--filter', 'volume=' + name, '-q')
                    or self._call('ps', '-a', '--filter',
                                  'label=com.docker.compose.project=' + project, '-q')
                    or self._call('network', 'ls', '--filter',
                                  'label=com.docker.compose.project=' + project, '-q')):
                raise AdmissionError('preserved volume still has a container or network: ' + name)
            project_volumes = self._call('volume', 'ls', '--filter',
                                         'label=com.docker.compose.project=' + project,
                                         '--format', '{{.Name}}').splitlines()
            if project_volumes != [name]:
                raise AdmissionError('preserved project has another volume: ' + project)
        return True

    def __enter__(self):
        self.handle = self.lock_path.open('a+')
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise AdmissionError('another local BTC Docker validation is active') from exc
        created = self.now().isoformat()
        try:
            self._call('image', 'inspect', 'python:3.11-slim')
        except AdmissionError as exc:
            self._unlock_local()
            raise AdmissionError('prebuilt python:3.11-slim image is required for safe admission') from exc
        try:
            self.claim_id = self._call('create', '--name', LOCK_NAME, '--label', 'btc.validation=admission',
                       '--label', 'btc.created_utc=' + created,
                       '--label', 'btc.owner_host=' + socket.gethostname(),
                       '--label', 'btc.owner_pid=' + str(os.getpid()),
                       '--label', 'btc.project=' + (self.project or 'unknown'),
                       '--network', 'none', '--restart', 'no', '--pull', 'never', '--memory', '32m',
                       '--cpus', '0.1', '--entrypoint', '/bin/cat', 'python:3.11-slim',
                       '/proc/meminfo')
            if not self.claim_id:
                raise AdmissionError('Docker claim returned no container identity')
            self.claimed = True
        except AdmissionError as exc:
            try:
                raw = self._call('inspect', '--format', '{{json .}}', LOCK_NAME)
                owner = json.loads(raw)
                created_at = dt.datetime.fromisoformat(owner['Created'].replace('Z', '+00:00'))
                age = self.now() - created_at
                state = 'stale' if age >= dt.timedelta(hours=1) else 'occupied'
                labels = owner.get('Config', {}).get('Labels', {})
                message = ('Docker validation lock ' + state + ' for ' +
                           labels.get('btc.project', 'unknown') +
                           '; inspect ' + LOCK_NAME + ' before recovery')
            except (AdmissionError, ValueError, KeyError, TypeError):
                message = 'Docker validation lock unavailable; inspect ' + LOCK_NAME + ' before recovery'
            self._unlock_local()
            raise AdmissionError(message) from exc
        try:
            names = self._call('ps', '-a', '--format', '{{.Names}}')
            volumes = self._call('volume', 'ls', '--format', '{{.Name}}')
            networks = self._call('network', 'ls', '--format', '{{.Name}}')
            container_conflicts = conflicting_stacks(names)
            network_conflicts = conflicting_stacks(networks)
            volume_conflicts = conflicting_stacks(volumes)
            if container_conflicts or network_conflicts:
                conflicts = sorted(set(container_conflicts + network_conflicts))
                raise AdmissionError('retained BTC validation resources require review: ' +
                                     ', '.join(conflicts[:3]) + (' ...' if len(conflicts) > 3 else ''))
            info = json.loads(self._call('info', '--format', '{{json .}}'))
            if not self._verify_preserved_volumes(info, volume_conflicts) and volume_conflicts:
                raise AdmissionError('retained BTC validation resources require review (volumes): ' +
                                     ', '.join(volume_conflicts[:3]) +
                                     (' ...' if len(volume_conflicts) > 3 else ''))
            # The claim container runs only `cat` and exits; this works in CI
            # without a pre-existing production container, and observes the VM
            # itself rather than the macOS host.
            memory = parse_meminfo(self._call('start', '-a', LOCK_NAME))
            check_budget(info, memory, stack_limit_bytes=self.stack_limit_bytes)
            until = self.now()
            since = until - dt.timedelta(hours=1)
            if self.platform_name == 'Darwin' and recent_vm_global_oom(self.vm_log_dir, since):
                raise AdmissionError('Docker VM kernel reported global OOM within the last hour')
            events = self._call('events', '--since', since.isoformat(), '--until', until.isoformat(),
                                '--filter', 'event=oom', '--format', '{{.Time}}', timeout=30)
            if events:
                raise AdmissionError('Docker reported OOM within the last hour')
            return self
        except Exception:
            try:
                self._remove_claim()
            finally:
                self._unlock_local()
            raise

    def verify_project_gone(self, project):
        if not project.startswith(PROJECT_PREFIXES):
            raise AdmissionError('invalid validation project name')
        remaining = self._call('ps', '-a', '--filter',
                               'label=com.docker.compose.project=' + project, '-q')
        networks = self._call('network', 'ls', '--filter',
                              'label=com.docker.compose.project=' + project, '-q')
        volumes = self._call('volume', 'ls', '--filter',
                             'label=com.docker.compose.project=' + project, '-q')
        if remaining or networks or volumes:
            raise AdmissionError('validation stack cleanup incomplete: ' + project)
        self.cleaned = True

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.claimed and (not self.project_started or self.cleaned):
                self._remove_claim()
            elif self.claimed:
                raise AdmissionError('validation stack may remain; admission claim retained for review')
        finally:
            self._unlock_local()
        return False


class ValidationInterrupted(RuntimeError):
    pass


def install_interrupt_handlers():
    previous = {}
    def interrupt(signum, _frame):
        raise ValidationInterrupted('validation interrupted by signal ' + str(signum))
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    return lambda: [signal.signal(signum, handler) for signum, handler in previous.items()]
