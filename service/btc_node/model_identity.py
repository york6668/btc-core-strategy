"""Model/profile representation shared by the node and native consumers.

This describes a requested claim, never proof of the model that actually ran.
Original claim strings stay intact; runtime author evidence is checked separately.
"""


def profile(value):
    if value is None:
        return None, None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('explicit model identity required')
    parts = value.split('/')
    if len(parts) > 2 or not parts[0] or any(c.isspace() for c in parts[0]):
        raise ValueError('model or model/effort required')
    effort = parts[1] if len(parts) == 2 else None
    if effort is not None and (not effort or any(c.isspace() for c in effort)):
        raise ValueError('model effort must be a nonempty identity')
    return parts[0], effort


def valid_selection(value):
    try:
        profile(value)
        return True
    except ValueError:
        return False


def runtime_profile(model, effort):
    """Validate exposed facts, never turn a default request into telemetry."""
    if model is not None and (not isinstance(model, str) or not model
            or '/' in model or any(c.isspace() for c in model)):
        raise ValueError('actual runtime model or null required')
    if effort is not None and (not isinstance(effort, str) or not effort
            or '/' in effort or any(c.isspace() for c in effort)):
        raise ValueError('actual runtime effort or null required')
    return {'name': model, 'reasoning_effort': effort}
