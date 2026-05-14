"""skip_list_current: current adapter's skip configuration.

Reads ~/.config/ascendo/skip-list.txt if present; falls back to noting
the adapter doesn't have a configured skip list.
"""
from __future__ import annotations

import os
from pathlib import Path


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ascendo"


def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    skip_path = _config_dir() / "skip-list.txt"
    if not skip_path.exists():
        return "## Skip list\n(no skip-list.txt configured)", 3
    try:
        text = skip_path.read_text()[:1000]
    except OSError:
        return "", 0
    return f"## Skip list\n```\n{text}\n```", 5
