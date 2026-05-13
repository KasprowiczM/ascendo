"""Regression tests for Sesja 66's apply-mark plumbing into plan + apply.

Operator report (DP5520WMK 2026-05-13): SoftSea.IMGtoISO was upgraded
in run 5a9a155d (apply-mark target=1.0 persisted to
~/.ascendo/state/winget_apply_marks.json), but the very next full
update re-applied it because plan.ps1 + apply.ps1 ignored the mark.
Only check.ps1 consulted it (Sesja 63 fix). Plan correctly classified
IMG to ISO as ``planned`` from winget's perspective (Available=1.0 vs
Unknown), apply ran the upgrade again, and the sidecar reported
``status=success cur=None resolved=1.0`` — operationally a no-op but
semantically wrong (the orchestrator counts it as an upgrade).

Sesja 66 ships matching apply-mark checks in plan.ps1 + apply.ps1:
when current is Unknown/blank AND ``mark.target == Available``, plan
skips emission and apply emits ``status=up_to_date`` without invoking
winget.

These tests are static-analysis checks against the .ps1 source so a
refactor cannot silently regress.
"""
from __future__ import annotations

import re
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
PLAN_PS1 = ADAPTER_ROOT / "scripts" / "winget" / "plan.ps1"
APPLY_PS1 = ADAPTER_ROOT / "scripts" / "winget" / "apply.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_plan_consults_apply_mark() -> None:
    """plan.ps1 must call Get-AscendoApplyMark when current is Unknown
    so that packages already marked as applied don't surface as planned.
    """
    text = _read(PLAN_PS1)
    assert "Get-AscendoApplyMark" in text, (
        "plan.ps1 must invoke Get-AscendoApplyMark to honour the "
        "Sesja 63 unknown-version apply-mark mechanism"
    )


def test_plan_skips_marked_packages_with_continue() -> None:
    """When mark.target == Available, plan must SKIP the package
    (continue), not emit it as planned.
    """
    text = _read(PLAN_PS1)
    # Look for the mark-target-matches-Available skip block. The
    # `continue` must come AFTER the mark check.
    block_match = re.search(
        r"Get-AscendoApplyMark.*?continue",
        text, re.DOTALL,
    )
    assert block_match, (
        "plan.ps1 must continue (skip emission) when "
        "mark.target == Available"
    )
    # Within the matched block, the comparison must be between
    # mark.target and the local $target variable (winget Available).
    block = block_match.group(0)
    assert "$mark.target" in block and "-eq $target" in block, (
        "plan.ps1's mark-check must compare $mark.target to $target"
    )


def test_apply_consults_apply_mark_before_invoking_winget() -> None:
    """apply.ps1 must consult Get-AscendoApplyMark BEFORE the skip-list
    check (5b) so that a marked package short-circuits to up_to_date
    without ever invoking winget upgrade.
    """
    text = _read(APPLY_PS1)
    assert "Get-AscendoApplyMark" in text, (
        "apply.ps1 must invoke Get-AscendoApplyMark"
    )
    # The mark check must appear BEFORE the skip-list check ("5b. Skip-list").
    mark_pos = text.find("Get-AscendoApplyMark")
    skip_pos = text.find("5b. Skip-list")
    assert mark_pos < skip_pos and mark_pos > 0, (
        f"apply-mark check ({mark_pos}) must appear before the skip-list "
        f"check ({skip_pos}) so marked packages short-circuit early"
    )


def test_apply_emits_up_to_date_for_marked_packages() -> None:
    """apply.ps1 must emit Status='up_to_date' for marked packages
    (NOT 'skipped' or 'success'). The point is that no work happened
    AND the registry already shows the marked version.
    """
    text = _read(APPLY_PS1)
    # Find the mark-handling block. It should emit Status = 'up_to_date'.
    block_match = re.search(
        r"Get-AscendoApplyMark[\s\S]*?Add-SidecarItem[\s\S]*?continue",
        text,
    )
    assert block_match, "Could not locate mark-handling block in apply.ps1"
    block = block_match.group(0)
    assert "Status" in block and "'up_to_date'" in block, (
        "apply.ps1's mark-handling block must emit Status='up_to_date'"
    )


def test_apply_mark_block_includes_current_version_from_mark() -> None:
    """The synthesized up_to_date item must surface the marked target
    as CurrentVersion so the SPA shows the user a sensible version
    (e.g. '1.0' instead of 'Unknown').
    """
    text = _read(APPLY_PS1)
    block_match = re.search(
        r"Get-AscendoApplyMark[\s\S]*?Add-SidecarItem[\s\S]*?continue",
        text,
    )
    assert block_match
    block = block_match.group(0)
    assert "CurrentVersion" in block and "$mark.target" in block, (
        "apply.ps1's mark-handling block must populate CurrentVersion "
        "from $mark.target"
    )
