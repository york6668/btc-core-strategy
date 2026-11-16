"""Load the shared identity contract from this exact source/release package."""
import importlib.util
from pathlib import Path

_source = Path(__file__).resolve().parents[3] / 'service/btc_node/model_identity.py'
_spec = importlib.util.spec_from_file_location('_btc_pinned_model_identity', _source)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
profile = _contract.profile
astra_max = _contract.astra_max
