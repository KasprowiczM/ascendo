"""Static-analysis tests for Tier-A web apply (handlers + dispatcher).

These tests don't require pwsh -- they read the PowerShell sources and
assert the new functions exist and are wired correctly into the
apply.ps1 dispatcher. Mirrors the pattern in test_sesja58_fixes.py:
catch contract drift at unit-test time so the operator's first
real-Windows run doesn't trip over a missing symbol.

Tests for the new Pydantic schema fields live in
test_web_registry_tier_a.py.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# ── Paths ────────────────────────────────────────────────────────────

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
GH_HANDLER = ADAPTER_ROOT / "lib" / "handlers" / "github_release.ps1"
RF_HANDLER = ADAPTER_ROOT / "lib" / "handlers" / "release_feed.ps1"
BUILTIN_HANDLER = ADAPTER_ROOT / "lib" / "handlers" / "builtin.ps1"
APPLY_DISPATCHER = ADAPTER_ROOT / "scripts" / "web" / "apply.ps1"
ASCENDO_WEB_PSM1 = ADAPTER_ROOT / "lib" / "AscendoWeb.psm1"
WEB_REGISTRY_PY = ADAPTER_ROOT / "ascendo_windows" / "web_registry.py"


def _read(path: Path) -> str:
    assert path.exists(), f"expected file: {path}"
    return path.read_text(encoding="utf-8")


# ── New Tier-A functions exist ───────────────────────────────────────


def test_github_release_handler_defines_apply_real() -> None:
    """The new Tier-A apply function for github_release must exist."""
    src = _read(GH_HANDLER)
    assert re.search(r"function\s+Invoke-GitHubReleaseApplyReal\s*\{", src), (
        "Invoke-GitHubReleaseApplyReal function not found in github_release.ps1"
    )


def test_release_feed_handler_defines_apply_real() -> None:
    """The new Tier-A apply function for release_feed must exist."""
    src = _read(RF_HANDLER)
    assert re.search(r"function\s+Invoke-ReleaseFeedApplyReal\s*\{", src), (
        "Invoke-ReleaseFeedApplyReal function not found in release_feed.ps1"
    )


def test_github_release_handler_still_defines_tier_b_apply() -> None:
    """Tier-B trigger-only apply must remain (apps without tier_a_apply
    still use it; tests + tier_a_apply=false operator setups depend on it)."""
    src = _read(GH_HANDLER)
    assert re.search(r"function\s+Invoke-GitHubReleaseApply\s*\{", src), (
        "Tier-B Invoke-GitHubReleaseApply removed; this breaks operators "
        "who haven't opted into Tier-A. Add it back."
    )


def test_release_feed_handler_still_defines_tier_b_apply() -> None:
    src = _read(RF_HANDLER)
    assert re.search(r"function\s+Invoke-ReleaseFeedApply\s*\{", src), (
        "Tier-B Invoke-ReleaseFeedApply removed; this breaks operators "
        "who haven't opted into Tier-A. Add it back."
    )


# ── Shared installer helpers (live in github_release.ps1, used by both) ──


def test_github_release_handler_defines_download_helper() -> None:
    src = _read(GH_HANDLER)
    assert re.search(r"function\s+Invoke-WebInstallerDownload\s*\{", src), (
        "Invoke-WebInstallerDownload helper not found; release_feed Tier-A "
        "depends on it being defined alongside the github_release functions."
    )


def test_github_release_handler_defines_verify_helper() -> None:
    src = _read(GH_HANDLER)
    assert re.search(r"function\s+Invoke-WebInstallerVerify\s*\{", src)
    # Must call Get-AuthenticodeSignature -- that's the whole point of T3.
    assert "Get-AuthenticodeSignature" in src, (
        "Invoke-WebInstallerVerify must call Get-AuthenticodeSignature"
    )


def test_github_release_handler_defines_run_helper() -> None:
    src = _read(GH_HANDLER)
    assert re.search(r"function\s+Invoke-WebInstallerRun\s*\{", src)
    # Must support msiexec /qn /norestart for .msi path.
    assert "msiexec" in src.lower(), (
        "Invoke-WebInstallerRun must support .msi via msiexec"
    )
    # Must default to NSIS-style /S for .exe path.
    assert "'/S'" in src or '"/S"' in src, (
        "Invoke-WebInstallerRun must default to NSIS-style /S for .exe"
    )


def test_github_release_handler_defines_readback_helper() -> None:
    src = _read(GH_HANDLER)
    assert re.search(r"function\s+Get-WebReinstalledVersion\s*\{", src), (
        "Get-WebReinstalledVersion helper not found; required for post-install "
        "DisplayVersion readback from the Uninstall registry"
    )


# ── Dispatcher wiring ────────────────────────────────────────────────


def test_dispatcher_references_github_release_apply_real() -> None:
    """The dispatcher must invoke the new Tier-A function for github_release."""
    src = _read(APPLY_DISPATCHER)
    assert "Invoke-GitHubReleaseApplyReal" in src, (
        "scripts/web/apply.ps1 does not reference Invoke-GitHubReleaseApplyReal; "
        "Tier-A apply will silently fall through to Tier-B"
    )


def test_dispatcher_references_release_feed_apply_real() -> None:
    src = _read(APPLY_DISPATCHER)
    assert "Invoke-ReleaseFeedApplyReal" in src, (
        "scripts/web/apply.ps1 does not reference Invoke-ReleaseFeedApplyReal; "
        "Tier-A apply will silently fall through to Tier-B"
    )


def test_dispatcher_checks_tier_a_apply_flag() -> None:
    """The dispatcher must gate on the per-app tier_a_apply field."""
    src = _read(APPLY_DISPATCHER)
    assert "tier_a_apply" in src, (
        "scripts/web/apply.ps1 must read the tier_a_apply field on each app "
        "to decide Tier-A vs Tier-B"
    )


def test_dispatcher_preserves_tier_b_path() -> None:
    """Both Tier-B trigger functions must still be referenced (default path)."""
    src = _read(APPLY_DISPATCHER)
    assert "Invoke-GitHubReleaseApply" in src  # Either Real or non-Real matches
    assert "Invoke-ReleaseFeedApply" in src
    assert "Invoke-BuiltinApply" in src, (
        "Builtin Tier-B handler must still be wired into the dispatcher"
    )


def test_dispatcher_handles_uac_cancelled_as_skipped() -> None:
    """When the operator cancels the UAC prompt the item should land as
    'skipped' not 'failed' -- it's a user gesture, not an error."""
    src = _read(APPLY_DISPATCHER)
    # Either explicit 'skipped' status mapping or UacCancelled handling.
    assert "UacCancelled" in src or "uac_cancelled" in src.lower(), (
        "Dispatcher must distinguish UAC cancellation from real install "
        "failure (map to status='skipped', not status='failed')"
    )


# ── Handler PowerShell hygiene (Sesja 58 lessons) ────────────────────


def test_handlers_have_no_export_module_member() -> None:
    """Per Sesja 58 lessons: lib/handlers/*.ps1 are dot-sourced as transient
    modules; Export-ModuleMember errors on PS5.1 in that context.

    We strip comment lines (anything starting with whitespace + #) before
    checking so the warning comment in the trailer doesn't trigger a
    false positive.
    """
    for path in (GH_HANDLER, RF_HANDLER, BUILTIN_HANDLER):
        src = _read(path)
        # Strip comment lines so the doc trailer doesn't false-positive.
        code_lines = [
            line for line in src.splitlines()
            if not line.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert "Export-ModuleMember" not in code, (
            f"{path.name} has an active Export-ModuleMember call (Sesja 58 "
            "forbids it in lib/handlers/*.ps1 -- breaks PS5.1 transient-module "
            "load). Comments mentioning the term are OK."
        )


def test_handlers_are_ascii_only() -> None:
    """Per Sesja 58 lessons: em-dashes (U+2014) and other non-ASCII chars
    break CP1252 parsing on PS 5.1 when .ps1 has no BOM."""
    for path in (GH_HANDLER, RF_HANDLER):
        raw = path.read_bytes()
        # Strip BOM if present, then scan for non-ASCII.
        body = raw
        if body.startswith(b"\xef\xbb\xbf"):
            body = body[3:]
        non_ascii = [b for b in body if b > 0x7F]
        assert not non_ascii, (
            f"{path.name} contains non-ASCII bytes; first offset "
            f"{body.index(bytes([non_ascii[0]]))}. PS5.1 + CP1252 misreads "
            f"these and breaks string parsing (Sesja 58)."
        )


# ── Apply dispatcher imports both handlers (Tier-A helpers are shared) ──


def test_dispatcher_imports_github_release_handler_first() -> None:
    """github_release.ps1 defines the shared Invoke-WebInstaller* helpers
    that release_feed.ps1's ApplyReal uses. apply.ps1 must import
    github_release.ps1 BEFORE release_feed.ps1 so the helpers are in
    scope when ReleaseFeedApplyReal runs."""
    src = _read(APPLY_DISPATCHER)
    gh_idx = src.find("github_release.ps1")
    rf_idx = src.find("release_feed.ps1")
    assert gh_idx >= 0 and rf_idx >= 0, (
        "apply.ps1 must Import both github_release.ps1 and release_feed.ps1"
    )
    assert gh_idx < rf_idx, (
        "apply.ps1 must Import github_release.ps1 BEFORE release_feed.ps1 "
        "so the shared Invoke-WebInstaller* helpers are in scope before "
        "Invoke-ReleaseFeedApplyReal runs"
    )


# ── Schema parses + new fields are accessible ────────────────────────


def test_web_registry_module_parses() -> None:
    """The Pydantic schema module must compile -- catches accidental
    syntax errors from the Tier-A field additions."""
    import py_compile
    py_compile.compile(str(WEB_REGISTRY_PY), doraise=True)


def test_web_registry_exports_tier_a_field() -> None:
    """WebAppV1 must expose a tier_a_apply field at the model level."""
    from ascendo_windows.web_registry import WebAppV1
    assert "tier_a_apply" in WebAppV1.model_fields, (
        "WebAppV1.tier_a_apply field not declared on the Pydantic model"
    )
    field = WebAppV1.model_fields["tier_a_apply"]
    # Default must be False so existing operators don't opt in by accident.
    assert field.default is False, (
        "WebAppV1.tier_a_apply must default to False (preserve Tier-B "
        "behaviour for apps that haven't opted in)"
    )
