"""Run reader tests with network, real credentials and non-temporary DBs blocked."""
from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
import types
import unittest
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent


def run() -> int:
    replay_mode = sys.argv[1:] == ["--replay"]
    if sys.argv[1:] and not replay_mode:
        raise SystemExit("Only --replay is supported")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    counts = {"network_attempts": 0, "credential_attempts": 0,
              "process_attempts": 0, "non_temporary_db_attempts": 0,
              "non_temporary_write_attempts": 0}
    workspace = (nullcontext(tempfile.mkdtemp(prefix="btc-reader-joint-replay-")) if replay_mode
                 else tempfile.TemporaryDirectory(prefix="btc-reader-offline-"))
    with workspace as directory:
        sandbox = Path(directory).resolve()
        tempfile.tempdir = str(sandbox)
        from build_test_fixtures import build
        build(sandbox / 'reader-fixtures')
        os.environ['BTC_READER_TEST_FIXTURE_DIR'] = str(sandbox / 'reader-fixtures')

        def is_temporary(path):
            if isinstance(path, int):
                return True
            return Path(os.fsdecode(path)).resolve().is_relative_to(sandbox)

        def audit(event, args):
            if event.startswith("socket."):
                counts["network_attempts"] += 1
                raise AssertionError("offline_tests_network_denied")
            if event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"):
                counts["process_attempts"] += 1
                raise AssertionError("offline_tests_process_denied")
            if event == "sqlite3.connect":
                path = os.fsdecode(args[0])
                if path == ":memory:":
                    return
                if path.startswith("file:"):
                    path = unquote(urlsplit(path).path)
                if not is_temporary(path):
                    counts["non_temporary_db_attempts"] += 1
                    raise AssertionError("offline_tests_non_temporary_db_denied")
            if event == "open":
                path, mode, flags = args
                writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
                if writing and not is_temporary(path):
                    counts["non_temporary_write_attempts"] += 1
                    raise AssertionError("offline_tests_non_temporary_write_denied")

        def no_credentials(*_args, **_kwargs):
            counts["credential_attempts"] += 1
            raise AssertionError("offline_tests_real_credentials_denied")

        sys.addaudithook(audit)
        sys.modules["keyring"] = types.SimpleNamespace(get_password=no_credentials)
        if replay_mode:
            from reader_replay_fixture import replay_fixture
            from collector_replay_fixture import CASES

            receipts = []
            for case in CASES:
                observations, receipt = replay_fixture(sandbox / case, case, from_payload=True)
                raw = json.dumps(observations, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")
                observation_path = sandbox / (case + "-collector-observations.json")
                observation_path.write_bytes(raw)
                receipt.update({"observations_path": str(observation_path),
                                "observations_bytes": len(raw),
                                "observations_sha256": hashlib.sha256(raw).hexdigest()})
                receipts.append(receipt)
            result = {"cases": receipts, "guards": counts, "module_sha256": {
                                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("btc_evidence.py", "btc_evidence_reader.py", "reader.py", "sync_engine.py", "bot_client.py", "tests/collector_replay_fixture.py")
                            }}
            print("BTC_READER_JOINT_REPLAY=" + json.dumps(result, sort_keys=True))
            return 0 if not any(counts.values()) else 1
        loader = unittest.TestLoader()
        legacy = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
        portable = loader.loadTestsFromNames(["test_btc_evidence", "test_btc_evidence_reader"])
        groups = {"reader_and_adapter": legacy.countTestCases(),
                  "root_portable": portable.countTestCases()}
        suite = unittest.TestSuite([legacy, portable])
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        summary = {
            "python": sys.executable, "python_version": sys.version.splitlines()[0],
            "groups": groups, "tests_run": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped),
            "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses), **counts,
        }
        print("BTC_READER_OFFLINE_RESULT=" + json.dumps(summary, sort_keys=True))
        return 0 if result.wasSuccessful() and not result.skipped and not any(counts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(run())
