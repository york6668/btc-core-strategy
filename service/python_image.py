"""Use the service Dockerfile's immutable Python base for validation helpers."""
from pathlib import Path
import re


def python_image(dockerfile=None):
    source = Path(dockerfile) if dockerfile is not None else Path(__file__).with_name('Dockerfile')
    lines = source.read_text().splitlines()
    first = lines[0] if lines else ''
    match = re.fullmatch(r'FROM (python:3\.11-slim@sha256:[0-9a-f]{64})', first)
    if not match:
        raise ValueError('service Dockerfile must pin Python by manifest digest')
    return match.group(1)


if __name__ == '__main__':
    print(python_image())
