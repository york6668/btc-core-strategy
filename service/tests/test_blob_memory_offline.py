"""Bounded immutable-blob checks without MySQL or a Docker service."""
import tempfile
import tracemalloc
import unittest

from btc_node.codec import Blobs, Conflict


class BlobMemoryTest(unittest.TestCase):
    def test_existing_blob_recheck_streams_and_rejects_corruption(self):
        data = b'x' * (4 * 1024 * 1024)
        with tempfile.TemporaryDirectory(prefix='btc-blob-recheck-') as root:
            blobs = Blobs(root)
            digest = blobs.put(data)
            tracemalloc.start()
            try:
                self.assertEqual(digest, blobs.put(data))
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertLess(peak, 3 * 1024 * 1024)
            blobs.path(digest).write_bytes(b'y' * len(data))
            with self.assertRaisesRegex(Conflict, 'stored content checksum mismatch'):
                blobs.put(data)


if __name__ == '__main__':
    unittest.main()
