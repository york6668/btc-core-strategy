"""MySQL persistence for the existing validated hourly observation contract.

Only the small, fixed SQL vocabulary of market_observation is adapted. JSON
columns keep their original bytes in CAS; consumers receive the same strings.
The old SQLite implementation remains available for offline and legacy reads.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .database import Database


FIELDS = {
    'evidence': ['evidence_id', 'observed_hour', 'revision', 'payload', 'first_seen'],
    'retrieval': ['retrieval_id', 'evidence_id', 'provenance', 'retrieved_at'],
    'review': ['review_id', 'evidence_id', 'observed_hour', 'context_id', 'payload', 'context_snapshot', 'created_at'],
    'baseline': ['kind', 'review_id', 'completed_at', 'proof_identity'],
    'completion_attempt': ['kind', 'review_id', 'proof_identity', 'completed_at', 'outcome', 'accepted_market_assessment', 'report_fingerprint'],
}
CONTENT = {'payload', 'provenance', 'context_snapshot', 'accepted_market_assessment'}
DDL = (
    '''CREATE TABLE IF NOT EXISTS mo_evidence (
    evidence_id VARCHAR(80) PRIMARY KEY, observed_hour VARCHAR(40) NOT NULL,
    revision BIGINT NOT NULL, payload VARCHAR(72) NOT NULL, first_seen VARCHAR(40) NOT NULL,
    UNIQUE(observed_hour,revision))''',
    '''CREATE TABLE IF NOT EXISTS mo_retrieval (
    retrieval_id VARCHAR(80) PRIMARY KEY,evidence_id VARCHAR(80) NOT NULL,
    provenance VARCHAR(72) NOT NULL,retrieved_at VARCHAR(40) NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS mo_review (
    review_id VARCHAR(80) PRIMARY KEY,evidence_id VARCHAR(80) NOT NULL,
    observed_hour VARCHAR(40) NOT NULL,context_id VARCHAR(80) NOT NULL,
    payload VARCHAR(72) NOT NULL,context_snapshot VARCHAR(72) NOT NULL,created_at VARCHAR(40) NOT NULL,
    INDEX mo_review_time(observed_hour,created_at))''',
    '''CREATE TABLE IF NOT EXISTS mo_baseline (
    kind VARCHAR(16) PRIMARY KEY,review_id VARCHAR(80) NOT NULL,
    completed_at VARCHAR(40) NOT NULL,proof_identity VARCHAR(80) NULL)''',
    '''CREATE TABLE IF NOT EXISTS mo_completion_attempt (
    kind VARCHAR(16) NOT NULL,review_id VARCHAR(80) NOT NULL,
    proof_identity VARCHAR(80) NOT NULL,completed_at VARCHAR(40) NOT NULL,outcome VARCHAR(32) NOT NULL,
    accepted_market_assessment VARCHAR(72) NULL,report_fingerprint VARCHAR(80) NULL,
    PRIMARY KEY(kind,review_id,proof_identity,outcome))''',
)


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Rows:
    def __init__(self, values=()):
        self.values = iter(values)

    def fetchone(self):
        return next(self.values, None)

    def fetchall(self):
        return list(self.values)

    def __iter__(self):
        return self.values


class Connection:
    def __init__(self, settings, readonly=False):
        self.db, self.readonly = Database(settings), readonly

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            self.db.connection.rollback() if error_type else self.db.connection.commit()
        finally:
            self.db.close()

    def execute(self, sql, args=()):
        if sql == 'PRAGMA table_info(completion_attempt)':
            return Rows([Row(cid=i, name=name) for i, name in enumerate(FIELDS['completion_attempt'])])
        if sql == 'BEGIN IMMEDIATE':
            self.db.connection.begin()
            return Rows()
        if self.readonly and not sql.lstrip().upper().startswith('SELECT'):
            raise ValueError('read-only observation connection')
        values = list(args)
        match = re.match(r'INSERT(?: OR (?:IGNORE|REPLACE))? INTO (\w+)(?:\s*\(([^)]+)\))?\s+VALUES', sql, re.I)
        if match:
            table = match.group(1)
            fields = [v.strip() for v in match.group(2).split(',')] if match.group(2) else FIELDS[table]
            # Existing insert statements use positional values; nullable trailing
            # literals do not shift any JSON-column parameter.
            for index, field in enumerate(fields):
                if field in CONTENT and index < len(values) and values[index] is not None:
                    values[index] = '@blob:' + self.db.put_blob(values[index].encode('utf-8'))
        translated = sql.replace('INSERT OR IGNORE', 'INSERT IGNORE').replace('INSERT OR REPLACE', 'REPLACE')
        # MySQL disallows reading a DELETE target through a direct subquery.
        translated = translated.replace('SELECT MIN(retrieval_id) FROM retrieval GROUP BY evidence_id',
            'SELECT oldest FROM (SELECT MIN(retrieval_id) AS oldest FROM retrieval GROUP BY evidence_id) retained')
        translated = re.sub(r'\b(' + '|'.join(FIELDS) + r')\b', lambda m: 'mo_' + m.group(0), translated)
        translated = translated.replace('?', '%s')
        with self.db.connection.cursor() as cursor:
            cursor.execute(translated, tuple(values))
            result = []
            if cursor.description:
                for record in cursor.fetchall():
                    result.append(Row({k: self.db.blobs.get(v[6:]).decode('utf-8')
                                       if isinstance(v, str) and v.startswith('@blob:') else v for k, v in record.items()}))
            return Rows(result)


def connect_if_configured(path, readonly=False):
    config = os.environ.get('BTC_SERVICE_CONFIG')
    if not config:
        return None
    settings = json.loads(Path(config).read_text())
    selected = settings.get('observation_db')
    if selected and Path(path).resolve() == Path(selected).resolve():
        return Connection(settings, readonly)
    return None


def import_sqlite(db, path):
    import sqlite3
    source = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        source.row_factory = sqlite3.Row
        source.execute('BEGIN')
        # This is a complete locked protocol snapshot, including deletions.
        # Merging only present rows would revive a removed baseline/retrieval.
        for table in reversed(list(FIELDS)):
            db.execute('DELETE FROM mo_'+table)
        counts = {}
        for table, fields in FIELDS.items():
            total = 0
            for row in source.execute('SELECT * FROM ' + table):
                values = []
                for field in fields:
                    value = row[field] if field in row.keys() else None
                    values.append('@blob:' + db.put_blob(value.encode('utf-8')) if field in CONTENT and value is not None else value)
                db.execute('INSERT INTO mo_' + table + '(' + ','.join(fields) + ') VALUES(' + ','.join('%s' for _ in fields) + ') '
                           'ON DUPLICATE KEY UPDATE ' + ','.join(field + '=VALUES(' + field + ')' for field in fields), tuple(values))
                total += 1
            counts[table] = total
        return counts
    finally:
        source.close()


def export_sqlite(db, destination):
    """Carry the latest MySQL protocol state into a fresh rollback checkout."""
    import sqlite3
    destination = Path(destination)
    if not destination.is_file():
        raise ValueError('restore the original SQLite schema before exporting current state')
    target = sqlite3.connect(destination)
    try:
        target.execute('PRAGMA foreign_keys=OFF')
        target.execute('BEGIN IMMEDIATE')
        for table in reversed(list(FIELDS)):
            target.execute('DELETE FROM ' + table)
        counts = {}
        for table, fields in FIELDS.items():
            actual = {r[1] for r in target.execute('PRAGMA table_info(' + table + ')')}
            for field in fields:
                if field not in actual:
                    target.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + field + ' TEXT')
            rows = db.rows('SELECT * FROM mo_' + table)
            for row in rows:
                values = [db.blobs.get(row[field][6:]).decode('utf-8') if field in CONTENT and row[field] is not None else row[field] for field in fields]
                target.execute('INSERT INTO ' + table + '(' + ','.join(fields) + ') VALUES(' + ','.join('?' for _ in fields) + ')', values)
            counts[table] = len(rows)
        target.commit()
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('rollback observation store failed integrity check')
        return counts
    finally:
        target.close()
