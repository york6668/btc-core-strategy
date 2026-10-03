"""Validate frozen market packages under the calculation runtime that reproduces them.
Never rewrite source bytes or relax numerical equality. Network access is absent.
"""
from pathlib import Path
import os
import subprocess
import sys
import market_observation
from .common import Invalid, read, require

import node_config

BUNDLED_PYTHON = Path(os.environ.get('BTC_FROZEN_MARKET_PYTHON',
    str(node_config.path('frozen_market_python', sys.executable))))


def validate_file(path):
    path = Path(path).resolve()
    value = read(path)
    try:
        market_observation.validate_evidence(value)
        return {'runtime': sys.executable, 'version': sys.version.split()[0]}
    except market_observation.ObservationError as exc:
        require(str(exc) == 'window measurement mismatch', str(exc))
    # Historical numerical semantics belong to their calculation runtime.
    # The node retains this interpreter independently of application caches.
    require(BUNDLED_PYTHON.is_file() and BUNDLED_PYTHON.resolve() != Path(sys.executable).resolve(),
            'node-owned frozen market calculation runtime unavailable; preserve frozen bytes and configure a compatible retained interpreter')
    code = "import sys,json;sys.path.insert(0,sys.argv[1]);import market_observation as m;m.validate_evidence(json.load(open(sys.argv[2])));print(sys.version.split()[0])"
    completed = subprocess.run([str(BUNDLED_PYTHON), '-I', '-c', code, str(Path(market_observation.__file__).parent), str(path)], text=True, capture_output=True, timeout=30)
    require(completed.returncode == 0, 'frozen market fails both supported exact runtime validations')
    expected = node_config.load().get('frozen_market_python_version')
    require(expected is None or completed.stdout.strip() == expected,
            'frozen market interpreter differs from the node-pinned version')
    return {'runtime': str(BUNDLED_PYTHON), 'version': completed.stdout.strip()}
