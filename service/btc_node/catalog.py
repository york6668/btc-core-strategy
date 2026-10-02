"""Lossless migration and recovery of the existing filesystem/SQLite contracts.

MySQL stores the directory manifest, exact document hashes, SQLite schema and
rows. The compatibility view is rebuildable. Existing IDs/bytes and cursors are
not translated or reset. Only immutable file formats are hardlinked to CAS.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .codec import Conflict, encode, fingerprint, identity, millis, sync_directory
from .lifecycle import DAY, RETENTION_DAYS


def safe_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('catalog path must remain within its namespace')
    root = Path(root).resolve()
    result = root / relative
    if not result.parent.resolve().is_relative_to(root):
        raise ValueError('catalog parent escapes its namespace')
    return result


def retention(relative):
    parts = Path(relative).parts
    if any(p in {'outbox', 'research', 'versions', 'main', 'deep', 'legacy', 'facts'} for p in parts):
        return 'KEY'
    if any(p in {'cache', 'tmp', 'exports', 'previews'} for p in parts):
        return 'CACHE'
    if Path(relative).suffix == '.log' or 'logs' in parts:
        return 'RUN'
    if any(p in {'runs', 'runtime', 'automation-runs', 'implementation', 'workflow-upgrades'} or p.startswith(('run-', 'auto-')) for p in parts):
        return 'RUN'
    if any(p in {'raw', 'evidence'} for p in parts):
        return 'RAW'
    return 'KEY'


def sqlite_signature(source):
    return ':'.join(str((p.stat().st_mtime_ns, p.stat().st_size)) if p.exists() else '-'
                    for p in (source, Path(str(source) + '-wal')))


class Catalog:
    def __init__(self, db):
        self.db = db

    def sync(self, namespace, root, *, hardlink=False, protect=(), default_retention=None):
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError('namespace source does not exist')
        existing = {row['path']: row for row in self.db.rows('SELECT * FROM documents WHERE namespace=%s', (namespace,))}
        existing_databases = {row['path'] for row in self.db.rows('SELECT path FROM legacy_databases WHERE namespace=%s', (namespace,))}
        # A run with a completed report decision has finished its analysis
        # flow. Pending research/delivery references are protected separately.
        pending = []
        for run in root.rglob('run.json'):
            if not any(run.parent.glob('report-*-receipt.json')):
                try:
                    if json.loads(run.read_bytes()).get('status') == 'COMPLETED':
                        continue
                except (ValueError, OSError):
                    pass
                pending.append(str(run.parent.relative_to(root)))
        protect = tuple(protect) + tuple(pending)
        seen, changed, databases = set(), [], []
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in list(dirs):
                if (Path(directory) / name).is_symlink():
                    files.append(name); dirs.remove(name)
            for name in files:
                path = Path(directory) / name
                relative = str(path.relative_to(root))
                if name.endswith(('-wal', '-shm', '-journal', '.lock', '.sock')) or name.startswith('.staging-'):
                    continue
                seen.add(relative)
                stat = path.lstat()
                policy = 'ACTIVE' if any(relative == p or relative.startswith(p.rstrip('/') + '/') for p in protect) else (default_retention or retention(relative))
                prior = existing.get(relative)
                if prior and prior['retention_class'] != policy:
                    ttl = RETENTION_DAYS[policy]
                    self.db.execute('UPDATE documents SET retention_class=%s,expires_ms=%s WHERE document_key=%s',
                                    (policy, stat.st_mtime_ns // 1000000 + ttl * DAY if ttl else None, prior['document_key']))
                    self.db.execute('DELETE FROM document_recycling WHERE document_key=%s', (prior['document_key'],))
                if path.is_symlink():
                    link = os.readlink(path)
                    prior = existing.get(relative)
                    if prior and prior['kind'] == 'LINK' and prior['link_target'] == link:
                        continue
                    digest, size, kind = None, 0, 'LINK'
                elif path.is_file():
                    if path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                        saved=self.sync_sqlite(namespace, root, relative)
                        databases.append(saved)
                        record=self.db.one('SELECT schema_digest FROM legacy_databases WHERE database_key=%s',
                                           (identity(namespace,relative),))
                        ttl=RETENTION_DAYS[policy]
                        self.db.execute('INSERT INTO documents(document_key,namespace,path,kind,digest,link_target,byte_length,'
                            'source_mtime_ns,recorded_ms,retention_class,expires_ms) VALUES(%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s) '
                            'ON DUPLICATE KEY UPDATE kind=VALUES(kind),digest=VALUES(digest),link_target=NULL,'
                            'byte_length=VALUES(byte_length),source_mtime_ns=VALUES(source_mtime_ns),'
                            'retention_class=VALUES(retention_class),expires_ms=VALUES(expires_ms)',
                            (identity(namespace,relative),namespace,relative,'SQLITE',record['schema_digest'],stat.st_size,
                             stat.st_mtime_ns,millis(),policy,stat.st_mtime_ns//1000000+ttl*DAY if ttl else None))
                        if saved['changed']:
                            self.db.execute('DELETE FROM document_recycling WHERE document_key=%s',(identity(namespace,relative),))
                        continue
                    prior = existing.get(relative)
                    if prior and prior['kind'] == 'FILE' and prior['byte_length'] == stat.st_size and (prior['source_mtime_ns'] == stat.st_mtime_ns or (path.parent.name == 'raw' and path.stem == prior['digest'])):
                        continue
                    data = path.read_bytes()
                    after = path.stat()
                    if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                        raise Conflict('source changed during catalog import: ' + relative)
                    digest = self.db.put_blob(data)
                    link, size, kind = None, len(data), 'FILE'
                else:
                    continue
                ttl = RETENTION_DAYS[policy]
                expires = stat.st_mtime_ns // 1000000 + ttl * DAY if ttl else None
                self.db.execute('INSERT INTO documents(document_key,namespace,path,kind,digest,link_target,byte_length,'
                                'source_mtime_ns,recorded_ms,retention_class,expires_ms) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                                'ON DUPLICATE KEY UPDATE kind=VALUES(kind),digest=VALUES(digest),link_target=VALUES(link_target),'
                                'byte_length=VALUES(byte_length),source_mtime_ns=VALUES(source_mtime_ns),recorded_ms=VALUES(recorded_ms),'
                                'retention_class=VALUES(retention_class),expires_ms=VALUES(expires_ms)',
                                (identity(namespace, relative), namespace, relative, kind, digest, link, size,
                                 stat.st_mtime_ns, millis(), policy, expires))
                changed.append(relative)
                self.db.execute('DELETE FROM document_recycling WHERE document_key=%s', (identity(namespace, relative),))
                if hardlink and kind == 'FILE' and policy != 'CACHE':
                    # Do not link arbitrary mutable binaries to immutable content.
                    if path.parent.name == 'raw' and path.stem == digest:
                        self.db.blobs.materialize(digest, path)
        missing = (set(existing) | existing_databases) - seen
        for relative in missing:
            self.remove_sqlite(identity(namespace,relative))
            self.db.execute('DELETE FROM documents WHERE document_key=%s', (identity(namespace, relative),))
            self.db.execute('DELETE FROM document_recycling WHERE document_key=%s', (identity(namespace, relative),))
        self.db.metadata('namespace:' + namespace, {'root': str(root), 'checked_ms': millis()}, write=True)
        return {'namespace': namespace, 'changed': changed, 'removed': sorted(missing), 'sqlite': databases}

    def remove_sqlite(self, database_key):
        for table in ('legacy_row_blobs','legacy_rows','legacy_databases'):
            self.db.execute('DELETE FROM '+table+' WHERE database_key=%s',(database_key,))

    def sync_sqlite(self, namespace, root, relative):
        source = safe_path(root, relative)
        database_key = identity(namespace, relative)
        signature = sqlite_signature(source)
        previous = self.db.one('SELECT * FROM legacy_databases WHERE database_key=%s', (database_key,))
        if previous and previous['source_signature'] == signature:
            return {'path': relative, 'changed': False}
        connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('BEGIN')
            schema = [dict(row) for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")]
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            schema_digest = self.db.put_blob(encode({'schema': schema, 'user_version': version}))
            self.db.execute('INSERT INTO legacy_databases(database_key,namespace,path,schema_digest,source_signature) '
                            'VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE schema_digest=VALUES(schema_digest),'
                            'source_signature=VALUES(source_signature)',
                            (database_key, namespace, relative, schema_digest, signature))
            count, updated = 0, 0
            tables = {row['name'] for row in schema if row['type'] == 'table'}
            old_tables = {r['table_name'] for r in self.db.rows('SELECT DISTINCT table_name FROM legacy_rows WHERE database_key=%s',(database_key,))}
            for removed in old_tables-tables:
                for target in ('legacy_rows','legacy_row_blobs'):
                    self.db.execute('DELETE FROM '+target+' WHERE database_key=%s AND table_name=%s',(database_key,removed))
            for table in sorted(tables):
                quoted = '"' + table.replace('"', '""') + '"'
                columns = list(connection.execute('PRAGMA table_info(' + quoted + ')'))
                primary = [row['name'] for row in sorted(columns, key=lambda r: r['pk']) if row['pk']]
                saved = {r['row_key']: r['digest'] for r in self.db.rows('SELECT row_key,digest FROM legacy_rows WHERE database_key=%s AND table_name=%s', (database_key, table))}
                seen = set()
                for index, row in enumerate(connection.execute('SELECT * FROM ' + quoted)):
                    value, binaries = {}, set()
                    for column in row.keys():
                        item = row[column]
                        if isinstance(item, bytes):
                            blob = self.db.put_blob(item); binaries.add(blob)
                            item = {'$binary_content': blob}
                        value[column] = item
                    row_key = identity(*[value[p] for p in primary]) if primary else identity(index)
                    digest = fingerprint(encode(value))
                    seen.add(row_key); count += 1
                    if saved.get(row_key) == digest:
                        continue
                    self.db.put_blob(encode(value)); updated += 1
                    self.db.execute('INSERT INTO legacy_rows(database_key,table_name,row_key,digest) VALUES(%s,%s,%s,%s) '
                                    'ON DUPLICATE KEY UPDATE digest=VALUES(digest)', (database_key, table, row_key, digest))
                    self.db.execute('DELETE FROM legacy_row_blobs WHERE database_key=%s AND table_name=%s AND row_key=%s', (database_key, table, row_key))
                    for blob in binaries:
                        self.db.execute('INSERT INTO legacy_row_blobs(database_key,table_name,row_key,digest) VALUES(%s,%s,%s,%s)', (database_key, table, row_key, blob))
                for row_key in set(saved) - seen:
                    for target in ('legacy_rows', 'legacy_row_blobs'):
                        self.db.execute('DELETE FROM ' + target + ' WHERE database_key=%s AND table_name=%s AND row_key=%s', (database_key, table, row_key))
            return {'path': relative, 'changed': True, 'rows': count, 'updated_rows': updated}
        finally:
            connection.close()

    def restore(self, namespace, root, *, hardlink=False):
        root = Path(root); root.mkdir(parents=True, exist_ok=True)
        count = 0
        for row in self.db.rows('SELECT * FROM documents WHERE namespace=%s ORDER BY kind,path', (namespace,)):
            if row['kind']=='SQLITE':continue
            path = safe_path(root, row['path'])
            path.parent.mkdir(parents=True, exist_ok=True)
            if row['kind'] == 'LINK':
                if path.is_symlink() and os.readlink(path) == row['link_target']:
                    continue
                if path.exists() and not path.is_symlink():
                    raise Conflict('cannot replace a real object with a restored pointer')
                temporary = path.with_name('.restore-' + row['document_key'])
                temporary.unlink(missing_ok=True)
                temporary.symlink_to(row['link_target'])
                os.replace(temporary, path)
                os.utime(path, ns=(row['source_mtime_ns'], row['source_mtime_ns']), follow_symlinks=False)
            elif hardlink and path.parent.name == 'raw' and path.stem == row['digest']:
                self.db.blobs.materialize(row['digest'], path)
            else:
                if path.is_file() and not path.is_symlink() and fingerprint(path.read_bytes())==row['digest']:
                    if path.stat().st_mtime_ns!=row['source_mtime_ns']:
                        os.utime(path,ns=(row['source_mtime_ns'],row['source_mtime_ns']))
                    count+=1
                    continue
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(self.db.blobs.get(row['digest'])); handle.flush(); os.fsync(handle.fileno())
                os.replace(temporary, path)
                os.utime(path, ns=(row['source_mtime_ns'], row['source_mtime_ns']))
            count += 1
        for row in self.db.rows('SELECT * FROM legacy_databases WHERE namespace=%s', (namespace,)):
            self.restore_sqlite(row, safe_path(root, row['path']))
        return {'restored_documents': count}

    def restore_sqlite(self, record, destination):
        schema = self.db.value(record['schema_digest'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=destination.parent, suffix='.sqlite3')
        os.close(descriptor)
        connection = sqlite3.connect(temporary)
        try:
            connection.execute('PRAGMA foreign_keys=OFF')
            for item in schema['schema']:
                if item['type'] == 'table' and not item['name'].startswith('sqlite_'):
                    connection.execute(item['sql'])
            if any(item['name']=='sqlite_sequence' for item in schema['schema']) and not connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
                # SQLite retains this internal table after the last
                # AUTOINCREMENT table is dropped; reproduce that legal state.
                temporary_table='btc_restore_sequence'
                while connection.execute('SELECT 1 FROM sqlite_master WHERE name=?',(temporary_table,)).fetchone():
                    temporary_table+='x'
                connection.execute('CREATE TABLE "'+temporary_table+'" (id INTEGER PRIMARY KEY AUTOINCREMENT)')
                connection.execute('DROP TABLE "'+temporary_table+'"')
            sequence_rows = []
            for row in self.db.rows('SELECT table_name,digest FROM legacy_rows WHERE database_key=%s ORDER BY table_name,row_key', (record['database_key'],)):
                value = self.db.value(row['digest'])
                if row['table_name'] == 'sqlite_sequence':
                    sequence_rows.append(value)
                    continue
                columns = ','.join('"' + key.replace('"', '""') + '"' for key in value)
                items = [self.db.blobs.get(v['$binary_content']) if isinstance(v, dict) and set(v) == {'$binary_content'} else v for v in value.values()]
                connection.execute('INSERT INTO "' + row['table_name'].replace('"', '""') + '" (' + columns + ') VALUES(' + ','.join('?' for _ in items) + ')', items)
            if sequence_rows:
                connection.execute('DELETE FROM sqlite_sequence')
                connection.executemany('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)', [(v['name'],v['seq']) for v in sequence_rows])
            for item in schema['schema']:
                if item['type'] != 'table':
                    connection.execute(item['sql'])
            connection.execute('PRAGMA user_version=' + str(int(schema['user_version'])))
            connection.commit()
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise Conflict('restored SQLite failed integrity check')
        finally:
            connection.close()
        for suffix in ('-wal', '-shm'):
            Path(str(destination) + suffix).unlink(missing_ok=True)
        os.replace(temporary, destination)
        sync_directory(destination.parent)

    def compare(self, namespace, root):
        mismatches = []
        expected_paths = {r['path'] for r in self.db.rows('SELECT path FROM documents WHERE namespace=%s', (namespace,))}
        expected_paths.update(r['path'] for r in self.db.rows('SELECT path FROM legacy_databases WHERE namespace=%s', (namespace,)))
        actual_paths = set()
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in list(dirs):
                if (Path(directory)/name).is_symlink(): files.append(name);dirs.remove(name)
            for name in files:
                if name.endswith(('-wal','-shm','-journal','.lock','.sock')) or name.startswith('.staging-'): continue
                actual_paths.add(str((Path(directory)/name).relative_to(root)))
        mismatches.extend('path-set:'+p for p in sorted(expected_paths ^ actual_paths))
        for row in self.db.rows('SELECT * FROM documents WHERE namespace=%s', (namespace,)):
            if row['kind']=='SQLITE':continue
            path = safe_path(root, row['path'])
            if row['kind'] == 'LINK':
                good = path.is_symlink() and os.readlink(path) == row['link_target']
            else:
                good = path.is_file() and fingerprint(path.read_bytes()) == row['digest']
            if not good:
                mismatches.append(row['path'])
        checked_databases = []
        for record in self.db.rows('SELECT * FROM legacy_databases WHERE namespace=%s', (namespace,)):
            path = safe_path(root, record['path'])
            connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute('BEGIN')
                schema = [dict(r) for r in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")]
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                if fingerprint(encode({'schema': schema, 'user_version': version})) != record['schema_digest']:
                    mismatches.append(record['path'] + ':schema')
                for table in [r['name'] for r in schema if r['type'] == 'table']:
                    quoted = '"' + table.replace('"', '""') + '"'
                    columns = list(connection.execute('PRAGMA table_info(' + quoted + ')'))
                    primary = [r['name'] for r in sorted(columns, key=lambda r: r['pk']) if r['pk']]
                    stored = {r['row_key']:r['digest'] for r in self.db.rows('SELECT row_key,digest FROM legacy_rows WHERE database_key=%s AND table_name=%s', (record['database_key'],table))}
                    seen = set()
                    for index, row in enumerate(connection.execute('SELECT * FROM ' + quoted)):
                        value = {k: {'$binary_content': fingerprint(v)} if isinstance(v, bytes) else v for k,v in dict(row).items()}
                        row_key = identity(*[value[p] for p in primary]) if primary else identity(index)
                        seen.add(row_key)
                        if stored.get(row_key) != fingerprint(encode(value)):
                            mismatches.append(record['path'] + ':' + table + ':' + row_key)
                    if seen != set(stored):
                        mismatches.append(record['path'] + ':' + table + ':row-set')
                checked_databases.append(record['path'])
            finally:
                connection.close()
        return {'equal': not mismatches, 'mismatches': mismatches, 'checked_databases': checked_databases}
