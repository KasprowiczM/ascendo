"""Server-side derivation of ``item_filter`` from /apps exclusions.

When the SPA fires apply with no explicit ``item_filter`` and the user
has opted out of specific packages via ``POST /apps/exclude``, the
dashboard auto-builds an inclusion list = installed-minus-excluded by
reading the latest check sidecar per category. This file tests
:func:`ascendo.dashboard.routes.runs._resolve_item_filter` end-to-end
without spinning up FastAPI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascendo.dashboard.routes.runs import (
    _enumerate_known_categories,
    _resolve_item_filter,
)
from ascendo.models.run import Phase


def _write_check_sidecar(runs_dir: Path, run_id: str, category: str,
                         item_names: list[str]) -> None:
    """Drop a minimal ``check__<cat>.json`` so _latest_check_items can read it."""
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {
        "schema": "ascendo/v1",
        "phase": "check",
        "category": category,
        "items": [
            {"id": name, "name": name, "status": "up_to_date"}
            for name in item_names
        ],
    }
    (run_dir / f"check__{category}.json").write_text(
        json.dumps(sidecar), encoding="utf-8",
    )


def test_explicit_filter_passes_through_unchanged(tmp_path: Path) -> None:
    """When caller provides an explicit filter, _resolve returns it verbatim."""
    out = _resolve_item_filter(
        explicit_filter=["uv", "node"],
        categories=["brew", "npm"],
        phases=(Phase.APPLY,),
        runs_dir=tmp_path,
    )
    assert out == ["uv", "node"]


def test_no_apply_phase_returns_none(tmp_path: Path) -> None:
    """check / plan / verify don't need a filter — those phases enumerate
    the universe regardless of exclusions."""
    out = _resolve_item_filter(
        explicit_filter=None,
        categories=["brew"],
        phases=(Phase.CHECK, Phase.PLAN, Phase.VERIFY),
        runs_dir=tmp_path,
    )
    assert out is None


def test_no_exclusions_returns_none(tmp_path: Path, monkeypatch) -> None:
    """When the exclusion store is empty, no filter is needed — the bash
    apply scripts default to all installed items."""
    from ascendo.dashboard.routes import apps as apps_mod
    monkeypatch.setattr(apps_mod, "excluded_keys", lambda: set())
    out = _resolve_item_filter(
        explicit_filter=None,
        categories=["brew"],
        phases=(Phase.APPLY,),
        runs_dir=tmp_path,
    )
    assert out is None


def test_apply_with_exclusion_builds_inclusion_list(
    tmp_path: Path, monkeypatch,
) -> None:
    """Real path: apply phase + exclusions → inclusion list = installed minus excluded."""
    _write_check_sidecar(tmp_path, "run-1", "brew", ["uv", "abseil", "zstd"])
    _write_check_sidecar(tmp_path, "run-1", "npm",  ["node", "npm", "bun"])
    from ascendo.dashboard.routes import apps as apps_mod
    monkeypatch.setattr(
        apps_mod,
        "excluded_keys",
        lambda: {"brew:abseil", "npm:bun"},
    )
    out = _resolve_item_filter(
        explicit_filter=None,
        categories=["brew", "npm"],
        phases=(Phase.APPLY,),
        runs_dir=tmp_path,
    )
    assert out is not None
    assert set(out) == {"uv", "zstd", "node", "npm"}


def test_apply_with_no_categories_falls_back_to_known_set(
    tmp_path: Path, monkeypatch,
) -> None:
    """Caller didn't specify categories → enumerate from disk."""
    _write_check_sidecar(tmp_path, "run-1", "brew", ["uv", "abseil"])
    from ascendo.dashboard.routes import apps as apps_mod
    monkeypatch.setattr(
        apps_mod, "excluded_keys", lambda: {"brew:abseil"},
    )
    out = _resolve_item_filter(
        explicit_filter=None,
        categories=None,
        phases=(Phase.APPLY,),
        runs_dir=tmp_path,
    )
    assert out == ["uv"]


def test_enumerate_categories_picks_up_recent_sidecars(tmp_path: Path) -> None:
    _write_check_sidecar(tmp_path, "run-1", "brew", [])
    _write_check_sidecar(tmp_path, "run-2", "mas", [])
    _write_check_sidecar(tmp_path, "run-3", "npm", [])
    cats = _enumerate_known_categories(tmp_path)
    assert sorted(cats) == ["brew", "mas", "npm"]


def test_apply_with_empty_runs_dir_returns_none(tmp_path: Path, monkeypatch) -> None:
    """No check sidecars yet → can't build a filter; fall through to None
    so apply runs against everything (which is what the user wants on
    a fresh install)."""
    from ascendo.dashboard.routes import apps as apps_mod
    monkeypatch.setattr(
        apps_mod, "excluded_keys", lambda: {"brew:abseil"},
    )
    out = _resolve_item_filter(
        explicit_filter=None,
        categories=["brew"],
        phases=(Phase.APPLY,),
        runs_dir=tmp_path,
    )
    assert out is None
