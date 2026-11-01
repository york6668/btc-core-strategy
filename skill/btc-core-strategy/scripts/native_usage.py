"""Consume the shared native-usage interlock; never estimate account credits."""
import json
from pathlib import Path
import subprocess


class NativeUsageBlocked(ValueError):
    """A known rejection before dispatch, never an ambiguous model outcome."""


def require_admission(config, *, runner=None):
    binding = config.get('usage_guard')
    if binding is None and not config.get('usage_guard_required'):
        # Compatibility for nodes that have not adopted this feature. Never
        # describe them as protected; the installed protected node sets required.
        return {'enforced': False, 'status': 'NOT_CONFIGURED'}
    if not isinstance(binding, dict):
        raise NativeUsageBlocked('native usage guard binding is required; no model work admitted')
    command, policy = binding.get('command'), binding.get('policy')
    if (not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command)
            or not Path(command[0]).is_absolute() or not isinstance(policy, str) or not Path(policy).is_absolute()):
        raise NativeUsageBlocked('explicit native usage guard command and policy paths required')
    try:
        result = (runner or subprocess.run)(command, input=json.dumps({'policy': policy}),
                     text=True, capture_output=True, timeout=45, check=False)
        value = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise NativeUsageBlocked('native usage measurement unavailable; new work blocked: ' + type(exc).__name__) from None
    if not isinstance(value, dict):
        raise NativeUsageBlocked('native usage interlock returned an invalid response; new work blocked')
    if (result.returncode != 0 or value.get('schema') != 'native-usage-guard-v1'
            or value.get('allowed') is not True or value.get('hard_account_spending_cap') is not False):
        lock = value.get('lock')
        reason = (lock.get('reason') if isinstance(lock, dict) else None) or value.get('status') or 'INVALID_GUARD_RESPONSE'
        raise NativeUsageBlocked('native usage interlock blocked new work: ' + str(reason))
    return {'enforced': True, 'allowed': True, 'measurement': value.get('measurement'),
            'assessment': value.get('assessment'), 'hard_account_spending_cap': False}
