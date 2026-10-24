"""Retention is independent of analytical validity and freshness."""
from __future__ import annotations

from bisect import bisect_left
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid
from stat import S_ISLNK
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from .codec import Conflict, encode, fingerprint, identity, millis


DAY = 86400000
MAX_INODE_KEYS = 65536
RETENTION_DAYS = {'CACHE': 1, 'RUN': 7, 'FAILURE_CLOSED': 14, 'RAW': 30,
                  'HOURLY': 400, 'KEY': None, 'ACTIVE': None, 'DAILY': None}


def status(db):
    content = db.one('SELECT COUNT(*) AS objects,COALESCE(SUM(byte_length),0) AS bytes FROM blobs')
    views = db.one('SELECT COUNT(*) AS paths,COALESCE(SUM(byte_length),0) AS bytes FROM documents')
    observation = db.one('SELECT COUNT(*) AS rows_count FROM observations')
    daily = db.metadata('capacity.daily') or []
    return {'unique_content': {k: int(v) for k, v in content.items()},
            'logical_documents': {k: int(v) for k, v in views.items()},
            'observations': observation['rows_count'],
            'maintenance': db.metadata('maintenance'), 'daily': daily,
            'live_filesystem_headroom': {'at_ms': millis(), 'volumes': filesystem_headroom(
                [db.blobs.root, *db.settings.get('capacity_paths', [])])},
            'last_physical_measurement': next((r for r in reversed(daily)
                                               if 'allocated_file_bytes' in r), None),
            'host_capacity': db.metadata('capacity.host'), 'host_daily': db.metadata('capacity.host.daily')}


def filesystem_headroom(paths):
    """Read live capacity pressure per filesystem without enumerating its files.

    This includes writes by processes outside the catalog. It is a filesystem
    health signal, not an attribution of shared-volume usage to this node.
    """
    volumes = {}
    for raw in paths:
        path = Path(raw).resolve(strict=True)
        device = str(path.stat().st_dev)
        if device not in volumes:
            blocks = os.statvfs(path)
            volumes[device] = {'device': device, 'roots': [],
                'total_bytes': blocks.f_blocks * blocks.f_frsize,
                'available_bytes': blocks.f_bavail * blocks.f_frsize,
                'free_bytes': blocks.f_bfree * blocks.f_frsize}
        volumes[device]['roots'].append(str(path))
    return sorted(volumes.values(), key=lambda item: item['device'])


def protected_documents(db):
    """Follow actual archived references, including semantic evidence IDs.

    A raw blob's existence is not an active judgement. Conversely a reference
    from a retained decision, research or delivery keeps its recovery paths.
    """
    # The graph needs the rows for indexed lookups, but need not first hold a
    # second, buffered copy of the complete MySQL result.
    by_key = {r['document_key']: r for r in db.iter_rows('SELECT document_key,namespace,path,kind,digest,link_target,byte_length,retention_class FROM documents')}
    by_digest, by_path, by_bundle, namespace_paths = {}, {}, {}, {}
    roots = {}
    for r in by_key.values():
        namespace = r['namespace']
        if namespace not in roots:
            roots[namespace] = (db.metadata('namespace:' + namespace) or {}).get('root')
        if r['digest']:
            by_digest.setdefault(r['digest'], []).append(r['document_key'])
        by_path[(namespace, r['path'])] = r['document_key']
        namespace_paths.setdefault(namespace, []).append(r['path'])
        match = re.search(r'(?:^|/)evidence/([a-f0-9]{64})(?:/|$)', r['path'])
        if match:
            by_bundle.setdefault(match[1], []).append(r['document_key'])
    # A report may repeat one digest many times, and that digest may have many
    # catalog paths. Queue each document once so graph expansion stays bounded
    # by catalog size instead of reference_count * path_count.
    pending, queued = [], set()
    def enqueue(keys):
        for key in keys:
            if key not in queued:
                queued.add(key)
                pending.append(key)
    enqueue(r['document_key'] for r in by_key.values() if r['retention_class'] in {'ACTIVE', 'KEY', 'DAILY'})
    for paths in namespace_paths.values():
        paths.sort()
    for row in db.rows("SELECT DISTINCT target_key FROM dependencies WHERE target_type='BLOB'"):
        enqueue(by_digest.get(row['target_key'], ()))
    def protect_value(value):
        if isinstance(value, dict):
            for item in value.values(): protect_value(item)
        elif isinstance(value, list):
            for item in value: protect_value(item)
        elif isinstance(value, str):
            enqueue(by_digest.get(value.removeprefix('sha256:'), ()))
            for namespace, root in roots.items():
                if root and value.startswith(root.rstrip('/')+'/'):
                    found=by_path.get((namespace,value[len(root.rstrip('/'))+1:]))
                    if found:enqueue((found,))
    for row in db.rows("SELECT request_digest FROM operations WHERE status<>'COMMITTED'"):
        protect_value(db.value(row['request_digest']))
    protected, decoded = set(), set()
    while pending:
        key = pending.pop()
        if key in protected:
            continue
        protected.add(key)
        row = by_key[key]
        if row['kind'] == 'LINK':
            target = Path(row['link_target'])
            root = roots[row['namespace']]
            if not target.is_absolute():
                target = Path(root or '/') / Path(row['path']).parent / target
            if root:
                relative = os.path.relpath(os.path.normpath(str(target)), root)
                key = by_path.get((row['namespace'], relative))
                if key:
                    enqueue((key,))
                prefix = relative.rstrip('/') + '/'
                paths = namespace_paths[row['namespace']]
                index = bisect_left(paths, prefix)
                while index < len(paths) and paths[index].startswith(prefix):
                    enqueue((by_path[(row['namespace'], paths[index])],))
                    index += 1
            continue
        if not row['digest'] or not row['path'].endswith('.json') or row['byte_length'] > 32*1024*1024:
            continue
        # Relative references in identical JSON are resolved per namespace.
        decode_context = (row['digest'], row['namespace'])
        if decode_context in decoded:
            continue
        decoded.add(decode_context)
        try:
            value = db.value(row['digest'])
        except (ValueError, UnicodeError):
            continue
        def walk(item):
            if isinstance(item, str): yield item
            elif isinstance(item, dict):
                for v in item.values(): yield from walk(v)
            elif isinstance(item, list):
                for v in item: yield from walk(v)
        for item in walk(value):
            if re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}', item):
                digest = item.removeprefix('sha256:')
                enqueue(by_digest.get(digest, ()))
                enqueue(by_bundle.get(digest, ()))
            for namespace, root in roots.items():
                if root and item.startswith(root.rstrip('/') + '/'):
                    relative = item[len(root.rstrip('/'))+1:]
                    referenced = by_path.get((namespace, relative))
                    if referenced: enqueue((referenced,))
            referenced = by_path.get((row['namespace'], item))
            if referenced: enqueue((referenced,))
    return protected


def allocated(paths):
    seen, total = set(), 0
    spill, connection, cursor = None, None, None
    roots = []
    for path in paths:
        root = Path(path).resolve()
        if not root.exists():
            raise FileNotFoundError(root)
        if any(root == parent or parent in root.parents for parent in roots):
            continue
        roots = [parent for parent in roots if root not in parent.parents]
        roots.append(root)
    def unreadable(error):
        raise error  # An incomplete traversal is not a zero-size directory.
    try:
        for root in roots:
            for directory, _, files in os.walk(root, followlinks=False, onerror=unreadable):
                for name in files:
                    try:
                        metadata = (Path(directory) / name).lstat()
                    except FileNotFoundError:
                        continue
                    if S_ISLNK(metadata.st_mode):
                        continue
                    key = (metadata.st_dev, metadata.st_ino)
                    if connection is None:
                        if key in seen:
                            continue
                        seen.add(key)
                        total += metadata.st_blocks * 512
                        if len(seen) == MAX_INODE_KEYS:
                            # The disposable disk index keeps exact hard-link
                            # accounting without retaining every inode in RAM.
                            spill = TemporaryDirectory(prefix='btc-capacity-')
                            connection = sqlite3.connect(Path(spill.name) / 'inodes.sqlite3')
                            connection.execute('PRAGMA journal_mode=OFF')
                            connection.execute('PRAGMA synchronous=OFF')
                            connection.execute('PRAGMA cache_size=-4096')
                            connection.execute('CREATE TABLE inodes (identity TEXT PRIMARY KEY) WITHOUT ROWID')
                            cursor = connection.cursor()
                            cursor.executemany('INSERT INTO inodes VALUES (?)',
                                ((str(dev) + ':' + str(ino),) for dev, ino in seen))
                            seen.clear()
                    elif cursor.execute('INSERT OR IGNORE INTO inodes VALUES (?)',
                                        (str(key[0]) + ':' + str(key[1]),)).rowcount:
                        total += metadata.st_blocks * 512
    finally:
        if connection is not None:
            connection.close()
        if spill is not None:
            spill.cleanup()
    return total


def maintain(db, *, apply=False, now=None, measure_files=True):
    with db.writer():
        result = _maintain(db, apply=apply, now=now)
    # The daily lifecycle needs a logical growth record, not a fresh physical
    # audit of every hard link in every retained backup. An explicit maintenance
    # call still provides the exact inode-based physical measurement.
    if measure_files:
        measure_capacity(db, now=now)
        result['content_allocated_bytes'] = sum(p.stat().st_blocks * 512 for p in db.blobs.root.glob('*/*')
            if p.is_file() and re.fullmatch('[a-f0-9]{64}', p.parent.name + p.name))
    else:
        record_capacity(db, now=now)
        result['content_allocated_bytes'] = None
    return result


def _maintain(db, *, apply, now):
    now_ms = millis(now)
    grace = int(db.settings.get('recycle_grace_hours', 48)) * 3600000
    with db.transaction():
        if apply:
            from .backup import reap_pins
            reap_pins(db)
            from .series import Series
            Series(db).backfill_originals()
            # A cited unchanged re-fetch is a source bridge even without its
            # own observation row. Retain its small provenance mapping with
            # the analytical dependency, rather than losing it after 30 days.
            db.execute('DELETE r FROM series_retrievals r WHERE r.known_ms<%s '
                'AND NOT EXISTS(SELECT 1 FROM dependencies d '
                "WHERE d.target_type='BLOB' AND (d.target_key=r.source_digest OR "
                'EXISTS(SELECT 1 FROM series_originals o WHERE o.source_digest=r.source_digest '
                'AND o.original_digest=d.target_key)))', (now_ms-30*DAY,))
        # A measurement referenced by an analytical object remains queryable,
        # including its revisions. Age alone cannot retire that object.
        candidates = db.rows('SELECT observation_key,product,series,item_key FROM observations '
                             'WHERE (interval_ms<3600000 AND recorded_ms<%s) OR '
                             '(interval_ms>=3600000 AND interval_ms<86400000 AND observed_ms<%s)',
                             (now_ms - 30 * DAY, now_ms - 400 * DAY))
        removed_observations = 0
        for row in candidates:
            target = identity(row['product'], row['series'], row['item_key'])
            if not db.one("SELECT 1 FROM dependencies WHERE target_type='OBSERVATION' AND target_key=%s LIMIT 1", (target,)):
                removed_observations += 1
                if apply:
                    db.execute('DELETE FROM observations WHERE observation_key=%s', (row['observation_key'],))
        if apply:
            from .series import Series
            Series(db).backfill_originals()
            db.execute('DELETE FROM series_originals WHERE '
                       'NOT EXISTS(SELECT 1 FROM observations o WHERE o.source_digest=series_originals.source_digest) '
                       'AND NOT EXISTS(SELECT 1 FROM series_retrievals r WHERE r.source_digest=series_originals.source_digest)')
            for repair in db.rows('SELECT name FROM node_metadata WHERE name LIKE %s', ('seller.ref-repair:%',)):
                digest = repair['name'].split(':', 1)[1]
                if not db.one('SELECT 1 FROM observations WHERE source_digest=%s LIMIT 1', (digest,)):
                    db.execute('DELETE FROM metadata_blobs WHERE name=%s', (repair['name'],))
                    db.execute('DELETE FROM node_metadata WHERE name=%s', (repair['name'],))
        protected = protected_documents(db)
        expired = db.rows('SELECT * FROM documents d WHERE expires_ms<=%s '
                          "AND retention_class NOT IN ('ACTIVE','KEY','DAILY') "
                          'AND NOT EXISTS(SELECT 1 FROM dependencies r WHERE r.target_key=d.digest '
                          "AND r.target_type='BLOB')", (now_ms,))
        expired = [r for r in expired if r['document_key'] not in protected]
        removed_documents = 0
        if apply:
            from .catalog import Catalog, safe_path, sqlite_signature
            for key in protected:
                db.execute('DELETE FROM document_recycling WHERE document_key=%s', (key,))
            for row in expired:
                candidate = db.one('SELECT * FROM document_recycling WHERE document_key=%s', (row['document_key'],))
                if not candidate or candidate['digest'] != row['digest']:
                    db.execute('REPLACE INTO document_recycling(document_key,digest,retired_ms) VALUES(%s,%s,%s)',
                               (row['document_key'], row['digest'], now_ms))
                    continue
                if candidate['retired_ms'] > now_ms - grace:
                    continue
                namespace = db.metadata('namespace:' + row['namespace'])
                if not namespace:
                    continue
                path = safe_path(namespace['root'], row['path'])
                if path.exists() or path.is_symlink():
                    if row['kind']=='SQLITE':
                        database=db.one('SELECT source_signature FROM legacy_databases WHERE database_key=%s',(row['document_key'],))
                        matches=bool(database and path.is_file() and not path.is_symlink() and sqlite_signature(path)==database['source_signature'])
                    else:
                        matches = (path.is_symlink() and os.readlink(path) == row['link_target']) if row['kind'] == 'LINK' else (
                            path.is_file() and not path.is_symlink() and fingerprint(path.read_bytes()) == row['digest'])
                    if not matches:
                        # A newer writer wins; the next catalog pass imports it.
                        db.execute('DELETE FROM document_recycling WHERE document_key=%s', (row['document_key'],))
                        continue
                    path.unlink()
                    if row['kind']=='SQLITE':
                        for suffix in ('-wal','-shm'):Path(str(path)+suffix).unlink(missing_ok=True)
                if row['kind']=='SQLITE':Catalog(db).remove_sqlite(row['document_key'])
                db.execute('DELETE FROM document_recycling WHERE document_key=%s', (row['document_key'],))
                db.execute('DELETE FROM documents WHERE document_key=%s', (row['document_key'],))
                removed_documents += 1
        # Filesystem erasure happens after references are committed away. The
        # next sweep removes content only after a full recycling grace window.
        unused = db.rows('SELECT b.digest,b.byte_length,b.unreferenced_ms FROM blobs b WHERE '
                         'NOT EXISTS(SELECT 1 FROM documents d WHERE d.digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM entity_versions e WHERE e.digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM observations o WHERE o.digest=b.digest OR o.source_digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM instruments i WHERE i.identity_digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM series_retrievals r WHERE r.source_digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM series_originals r WHERE r.source_digest=b.digest OR r.original_digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM operations o WHERE o.result_digest=b.digest OR o.request_digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM legacy_databases l WHERE l.schema_digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM legacy_rows l WHERE l.digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM legacy_row_blobs l WHERE l.digest=b.digest) AND '
                         "NOT EXISTS(SELECT 1 FROM mo_evidence m WHERE m.payload=CONCAT('@blob:',b.digest)) AND "
                         "NOT EXISTS(SELECT 1 FROM mo_retrieval m WHERE m.provenance=CONCAT('@blob:',b.digest)) AND "
                         "NOT EXISTS(SELECT 1 FROM mo_review m WHERE m.payload=CONCAT('@blob:',b.digest) OR m.context_snapshot=CONCAT('@blob:',b.digest)) AND "
                         "NOT EXISTS(SELECT 1 FROM mo_completion_attempt m WHERE m.accepted_market_assessment=CONCAT('@blob:',b.digest)) AND "
                         'NOT EXISTS(SELECT 1 FROM node_metadata m WHERE m.digest=b.digest) AND '
                         'NOT EXISTS(SELECT 1 FROM metadata_blobs m WHERE m.digest=b.digest) AND '
                         "NOT EXISTS(SELECT 1 FROM dependencies d WHERE d.target_type='BLOB' AND d.target_key=b.digest)")
        garbage = []
        for row in unused:
            if row['unreferenced_ms'] is not None and row['unreferenced_ms'] <= now_ms - grace:
                garbage.append(row)
            elif apply and row['unreferenced_ms'] is None:
                db.execute('UPDATE blobs SET unreferenced_ms=%s WHERE digest=%s', (now_ms, row['digest']))
        result = {'at_ms': now_ms, 'applied': apply, 'expired_documents': len(expired),
                  'removed_documents': removed_documents,
                  'expired_observations': removed_observations,
                  'recyclable_objects': len(garbage),
                  'recyclable_bytes': sum(r['byte_length'] for r in garbage)}
        if apply:
            for row in garbage:
                db.execute('DELETE FROM blobs WHERE digest=%s', (row['digest'],))
            db.metadata('maintenance', result, write=True)
    if apply:
        for row in garbage:
            db.blobs.path(row['digest']).unlink(missing_ok=True)
        # An interrupted transaction may have left a CAS file without a row.
        # The same writer lock prevents racing admission of a new reference.
        registered = {r['digest'] for r in db.rows('SELECT digest FROM blobs')}
        for path in db.blobs.root.glob('*/*'):
            digest = path.parent.name + path.name
            if re.fullmatch('[a-f0-9]{64}', digest) and digest not in registered and path.stat().st_mtime_ns // 1000000 <= now_ms - grace:
                path.unlink()
    return result


def record_capacity(db, *, now=None, measured=None):
    """Record daily logical growth and live filesystem pressure without a walk."""
    headroom = filesystem_headroom([db.blobs.root, *db.settings.get('capacity_paths', [])])
    with db.writer(), db.transaction():
        now_ms = millis(now)
        daily = db.metadata('capacity.daily') or []
        day = datetime.fromtimestamp(now_ms / 1000, ZoneInfo('Asia/Tokyo')).date().isoformat()
        total = int(db.one('SELECT COALESCE(SUM(byte_length),0) AS bytes FROM blobs')['bytes'])
        db_bytes = db.one('SELECT COALESCE(SUM(data_length+index_length),0) AS bytes FROM information_schema.tables WHERE table_schema=DATABASE()')['bytes']
        record = {'day': day, 'at_ms': now_ms, 'unique_content_bytes': total,
                  'mysql_table_allocated_bytes': int(db_bytes),
                  'filesystem_headroom_at_ms': millis(), 'filesystem_headroom': headroom}
        same_day = next((r for r in reversed(daily) if r['day'] == day), None)
        if measured is not None:
            record.update(measured)
            record['allocated_file_bytes_status'] = 'MEASURED'
        elif same_day and 'allocated_file_bytes' in same_day:
            for field in ('measurement_started_ms', 'physical_measured_at_ms', 'allocated_file_bytes',
                          'allocated_file_growth_bytes', 'allocated_file_growth_since_day'):
                if field in same_day:
                    record[field] = same_day[field]
            record['allocated_file_bytes_status'] = 'MEASURED_EARLIER_TODAY'
        else:
            record['allocated_file_bytes_status'] = 'NOT_MEASURED'
        history = [r for r in daily if r['day'] != day]
        if history:
            record['net_growth_bytes'] = total - history[-1]['unique_content_bytes']
            previous = {r['device']: r for r in history[-1].get('filesystem_headroom', [])}
            for volume in headroom:
                prior_volume = previous.get(volume['device'])
                if prior_volume:
                    volume['available_change_bytes'] = volume['available_bytes'] - prior_volume['available_bytes']
                    volume['available_change_since_day'] = history[-1]['day']
        if measured is not None:
            prior = next((r for r in reversed(history) if 'allocated_file_bytes' in r), None)
            if prior:
                record['allocated_file_growth_bytes'] = measured['allocated_file_bytes'] - prior['allocated_file_bytes']
                record['allocated_file_growth_since_day'] = prior['day']
        db.metadata('capacity.daily', (history + [record])[-401:], write=True)
    return record


def measure_capacity(db, *, now=None):
    started = millis()
    real_bytes = allocated([db.blobs.root, *db.settings.get('capacity_paths', [])])
    # The interval matters because live analysis can write during an audit.
    # No unreadable path produces a successful zero sample.
    return record_capacity(db, now=now, measured={'measurement_started_ms': started,
        'physical_measured_at_ms': millis(), 'allocated_file_bytes': real_bytes})


def prune_backups(root, *, now_ms, keep_days):
    backups=[]
    for path in Path(root).iterdir():
        manifest=path/'manifest.json'
        if path.is_symlink() or not path.is_dir() or not manifest.is_file():continue
        value=json.loads(manifest.read_bytes())
        if value.get('format')!='BTC_MYSQL_BACKUP_V1':continue
        created=int(value.get('created_ms',manifest.stat().st_mtime_ns//1000000))
        backups.append((created,path))
    removed=[]
    for created,path in sorted(backups)[:-2]:
        if created < now_ms-keep_days*DAY:
            shutil.rmtree(path);removed.append(path.name)
    return removed


def daily(db):
    """Create a recoverable snapshot before retention; backup age is separate."""
    from .backup import create
    from .deep_gate import check_scope
    if not check_scope(db, scope='btc/maintenance')['allowed']:
        raise Conflict('known maintenance defect blocks daily work')
    day = datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat()
    if (db.metadata('maintenance.daily') or {}).get('day') == day:
        return {'day':day,'already_maintained':True}
    from .catalog import Catalog
    with db.writer(), db.transaction():
        for name, root in db.settings.get('auxiliary_namespaces',{}).items():
            if Path(root).is_dir():
                Catalog(db).sync(name,root,default_retention='KEY' if name=='facts' else 'RUN')
    if db.settings.get('maintenance_enabled'):
        directory = db.settings.get('backup_directory')
        if not directory:
            raise ValueError('enabled lifecycle requires a node-owned backup directory')
        root = Path(directory)
        destination = root / day
        if not destination.exists():
            create(db, destination)
        if not (destination / 'manifest.json').is_file():
            raise ValueError('daily backup is incomplete; retention deferred')
        keep_days = int(db.settings.get('backup_retention_days', 7))
        # Named migration/recovery backups obey the same lifetime as daily
        # backups; a non-date directory name must not retain raw data forever.
        prune_backups(root,now_ms=millis(),keep_days=keep_days)
    result=maintain(db, apply=db.settings.get('maintenance_enabled', False), measure_files=False)
    if db.settings.get('maintenance_enabled'):
        with db.writer(),db.transaction(): db.metadata('maintenance.daily',{'day':day,'result':result},write=True)
    return result


def loop_daily(db):
    """Durable admission for the automatically restarted maintenance process.

    The process lock distinguishes a live peer from a crashed predecessor;
    the attempt record makes a crash before the daily marker fail closed after
    Docker restarts the process. Both live in the existing MySQL authority.
    """
    from .deep_gate import check_scope, open_fault
    if not check_scope(db, scope='btc/maintenance')['allowed']:
        raise Conflict('known maintenance defect blocks daily work')
    day = datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat()
    if (db.metadata('maintenance.daily') or {}).get('day') == day:
        return {'day':day,'already_maintained':True}
    db.connection.rollback()
    lock = 'btc-maintenance:' + db.settings['mysql']['database']
    if db.one('SELECT GET_LOCK(%s,0) AS acquired', (lock,))['acquired'] != 1:
        raise Conflict('another maintenance process is active')
    try:
        interrupted = False
        with db.writer(), db.transaction():
            # Another process may have completed the day while this process
            # waited to enter the writer transaction.
            if (db.metadata('maintenance.daily') or {}).get('day') == day:
                return {'day':day,'already_maintained':True}
            prior = db.metadata('maintenance.attempt') or {}
            if prior.get('state') == 'RUNNING':
                prior['state'] = 'FAILED'
                prior['failed_ms'] = millis()
                db.metadata('maintenance.attempt', prior, write=True)
                signature = 'sha256:' + fingerprint(encode(prior))
                open_fault(db, scope='btc/maintenance', kind='UNKNOWN',
                           signature=signature, fault_digest=signature,
                           evidence_ref='node:maintenance.attempt:' + prior['attempt_id'])
                interrupted = True
            else:
                db.metadata('maintenance.attempt', {
                    'attempt_id':uuid.uuid4().hex,'day':day,'state':'RUNNING',
                    'started_ms':millis(),'finished_ms':None}, write=True)
        if interrupted:
            raise Conflict('interrupted maintenance attempt requires fault review')
        result = daily(db)
        with db.writer(), db.transaction():
            attempt = db.metadata('maintenance.attempt')
            attempt['state'] = 'COMPLETE'
            attempt['finished_ms'] = millis()
            db.metadata('maintenance.attempt', attempt, write=True)
        return result
    finally:
        try:
            db.one('SELECT RELEASE_LOCK(%s) AS released', (lock,))
        except Exception:
            # Connection loss releases the named lock server-side. The
            # persistent RUNNING record still blocks an automatic retry.
            pass
