"""Regression tests for the sibling-sidecar lookup helper + post-apply
ResolvedVersion plumbing.

Operator observation on DP5520WMK (run 91769201, 2026-05-13 12:36 UTC):
After a successful Tier-A apply that upgraded OpenCode 1.14.33 ->
1.14.48, the inventory.db kept showing the old version as outdated.
Two root causes:

  1. ``apply.ps1`` (web Tier-A branch) set ``CurrentVersion`` to the
     pre-install reading and ``TargetVersion`` to the post-install
     readback, but DID NOT set ``ResolvedVersion``. The orchestrator's
     post-run inventory flush reads ``resolved_version`` when status=
     success to update the ``installed`` column; without it, the row
     stays at the pre-install value.

  2. ``verify.ps1`` (winget / npm / pip / windows_update / web) all
     looked for the sibling apply sidecar at
     ``<OutputDir>/<RunId>/apply__<cat>.json``. Each phase script
     runs in its OWN ``tempfile.TemporaryDirectory`` (per-phase
     ``ascendo-<cat>-XXX/``), so the apply sidecar isn't co-located
     in the verify phase's tempdir -- it lives in the canonical
     ``~/.ascendo/runs/<RunId>/`` location. Every verify reported
     "No apply sidecar found; verify is a no-op" despite a real
     apply having run.

Fixes pinned by these tests:

  * New ``Find-AscendoSiblingSidecar`` helper in AscendoJson.psm1
    tries the per-phase tempdir first, falls back to
    ``~/.ascendo/runs/<RunId>/`` (or ``$env:ASCENDO_RUNS_DIR`` when
    set). Returns the resolved absolute path or $null.

  * Each verify script uses the helper, with a legacy-path fallback
    for the "no sidecar found at <path>" log message.

  * Web apply.ps1 Tier-A branch now sets
    ``ResolvedVersion = result.InstalledVersion`` on success so the
    post-flush correctly updates inventory.db.

  * windows_update apply.ps1 sets ``ResolvedVersion = $kb`` on
    success so KBs don't stay flagged as planned in inventory.db.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


ADAPTER_ROOT = Path(__file__).resolve().parents[1]
ASCJSON_PSM1 = ADAPTER_ROOT / "lib" / "AscendoJson.psm1"

VERIFY_SCRIPTS = {
    "winget":          ADAPTER_ROOT / "scripts" / "winget" / "verify.ps1",
    "npm":             ADAPTER_ROOT / "scripts" / "npm" / "verify.ps1",
    "pip":             ADAPTER_ROOT / "scripts" / "pip" / "verify.ps1",
    "windows_update":  ADAPTER_ROOT / "scripts" / "windows_update" / "verify.ps1",
    "web":             ADAPTER_ROOT / "scripts" / "web" / "verify.ps1",
}

APPLY_WEB = ADAPTER_ROOT / "scripts" / "web" / "apply.ps1"
APPLY_WU = ADAPTER_ROOT / "scripts" / "windows_update" / "apply.ps1"


def test_helper_exists_in_ascendojson_psm1() -> None:
    """``Find-AscendoSiblingSidecar`` must be defined + exported."""
    text = ASCJSON_PSM1.read_text(encoding="utf-8")
    assert "function Find-AscendoSiblingSidecar" in text, (
        "Helper must be defined in AscendoJson.psm1"
    )
    assert "'Find-AscendoSiblingSidecar'" in text, (
        "Helper must appear in the Export-ModuleMember list"
    )


def test_helper_tries_canonical_run_dir_fallback() -> None:
    """The helper must fall back to ``~/.ascendo/runs/`` when the
    per-phase tempdir doesn't have the sidecar.
    """
    text = ASCJSON_PSM1.read_text(encoding="utf-8")
    assert "ASCENDO_RUNS_DIR" in text, (
        "Helper must honour the ASCENDO_RUNS_DIR env-var override"
    )
    assert ".ascendo\\runs" in text or ".ascendo/runs" in text, (
        "Helper must default to ~/.ascendo/runs/"
    )


@pytest.mark.parametrize("category", list(VERIFY_SCRIPTS.keys()))
def test_verify_script_uses_helper(category: str) -> None:
    """Each verify.ps1 must call ``Find-AscendoSiblingSidecar``
    instead of (or in addition to) hard-coding the per-phase tempdir
    path. Without this, verify is a permanent no-op.
    """
    text = VERIFY_SCRIPTS[category].read_text(encoding="utf-8")
    assert "Find-AscendoSiblingSidecar" in text, (
        f"{category}/verify.ps1 must call Find-AscendoSiblingSidecar "
        f"so it can locate the apply sidecar across the per-phase "
        f"tempdir / canonical-run-dir layouts."
    )


def test_web_apply_sets_resolved_version_on_tier_a_success() -> None:
    """Web Tier-A apply must set ResolvedVersion on success items so
    the post-flush updates inventory.db with the new installed
    version. Operator observation 2026-05-13: OpenCode upgrade
    succeeded but DB kept showing 1.14.33 because resolved_version
    was null in apply sidecar items.
    """
    text = APPLY_WEB.read_text(encoding="utf-8")
    # The Tier-A branch must mention ResolvedVersion.
    assert "ResolvedVersion" in text, (
        "web/apply.ps1 must set ResolvedVersion in the Tier-A branch"
    )
    # Specifically: assigned from result.InstalledVersion on success.
    assert re.search(
        r"ResolvedVersion.+InstalledVersion|InstalledVersion.+ResolvedVersion",
        text,
        re.DOTALL,
    ), (
        "ResolvedVersion must be sourced from result.InstalledVersion "
        "(the post-install registry readback)."
    )


def test_windows_update_apply_sets_resolved_version_to_kb_id() -> None:
    """For successfully-installed Windows updates, ResolvedVersion
    must surface the KB id so the post-flush can mark the row
    'up_to_date' in inventory.db instead of leaving it 'planned'.
    """
    text = APPLY_WU.read_text(encoding="utf-8")
    assert "ResolvedVersion" in text, (
        "windows_update/apply.ps1 must set ResolvedVersion on success"
    )
    # ResolvedVersion = $kb (the KB id is the canonical version marker)
    assert re.search(r"ResolvedVersion[\s'\"=]*\]?\s*=\s*\$kb", text), (
        "ResolvedVersion should be set to the KB id (\\$kb)"
    )


def test_helper_signature_minimal_args() -> None:
    """Pin the helper signature so verify scripts don't break on
    a future param rename.
    """
    text = ASCJSON_PSM1.read_text(encoding="utf-8")
    fn_block = text.split("function Find-AscendoSiblingSidecar", 1)[1].split("function ", 1)[0]
    for required in ("OutputDir", "RunId", "Filename"):
        assert f"[string] ${required}" in fn_block, (
            f"Find-AscendoSiblingSidecar param ${required} must remain "
            f"in the signature."
        )
