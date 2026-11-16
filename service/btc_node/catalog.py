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
import re
import sqlite3
import stat
import tempfile
import uuid

from .codec import Conflict, encode, fingerprint, fingerprint_file, identity, millis, sync_directory
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
    # A read-only WAL reopen can create an empty -wal without new data.
    # Treat it like an absent WAL so unchanged checkpoints remain idempotent.
    return 'rowid-v2:' + ':'.join(str((p.stat().st_mtime_ns, p.stat().st_size)) if p.exists() and (p==source or p.stat().st_size) else '-'
                    for p in (source, Path(str(source) + '-wal')))


def sqlite_rows(connection, table):
    quoted='"'+table.replace('"','""')+'"'
    columns=list(connection.execute('PRAGMA table_info('+quoted+')'))
    primary=[r['name'] for r in sorted(columns,key=lambda r:r['pk']) if r['pk']]
    names={r['name'].lower() for r in columns}
    selected=','.join('"'+r['name'].replace('"','""')+'"' for r in columns)
    alias=next((name for name in ('_rowid_','rowid','oid') if name not in names),None)
    if alias:
        try:cursor=connection.execute('SELECT '+alias+','+selected+' FROM '+quoted)
        except sqlite3.OperationalError:alias=None
    if not alias:cursor=connection.execute('SELECT '+selected+' FROM '+quoted)
    for index,row in enumerate(cursor):
        values=list(row)
        implicit=values.pop(0) if alias else None
        value=dict(zip([r['name'] for r in columns],values))
        row_key=identity(*[{'$binary_content':fingerprint(value[p])} if isinstance(value[p],bytes) else value[p] for p in primary]) if primary else identity(implicit if alias else index)
        yield row_key,value,implicit,alias,index


class Catalog:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _dependency_entries(manifest):
        """An explicit archive selection, never a replacement namespace/view."""
        if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'files'}
                or manifest['schema'] != 'BTC_RESEARCH_DEPENDENCIES_V1'
                or not isinstance(manifest['files'], list) or not manifest['files']):
            raise ValueError('explicit research dependency manifest required')
        seen, entries = set(), []
        research = {'requests', 'inputs', 'independent', 'candidates', 'results',
                    'author_outputs', 'public_case_sources', 'user_requests'}
        investment = {'policies', 'rules', 'validations', 'evaluations', 'decisions'}
        for item in manifest['files']:
            if not isinstance(item, dict) or set(item) != {'path', 'sha256'}:
                raise ValueError('dependency needs exact path and byte digest')
            name, digest = item['path'], item['sha256']
            if not isinstance(name, str) or not isinstance(digest, str):
                raise ValueError('dependency path and digest must be text')
            path = Path(name)
            if (path.is_absolute() or '..' in path.parts or '\\' in name
                    or path.as_posix() != name or name in seen):
                raise ValueError('unique canonical relative dependency path required')
            parts = path.parts
            allowed = (len(parts) >= 4 and parts[:2] == ('v4', 'research')
                       and parts[2] in research)
            allowed |= len(parts) >= 4 and parts[:2] == ('v4', 'evidence')
            allowed |= (len(parts) >= 4 and parts[:2] == ('v4', 'investment')
                        and (parts[2] in investment or parts[2:4] == ('ledger', 'versions')))
            if (not allowed or any(p.startswith('.') for p in parts)
                    or path.name == 'current' or path.name.endswith('-current.json')):
                raise ValueError('only archived research/evidence/investment dependencies may be appended')
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
                raise ValueError('dependency requires its original SHA256 byte digest')
            seen.add(name)
            entries.append({'path': name, 'sha256': digest})
        return entries

    @staticmethod
    def _dependency_bytes(root, item):
        root = Path(root).resolve(strict=True)
        # Open each component relative to an already-open directory. Checking
        # Path.is_symlink before open alone would leave an ancestor-swap race.
        parts = Path(item['path']).parts
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise Conflict('dependency source is not a regular file')
            data = handle.read()
            after = os.fstat(handle.fileno())
        if ((before.st_ino, before.st_mtime_ns, before.st_size, before.st_ctime_ns) !=
                (after.st_ino, after.st_mtime_ns, after.st_size, after.st_ctime_ns)
                or fingerprint(data) != item['sha256'][7:]):
            raise Conflict('dependency source bytes changed: ' + item['path'])
        return data, after

    def plan_dependencies(self, root, manifest):
        """Read-only preflight; content hashes confer neither authorship nor approval."""
        planned = []
        archive = self._dependency_archive()
        for item in self._dependency_entries(manifest):
            if item['path'] in archive and archive[item['path']] != item['sha256']:
                raise Conflict('archived dependency identity has different content: ' + item['path'])
            data, source = self._dependency_bytes(root, item)
            row = self.db.one('SELECT * FROM documents WHERE document_key=%s',
                              (identity('strategy', item['path']),))
            if row:
                if (row['namespace'] != 'strategy' or row['path'] != item['path']
                        or row['kind'] != 'FILE' or row['link_target'] is not None
                        or row['digest'] != item['sha256'][7:] or row['byte_length'] != len(data)):
                    raise Conflict('existing dependency identity has different content: ' + item['path'])
                blob = self.db.blobs.path(row['digest'])
                if not blob.is_file() or blob.is_symlink() or fingerprint_file(blob) != row['digest']:
                    raise Conflict('existing dependency blob is unavailable or corrupt: ' + item['path'])
            planned.append({**item, 'byte_length': len(data),
                            'source_mtime_ns': source.st_mtime_ns,
                            'retention_action': ('PROMOTE_KEY' if row and
                                (row['retention_class'] != 'KEY' or row['expires_ms'] is not None) else 'KEEP'),
                            'action': 'UNCHANGED' if row else 'APPEND'})
        return {'status': 'CHECKED', 'scope': 'ARCHIVAL_DEPENDENCIES_ONLY',
                'counts': {'append': sum(v['action'] == 'APPEND' for v in planned),
                           'existing_content_unchanged': sum(v['action'] == 'UNCHANGED' for v in planned),
                           'promote_key': sum(v['retention_action'] == 'PROMOTE_KEY' for v in planned)},
                'files': planned, 'manifest_sha256': fingerprint(encode(manifest)),
                'strategy_adopted': False, 'compatibility_view_materialized': False}

    def _dependency_archive(self):
        value = self.db.metadata('catalog.dependencies:strategy')
        return {v['path']: v['sha256'] for v in self._dependency_entries(value)} if value else {}

    def plan_materialization(self, root, manifest):
        """Verify registered original bytes and a view; no filesystem/DB writes."""
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('existing compatibility directory required')
        archive, files = self._dependency_archive(), []
        for item in self._dependency_entries(manifest):
            row = self.db.one('SELECT * FROM documents WHERE document_key=%s',
                              (identity('strategy', item['path']),))
            if (archive.get(item['path']) != item['sha256'] or not row or row['kind'] != 'FILE'
                    or row['namespace'] != 'strategy' or row['path'] != item['path']
                    or row['digest'] != item['sha256'][7:] or row['link_target'] is not None):
                raise Conflict('materialization requires the exact registered archive: ' + item['path'])
            data = self.db.blobs.get(row['digest'])
            if len(data) != row['byte_length']:
                raise Conflict('registered archive length changed')
            try:
                self._dependency_bytes(root, item)
            except FileNotFoundError:
                action = 'CREATE'
            else:
                action = 'UNCHANGED'
            files.append({**item, 'action': action})
        return {'status': 'CHECKED', 'scope': 'REGISTERED_ARCHIVE_VIEW_ONLY', 'files': files,
                'create': sum(v['action'] == 'CREATE' for v in files),
                'unchanged': sum(v['action'] == 'UNCHANGED' for v in files), 'strategy_adopted': False}

    @staticmethod
    def _dependency_parent(root, relative, *, create=False):
        parts = Path(relative).parts
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                if create:
                    try: os.mkdir(part, mode=0o700, dir_fd=parent)
                    except FileExistsError: pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent); parent = child
            return parent, parts[-1]
        except BaseException:
            os.close(parent)
            raise

    def materialize_dependencies(self, root, manifest):
        """Only create missing originals in the configured view; never replace.

        Temp bytes are fsynced then linked with no-replace semantics. A crash
        can leave complete matching originals, never a partial visible file.
        Rechecking the explicit manifest identifies completed work. Exceptions
        retain already published originals: checking then unlinking a target
        cannot safely exclude an external concurrent replacement.
        """
        root = Path(root).resolve(strict=True)
        created = 0
        with self.db.writer(), self.db.transaction():
            if self.db.one("SELECT operation_id FROM operations WHERE status<>'COMMITTED' LIMIT 1"):
                raise Conflict('reconcile pending operations before materializing dependencies')
            plan = self.plan_materialization(root, manifest)
            for item in plan['files']:
                if item['action'] == 'UNCHANGED':
                    continue
                parent, leaf = self._dependency_parent(root, item['path'], create=True)
                temporary = '.staging-dependency-' + uuid.uuid4().hex
                try:
                    data = self.db.blobs.get(item['sha256'][7:])
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 mode=0o600, dir_fd=parent)
                    with os.fdopen(fd, 'wb') as handle:
                        handle.write(data); handle.flush(); os.fsync(handle.fileno())
                    try:
                        os.link(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    except FileExistsError:
                        # A competing creator never grants overwrite rights.
                        self._dependency_bytes(root, item)
                    else:
                        created += 1
                        os.fsync(parent)
                finally:
                    try: os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError: pass
                    os.close(parent)
            checked = self.plan_materialization(root, manifest)
            if checked['create']:
                raise Conflict('compatibility view changed during materialization')
            return {**checked, 'status': 'MATERIALIZED', 'created': created,
                    'database_changed': False}

    def append_dependencies(self, root, manifest):
        """Add references and retain selected archives atomically; never adopt/project.

        A failed transaction may leave unreferenced CAS bytes, as normal blob
        writes do; no partially committed documents survive. The existing
        grace-period GC owns those bytes. Reader materialization and business
        applicability remain separate; this operation is not strategy adoption.
        """
        with self.db.writer(), self.db.transaction():
            if self.db.one("SELECT operation_id FROM operations WHERE status<>'COMMITTED' LIMIT 1"):
                raise Conflict('reconcile pending operations before appending dependencies')
            plan = self.plan_dependencies(root, manifest)
            archive = self._dependency_archive()
            previous_archive = dict(archive)
            recycling_cleared = 0
            for item in plan['files']:
                if item['action'] == 'UNCHANGED':
                    if item['retention_action'] == 'PROMOTE_KEY':
                        self.db.execute("UPDATE documents SET retention_class='KEY',expires_ms=NULL WHERE document_key=%s",
                                        (identity('strategy', item['path']),))
                    recycling_cleared += self.db.execute('DELETE FROM document_recycling WHERE document_key=%s',
                                                        (identity('strategy', item['path']),))
                    continue
                data, source = self._dependency_bytes(root, item)
                digest = self.db.put_blob(data)
                self.db.execute('INSERT INTO documents(document_key,namespace,path,kind,digest,link_target,'
                                'byte_length,source_mtime_ns,recorded_ms,retention_class,expires_ms) '
                                'VALUES(%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,NULL)',
                                (identity('strategy', item['path']), 'strategy', item['path'], 'FILE', digest,
                                 len(data), source.st_mtime_ns, millis(), 'KEY'))
                recycling_cleared += self.db.execute('DELETE FROM document_recycling WHERE document_key=%s',
                                                    (identity('strategy', item['path']),))
            archive.update({v['path']: v['sha256'] for v in plan['files']})
            if archive != previous_archive:
                # Existing metadata/blob references keep these archive-only
                # rows durable when the live compatibility view omits them.
                # This is storage provenance, never permission or approval.
                self.db.metadata('catalog.dependencies:strategy',
                    {'schema': 'BTC_RESEARCH_DEPENDENCIES_V1',
                     'files': [{'path': p, 'sha256': archive[p]} for p in sorted(archive)]}, write=True)
            return {**plan, 'status': 'APPENDED',
                    'appended': sum(v['action'] == 'APPEND' for v in plan['files']),
                    'unchanged': sum(v['action'] == 'UNCHANGED' for v in plan['files']),
                    'retention_promoted': plan['counts']['promote_key'],
                    'recycling_cleared': recycling_cleared}

    @staticmethod
    def raw_directories(records):
        """Only content-addressed originals qualify; mutable files never do."""
        grouped = {}
        for row in records:
            path = Path(row['path'])
            if path.parent.name == 'raw':
                grouped.setdefault(str(path.parent), []).append(row)
        return {name: rows for name, rows in grouped.items()
                if all(row['kind'] == 'FILE' and Path(row['path']).stem == row['digest']
                       and row['retention_class'] != 'CACHE' for row in rows)}

    @staticmethod
    def raw_manifest(rows):
        return fingerprint(encode(sorted((row['path'], row['digest'], row['byte_length']) for row in rows)))

    def share_original(self, path, digest):
        """Share originals and verified frozen inputs; ordinary JSON stays writable."""
        named_original = path.parent.name == 'raw' and path.stem == digest
        if not named_original and path.name not in ('collector.json', 'market.json'):
            return
        target = self.db.blobs.path(digest)
        if target.is_file() and target.samefile(path):
            return
        if not named_original and not self.frozen_input(path):
            return
        if fingerprint(path.read_bytes()) != digest:
            raise Conflict('immutable original changed before sharing: ' + str(path))
        self.db.blobs.materialize(digest, path,
            require_link=self.db.settings.get('require_shared_evidence', False))

    @staticmethod
    def frozen_input(path):
        """Use the existing run/evidence hash contract, never a filename alone."""
        try:
            run = json.loads((path.parent / 'run.json').read_bytes())
            if run.get('schema') != 'BTC_RUN_MATERIAL_V1':
                return False
            field = 'market' if path.name == 'market.json' else 'collector'
            if not run.get(field) or Path(run[field]).resolve() != path.resolve():
                return False
            raw = path.read_bytes()
            value = json.loads(raw)
            bound = 'sha256:' + fingerprint(encode(value))
            if field == 'market':
                return bound == run.get('market_hash')
            evidence = json.loads((Path(run['evidence']) / 'evidence.json').read_bytes())
            binding = evidence.get('collector', {})
            source = next((v for v in evidence.get('sources', []) if v['id'] == binding.get('source_id')), {})
            return (evidence.get('evidence_id') == run.get('evidence_id') ==
                    'sha256:' + fingerprint(encode({k:v for k,v in evidence.items() if k != 'evidence_id'}))
                    and binding.get('snapshot_id') == bound and source.get('raw_hash') == 'sha256:' + fingerprint(raw))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def sync(self, namespace, root, *, hardlink=False, protect=(), default_retention=None):
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError('namespace source does not exist')
        existing = {row['path']: row for row in self.db.iter_rows('SELECT * FROM documents WHERE namespace=%s', (namespace,))}
        archived = self._dependency_archive() if namespace == 'strategy' else {}
        for name, digest in archived.items():
            row = existing.get(name)
            if not row or row['kind'] != 'FILE' or row['digest'] != digest[7:]:
                raise Conflict('archived dependency registration is missing or changed: ' + name)
            source = root / name
            if source.exists() or source.is_symlink():
                self._dependency_bytes(root, {'path': name, 'sha256': digest})
        # A verified raw directory contains immutable CAS hardlinks. Its entry
        # clock and complete filename/manifest set detect additions, removal,
        # and recovery. Do not stat every historical original on every command.
        # Source hash validation on consumption remains unchanged.
        shared = hardlink and self.db.settings.get('require_shared_evidence', False)
        cache_key = 'catalog.raw-directories:' + namespace
        cache = self.db.metadata(cache_key) if shared else None
        cached = (cache or {}).get('directories', {}) if (cache or {}).get('root') == str(root) else {}
        raw_groups = self.raw_directories(existing.values()) if shared else {}
        candidates, reused, reused_files = {}, 0, 0
        existing_databases = {row['path'] for row in self.db.iter_rows('SELECT path FROM legacy_databases WHERE namespace=%s', (namespace,))}
        # A run with a completed report decision has finished its analysis
        # flow. Pending research/delivery references are protected separately.
        pending = []
        def pending_run(run):
            if not any(run.parent.glob('report-*-receipt.json')):
                try:
                    if json.loads(run.read_bytes()).get('status') == 'COMPLETED':
                        return None
                except (ValueError, OSError):
                    pass
                return str(run.parent.relative_to(root))
            return None
        # Only known run descriptors need a read. Newly created run descriptors
        # are picked up when their parent directory is reached below.
        for relative in existing:
            if Path(relative).name == 'run.json':
                candidate = root / relative
                if candidate.is_file():
                    value = pending_run(candidate)
                    if value is not None:
                        pending.append(value)
        protect = set(protect) | set(pending)
        def is_protected(relative):
            if relative in protect:
                return True
            slash = relative.rfind('/')
            while slash >= 0:
                if relative[:slash] in protect:
                    return True
                slash = relative.rfind('/', 0, slash)
            return False
        seen, changed, databases = set(), [], []
        def reuse_raw(relative_directory, group, clock):
            # Directory entry and change clocks are the invalidation signal;
            # all document and retention decisions still come from MySQL.
            for row in group:
                relative = row['path']
                policy = 'KEY' if relative in archived else 'ACTIVE' if is_protected(relative) else (default_retention or retention(relative))
                ttl = RETENTION_DAYS[policy]
                expires = row['source_mtime_ns'] // 1000000 + ttl * DAY if ttl else None
                if row['retention_class'] != policy or row['expires_ms'] != expires:
                    self.db.execute('UPDATE documents SET retention_class=%s,expires_ms=%s WHERE document_key=%s',
                                    (policy, expires, row['document_key']))
                    self.db.execute('DELETE FROM document_recycling WHERE document_key=%s', (row['document_key'],))
                seen.add(relative)
            candidates[relative_directory] = (None, clock.st_mtime_ns, clock.st_ctime_ns)
            return len(group)
        for directory, dirs, files in os.walk(root, followlinks=False):
            relative_directory = str(Path(directory).relative_to(root))
            run = Path(directory) / 'run.json'
            if 'run.json' in files and str(run.relative_to(root)) not in existing:
                value = pending_run(run)
                if value is not None:
                    protect.add(value)
            # The immutable raw leaf can contain many historical originals.
            # Check its directory clocks before entering and enumerating every
            # name; a changed directory takes the full import/verification path.
            if shared:
                for name in list(dirs):
                    child = Path(directory) / name
                    child_relative = str(child.relative_to(root))
                    group = raw_groups.get(child_relative)
                    prior = cached.get(child_relative)
                    if (name == 'raw' and group and prior and not child.is_symlink()
                            and prior.get('source_ctime_ns') is not None):
                        clock = child.stat()
                        if (clock.st_mtime_ns == prior['source_mtime_ns']
                                and clock.st_ctime_ns == prior['source_ctime_ns']
                                and prior['manifest'] == self.raw_manifest(group)):
                            reused_files += reuse_raw(child_relative, group, clock)
                            reused += 1
                            dirs.remove(name)
            group = raw_groups.get(relative_directory)
            prior_directory = cached.get(relative_directory)
            if (shared and not dirs and group and prior_directory
                    and set(files) == {Path(row['path']).name for row in group}
                    and prior_directory['source_mtime_ns'] == Path(directory).stat().st_mtime_ns
                    and prior_directory['manifest'] == self.raw_manifest(group)):
                reused_files += reuse_raw(relative_directory, group, Path(directory).stat())
                reused += 1
                continue
            for name in list(dirs):
                if (Path(directory) / name).is_symlink():
                    files.append(name); dirs.remove(name)
            for name in files:
                path = Path(directory) / name
                relative = str(path.relative_to(root))
                if name.endswith(('-wal', '-shm', '-journal', '.lock', '.sock')) or name.startswith('.staging-'):
                    continue
                seen.add(relative)
                if relative in archived:
                    # Recheck at the actual scan point, then NEVER import or
                    # reclassify this identity through the ordinary UPSERT or
                    # SQLite path. A preflight/read race cannot change its bytes.
                    self._dependency_bytes(root, {'path': relative, 'sha256': archived[relative]})
                    row = existing[relative]
                    if row['retention_class'] != 'KEY' or row['expires_ms'] is not None:
                        self.db.execute("UPDATE documents SET retention_class='KEY',expires_ms=NULL WHERE document_key=%s",
                                        (row['document_key'],))
                    self.db.execute('DELETE FROM document_recycling WHERE document_key=%s', (row['document_key'],))
                    continue
                stat = path.lstat()
                prior = existing.get(relative)
                # A shared inode carries the CAS creation time. Retention uses
                # the original document clock, not that physical inode clock.
                immutable = (prior and prior['kind'] == 'FILE' and path.parent.name == 'raw'
                             and path.stem == prior['digest'] and stat.st_size == prior['byte_length'])
                if (prior and prior['kind'] == 'FILE' and path.name in ('collector.json', 'market.json')
                        and stat.st_size == prior['byte_length']):
                    original = self.db.blobs.path(prior['digest'])
                    immutable = immutable or (original.is_file() and original.samefile(path))
                source_mtime_ns = prior['source_mtime_ns'] if immutable else stat.st_mtime_ns
                policy = 'ACTIVE' if is_protected(relative) else (default_retention or retention(relative))
                ttl = RETENTION_DAYS[policy]
                expires = source_mtime_ns // 1000000 + ttl * DAY if ttl else None
                if not path.is_symlink() and (name == 'failure-receipt.json' or name.endswith(('-failure.json', '-error.json'))):
                    from .failures import is_receipt
                    raw = path.read_bytes()
                    try:
                        failure = is_receipt(relative, json.loads(raw))
                    except (ValueError, UnicodeError):
                        failure = False
                    if failure:
                        closed = self.db.metadata('failure:' + identity(namespace, relative))
                        if policy != 'ACTIVE' and closed and closed['digest'] == fingerprint(raw):
                            policy, expires = 'FAILURE_CLOSED', closed['closed_ms'] + 14 * DAY
                        else:
                            policy, expires = 'ACTIVE', None
                prior = existing.get(relative)
                if prior and (prior['retention_class'] != policy or prior['expires_ms'] != expires):
                    self.db.execute('UPDATE documents SET retention_class=%s,expires_ms=%s WHERE document_key=%s',
                                    (policy, expires, prior['document_key']))
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
                        self.db.execute('INSERT INTO documents(document_key,namespace,path,kind,digest,link_target,byte_length,'
                            'source_mtime_ns,recorded_ms,retention_class,expires_ms) VALUES(%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s) '
                            'ON DUPLICATE KEY UPDATE kind=VALUES(kind),digest=VALUES(digest),link_target=NULL,'
                            'byte_length=VALUES(byte_length),source_mtime_ns=VALUES(source_mtime_ns),'
                            'retention_class=VALUES(retention_class),expires_ms=VALUES(expires_ms)',
                            (identity(namespace,relative),namespace,relative,'SQLITE',record['schema_digest'],stat.st_size,
                             stat.st_mtime_ns,millis(),policy,expires))
                        if saved['changed']:
                            self.db.execute('DELETE FROM document_recycling WHERE document_key=%s',(identity(namespace,relative),))
                        continue
                    prior = existing.get(relative)
                    if prior and prior['kind'] == 'FILE' and prior['byte_length'] == stat.st_size and (prior['source_mtime_ns'] == stat.st_mtime_ns or immutable):
                        if hardlink and policy != 'CACHE':
                            self.share_original(path, prior['digest'])
                        continue
                    data = path.read_bytes()
                    after = path.stat()
                    if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                        raise Conflict('source changed during catalog import: ' + relative)
                    digest = self.db.put_blob(data)
                    link, size, kind = None, len(data), 'FILE'
                else:
                    continue
                self.db.execute('INSERT INTO documents(document_key,namespace,path,kind,digest,link_target,byte_length,'
                                'source_mtime_ns,recorded_ms,retention_class,expires_ms) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                                'ON DUPLICATE KEY UPDATE kind=VALUES(kind),digest=VALUES(digest),link_target=VALUES(link_target),'
                                'byte_length=VALUES(byte_length),source_mtime_ns=VALUES(source_mtime_ns),recorded_ms=VALUES(recorded_ms),'
                                'retention_class=VALUES(retention_class),expires_ms=VALUES(expires_ms)',
                                (identity(namespace, relative), namespace, relative, kind, digest, link, size,
                                 source_mtime_ns, millis(), policy, expires))
                changed.append(relative)
                self.db.execute('DELETE FROM document_recycling WHERE document_key=%s', (identity(namespace, relative),))
                if hardlink and kind == 'FILE' and policy != 'CACHE':
                    # Do not link arbitrary mutable binaries to immutable content.
                    self.share_original(path, digest)
            if shared and Path(directory).name == 'raw' and not dirs:
                clock = Path(directory).stat()
                candidates[relative_directory] = (set(files), clock.st_mtime_ns, clock.st_ctime_ns)
        missing = (set(existing) | existing_databases) - seen - set(archived)
        for relative in missing:
            self.remove_sqlite(identity(namespace,relative))
            self.db.execute('DELETE FROM documents WHERE document_key=%s', (identity(namespace, relative),))
            self.db.execute('DELETE FROM document_recycling WHERE document_key=%s', (identity(namespace, relative),))
        self.db.metadata('namespace:' + namespace, {'root': str(root), 'checked_ms': millis()}, write=True)
        if shared:
            final_groups = self.raw_directories(self.db.iter_rows('SELECT path,digest,kind,retention_class,byte_length '
                'FROM documents WHERE namespace=%s', (namespace,)))
            directories = {}
            for name, (filenames, clock, change_clock) in candidates.items():
                group = final_groups.get(name)
                if not group or (filenames is not None and filenames != {Path(row['path']).name for row in group}):
                    continue
                # First entry requires actual inode verification. Subsequent
                # unchanged entries reuse that result; a dirty directory is
                # always scanned and verified again.
                if (name not in cached or cached[name].get('source_mtime_ns') != clock
                        or cached[name].get('source_ctime_ns') != change_clock):
                    if not all(self.db.blobs.path(row['digest']).samefile(root / row['path']) for row in group):
                        continue
                directories[name] = {'source_mtime_ns': clock, 'source_ctime_ns': change_clock,
                                     'manifest': self.raw_manifest(group)}
            value = {'root': str(root), 'directories': directories}
            if value != cache:
                self.db.metadata(cache_key, value, write=True)
        return {'namespace': namespace, 'changed': changed, 'removed': sorted(missing), 'sqlite': databases,
                'immutable_directories_reused': reused, 'immutable_files_reused': reused_files}

    def remove_sqlite(self, database_key):
        for table in ('legacy_row_identity','legacy_row_blobs','legacy_rows','legacy_databases'):
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
                for target in ('legacy_rows','legacy_row_blobs','legacy_row_identity'):
                    self.db.execute('DELETE FROM '+target+' WHERE database_key=%s AND table_name=%s',(database_key,removed))
            for table in sorted(tables):
                saved = {r['row_key']: r['digest'] for r in self.db.rows('SELECT row_key,digest FROM legacy_rows WHERE database_key=%s AND table_name=%s', (database_key, table))}
                seen = set()
                for row_key,row,implicit,alias,index in sqlite_rows(connection,table):
                    value, binaries = {}, set()
                    for column in row.keys():
                        item = row[column]
                        if isinstance(item, bytes):
                            blob = self.db.put_blob(item); binaries.add(blob)
                            item = {'$binary_content': blob}
                        value[column] = item
                    self.db.execute('REPLACE INTO legacy_row_identity(database_key,table_name,row_key,implicit_rowid,rowid_alias,source_order) '
                                    'VALUES(%s,%s,%s,%s,%s,%s)',(database_key,table,row_key,implicit,alias,index))
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
                    for target in ('legacy_rows', 'legacy_row_blobs','legacy_row_identity'):
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
            def insert_row(table,value):
                columns=','.join('"'+key.replace('"','""')+'"' for key in value)
                items=[self.db.blobs.get(v['$binary_content']) if isinstance(v,dict) and set(v)=={'$binary_content'} else v for v in value.values()]
                connection.execute('INSERT INTO "'+table.replace('"','""')+'" ('+columns+') VALUES('+','.join('?' for _ in items)+')',items)
            for row in self.db.rows('SELECT l.table_name,l.digest,i.implicit_rowid,i.rowid_alias FROM legacy_rows l '
                    'LEFT JOIN legacy_row_identity i USING(database_key,table_name,row_key) '
                    'WHERE l.database_key=%s ORDER BY l.table_name,i.source_order,l.row_key', (record['database_key'],)):
                value = self.db.value(row['digest'])
                if row['rowid_alias'] is not None:value[row['rowid_alias']]=row['implicit_rowid']
                if row['table_name'] == 'sqlite_sequence':
                    sequence_rows.append(value)
                    continue
                insert_row(row['table_name'],value)
            if sequence_rows:
                connection.execute('DELETE FROM sqlite_sequence')
                for value in sequence_rows:insert_row('sqlite_sequence',value)
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

    def compare(self, namespace, root, *, allow_archive_only=False):
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
        archive_only = set()
        if allow_archive_only and namespace == 'strategy':
            for name, digest in self._dependency_archive().items():
                row = self.db.one('SELECT * FROM documents WHERE document_key=%s',
                                  (identity(namespace, name),))
                blob = self.db.blobs.path(digest[7:])
                if (not row or row['kind'] != 'FILE' or row['digest'] != digest[7:]
                        or row['namespace'] != namespace or row['path'] != name
                        or row['link_target'] is not None):
                    mismatches.append('archive-registration-changed:' + name)
                    continue
                if (not blob.is_file() or blob.is_symlink()
                        or row['byte_length'] != blob.stat().st_size
                        or fingerprint_file(blob) != digest[7:]):
                    mismatches.append('archive-unavailable:' + name)
                elif name not in actual_paths:
                    archive_only.add(name)
        mismatches.extend('path-set:'+p for p in sorted((expected_paths - archive_only) ^ actual_paths))
        for row in self.db.rows('SELECT * FROM documents WHERE namespace=%s', (namespace,)):
            if row['kind']=='SQLITE' or row['path'] in archive_only:continue
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
                    stored = {r['row_key']:r['digest'] for r in self.db.rows('SELECT row_key,digest FROM legacy_rows WHERE database_key=%s AND table_name=%s', (record['database_key'],table))}
                    identities={r['row_key']:r for r in self.db.rows('SELECT row_key,implicit_rowid,rowid_alias,source_order FROM legacy_row_identity WHERE database_key=%s AND table_name=%s',(record['database_key'],table))}
                    seen = set()
                    for row_key,row,implicit,alias,index in sqlite_rows(connection,table):
                        value = {k: {'$binary_content': fingerprint(v)} if isinstance(v, bytes) else v for k,v in dict(row).items()}
                        seen.add(row_key)
                        position=identities.get(row_key)
                        if (stored.get(row_key) != fingerprint(encode(value)) or not position or
                                (position['implicit_rowid'],position['rowid_alias'],position['source_order'])!=(implicit,alias,index)):
                            mismatches.append(record['path'] + ':' + table + ':' + row_key)
                    if seen != set(stored):
                        mismatches.append(record['path'] + ':' + table + ':row-set')
                checked_databases.append(record['path'])
            finally:
                connection.close()
        return {'equal': not mismatches, 'mismatches': mismatches, 'checked_databases': checked_databases,
                'comparison_scope': 'LIVE_VIEW_AND_VERIFIED_ARCHIVES' if allow_archive_only else 'COMPLETE_NAMESPACE',
                'archive_only': sorted(archive_only)}
