"""Retention is independent of analytical validity and freshness."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
from zoneinfo import ZoneInfo

from .codec import fingerprint, identity, millis


DAY = 86400000
RETENTION_DAYS = {'CACHE': 1, 'RUN': 7, 'FAILURE_CLOSED': 14, 'RAW': 30,
                  'HOURLY': 400, 'KEY': None, 'ACTIVE': None, 'DAILY': None}


def status(db):
    content = db.one('SELECT COUNT(*) AS objects,COALESCE(SUM(byte_length),0) AS bytes FROM blobs')
    views = db.one('SELECT COUNT(*) AS paths,COALESCE(SUM(byte_length),0) AS bytes FROM documents')
    observation = db.one('SELECT COUNT(*) AS rows_count FROM observations')
    return {'unique_content': {k: int(v) for k, v in content.items()},
            'logical_documents': {k: int(v) for k, v in views.items()},
            'observations': observation['rows_count'],
            'maintenance': db.metadata('maintenance'), 'daily': db.metadata('capacity.daily'),
            'host_capacity': db.metadata('capacity.host'), 'host_daily': db.metadata('capacity.host.daily')}


def protected_documents(db):
    """Follow actual archived references, including semantic evidence IDs.

    A raw blob's existence is not an active judgement. Conversely a reference
    from a retained decision, research or delivery keeps its recovery paths.
    """
    records = db.rows('SELECT * FROM documents')
    by_key = {r['document_key']: r for r in records}
    by_digest, by_path, by_bundle = {}, {}, {}
    roots = {}
    for r in records:
        namespace = r['namespace']
        if namespace not in roots:
            roots[namespace] = (db.metadata('namespace:' + namespace) or {}).get('root')
        if r['digest']:
            by_digest.setdefault(r['digest'], []).append(r['document_key'])
        by_path[(namespace, r['path'])] = r['document_key']
        match = re.search(r'(?:^|/)evidence/([a-f0-9]{64})(?:/|$)', r['path'])
        if match:
            by_bundle.setdefault(match[1], []).append(r['document_key'])
    pending = [r['document_key'] for r in records if r['retention_class'] in {'ACTIVE', 'KEY', 'DAILY'}]
    for row in db.rows("SELECT DISTINCT target_key FROM dependencies WHERE target_type='BLOB'"):
        pending.extend(by_digest.get(row['target_key'], ()))
    def protect_value(value):
        if isinstance(value, dict):
            for item in value.values(): protect_value(item)
        elif isinstance(value, list):
            for item in value: protect_value(item)
        elif isinstance(value, str):
            pending.extend(by_digest.get(value.removeprefix('sha256:'), ()))
            for namespace, root in roots.items():
                if root and value.startswith(root.rstrip('/')+'/'):
                    found=by_path.get((namespace,value[len(root.rstrip('/'))+1:]))
                    if found:pending.append(found)
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
                for candidate in records:
                    if candidate['namespace'] == row['namespace'] and (candidate['path'] == relative or candidate['path'].startswith(relative.rstrip('/') + '/')):
                        pending.append(candidate['document_key'])
            continue
        if not row['digest'] or row['digest'] in decoded or not row['path'].endswith('.json') or row['byte_length'] > 32*1024*1024:
            continue
        decoded.add(row['digest'])
        try:
            value = db.value(row['digest'])
        except (ValueError, UnicodeError):
            continue
        strings = []
        def walk(item):
            if isinstance(item, str): strings.append(item)
            elif isinstance(item, dict):
                for v in item.values(): walk(v)
            elif isinstance(item, list):
                for v in item: walk(v)
        walk(value)
        for item in strings:
            if re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}', item):
                digest = item.removeprefix('sha256:')
                pending.extend(by_digest.get(digest, ()))
                pending.extend(by_bundle.get(digest, ()))
            for namespace, root in roots.items():
                if root and item.startswith(root.rstrip('/') + '/'):
                    relative = item[len(root.rstrip('/'))+1:]
                    referenced = by_path.get((namespace, relative))
                    if referenced: pending.append(referenced)
            referenced = by_path.get((row['namespace'], item))
            if referenced: pending.append(referenced)
    return protected


def allocated(paths):
    seen, total = set(), 0
    for root in paths:
        root = Path(root)
        if not root.exists(): continue
        for directory, _, files in os.walk(root, followlinks=False):
            for name in files:
                try:
                    item = Path(directory) / name
                    if item.is_symlink(): continue
                    stat = item.stat()
                except FileNotFoundError:
                    continue
                key = (stat.st_dev, stat.st_ino)
                if key not in seen:
                    seen.add(key); total += stat.st_blocks * 512
    return total


def maintain(db, *, apply=False, now=None):
    with db.writer():
        return _maintain(db, apply=apply, now=now)


def _maintain(db, *, apply, now):
    now_ms = millis(now)
    grace = int(db.settings.get('recycle_grace_hours', 48)) * 3600000
    with db.transaction():
        if apply:
            db.execute('DELETE FROM series_retrievals WHERE known_ms<%s', (now_ms-30*DAY,))
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
        daily = db.metadata('capacity.daily') or []
        day = datetime.fromtimestamp(now_ms / 1000, ZoneInfo('Asia/Tokyo')).date().isoformat()
        total = int(db.one('SELECT COALESCE(SUM(byte_length),0) AS bytes FROM blobs')['bytes'])
        paths = [db.blobs.root, *db.settings.get('capacity_paths', [])]
        real_bytes = allocated(paths)
        db_bytes = db.one('SELECT COALESCE(SUM(data_length+index_length),0) AS bytes FROM information_schema.tables WHERE table_schema=DATABASE()')['bytes']
        record = {'day': day, 'at_ms': now_ms, 'unique_content_bytes': total,
                  'allocated_file_bytes': real_bytes, 'mysql_table_allocated_bytes': int(db_bytes)}
        history = [r for r in daily if r['day'] != day]
        if history:
            record['net_growth_bytes'] = total - history[-1]['unique_content_bytes']
            if 'allocated_file_bytes' in history[-1]:
                record['allocated_file_growth_bytes'] = real_bytes - history[-1]['allocated_file_bytes']
        db.metadata('capacity.daily', (history + [record])[-401:], write=True)
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
    # These are actual allocated bytes, not a promise that deleting InnoDB
    # rows has shrunk the volume. Host capacity collection adds Docker stats.
    result['content_allocated_bytes'] = sum(p.stat().st_blocks * 512 for p in db.blobs.root.glob('*/*') if p.is_file())
    return result


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
    result=maintain(db, apply=db.settings.get('maintenance_enabled', False))
    if db.settings.get('maintenance_enabled'):
        with db.writer(),db.transaction(): db.metadata('maintenance.daily',{'day':day,'result':result},write=True)
    return result
