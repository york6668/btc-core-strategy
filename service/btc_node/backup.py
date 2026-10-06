"""Consistent MySQL snapshots and independent, hash-verified content backups."""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .codec import Blobs, Conflict, encode, fingerprint, millis, sync_directory
from .database import Database


PIN_PREFIX = 'btc-backup:'


def reap_pins(db):
    """Release interrupted backup references inside the caller's writer transaction.

    The per-backup MySQL lock lives for the copying connection, independently
    of the short node writer lock. A disconnected process cannot retain it.
    """
    removed = []
    for row in db.rows('SELECT DISTINCT name FROM metadata_blobs WHERE name LIKE %s', (PIN_PREFIX+'%',)):
        name = row['name']
        if db.one('SELECT IS_USED_LOCK(%s) AS owner', (name,))['owner'] is None:
            db.execute('DELETE FROM metadata_blobs WHERE name=%s', (name,))
            removed.append(name)
    return removed


def identifier(value):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', value):
        raise ValueError('invalid SQL identifier in backup')
    return '`' + value + '`'


def create(db, destination):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.backup-', dir=destination.parent))
    snapshot = None
    pin = PIN_PREFIX + uuid.uuid4().hex
    acquired = False
    cleanup_deferred = False
    try:
        snapshot = Database(db.settings)
        acquired = db.one('SELECT GET_LOCK(%s,0) AS acquired', (pin,))['acquired'] == 1
        if not acquired:
            raise Conflict('backup identity is already active')
        with db.writer(), db.transaction():
            reap_pins(db)
            # Establish a read view on a separate connection before releasing
            # admission. Pin its originals atomically against concurrent GC.
            snapshot.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            snapshot.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
            snapshot_ms = millis()
            db.execute('INSERT INTO metadata_blobs(name,digest) SELECT %s,digest FROM blobs', (pin,))
        (stage/'in-progress.json').write_bytes(encode({'format':'BTC_BACKUP_PROGRESS_V1',
            'pin':pin, 'destination':str(destination), 'snapshot_ms':snapshot_ms}))
        tables = [next(iter(r.values())) for r in snapshot.rows('SHOW TABLES')]
        backup_blobs = Blobs(stage/'objects')
        counts = {}
        with gzip.open(stage/'database.jsonl.gz', 'wb') as stream:
            stream.write(encode({'format':'BTC_MYSQL_BACKUP_V1','created_ms':snapshot_ms}))
            for table in tables:
                quoted=identifier(table)
                schema=snapshot.one('SHOW CREATE TABLE '+quoted)['Create Table']
                stream.write(encode({'table':table,'create':schema}))
                # Temporary copying leases must never become permanent
                # references when restoring this consistent snapshot.
                rows = (snapshot.rows('SELECT * FROM '+quoted+' WHERE name NOT LIKE %s', (PIN_PREFIX+'%',))
                        if table == 'metadata_blobs' else snapshot.rows('SELECT * FROM '+quoted))
                counts[table]=len(rows)
                for row in rows:
                    stream.write(encode({'table':table,'row':row}))
        for row in snapshot.rows('SELECT digest FROM blobs'):
            digest=row['digest']
            target=backup_blobs.path(digest)
            target.parent.mkdir(parents=True,exist_ok=True)
            # Original bytes are immutable and pinned even if new writes or
            # lifecycle sweeps run while these files are copied/verified.
            db.blobs.materialize(digest,target)
        manifest={'format':'BTC_MYSQL_BACKUP_V1','created_ms':snapshot_ms,'completed_ms':millis(),
                  'tables':counts,'database_sha256':fingerprint((stage/'database.jsonl.gz').read_bytes())}
        (stage/'manifest.json').write_bytes(encode(manifest))
        (stage/'in-progress.json').unlink()
        for path in (stage/'manifest.json',stage/'database.jsonl.gz'):
            with path.open('rb') as handle:os.fsync(handle.fileno())
        sync_directory(stage)
        os.rename(stage,destination);sync_directory(destination.parent)
    finally:
        if snapshot is not None:
            snapshot.close()
        if acquired:
            try:
                with db.writer(), db.transaction():
                    db.execute('DELETE FROM metadata_blobs WHERE name=%s', (pin,))
            except Conflict:
                # A completed backup remains valid if admission is busy. The
                # next sweep reaps this now-ownerless temporary reference.
                cleanup_deferred = True
            finally:
                db.one('SELECT RELEASE_LOCK(%s) AS released', (pin,))
        if stage.exists():shutil.rmtree(stage)
    return {'backup':str(destination), 'pin_cleanup_deferred':cleanup_deferred, **manifest}


def restore(db, source):
    source=Path(source)
    manifest=json.loads((source/'manifest.json').read_bytes())
    if manifest.get('format')!='BTC_MYSQL_BACKUP_V1' or fingerprint((source/'database.jsonl.gz').read_bytes())!=manifest['database_sha256']:
        raise Conflict('backup is damaged')
    with db.writer():
        existing=[next(iter(r.values())) for r in db.rows('SHOW TABLES')]
        for table in existing:
            if table!='node_schema' and db.one('SELECT 1 FROM '+identifier(table)+' LIMIT 1'):
                raise Conflict('restore only into an empty database; never replace newer business or delivery state')
        # Restore every original before committing any foreign-key reference.
        archived=Blobs(source/'objects')
        for path in archived.root.glob('*/*'):
            digest=path.parent.name+path.name
            if db.blobs.put(archived.get(digest))!=digest:
                raise Conflict('restored evidence hash changed')
        db.execute('SET FOREIGN_KEY_CHECKS=0')
        try:
            for table in existing:db.execute('DROP TABLE '+identifier(table))
            counts={}
            with gzip.open(source/'database.jsonl.gz','rt',encoding='utf-8') as stream:
                header=json.loads(next(stream))
                if header.get('format')!='BTC_MYSQL_BACKUP_V1':raise Conflict('unsupported snapshot')
                for line in stream:
                    item=json.loads(line);table=item['table'];quoted=identifier(table)
                    if 'create' in item:
                        if not item['create'].startswith('CREATE TABLE '+quoted+' ('):
                            raise Conflict('unexpected schema statement')
                        db.execute(item['create']);counts[table]=0
                    else:
                        row=item['row']
                        columns=','.join(identifier(k) for k in row)
                        db.execute('INSERT INTO '+quoted+' ('+columns+') VALUES ('+','.join('%s' for _ in row)+')',tuple(row.values()))
                        counts[table]+=1
            if counts!=manifest['tables']:raise Conflict('restored table counts differ')
            db.connection.commit()
        finally:
            db.execute('SET FOREIGN_KEY_CHECKS=1')
        for row in db.rows('SELECT digest FROM blobs'):db.blobs.get(row['digest'])
        return {'restored':True,'tables':counts,'source':str(source)}
