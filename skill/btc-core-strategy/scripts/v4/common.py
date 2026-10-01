from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import uuid


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def exact(value, keys, name):
    require(isinstance(value, dict) and set(value) == set(keys.split()), name + ': invalid fields')


def text(value, name):
    require(isinstance(value, str) and bool(value.strip()), name + ': empty text')


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(value):
    return 'sha256:' + hashlib.sha256(value if isinstance(value, bytes) else encoded(value)).hexdigest()


def instant(value):
    require(isinstance(value, str), 'timestamp must be text')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise Invalid('invalid timestamp') from exc
    require(result.tzinfo is not None, 'timestamp needs timezone')
    return result


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_bytes())


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_tree(path):
    for item in Path(path).rglob('*'):
        if item.is_file():
            with item.open('rb') as stream:
                os.fsync(stream.fileno())
    for item in sorted(Path(path).rglob('*'), reverse=True):
        if item.is_dir():
            fsync_dir(item)
    fsync_dir(path)


def create(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, bytes) else encoded(value)
    with path.open('xb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    fsync_dir(path.parent)


def replace(path, value):
    path = Path(path)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    create(tmp, value)
    os.replace(tmp, path)
    fsync_dir(path.parent)


@contextmanager
def locked(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.writer.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def pointer(value, path):
    require(isinstance(path, str) and path.startswith('/'), 'reference must be JSON pointer')
    for part in path[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, ValueError, IndexError, TypeError) as exc:
            raise Invalid('missing reference: ' + path) from exc
    return value


def numbered(value):
    require(not isinstance(value, (float, bool)), 'use exact decimal strings, not binary floats')
    try:
        number = Decimal(value)
    except Exception as exc:
        raise Invalid('invalid decimal') from exc
    require(number.is_finite(), 'non-finite number')
    return format(number.normalize(), 'f')


ATOM = re.compile(r'[A-Z][A-Z0-9_]*\Z')


def atoms(values, name, empty=False):
    require(isinstance(values, list) and (empty or bool(values)), name + ': expected list')
    require(all(isinstance(v, str) and ATOM.fullmatch(v) for v in values), name + ': stable concepts required')
    require(len(set(values)) == len(values), name + ': duplicate concept')


def facts(project):
    root = Path(project)
    return {name: digest((root / name).read_bytes()) for name in ('资产配置.md', '操作记录.md', '纠正记录.md')}
