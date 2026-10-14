"""Small, payload-free resource trace on the existing service stderr stream."""
from __future__ import annotations

from contextvars import ContextVar
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import threading
import time
from uuid import uuid4

from .api import HOST_METHODS, READS, WRITES


_CURRENT = ContextVar('btc_request_trace', default=None)
_LOCK = threading.Lock()
_INFLIGHT = 0
_LAST_STATUS_NS = 0
STATUS_SAMPLE_NS = 60_000_000_000
_CGROUP = Path('/sys/fs/cgroup')
_STATUS = Path('/proc/self/status')


def _integer(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(32).strip()
        return int(raw) if raw.isdigit() else None
    except (OSError, ValueError):
        return None


def _memory_classes(path):
    values = {'anon': None, 'file': None, 'inactive_file': None}
    try:
        with path.open('rb') as stream:
            lines = stream.read(4096).splitlines()
        for line in lines:
            key, separator, raw = line.partition(b' ')
            name = key.decode('ascii', errors='ignore')
            if separator and name in values and raw.isdigit():
                values[name] = int(raw)
    except OSError:
        pass
    return values


def sample():
    """Read fixed, small kernel fields; unavailable values remain explicit nulls."""
    rss = None
    try:
        with _STATUS.open('rb') as stream:
            for line in stream.read(4096).splitlines():
                if line.startswith(b'VmRSS:'):
                    rss = int(line.split()[1]) * 1024
                    break
    except (OSError, ValueError, IndexError):
        pass
    current = _integer(_CGROUP / 'memory.current')
    peak = _integer(_CGROUP / 'memory.peak')
    swap = _integer(_CGROUP / 'memory.swap.current')
    classes = _memory_classes(_CGROUP / 'memory.stat')
    try:
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
    except OSError:
        children = None
    return {'process_rss_bytes': rss,
            'child_peak_rss_bytes_cumulative': (int(children.ru_maxrss) * 1024
                                                  if children and sys.platform.startswith('linux') else None),
            'cgroup_memory_current_bytes': current,
            'cgroup_memory_peak_bytes_cumulative': peak,
            'cgroup_swap_current_bytes': swap,
            'cgroup_anon_bytes': classes['anon'],
            'cgroup_file_bytes': classes['file'],
            'cgroup_inactive_file_bytes': classes['inactive_file'],
            'cgroup_state': 'READABLE' if current is not None and peak is not None else 'UNKNOWN'}


def _record(fields):
    # Values are from fixed enums, clocks, kernel counters, and generated IDs.
    # A single line uses the already bounded Docker json-file stderr log.
    try:
        line = json.dumps({'schema': 'BTC_RESOURCE_TRACE_V1', **fields},
                          sort_keys=True, separators=(',', ':'))
        if len(line) <= 1536:
            print(line, file=sys.stderr, flush=True)
    except (OSError, ValueError, TypeError):
        # Observability must never become a business failure.
        pass


def record_input_failure(outcome):
    if outcome not in {'READ_TIMEOUT', 'READ_ERROR', 'OVERSIZE', 'INVALID_FRAME', 'INVALID_JSON'}:
        raise ValueError('invalid trace outcome')
    _record({'event': 'INPUT_FAILURE', 'request_id': uuid4().hex,
             'method': 'UNKNOWN', 'outcome': outcome, 'at_ns': time.time_ns(),
             'pid': os.getpid(), 'thread_id': threading.get_native_id(), **sample()})


class RequestTrace:
    def __init__(self, method):
        self.method = method if isinstance(method, str) and method in READS | WRITES | HOST_METHODS else 'UNKNOWN'
        self.request_id = uuid4().hex
        self.started_ns = None
        self.started_mono_ns = None
        self.outcome = 'UNKNOWN'
        self._token = None
        self._sampled = True

    def __enter__(self):
        global _INFLIGHT, _LAST_STATUS_NS
        self.started_ns = time.time_ns()
        self.started_mono_ns = time.monotonic_ns()
        with _LOCK:
            _INFLIGHT += 1
            self.inflight_start = _INFLIGHT
            if self.method == 'status' and self.started_mono_ns - _LAST_STATUS_NS < STATUS_SAMPLE_NS:
                self._sampled = False
            elif self.method == 'status':
                _LAST_STATUS_NS = self.started_mono_ns
        self._token = _CURRENT.set(self)
        if self._sampled:
            _record({'event': 'START', 'request_id': self.request_id,
                     'method': self.method, 'at_ns': self.started_ns,
                     'pid': os.getpid(), 'thread_id': threading.get_native_id(),
                     'inflight': self.inflight_start, **sample()})
        return self

    def __exit__(self, error_type, error, traceback):
        global _INFLIGHT
        finished = time.time_ns()
        outcome = ('TIMEOUT' if error_type and issubclass(error_type, TimeoutError)
                   else 'EXCEPTION' if error_type else self.outcome)
        with _LOCK:
            inflight = _INFLIGHT
            _INFLIGHT -= 1
        try:
            if self._sampled or outcome != 'OK':
                _record({'event': 'END', 'request_id': self.request_id,
                         'method': self.method, 'outcome': outcome,
                         'at_ns': finished, 'duration_ms': round((time.monotonic_ns() - self.started_mono_ns) / 1e6, 3),
                         'pid': os.getpid(), 'thread_id': threading.get_native_id(),
                         'inflight': inflight, **sample()})
        finally:
            _CURRENT.reset(self._token)


def run_child(command, *, cwd, env):
    """Preserve the old captured result and attach a child PID when traced."""
    trace = _CURRENT.get()
    if trace is None:
        return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
    except OSError:
        _record({'event': 'CHILD_END', 'request_id': trace.request_id,
                 'method': trace.method, 'outcome': 'SPAWN_ERROR',
                 'at_ns': time.time_ns(), 'pid': os.getpid(),
                 'child_pid': None, 'thread_id': threading.get_native_id(), **sample()})
        raise
    _record({'event': 'CHILD_START', 'request_id': trace.request_id,
             'method': trace.method, 'at_ns': time.time_ns(),
             'pid': os.getpid(), 'child_pid': process.pid,
             'thread_id': threading.get_native_id(), **sample()})
    try:
        stdout, stderr = process.communicate()
    except BaseException:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.communicate()
        except BaseException:
            pass
        _record({'event': 'CHILD_END', 'request_id': trace.request_id,
                 'method': trace.method, 'outcome': 'INTERRUPTED',
                 'at_ns': time.time_ns(), 'pid': os.getpid(),
                 'child_pid': process.pid, 'thread_id': threading.get_native_id(), **sample()})
        raise
    _record({'event': 'CHILD_END', 'request_id': trace.request_id,
             'method': trace.method, 'outcome': 'OK' if process.returncode == 0 else 'NONZERO',
             'at_ns': time.time_ns(), 'pid': os.getpid(),
             'child_pid': process.pid, 'thread_id': threading.get_native_id(), **sample()})
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
