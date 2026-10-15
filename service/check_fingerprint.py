"""Bounded, byte-for-byte equivalent fingerprint for disposable node checks."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from btc_node.codec import Conflict, fingerprint_file


def fingerprint_state(database):
    tables = [next(iter(row.values())) for row in database.rows('SHOW TABLES')]
    digest = hashlib.sha256()
    digest.update(b'{')
    # json.dumps(..., sort_keys=True) orders table names and then orders each
    # table's serialized rows. Each row string is ASCII because ensure_ascii
    # defaults to True, so SQLite's binary order equals Python string order.
    with tempfile.TemporaryDirectory(prefix='btc-check-fingerprint-') as directory:
        with sqlite3.connect(str(Path(directory) / 'rows.sqlite')) as spool:
            spool.execute('PRAGMA temp_store=FILE')
            spool.execute('PRAGMA cache_size=-1024')
            spool.execute('CREATE TABLE rows(value TEXT NOT NULL)')
            for table_index, table in enumerate(sorted(tables)):
                if table_index:
                    digest.update(b', ')
                digest.update(json.dumps(table).encode())
                digest.update(b': [')
                spool.execute('DELETE FROM rows')
                for row in database.iter_rows('SELECT * FROM `'+table+'`'):
                    spool.execute('INSERT INTO rows(value) VALUES (?)',
                                  (json.dumps(row, sort_keys=True),))
                spool.commit()
                for row_index, (value,) in enumerate(spool.execute(
                        'SELECT value FROM rows ORDER BY value COLLATE BINARY')):
                    if row_index:
                        digest.update(b', ')
                    digest.update(json.dumps(value).encode())
                digest.update(b']')
    digest.update(b'}')
    # Keep the original read-and-check requirement without retaining every
    # blob (or even one whole blob) in the Python process.
    for row in database.iter_rows('SELECT digest FROM blobs'):
        original = row['digest']
        if fingerprint_file(database.blobs.path(original)) != original:
            raise Conflict('stored content checksum mismatch')
    return digest.hexdigest()


def main():
    from btc_node.database import Database
    with open('/run/btc/settings.json') as source:
        database = Database(json.load(source))
    try:
        print(fingerprint_state(database))
    finally:
        database.close()


if __name__ == '__main__':
    main()
