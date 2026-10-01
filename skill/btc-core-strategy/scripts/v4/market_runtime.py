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
    # Python 3.12 changed float sum. Previously collected 3.12 packages stay
    # immutable and are checked by 3.12, while production keeps its 3.9 clock.
    require(BUNDLED_PYTHON.is_file() and BUNDLED_PYTHON.resolve() != Path(sys.executable).resolve(), 'frozen market calculation runtime unavailable')
    code = "import sys,json;sys.path.insert(0,sys.argv[1]);import market_observation as m;m.validate_evidence(json.load(open(sys.argv[2])));print(sys.version.split()[0])"
    completed = subprocess.run([str(BUNDLED_PYTHON), '-I', '-c', code, str(Path(market_observation.__file__).parent), str(path)], text=True, capture_output=True, timeout=30)
    require(completed.returncode == 0, 'frozen market fails both supported exact runtime validations')
    return {'runtime': str(BUNDLED_PYTHON), 'version': completed.stdout.strip()}
