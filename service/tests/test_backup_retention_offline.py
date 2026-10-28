"""Backup expiry remains recoverable when the process stops during deletion."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from btc_node.lifecycle import DAY, prune_backups


class BackupRetentionOfflineTest(unittest.TestCase):
    def test_interrupted_deletion_resumes_after_manifest_has_gone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'backups'
            root.mkdir()
            now = 30 * DAY
            for name, age in [('expired', 12), ('last-two', 10), ('latest', 1)]:
                backup = root / name
                backup.mkdir()
                (backup / 'manifest.json').write_text(json.dumps({
                    'format': 'BTC_MYSQL_BACKUP_V1', 'created_ms': now - age * DAY}))
                (backup / 'content').write_bytes(name.encode())
            incomplete = root / '.backup-incomplete'
            incomplete.mkdir()
            (incomplete / 'content').write_bytes(b'partial backup')
            outside = Path(directory) / 'outside'
            outside.mkdir()
            (outside / 'content').write_bytes(b'not owned by backup retention')
            link = root / ('.pruning-' + 'a' * 32)
            link.symlink_to(outside, target_is_directory=True)

            def interrupted(path):
                (path / 'manifest.json').unlink()
                raise OSError('simulated interruption during backup deletion')

            with patch('btc_node.lifecycle.shutil.rmtree', side_effect=interrupted):
                with self.assertRaisesRegex(OSError, 'simulated interruption'):
                    prune_backups(root, now_ms=now, keep_days=7)

            self.assertFalse((root / 'expired').exists())
            retired = [p for p in root.iterdir() if p.name.startswith('.pruning-') and not p.is_symlink()]
            self.assertEqual(len(retired), 1)
            self.assertEqual((retired[0] / 'content').read_bytes(), b'expired')
            self.assertFalse((retired[0] / 'manifest.json').exists())
            prune_backups(root, now_ms=now, keep_days=7)
            self.assertFalse(retired[0].exists())
            self.assertEqual((root / 'last-two' / 'content').read_bytes(), b'last-two')
            self.assertEqual((root / 'latest' / 'content').read_bytes(), b'latest')
            self.assertEqual((incomplete / 'content').read_bytes(), b'partial backup')
            self.assertTrue(link.is_symlink())
            self.assertEqual((outside / 'content').read_bytes(), b'not owned by backup retention')


if __name__ == '__main__':
    unittest.main()
