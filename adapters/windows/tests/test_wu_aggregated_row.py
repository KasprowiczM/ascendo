"""Regression tests for Install-WindowsUpdateBatch's aggregated-row expansion.

## Bug (DP5520WMK run f3f9d20f, 2026-05-13 09:09 UTC)

PSWindowsUpdate's Install-WindowsUpdate (with -AcceptAll + -IgnoreReboot)
returned a single result row whose per-property values were parallel
Object[] arrays:

    KB             = [KB5087051, KB5089549]
    Title          = ["Cumulative Update ...", "Defender ..."]
    Result         = ["Installed", "Installed"]
    HResult        = [0, 0]
    RebootRequired = [True, True]

Both updates ACTUALLY installed (system asked for reboot afterwards),
but the dedup-by-KB loop in ``Install-WindowsUpdateBatch`` keyed on the
string-joined "KB5087051 KB5089549" and emitted ONE item with
``Result="Installed Installed"``. ``Convert-WUResultToItemStatus`` only
matches the anchored regex ``^Installed$``, so the joined string fell
to ``'failed'`` -- the apply phase reported ``items=1 failed=1
success=0`` for what was actually a successful 2-KB install.

## Fix

New helper ``Expand-WUAggregatedRow`` in AscendoPSWindowsUpdate.psm1:

  * Scans the row's properties for any ``System.Array`` value (treating
    strings as opaque scalars even though they're enumerable).
  * If found, fans the row out into N output rows by walking each array
    in parallel; scalar properties broadcast (same value on every output).
  * Returns ``,@($Row)`` unchanged when no fan-out is needed -- callers
    can iterate uniformly.

``Install-WindowsUpdateBatch`` calls the helper on every $raw row
BEFORE the dedup loop, so the rest of the pipeline sees proper per-KB
rows.

These tests are pure static analysis on the .psm1 source; they assert
the helper exists, is exported, is wired into the install-batch
foreach loop, and that the apply.ps1 ``$sidecar['needs_reboot']``
top-level flag is now set alongside the warn message.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PSM1 = (
    _REPO_ROOT
    / "adapters"
    / "windows"
    / "lib"
    / "AscendoPSWindowsUpdate.psm1"
)
_APPLY_PS1 = (
    _REPO_ROOT
    / "adapters"
    / "windows"
    / "scripts"
    / "windows_update"
    / "apply.ps1"
)


@pytest.fixture(scope="module")
def psm1_text() -> str:
    assert _PSM1.exists(), f"missing fixture: {_PSM1}"
    return _PSM1.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def apply_text() -> str:
    assert _APPLY_PS1.exists(), f"missing fixture: {_APPLY_PS1}"
    return _APPLY_PS1.read_text(encoding="utf-8")


def test_expand_helper_defined(psm1_text: str) -> None:
    """``Expand-WUAggregatedRow`` MUST exist as a function in the
    AscendoPSWindowsUpdate module, with the exact name (no rename
    or signature drift)."""
    assert re.search(
        r"^function\s+Expand-WUAggregatedRow\b", psm1_text, flags=re.MULTILINE
    ), (
        "Expand-WUAggregatedRow function not defined in "
        "AscendoPSWindowsUpdate.psm1. Without it the dedup loop in "
        "Install-WindowsUpdateBatch sees aggregated rows as single "
        "merged items and Convert-WUResultToItemStatus mis-classifies "
        "them as failed."
    )


def test_expand_helper_exported(psm1_text: str) -> None:
    """The helper must appear in the module's Export-ModuleMember list
    so callers loading via ``Import-Module`` see it. Without the export
    the wire-up call in Install-WindowsUpdateBatch would silently fail
    with 'unrecognized function' caught by the outer try/catch."""
    export_match = re.search(
        r"Export-ModuleMember\s+-Function\s+@\(([^)]*)\)",
        psm1_text,
        flags=re.DOTALL,
    )
    assert export_match, "Export-ModuleMember block not found"
    block = export_match.group(1)
    assert "Expand-WUAggregatedRow" in block, (
        "Expand-WUAggregatedRow must be in the module's export list "
        "so Import-Module callers see it."
    )


def test_install_batch_calls_expand_before_dedup(psm1_text: str) -> None:
    """The install-batch loop must invoke ``Expand-WUAggregatedRow``
    on every ``$raw`` row BEFORE the dedup-by-KB foreach. Calling AFTER
    dedup defeats the purpose -- the dedup will have already merged
    the aggregated row's parallel-array KBs into a single bogus key.
    """
    expand_idx = psm1_text.find("Expand-WUAggregatedRow -Row")
    dedup_idx = psm1_text.find("# Deduplicate by KB id;")
    assert expand_idx != -1, (
        "Install-WindowsUpdateBatch must call Expand-WUAggregatedRow "
        "on each $raw row before the dedup loop."
    )
    assert dedup_idx != -1, "Dedup comment marker missing in psm1"
    assert expand_idx < dedup_idx, (
        "Expand-WUAggregatedRow must run BEFORE the dedup foreach -- "
        "expanding after dedup would be a no-op (dedup already merged "
        "the aggregated row into one key)."
    )


def test_expand_helper_handles_scalar_rows(psm1_text: str) -> None:
    """The helper must return a 1-element array containing the original
    row when there's no fan-out needed. Callers iterate the result
    blindly; returning a bare scalar would break the foreach contract.
    """
    # The function should have a guard like `if ($maxLen -le 1 ...) { return ,@($Row) }`
    pattern = re.compile(
        r"if\s*\(\$maxLen\s+-le\s+1[^)]*\)\s*\{[^}]*return\s+,@\(\$Row\)",
        flags=re.DOTALL,
    )
    assert pattern.search(psm1_text), (
        "Expand-WUAggregatedRow must short-circuit non-aggregated rows "
        "by returning `,@($Row)` (1-element array containing the "
        "original row) so the calling foreach iterates uniformly."
    )


def test_expand_helper_treats_strings_as_scalars(psm1_text: str) -> None:
    """Strings ARE IEnumerable but must be treated as opaque scalars by
    the expand helper (otherwise every string property would fan out
    to one-row-per-character).
    """
    # Look for the explicit string skip in the array-detection loop.
    fn_match = re.search(
        r"function\s+Expand-WUAggregatedRow\s*\{(.*?)(?=\n\s*function\b|\Z)",
        psm1_text,
        flags=re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)
    assert re.search(r"\$v\s+-is\s+\[string\]", body), (
        "Expand-WUAggregatedRow must explicitly skip string values when "
        "detecting Object[] properties; strings are enumerable in PS "
        "but must be treated as opaque scalars to avoid character-wise "
        "fan-out."
    )


def test_apply_sets_needs_reboot_on_sidecar(apply_text: str) -> None:
    """When reboot is required, apply.ps1 must set the top-level
    ``sidecar['needs_reboot'] = $true`` flag (per ADR-0003 + Sesja 26
    schema move). Without this, downstream consumers reading the JSON
    see needs_reboot=False even though the warn message is present.
    """
    assert "$sidecar['needs_reboot'] = $true" in apply_text, (
        "apply.ps1 must set $sidecar['needs_reboot'] = $true when "
        "rebootRequired so the top-level JSON field matches the warn "
        "message. The orchestrator CLI banner picks up the message "
        "via text matching, but other consumers (dashboard, history "
        "DB) read the typed field."
    )
