"""Lossless content storage; business time is always explicit."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


class Conflict(ValueError):
    pass


def utc(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('time must include a timezone')
    return value.astimezone(timezone.utc)


def stamp(value=None):
    return utc(value).isoformat()


def millis(value=None):
    return int(utc(value).timestamp() * 1000)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def identity(*parts):
    return fingerprint(encode(list(parts)))


def sync_directory(path):
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Blobs:
    """Immutable originals, including already-canonical legacy bytes.

    Files become visible atomically before a database reference is committed.
    An interrupted write can leave an unreferenced object, never a dangling
    committed reference. Garbage collection uses a separate grace period.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, digest):
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('invalid content digest')
        return self.root / digest[:2] / digest[2:]

    def put(self, data):
        if not isinstance(data, bytes):
            raise TypeError('blob input must be original bytes')
        digest = fingerprint(data)
        target = self.path(digest)
        if target.exists():
            if target.stat().st_size != len(data):
                raise Conflict('stored content length mismatch')
            if fingerprint(target.read_bytes()) != digest:
                raise Conflict('stored content checksum mismatch')
            return digest
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            # link is create-only; concurrent identical uploads cannot replace
            # an inode that a compatibility view is already reading.
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.stat().st_size != len(data):
                    raise Conflict('concurrent content mismatch')
            sync_directory(target.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return digest

    def get(self, digest):
        data = self.path(digest).read_bytes()
        if fingerprint(data) != digest:
            raise Conflict('stored content checksum mismatch')
        return data

    def materialize(self, digest, destination, *, require_link=False):
        destination = Path(destination)
        source = self.path(digest)
        self.get(digest)
        if destination.is_file() and source.samefile(destination):
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
        try:
            temporary.unlink()
            try:
                os.link(source, temporary)
            except OSError as error:
                if require_link:
                    raise Conflict('immutable evidence must share the content volume; hardlink failed: '
                                   + str(error.errno)) from error
                with temporary.open('xb') as handle:
                    handle.write(self.get(digest))
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(temporary, destination)
            sync_directory(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)
