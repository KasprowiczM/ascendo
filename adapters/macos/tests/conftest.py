"""Shared fixtures for adapters/macos/tests/."""
from __future__ import annotations

import sys
from pathlib import Path

# Make ascendo (core) importable in tests without an editable install.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CORE_SRC = _REPO_ROOT / "core"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

# Make ascendo_macos importable.
_ADAPTER_SRC = Path(__file__).resolve().parents[1]
if str(_ADAPTER_SRC) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_SRC))
