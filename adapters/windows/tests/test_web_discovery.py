"""Static-analysis tests for the AscendoWebDiscovery.psm1 module.

Mirrors the test_sesja58_fixes.py pattern: read PowerShell source as text,
assert structural invariants that the Windows discovery path requires.

The module itself runs only on Windows (registry access), so behavioural
tests live in bin/validate-windows.ps1 — these tests guarantee the module
file exists, exports the right names, contains no PS5.1-breaking unicode,
and that scripts/web/check.ps1 wires the discovery in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_ROOT = REPO_ROOT / "adapters" / "windows"
DISCOVERY_PSM1 = ADAPTER_ROOT / "lib" / "AscendoWebDiscovery.psm1"
WEB_CHECK_PS1 = ADAPTER_ROOT / "scripts" / "web" / "check.ps1"

# Dangerous unicode punctuation that breaks PS5.1 parsing when files are
# read via CP1252 (Sesja-58 bug class).
DANGEROUS_CHARS = {
    "—": "em-dash -> '--'",
    "–": "en-dash -> '-'",
    "…": "ellipsis -> '...'",
    "‘": "left single quote -> ASCII apostrophe",
    "’": "right single quote -> ASCII apostrophe",
    "“": "left double quote -> ASCII double-quote",
    "”": "right double quote -> ASCII double-quote",
    " ": "non-breaking space -> regular space",
    "→": "right arrow -> '->'",
    "←": "left arrow -> '<-'",
}


# ── Module-file invariants ───────────────────────────────────────────


def test_discovery_module_exists() -> None:
    assert DISCOVERY_PSM1.is_file(), (
        f"AscendoWebDiscovery.psm1 missing at {DISCOVERY_PSM1}"
    )


def test_discovery_module_exports_invoke_function() -> None:
    text = DISCOVERY_PSM1.read_text(encoding="utf-8")
    # Function must be defined.
    assert re.search(
        r"function\s+Invoke-AscendoWebDiscovery\b",
        text,
    ), "Invoke-AscendoWebDiscovery function not defined"
    # Function must be exported via Export-ModuleMember (real .psm1 module,
    # not a transient .ps1, so the export line is required for the function
    # to be visible to importers).
    assert "Invoke-AscendoWebDiscovery" in text.split("Export-ModuleMember", 1)[-1], (
        "Invoke-AscendoWebDiscovery must appear in Export-ModuleMember block"
    )


def test_discovery_module_exports_clear_cache_helper() -> None:
    text = DISCOVERY_PSM1.read_text(encoding="utf-8")
    assert re.search(
        r"function\s+Clear-AscendoWebDiscoveryCache\b",
        text,
    ), "Clear-AscendoWebDiscoveryCache function not defined"
    assert "Clear-AscendoWebDiscoveryCache" in text.split("Export-ModuleMember", 1)[-1], (
        "Clear-AscendoWebDiscoveryCache must appear in Export-ModuleMember block"
    )


def test_discovery_module_is_strictmode_safe() -> None:
    """Every real .psm1 in the adapter sets `Set-StrictMode -Version Latest`
    near the top of the file so writers can't silently introduce missing-
    property bugs.
    """
    text = DISCOVERY_PSM1.read_text(encoding="utf-8")
    assert "Set-StrictMode -Version Latest" in text, (
        "AscendoWebDiscovery.psm1 must declare Set-StrictMode -Version Latest"
    )


def test_discovery_module_walks_three_arp_hives() -> None:
    """The Windows ARP roots split across HKLM (native + WOW6432Node) and
    HKCU. Discovery must scan all three; missing one would hide per-user
    installs (HKCU) or 32-bit installs on x64 (WOW6432Node).
    """
    text = DISCOVERY_PSM1.read_text(encoding="utf-8")
    required_roots = [
        "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
        "HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
        "HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
    ]
    for root in required_roots:
        assert root in text, f"AscendoWebDiscovery.psm1 missing registry root: {root}"


def test_discovery_module_has_no_dangerous_unicode() -> None:
    """Sesja-58 bug class: em-dash + ellipsis chars decode to closing-quote
    bytes under CP1252 on PS5.1, ending string literals early.
    """
    text = DISCOVERY_PSM1.read_text(encoding="utf-8")
    bad: list[tuple[str, str, int]] = []
    for ch, hint in DANGEROUS_CHARS.items():
        idx = text.find(ch)
        if idx >= 0:
            line_no = text[:idx].count("\n") + 1
            bad.append((ch, hint, line_no))
    assert not bad, (
        f"AscendoWebDiscovery.psm1 contains non-ASCII punctuation that "
        f"breaks PS5.1 parsing under CP1252:\n"
        + "\n".join(
            f"  U+{ord(ch):04X} on line {line}: {hint}"
            for ch, hint, line in bad
        )
    )


def test_discovery_module_excludes_system_components_by_default() -> None:
    """Default exclusion list must include the major Windows / Microsoft
    runtime patterns so a discovery run on a stock install doesn't surface
    fifty redistributables / KB rollups as third-party apps.
    """
    text = DISCOVERY_PSM1.read_text(encoding="utf-8")
    # Patterns we rely on for the Windows system-component filter.
    # Patterns appear in the .psm1 as regex literals (e.g. `C\+\+`), so the
    # raw string sometimes carries the regex escape. Match either form.
    must_have_patterns = [
        ("Windows",),
        ("Visual C++", r"Visual\s+C\+\+"),
        (".NET", r"\.NET"),
        ("KB",),
        ("Driver",),
    ]
    for variants in must_have_patterns:
        found = any((v in text) for v in variants)
        assert found, (
            f"AscendoWebDiscovery.psm1 default ineligibility list missing "
            f"reference to any of: {variants}"
        )


def test_discovery_module_honours_env_extension() -> None:
    """ASCENDO_WEB_INELIGIBLE_PATTERNS is documented as the operator's
    escape hatch; the module must read it at discovery time.
    """
    text = DISCOVERY_PSM1.read_text(encoding="utf-8")
    assert "ASCENDO_WEB_INELIGIBLE_PATTERNS" in text, (
        "Module must reference $env:ASCENDO_WEB_INELIGIBLE_PATTERNS so "
        "callers can extend the exclusion list at runtime"
    )


def test_discovery_module_filters_inno_update_bundles() -> None:
    """{GUID}_isN keys for N>=2 are Inno Setup update bundles, not the
    base app; emitting them double-counts every Inno-installed app.
    """
    text = DISCOVERY_PSM1.read_text(encoding="utf-8")
    # The regex distinguishing _is1 (base) from _is2+ (update) is the
    # critical piece. Without it the discovery surfaces N+1 rows per Inno
    # app where N = number of updates ever installed.
    assert "_is[2-9]" in text, (
        "Module must filter Inno-Setup update bundle keys "
        "({GUID}_is[2-9][0-9]*) so updates don't surface as separate apps"
    )


# ── check.ps1 wire-up ────────────────────────────────────────────────


def test_web_check_imports_discovery_module() -> None:
    text = WEB_CHECK_PS1.read_text(encoding="utf-8")
    assert "AscendoWebDiscovery.psm1" in text, (
        "scripts/web/check.ps1 must Import-Module AscendoWebDiscovery.psm1 "
        "to surface registry-discovered apps"
    )


def test_web_check_invokes_discovery() -> None:
    text = WEB_CHECK_PS1.read_text(encoding="utf-8")
    assert "Invoke-AscendoWebDiscovery" in text, (
        "scripts/web/check.ps1 must call Invoke-AscendoWebDiscovery"
    )


def test_web_check_honours_skip_discovery_env() -> None:
    """The test hook ASCENDO_WEB_SKIP_DISCOVERY allows validate-windows.ps1
    + the curated-loop regression tests to disable auto-discovery so the
    sidecar's item count stays deterministic.
    """
    text = WEB_CHECK_PS1.read_text(encoding="utf-8")
    assert "ASCENDO_WEB_SKIP_DISCOVERY" in text, (
        "scripts/web/check.ps1 must gate discovery on "
        "$env:ASCENDO_WEB_SKIP_DISCOVERY so callers can opt out"
    )


def test_web_check_emits_auto_id_prefix() -> None:
    """Auto-discovered items must use the `web:auto:<slug>` id prefix so the
    SPA can visually distinguish them from curated entries (web:<slug>).
    """
    text = WEB_CHECK_PS1.read_text(encoding="utf-8")
    assert "web:auto:" in text, (
        "scripts/web/check.ps1 must emit auto-discovered items with the "
        "'web:auto:<slug>' id prefix to distinguish them from curated rows"
    )


def test_web_check_tracks_curated_slugs_before_discovery() -> None:
    """Curated emission must happen BEFORE auto-discovery emission so the
    de-dup tracker (`$emittedSlugs`) is populated when discovery runs.
    """
    text = WEB_CHECK_PS1.read_text(encoding="utf-8")
    emitted_slugs_idx = text.find("$emittedSlugs")
    discovery_call_idx = text.find("Invoke-AscendoWebDiscovery")
    assert emitted_slugs_idx > -1, "must initialise $emittedSlugs tracker"
    assert discovery_call_idx > -1, "must call Invoke-AscendoWebDiscovery"
    assert emitted_slugs_idx < discovery_call_idx, (
        "$emittedSlugs tracker must be populated before discovery runs; "
        "otherwise discovery double-emits curated apps"
    )


def test_web_check_has_no_dangerous_unicode() -> None:
    """The Sesja-58 fix forbade dangerous unicode in scripts/web/check.ps1;
    this regression guards the new edits.
    """
    text = WEB_CHECK_PS1.read_text(encoding="utf-8")
    bad: list[tuple[str, str, int]] = []
    for ch, hint in DANGEROUS_CHARS.items():
        idx = text.find(ch)
        if idx >= 0:
            line_no = text[:idx].count("\n") + 1
            bad.append((ch, hint, line_no))
    assert not bad, (
        f"scripts/web/check.ps1 reintroduced non-ASCII punctuation that "
        f"breaks PS5.1 parsing under CP1252:\n"
        + "\n".join(
            f"  U+{ord(ch):04X} on line {line}: {hint}"
            for ch, hint, line in bad
        )
    )
