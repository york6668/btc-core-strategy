"""Small transaction boundary shared by CLI, MCP, import and execution."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

from . import schema
from .codec import Blobs, Conflict, encode, millis


class Database:
    def __init__(self, settings):
        import pymysql
        self.settings = settings
        options = dict(settings['mysql'])
        password_file = options.pop('password_file')
        options['password'] = Path(password_file).read_text().strip()
        options.setdefault('connect_timeout', 10)
        options.setdefault('read_timeout', 120)
        options.setdefault('write_timeout', 120)
        self.connection = pymysql.connect(**options, charset='utf8mb4',
                                          autocommit=False,
                                          cursorclass=pymysql.cursors.DictCursor)
        self.blobs = Blobs(settings['objects'])

    def close(self):
        self.connection.close()

    def execute(self, sql, args=()):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, args)
            return cursor.rowcount

    def rows(self, sql, args=()):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, args)
            return list(cursor.fetchall())

    def iter_rows(self, sql, args=()):
        """Stream a large result without buffering it in the client process.

        The caller must consume or close the iterator before issuing another
        query on this connection. In particular, backups use a dedicated
        consistent-snapshot connection for this stream.
        """
        import pymysql
        with self.connection.cursor(pymysql.cursors.SSDictCursor) as cursor:
            cursor.execute(sql, args)
            yield from cursor

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def initialize(self):
        from .market_store import DDL as MARKET_DDL
        # MySQL DDL auto-commits; only initialize on explicit service startup.
        for statement in schema.DDL + MARKET_DDL:
            self.execute(statement)
        row = self.one('SELECT version FROM node_schema WHERE singleton=1')
        if row and row['version'] != schema.VERSION:
            raise Conflict('unsupported database schema; explicit migration required')
        if not row:
            self.execute('INSERT INTO node_schema(singleton,version) VALUES(1,%s)', (schema.VERSION,))
        self.connection.commit()

    @contextmanager
    def transaction(self):
        self.connection.begin()
        try:
            yield self
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    @contextmanager
    def writer(self, timeout=30):
        # Serializes service writers and GC across CLI/MCP/server processes.
        self.connection.rollback()
        row = self.one('SELECT GET_LOCK(%s,%s) AS acquired',
                       ('btc-node:' + self.settings['mysql']['database'], timeout))
        if not row or row['acquired'] != 1:
            raise Conflict('another node writer is active; retry the same operation')
        try:
            yield
        finally:
            self.connection.rollback()
            self.one('SELECT RELEASE_LOCK(%s) AS released',
                     ('btc-node:' + self.settings['mysql']['database'],))

    def put_blob(self, data):
        digest = self.blobs.put(data)
        self.execute('INSERT IGNORE INTO blobs(digest,byte_length,created_ms) VALUES(%s,%s,%s)',
                     (digest, len(data), millis()))
        self.execute('UPDATE blobs SET unreferenced_ms=NULL WHERE digest=%s', (digest,))
        return digest

    def value(self, digest):
        return json.loads(self.blobs.get(digest))

    def metadata(self, name, value=None, *, write=False):
        if write:
            digest = self.put_blob(encode(value))
            self.execute('INSERT INTO node_metadata(name,digest) VALUES(%s,%s) '
                         'ON DUPLICATE KEY UPDATE digest=VALUES(digest)', (name, digest))
            refs = set()
            def collect(item):
                if isinstance(item, str):
                    candidate = item.removeprefix('sha256:')
                    if len(candidate) == 64 and all(c in '0123456789abcdef' for c in candidate): refs.add(candidate)
                elif isinstance(item, dict):
                    for v in item.values(): collect(v)
                elif isinstance(item, list):
                    for v in item: collect(v)
            collect(value)
            self.execute('DELETE FROM metadata_blobs WHERE name=%s', (name,))
            for ref in refs:
                self.execute('INSERT IGNORE INTO metadata_blobs(name,digest) SELECT %s,digest FROM blobs WHERE digest=%s', (name,ref))
            return value
        row = self.one('SELECT digest FROM node_metadata WHERE name=%s', (name,))
        return self.value(row['digest']) if row else None
