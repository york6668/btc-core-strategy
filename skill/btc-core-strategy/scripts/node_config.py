"""Node-owned paths. No account data or machine identity belongs in source."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def load(path=None):
    selected = path or os.environ.get("BTC_NODE_CONFIG")
    if not selected:
        return {}
    source = Path(selected).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "BTC_NODE_CONFIG_V1":
        raise ValueError("expected BTC_NODE_CONFIG_V1: " + str(source))
    return value


def path(key, default=None):
    value = load().get(key, default)
    if value is None:
        raise ValueError("node configuration requires " + key)
    return Path(value).expanduser().resolve()


def project():
    return path("project_dir", Path.cwd())


def python():
    return path("python", sys.executable)


def factor_guide():
    return Path(__file__).resolve().parents[3] / 'project-methods/价值因子.md'


def legacy_directory():
    # Empty installations can use the current strategy without a legacy archive.
    return path("legacy_revision2_dir", project() / ".strategy" / "legacy" / "revision2")


def legacy_profile():
    selected = legacy_directory() / "profile.json"
    if not selected.is_file():
        return {}
    value = json.loads(selected.read_text(encoding="utf-8"))
    if value.get("schema") != "BTC_LEGACY_PROFILE_V1":
        raise ValueError("invalid legacy profile schema")
    for entry in value.get("artifacts", {}).values():
        name, fingerprint = entry
        if Path(name).name != name or not fingerprint.startswith("sha256:"):
            raise ValueError("invalid legacy artifact binding")
    return value
