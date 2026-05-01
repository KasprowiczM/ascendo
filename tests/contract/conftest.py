"""Shared fixtures for the JSON v1 sidecar contract tests.

These fixtures are deliberately concrete dicts (not Pydantic instances)
so each test can exercise the parse path independently. The instances
are produced by the tests themselves via :func:`parse_sidecar`.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

# Resolve the fixtures directory once. Both the JSON disk fixtures and
# the inline dicts share this conftest.
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sidecars"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture file and return it as a dict."""
    path = _FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to the on-disk sidecar fixtures directory."""
    return _FIXTURES_DIR


@pytest.fixture
def sample_ascendo_v1_apply_winget() -> dict[str, Any]:
    """A full valid ascendo/v1 sidecar for a winget apply phase.

    Returns a fresh deepcopy on each call so tests can mutate freely
    without poisoning siblings.
    """
    return copy.deepcopy(_load_fixture("ascendo_v1_apply_winget.json"))


@pytest.fixture
def sample_ascendo_v1_check_apt() -> dict[str, Any]:
    """A valid ascendo/v1 sidecar for an apt check phase (one planned item)."""
    return copy.deepcopy(_load_fixture("ascendo_v1_check_apt.json"))


@pytest.fixture
def sample_legacy_v1_apt_check() -> dict[str, Any]:
    """A legacy ubuntu-aktualizacje/v1 sidecar for the apt check phase."""
    return copy.deepcopy(_load_fixture("legacy_v1_apt_check.json"))


@pytest.fixture
def sample_legacy_v1_npm_apply() -> dict[str, Any]:
    """A legacy ubuntu-aktualizacje/v1 sidecar for the npm apply phase (with one error)."""
    return copy.deepcopy(_load_fixture("legacy_v1_npm_apply.json"))


@pytest.fixture
def tmp_run_dir(tmp_path: Path) -> Path:
    """Create a fresh ``<tmp>/<run-id>/`` directory for sidecar I/O tests.

    Uses a generated UUID name so multiple invocations within one test
    session never collide.
    """
    run_id = str(uuid.uuid4())
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
