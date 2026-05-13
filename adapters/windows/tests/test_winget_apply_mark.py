"""Static-analysis regression tests for the unknown-version apply-mark.

Operator observation on DP5520WMK (2026-05-13 14:42 UTC):

  "img to iso always reports unknown version, fix it, even after update"

Root cause: ``winget list --id SoftSea.IMGtoISO`` returns
``Version=Unknown`` BOTH before and after the operator runs
``winget upgrade --id SoftSea.IMGtoISO``. The Inno Setup uninstaller
writes the registry key (``{GUID}_is1``) with DisplayName but NO
DisplayVersion field, so neither winget nor the ARP-registry fallback
can determine the installed version. Without a state marker, every
check phase classifies the package as ``planned`` (Available='1.0'
differs from current='Unknown'), every apply phase re-runs the
installer, and the inventory.db row stays stuck at
``cur=Unknown, status=outdated`` forever.

Fix shape (Sesja 63):

  * ``AscendoWinget.psm1`` gains two new exported functions:
      Get-AscendoApplyMark -Id <id>            -> [pscustomobject] or $null
      Set-AscendoApplyMark -Id <id> -Target <v>
    Both persist to ``$env:ASCENDO_STATE_DIR/winget_apply_marks.json``
    defaulting to ``$env:USERPROFILE/.ascendo/state/winget_apply_marks.json``.

  * ``scripts/winget/apply.ps1`` writes the mark on a successful
    upgrade when the pre-install reading was Unknown / blank.

  * ``scripts/winget/check.ps1`` reads the mark when current is
    Unknown / blank. If Available == mark.target, classifies the
    row as ``up_to_date`` and surfaces the marked version as
    installed.

End-to-end verification: pre-populating the mark for SoftSea.IMGtoISO
at target=1.0 then running check.ps1 reported the row as
``status='up_to_date' cur='1.0' tgt='1.0'`` (was previously
``status='planned' cur='Unknown' tgt='1.0'``).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


ADAPTER_ROOT = Path(__file__).resolve().parents[1]
ASCWINGET = ADAPTER_ROOT / "lib" / "AscendoWinget.psm1"
CHECK_PS1 = ADAPTER_ROOT / "scripts" / "winget" / "check.ps1"
APPLY_PS1 = ADAPTER_ROOT / "scripts" / "winget" / "apply.ps1"


def test_helpers_defined_in_ascendowinget_psm1() -> None:
    """Both Get-AscendoApplyMark and Set-AscendoApplyMark must be
    defined and exported from the winget helper module.
    """
    text = ASCWINGET.read_text(encoding="utf-8")
    assert "function Get-AscendoApplyMark" in text
    assert "function Set-AscendoApplyMark" in text
    assert "'Get-AscendoApplyMark'" in text, (
        "Get-AscendoApplyMark must be in the Export-ModuleMember list"
    )
    assert "'Set-AscendoApplyMark'" in text


def test_state_file_path_honours_env_override() -> None:
    """The state-file path resolver must honour ``ASCENDO_STATE_DIR``
    (for tests + non-standard installs) AND default to
    ``USERPROFILE/.ascendo/state/`` when not set.
    """
    text = ASCWINGET.read_text(encoding="utf-8")
    assert "ASCENDO_STATE_DIR" in text
    assert "winget_apply_marks.json" in text
    assert ".ascendo\\state" in text or ".ascendo/state" in text


def test_set_apply_mark_refuses_unknown_target() -> None:
    """``Set-AscendoApplyMark`` MUST refuse to write ``Target='Unknown'``
    or empty string — marking with Unknown would defeat the purpose
    (the whole point is that Unknown is what we're suppressing).
    """
    text = ASCWINGET.read_text(encoding="utf-8")
    block = text.split("function Set-AscendoApplyMark", 1)[1].split("function ", 1)[0]
    # Must reject empty / Unknown explicitly
    assert "IsNullOrWhiteSpace($Target)" in block, (
        "Set-AscendoApplyMark must reject empty Target"
    )
    assert "$Target -eq 'Unknown'" in block, (
        "Set-AscendoApplyMark must reject Target='Unknown' (marking "
        "with Unknown is meaningless — that's what we're suppressing)"
    )


def test_check_consults_mark_for_unknown_version_packages() -> None:
    """check.ps1 must call Get-AscendoApplyMark in its upgradable loop
    so packages with current=Unknown but Available=X get checked
    against the persisted mark.
    """
    text = CHECK_PS1.read_text(encoding="utf-8")
    assert "Get-AscendoApplyMark" in text, (
        "check.ps1 must call Get-AscendoApplyMark for the unknown-"
        "version suppression to take effect"
    )
    # The lookup must be GATED on current being null/Unknown so we
    # don't override a legitimate registry-supplied version.
    assert re.search(
        r"IsNullOrWhiteSpace\(\$current\).*Unknown.*Get-AscendoApplyMark",
        text,
        re.DOTALL,
    ), (
        "check.ps1 must gate the Get-AscendoApplyMark call on current "
        "being Unknown/blank (we trust the registry first)"
    )


def test_check_flips_to_up_to_date_when_mark_matches_available() -> None:
    """When mark.target == Available, the row must be classified as
    ``up_to_date`` (not the default ``planned``).
    """
    text = CHECK_PS1.read_text(encoding="utf-8")
    block = text.split("Unknown-version suppression", 1)[1].split("# ─", 1)[0]
    assert "$target -eq $current" in block, (
        "When mark.target equals Available (which becomes $target), "
        "the row must be up_to_date — block must compare them."
    )
    assert "$status = 'up_to_date'" in block, (
        "Must flip status to up_to_date on mark/Available match"
    )


def test_apply_writes_mark_on_success_when_current_was_unknown() -> None:
    """apply.ps1 must call Set-AscendoApplyMark after a successful
    upgrade WHEN the pre-install current was Unknown or empty.

    The "needs mark" gate is important: don't mark every successful
    upgrade — only the ones where future check phases would
    otherwise re-plan the package.
    """
    text = APPLY_PS1.read_text(encoding="utf-8")
    assert "Set-AscendoApplyMark" in text, (
        "apply.ps1 must call Set-AscendoApplyMark on successful upgrades"
    )
    # The call must be gated on itemStatus=success.
    assert re.search(
        r"\$itemStatus\s+-eq\s+'success'.*Set-AscendoApplyMark",
        text,
        re.DOTALL,
    ), "Set-AscendoApplyMark must be gated on itemStatus='success'"
    # And on current being Unknown / blank (don't pollute state for
    # packages with normal version reporting).
    assert re.search(
        r"(-not \$current|\$current\s+-eq\s+'Unknown')",
        text,
    ), (
        "apply.ps1 must gate the mark-write on pre-install current "
        "being Unknown / blank so we don't pollute state with "
        "marks for packages that already self-report"
    )


def test_apply_swallows_set_mark_errors() -> None:
    """A failure to write the state file must NOT abort a successful
    apply. State persistence is best-effort.
    """
    text = APPLY_PS1.read_text(encoding="utf-8")
    block = text.split("Set-AscendoApplyMark", 1)[1].split("Add-SidecarItem", 1)[0]
    assert "try" in block.lower() and "catch" in block.lower(), (
        "Set-AscendoApplyMark call must be wrapped in try/catch so "
        "a state-file write failure doesn't crash the apply phase"
    )


def test_module_exports_both_apply_mark_functions() -> None:
    """Pin the Export-ModuleMember list so neither function silently
    disappears in a future refactor.
    """
    text = ASCWINGET.read_text(encoding="utf-8")
    # The exact Export-ModuleMember block should contain both names.
    export_block = text.split("Export-ModuleMember -Function", 1)[1].split(")", 1)[0]
    assert "'Get-AscendoApplyMark'" in export_block
    assert "'Set-AscendoApplyMark'" in export_block
