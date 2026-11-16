"""Load the shared identity contract from this exact source/release package."""
import importlib.util
from pathlib import Path

_source = Path(__file__).resolve().parents[3] / 'service/btc_node/model_identity.py'
_spec = importlib.util.spec_from_file_location('_btc_pinned_model_identity', _source)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
profile = _contract.profile
valid_selection = _contract.valid_selection
runtime_profile = _contract.runtime_profile


def native_runtime_profile(config, agent_id):
    """Read current native turn metadata, without treating a selection as fact."""
    import json
    import os
    import run_usage
    unknown = runtime_profile(None, None)
    if agent_id != os.environ.get('CODEX_THREAD_ID'):
        return unknown
    try:
        db = run_usage.usage_database(configured=config.get('codex_state_db'))
        threads, _ = run_usage.native_threads(db)
        row = threads.get(agent_id)
        if not row:
            return unknown
        session, current = None, None
        with run_usage.rollout_stream(row['rollout_path']) as (stream, compressed):
            for raw in iter(lambda: stream.readline(16 * 1024 * 1024 + 1), b''):
                if len(raw) > 16 * 1024 * 1024:
                    return unknown
                if not raw.endswith(b'\n'):
                    if compressed:
                        return unknown
                    break
                event = json.loads(raw)
                payload = event.get('payload', {})
                if event.get('type') == 'session_meta':
                    session = payload.get('id', payload.get('session_id'))
                elif event.get('type') == 'turn_context' and payload.get('turn_id'):
                    current = payload
        if session != agent_id or current is None:
            return unknown
    except (OSError, ValueError, KeyError, run_usage.UsageSourceUnavailable):
        return unknown
    settings = (current.get('collaboration_mode') or {}).get('settings', {})
    return runtime_profile(current.get('model'), current.get('effort', settings.get('reasoning_effort')))
