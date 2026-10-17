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


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_ipad_scan(tmp_path_factory, monkeypatch):
    """The mas phase scripts scan /Applications for iPad wrapper apps and, when
    any exist, hit the network (iTunes Lookup). Point every test at an empty app
    dir so the suite never depends on -- or contacts anything for -- the host's
    real apps. Tests that exercise the iPad path set ASCENDO_APP_DIRS themselves.
    """
    empty = tmp_path_factory.mktemp("no_ipad_apps")
    monkeypatch.setenv("ASCENDO_APP_DIRS", str(empty))


@pytest.fixture(autouse=True)
def _isolate_web_state(tmp_path_factory, monkeypatch):
    """The web phases hand state to each other through the canonical run dir
    ($ASCENDO_RUNS_DIR, else ~/.ascendo/runs) and the version-seen file. Point
    every test at throw-away locations so no test ever writes into the real
    ~/.ascendo (tests that care set these themselves)."""
    base = tmp_path_factory.mktemp("web_state")
    monkeypatch.setenv("ASCENDO_RUNS_DIR", str(base / "runs"))
    monkeypatch.setenv("ASCENDO_WEB_SEEN_FILE", str(base / "seen.tsv"))
