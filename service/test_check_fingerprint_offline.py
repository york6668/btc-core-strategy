"""Synthetic equivalence and memory regression for the disposable DB check."""
import hashlib
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest

from btc_node.codec import Blobs, Conflict
from check_fingerprint import fingerprint_state


class FakeDatabase:
    def __init__(self, root, tables, blob_digests=()):
        self.tables = tables
        self.blob_digests = blob_digests
        self.blobs = Blobs(root)

    def rows(self, sql):
        if sql == 'SHOW TABLES':
            return [{'Tables_in_btc': name} for name in reversed(self.tables)]
        if sql == 'SELECT digest FROM blobs':
            return [{'digest': digest} for digest in self.blob_digests]
        return list(self.iter_rows(sql))

    def iter_rows(self, sql):
        if sql == 'SELECT digest FROM blobs':
            for digest in self.blob_digests:
                yield {'digest': digest}
        else:
            table = sql.removeprefix('SELECT * FROM `').removesuffix('`')
            yield from self.tables[table]


def original_fingerprint(database):
    tables = [next(iter(row.values())) for row in database.rows('SHOW TABLES')]
    state = {table: sorted(json.dumps(row, sort_keys=True)
                           for row in database.rows('SELECT * FROM `'+table+'`'))
             for table in tables}
    [database.blobs.get(row['digest']) for row in database.rows('SELECT digest FROM blobs')]
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


class FingerprintTest(unittest.TestCase):
    def test_exact_legacy_digest_for_sorted_duplicate_and_unicode_rows(self):
        with tempfile.TemporaryDirectory() as root:
            tables = {'zeta': [{'id': 2, 'text': '汉字'}, {'id': 1, 'text': 'quote " \\'},
                               {'id': 1, 'text': 'quote " \\'}],
                      'alpha': [], 'Beta': [{'value': None, 'nested': {'z': 1, 'a': True}}]}
            database = FakeDatabase(root, tables)
            digest = database.blobs.put(b'synthetic original')
            database.blob_digests = (digest, digest)
            self.assertEqual(original_fingerprint(database), fingerprint_state(database))

    def test_missing_or_corrupt_blob_still_fails(self):
        with tempfile.TemporaryDirectory() as root:
            database = FakeDatabase(root, {'rows': [{'id': 1}]})
            digest = database.blobs.put(b'synthetic original')
            database.blob_digests = (digest,)
            path = database.blobs.path(digest)
            path.write_bytes(b'corrupt')
            with self.assertRaisesRegex(Conflict, 'stored content checksum mismatch'):
                fingerprint_state(database)
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                fingerprint_state(database)

    def test_peak_python_memory_is_bounded_for_many_rows_and_large_blob(self):
        with tempfile.TemporaryDirectory() as root:
            class GeneratedRows:
                def __iter__(self):
                    for index in range(20000):
                        yield {'id': 20000-index, 'value': 'synthetic-' + 'x'*120}
            database = FakeDatabase(root, {'rows': GeneratedRows()})
            digest = database.blobs.put(b'z' * (8 * 1024 * 1024))
            database.blob_digests = (digest,)
            tracemalloc.start()
            try:
                fingerprint_state(database)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            self.assertLess(peak, 3 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
