"""Consistent MySQL snapshots and independent, hash-verified content backups."""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .codec import Blobs, Conflict, encode, fingerprint, millis, sync_directory


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
    try:
        with db.writer(), db.transaction():
            tables = [next(iter(r.values())) for r in db.rows('SHOW TABLES')]
            backup_blobs = Blobs(stage/'objects')
            counts = {}
            with gzip.open(stage/'database.jsonl.gz', 'wb') as stream:
                stream.write(encode({'format':'BTC_MYSQL_BACKUP_V1','created_ms':millis()}))
                for table in tables:
                    quoted=identifier(table)
                    schema=db.one('SHOW CREATE TABLE '+quoted)['Create Table']
                    stream.write(encode({'table':table,'create':schema}))
                    rows=db.rows('SELECT * FROM '+quoted)
                    counts[table]=len(rows)
                    for row in rows:
                        stream.write(encode({'table':table,'row':row}))
                for row in db.rows('SELECT digest FROM blobs'):
                    digest=row['digest']
                    target=backup_blobs.path(digest)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    # Originals are immutable. Hardlinks preserve backup bytes
                    # even after live GC unlinks its own directory entry.
                    db.blobs.materialize(digest,target)
            manifest={'format':'BTC_MYSQL_BACKUP_V1','tables':counts,
                      'database_sha256':fingerprint((stage/'database.jsonl.gz').read_bytes())}
            (stage/'manifest.json').write_bytes(encode(manifest))
        for path in (stage/'manifest.json',stage/'database.jsonl.gz'):
            with path.open('rb') as handle:os.fsync(handle.fileno())
        sync_directory(stage)
        os.rename(stage,destination);sync_directory(destination.parent)
        return {'backup':str(destination),**manifest}
    finally:
        if stage.exists():shutil.rmtree(stage)


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
