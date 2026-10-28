"""Reference graph growth stays bounded without an isolated MySQL service."""
import tracemalloc
import unittest

from btc_node.lifecycle import protected_documents


class RepeatedReferenceDB:
    root_digest = 'a' * 64
    target_digest = 'b' * 64
    targets = 1500
    references = 500

    def iter_rows(self, sql):
        # The current catalog scan deliberately excludes large payload columns.
        assert sql == ('SELECT document_key,namespace,path,kind,digest,link_target,'
                       'byte_length,retention_class FROM documents')
        yield {'document_key': 'root', 'namespace': 'fixture', 'path': 'root.json',
               'digest': self.root_digest, 'retention_class': 'ACTIVE',
               'kind': 'FILE', 'byte_length': 16, 'link_target': None}
        for number in range(self.targets):
            yield {'document_key': f'target-{number}', 'namespace': 'fixture',
                   'path': f'raw/{number}.bin', 'digest': self.target_digest,
                   'retention_class': 'RUN', 'kind': 'FILE',
                   'byte_length': 16, 'link_target': None}

    def metadata(self, key):
        assert key == 'namespace:fixture'
        return {'root': '/offline/fixture'}

    def rows(self, sql):
        assert sql in ("SELECT DISTINCT target_key FROM dependencies WHERE target_type='BLOB'",
                       "SELECT request_digest FROM operations WHERE status<>'COMMITTED'")
        return []

    def value(self, digest):
        assert digest == self.root_digest
        return {'references': [self.target_digest] * self.references}


class LifecycleQueueOfflineTest(unittest.TestCase):
    def test_repeated_digest_references_preserve_all_paths_with_bounded_queue(self):
        db = RepeatedReferenceDB()
        tracemalloc.start()
        try:
            protected = protected_documents(db)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(db.targets + 1, len(protected))
        self.assertIn('root', protected)
        self.assertIn('target-0', protected)
        self.assertIn('target-1499', protected)
        # The old graph traversal queued 750,000 duplicate keys and exceeded
        # 7 MiB; one entry per catalog row stays below this generous bound.
        self.assertLess(peak, 4 * 1024 * 1024)

    def test_repeated_path_through_link_keeps_link_and_target(self):
        class LinkedDB(RepeatedReferenceDB):
            def iter_rows(self, sql):
                yield {'document_key': 'root', 'namespace': 'fixture', 'path': 'root.json',
                       'digest': self.root_digest, 'retention_class': 'ACTIVE',
                       'kind': 'FILE', 'byte_length': 16, 'link_target': None}
                yield {'document_key': 'link', 'namespace': 'fixture', 'path': 'alias',
                       'digest': None, 'retention_class': 'RUN', 'kind': 'LINK',
                       'byte_length': 0, 'link_target': 'raw/target.bin'}
                yield {'document_key': 'target', 'namespace': 'fixture',
                       'path': 'raw/target.bin', 'digest': self.target_digest,
                       'retention_class': 'RUN', 'kind': 'FILE',
                       'byte_length': 16, 'link_target': None}

            def value(self, digest):
                return {'references': ['/offline/fixture/alias'] * 50}

        self.assertEqual({'root', 'link', 'target'}, protected_documents(LinkedDB()))


if __name__ == '__main__':
    unittest.main()
