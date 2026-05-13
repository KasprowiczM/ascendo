"""Regression tests for Sesja 66's post-apply overlay scoping fix.

Operator report (DP5520WMK 2026-05-13): VSCode was manually updated to
1.120.0. After ``ascendo build-inventory`` Ascendo still reported the
web row as ``installed=1.119.1, candidate=1.120.0, outdated``. Every
subsequent run kept asking to upgrade VSCode even though the latest
check/apply/verify sidecars all confirmed 1.120.0 up_to_date.

Root cause: ``_latest_check_overlay`` walked apply/verify sidecars from
**all** runs in ``post_apply_payloads`` and let any ``triggered`` /
``success`` status overwrite the freshest check baseline. An old
``triggered`` status from run 6149fbba (11:51) overrode every newer
run's ``up_to_date`` signal because the post-apply overlay block
skips non-(success|triggered) statuses (so newer up_to_date sidecars
never cleared the stale 1.119.1 value).

Fix: only include post-apply payloads from the SAME RUN as the chosen
check baseline. Older runs' state is already subsumed by the freshest
check, so their apply/verify sidecars must not leak through.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Force-load from THIS worktree's core/, not the system editable install
# which may point at a stale main checkout.
_WORKTREE_CORE = Path(__file__).resolve().parents[2] / "core"
if _WORKTREE_CORE.is_dir() and str(_WORKTREE_CORE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_CORE))

# Re-import if a stale copy is already cached.
import importlib  # noqa: E402

if "ascendo.dashboard.routes.spa_real" in sys.modules:
    importlib.reload(sys.modules["ascendo.dashboard.routes.spa_real"])

from ascendo.dashboard.routes.spa_real import _latest_check_overlay  # noqa: E402


def _write_sidecar(
    run_dir: Path,
    phase: str,
    category: str,
    items: list[dict],
    *,
    mtime: float | None = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"{phase}__{category}.json"
    payload = {
        "schema": "ascendo/v1",
        "phase": phase,
        "category": category,
        "items": items,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))
    return path


def test_stale_triggered_from_old_run_does_not_override_newer_check(
    tmp_path: Path,
) -> None:
    """The exact bug operator hit: old triggered + newer up_to_date."""
    runs = tmp_path / "runs"

    # Old run: apply marked vscode-user as triggered with current=1.119.1
    old_run = runs / "0000-old"
    _write_sidecar(
        old_run, "check", "web",
        [{"id": "web:vscode-user",
          "name": "Microsoft Visual Studio Code (User)",
          "current_version": "1.119.1",
          "target_version": "1.120.0",
          "status": "planned"}],
        mtime=1000.0,
    )
    _write_sidecar(
        old_run, "apply", "web",
        [{"id": "web:vscode-user",
          "name": "Microsoft Visual Studio Code (User)",
          "current_version": "1.119.1",
          "target_version": "1.120.0",
          "status": "triggered"}],
        mtime=1100.0,
    )
    # Force the run dir mtime EARLIER than the new run dir so the
    # sort-by-mtime newest-first picks the new run first.
    import os
    os.utime(old_run, (1100.0, 1100.0))

    # New run: check + apply + verify all confirm 1.120.0 up_to_date
    new_run = runs / "9999-new"
    _write_sidecar(
        new_run, "check", "web",
        [{"id": "web:vscode-user",
          "name": "Microsoft Visual Studio Code (User)",
          "current_version": "1.120.0",
          "target_version": "1.120.0",
          "status": "up_to_date"}],
        mtime=2000.0,
    )
    _write_sidecar(
        new_run, "apply", "web",
        [{"id": "web:vscode-user",
          "name": "Microsoft Visual Studio Code (User)",
          "current_version": "1.120.0",
          "target_version": "1.120.0",
          "status": "up_to_date"}],
        mtime=2100.0,
    )
    _write_sidecar(
        new_run, "verify", "web",
        [{"id": "web:vscode-user",
          "name": "Microsoft Visual Studio Code (User)",
          "current_version": "1.120.0",
          "target_version": "1.120.0",
          "status": "up_to_date"}],
        mtime=2200.0,
    )
    os.utime(new_run, (2200.0, 2200.0))

    overlay = _latest_check_overlay(runs, "web")
    rec = overlay.get("Microsoft Visual Studio Code (User)")
    assert rec is not None, "overlay must contain vscode-user entry"
    assert rec["installed"] == "1.120.0", (
        f"installed must be 1.120.0 (from the freshest check), "
        f"not 1.119.1 from the stale triggered apply. Got {rec!r}"
    )
    assert rec["candidate"] == "1.120.0"


def test_post_apply_overlay_from_same_run_still_works(tmp_path: Path) -> None:
    """The intended behaviour: same-run apply success overlays its
    resolved_version on top of the check baseline.

    Without this, the Sesja 53 fix (opencode upgrade visible immediately
    after apply) would regress.
    """
    runs = tmp_path / "runs"
    run = runs / "active"
    # Check phase saw 1.14.40 outdated, target 1.14.41
    _write_sidecar(
        run, "check", "web",
        [{"id": "web:opencode",
          "name": "OpenCode",
          "current_version": "1.14.40",
          "target_version": "1.14.41",
          "status": "planned"}],
        mtime=3000.0,
    )
    # Apply succeeded and resolved to 1.14.41
    _write_sidecar(
        run, "apply", "web",
        [{"id": "web:opencode",
          "name": "OpenCode",
          "current_version": "1.14.40",
          "target_version": "1.14.41",
          "resolved_version": "1.14.41",
          "status": "success"}],
        mtime=3100.0,
    )

    overlay = _latest_check_overlay(runs, "web")
    rec = overlay.get("OpenCode")
    assert rec is not None
    assert rec["installed"] == "1.14.41", (
        f"installed must reflect post-apply resolved_version (1.14.41), "
        f"not pre-apply current_version. Got {rec!r}"
    )
    assert rec["status"] == "up_to_date"


def test_old_run_apply_success_does_not_leak_into_newer_check(
    tmp_path: Path,
) -> None:
    """Symmetric coverage: an old SUCCESS overlay also must not bleed
    through. Only post-apply from the same run matters.
    """
    runs = tmp_path / "runs"
    old = runs / "0000-old"
    _write_sidecar(
        old, "apply", "web",
        [{"id": "web:opencode",
          "name": "OpenCode",
          "current_version": "1.14.40",
          "target_version": "1.14.41",
          "resolved_version": "1.14.41",
          "status": "success"}],
        mtime=1000.0,
    )
    import os
    os.utime(old, (1000.0, 1000.0))

    new = runs / "9999-new"
    _write_sidecar(
        new, "check", "web",
        [{"id": "web:opencode",
          "name": "OpenCode",
          "current_version": "1.14.43",
          "target_version": "1.14.48",
          "status": "planned"}],
        mtime=2000.0,
    )
    os.utime(new, (2000.0, 2000.0))

    overlay = _latest_check_overlay(runs, "web")
    rec = overlay.get("OpenCode")
    assert rec is not None
    # The new check shows 1.14.43 -> 1.14.48 outdated. The old
    # apply at 1.14.41 must NOT override the current truth.
    assert rec["installed"] == "1.14.43", (
        f"new check should win over old apply. Got {rec!r}"
    )
    assert rec["candidate"] == "1.14.48"
