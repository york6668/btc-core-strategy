"""Model/profile representation shared by the node and native consumers.

This describes a requested claim, never proof of the model that actually ran.
Original claim strings stay intact; runtime author evidence is checked separately.
"""


def profile(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('explicit model identity required')
    parts = value.split('/')
    if len(parts) > 2 or not parts[0] or any(c.isspace() for c in parts[0]):
        raise ValueError('model or model/effort required')
    effort = parts[1] if len(parts) == 2 else None
    if effort is not None and effort not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}:
        raise ValueError('unknown model effort representation')
    # Existing bare Astra claims reserve this project's Astra/max workflow.
    # Native dispatch and completed-author checks must still prove both fields.
    if parts[0] == 'gpt-6-astra' and effort is None:
        effort = 'max'
    return parts[0], effort


def astra_max(value):
    try:
        return profile(value) == ('gpt-6-astra', 'max')
    except ValueError:
        return False
