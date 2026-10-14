"""Exercise failed disposable-stack evidence collection without Docker."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check


class FailureDiagnosticsTests(unittest.TestCase):
    def test_compose_failure_captures_bounded_logs_and_cleans_temp_data(self):
        with tempfile.TemporaryDirectory() as root:
            reader = Path(root) / 'reader'
            reader.mkdir()
            (reader / 'seller_exhaustion.py').write_text('')
            guard = type('Guard', (), {'project_started': False, 'cleaned': False})()
            failure = subprocess.CalledProcessError(1, ['docker', 'compose', 'config'])
            with patch.object(check.subprocess, 'check_output', side_effect=failure), \
                    patch.object(check.subprocess, 'run') as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    check._run('docker', reader, None, guard, 'btc-test-diagnostics')
            calls = [item.args[0] for item in run.call_args_list]
            self.assertTrue(any(command[-2:] == ['ps', '--all'] for command in calls))
            self.assertTrue(any(command[-4:] == ['logs', '--tail', '60', 'core'] for command in calls))
            self.assertTrue(guard.cleaned)


if __name__ == '__main__':
    unittest.main()
