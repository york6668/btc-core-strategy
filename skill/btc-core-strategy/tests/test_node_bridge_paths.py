"""Native path semantics survive a different Docker working directory."""
from pathlib import Path
import tempfile
import unittest
import node_bridge


class NativePaths(unittest.TestCase):
    def test_relative_run_template_and_snapshot_keep_native_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            args=['--project','.', '--root','.strategy/v4', 'run-advance',
                  '--run','.strategy/v4/run/run.json','--template','.strategy/v4/run/template.json',
                  '--collector-snapshot','.strategy/v4/old/collector.json','--stage','market']
            actual=node_bridge.absolute_paths(args,root)
            for flag in ('--project','--root','--run','--template','--collector-snapshot'):
                index=args.index(flag)+1
                self.assertEqual(actual[index],str((root/args[index]).absolute()))
            self.assertEqual(args[args.index('--run')+1],'.strategy/v4/run/run.json')
            self.assertEqual(actual[-1],'market')
            identity=['delivery','--identity','sha256:original','--action','UNKNOWN']
            self.assertEqual(node_bridge.absolute_paths(identity,root),identity)
