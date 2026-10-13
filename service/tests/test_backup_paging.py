"""Backup pagination must never leave a server result open during file work."""
import unittest

from btc_node.backup import BLOB_PAGE_SIZE, snapshot_blob_digests, snapshot_table_rows


class SnapshotPages(unittest.TestCase):
    def test_snapshot_primary_key_pages_at_scale_boundary(self):
        digests = [format(index, '064x') for index in range(2 * BLOB_PAGE_SIZE + 1)]

        class Snapshot:
            def __init__(self):
                self.calls = []

            def rows(self, sql, args):
                self.calls.append(args)
                self_test.assertIn('ORDER BY digest LIMIT %s', sql)
                self_test.assertLessEqual(args[1], BLOB_PAGE_SIZE)
                return [{'digest': digest} for digest in digests if digest > args[0]][:args[1]]

        self_test = self
        snapshot = Snapshot()
        self.assertEqual(list(snapshot_blob_digests(snapshot)), digests)
        self.assertEqual(len(snapshot.calls), 4)
        self.assertEqual(snapshot.calls[1][0], digests[BLOB_PAGE_SIZE - 1])
        self.assertEqual(snapshot.calls[2][0], digests[2 * BLOB_PAGE_SIZE - 1])
        self.assertEqual(snapshot.calls[3][0], digests[-1])

    def test_invalid_page_size_is_rejected(self):
        with self.assertRaises(ValueError):
            list(snapshot_blob_digests(None, page_size=BLOB_PAGE_SIZE + 1))

    def test_composite_key_page_and_pin_filter(self):
        records = [{'name':'business','digest':'a'}, {'name':'business','digest':'b'},
                   {'name':'btc-backup:temporary','digest':'c'}, {'name':'other','digest':'d'}]
        class Snapshot:
            def __init__(self): self.queries = []
            def rows(self, sql, args):
                self.queries.append((sql, args))
                if sql.startswith('SHOW INDEX'):
                    return [{'Column_name':'digest','Seq_in_index':2},
                            {'Column_name':'name','Seq_in_index':1}]
                values = [row for row in records if not row['name'].startswith('btc-backup:')]
                if '(`name`,`digest`) >' in sql:
                    values = [row for row in values if (row['name'],row['digest']) > tuple(args[1:3])]
                return values[:args[-1]]
        snapshot = Snapshot()
        self.assertEqual(list(snapshot_table_rows(snapshot, 'metadata_blobs', page_size=2)),
                         [records[0], records[1], records[3]])
        self.assertEqual(len([q for q in snapshot.queries if q[0].startswith('SELECT')]), 3)


if __name__ == '__main__':
    unittest.main()
