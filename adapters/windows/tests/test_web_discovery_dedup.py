"""Regression tests for the web-discovery dedup against winget (Sesja 65).

Operator observation on DP5520WMK (2026-05-13):
``ascendo build-inventory`` correctly reported 221 winget apps + 108
``web`` apps. Of the 108 web apps, **31 were duplicates** of apps
that winget already manages (7-Zip, VLC, WinRAR, KeePassXC, Foxit
PDF Reader, CCleaner, etc.). winget already updates those apps
silently via ``winget upgrade --silent`` -- showing them under
``web:auto:*`` was misleading the operator into thinking the apps
weren't managed.

Root cause: ``_Get-AscendoWingetIds`` populated the ownership cache
keyed on winget's **PackageId** (e.g., ``7zip.7zip``), but
``_owned_by`` looked up by the registry **sub-key name** (e.g.,
``7-Zip``). Those never matched, so discovery wrongly emitted
``web:auto:7-zip-26-01-x64-edition`` alongside winget's ``7-Zip
26.01 (x64 edition)`` row.

Sesja 65 fix:
  1. ``_Get-AscendoWingetIds`` now ALSO populates a by-name cache
     (``$_AscendoWingetNameCache`` and the msstore equivalent),
     keyed on the DisplayName lowercased.
  2. ARP-style rows (Source='') -- the most common case for apps the
     user installed manually but that winget can still upgrade --
     get tracked in the by-name cache too, even though they don't
     populate the by-Id cache.
  3. The eligibility check at the registry walk falls back to a
     DisplayName lookup against the by-name cache when the sub-key
     match misses.

Result on DP5520WMK: web auto-discovery emits 28 apps (was 108).
The deduped 80 were all winget-managed -- the operator now sees
clear category boundaries without the duplicate confusion.
"""
from __future__ import annotations

import re
from pathlib import Path


ADAPTER_ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_PSM1 = ADAPTER_ROOT / "lib" / "AscendoWebDiscovery.psm1"


def _read() -> str:
    return DISCOVERY_PSM1.read_text(encoding="utf-8")


def test_by_name_cache_populated_for_winget_source() -> None:
    """``_Get-AscendoWingetIds`` must populate the by-name cache for
    winget-source rows in addition to the by-id cache.
    """
    text = _read()
    block = (
        text.split("function _Get-AscendoWingetIds", 1)[1]
            .split("function ", 1)[0]
    )
    assert "$script:_AscendoWingetNameCache" in block, (
        "_Get-AscendoWingetIds must declare and populate the by-name "
        "cache so ownership lookups can fall back to DisplayName"
    )
    # The cache must be populated in the winget-source branch.
    assert re.search(
        r"sourceLower\s+-eq\s+'winget'.*nameKey",
        block, re.DOTALL,
    ), "winget-source rows must add to the by-name cache"


def test_by_name_cache_populated_for_arp_rows() -> None:
    """ARP-style rows (Source='', Id starts with ``ARP\\...``) are the
    typical case for apps the user installed manually but that winget
    can still upgrade. They MUST land in the winget by-name cache so
    the registry walker can dedup them against the user's `winget`
    category rows.
    """
    text = _read()
    block = (
        text.split("function _Get-AscendoWingetIds", 1)[1]
            .split("function ", 1)[0]
    )
    # The ARP-style else branch starts AFTER the winget elseif arm.
    # Anchor on the elseif so we skip the unrelated `} else { @{} }`
    # expression earlier in the function.
    after_elseif = block.split("elseif ($sourceLower -eq 'winget')", 1)[1]
    else_branch = after_elseif.split("} else {", 1)[1].split("\n        }", 1)[0]
    assert "wingetByName" in block, (
        "_Get-AscendoWingetIds must expose a wingetByName entry in its "
        "return hashtable"
    )
    assert "_AscendoWingetNameCache" in else_branch, (
        "ARP-style rows must populate _AscendoWingetNameCache so "
        "discovery can dedup them"
    )


def test_get_winget_ids_returns_by_name_indices() -> None:
    """The hashtable returned by ``_Get-AscendoWingetIds`` must include
    ``wingetByName`` + ``msstoreByName`` keys so the caller can do
    DisplayName lookups.
    """
    text = _read()
    # Look for the return statements; all must include the new keys.
    fn = (
        text.split("function _Get-AscendoWingetIds", 1)[1]
            .split("function ", 1)[0]
    )
    return_count = fn.count("wingetByName")
    assert return_count >= 4, (
        f"Function should expose wingetByName in every return path "
        f"(found {return_count}); audit early-exits + the final return"
    )
    assert "msstoreByName" in fn


def test_eligibility_check_falls_back_to_by_name() -> None:
    """The eligibility check in the registry walker must fall back to
    a DisplayName lookup against the by-name cache when the sub-key
    name lookup misses.
    """
    text = _read()
    # Block 4 in the walker is the "Owned-by-winget / -msstore" check.
    eligibility_block = text.split(
        "# 4. Owned-by-winget / -msstore", 1
    )[1].split("# 5.", 1)[0]
    assert "displayNameLower" in eligibility_block, (
        "Eligibility check must compute lowercased DisplayName"
    )
    assert "wingetByName" in eligibility_block, (
        "Eligibility check must consult wingetByName cache"
    )
    # The fall-back must be an OR, not a replacement (sub-key match
    # remains the primary check; by-name is fallback).
    assert re.search(
        r"owns\.winget\.ContainsKey.*-or.*wingetByName\.ContainsKey",
        eligibility_block, re.DOTALL,
    ), (
        "Sub-key match must remain the primary check; DisplayName is "
        "a fallback OR'd in"
    )


def test_clear_cache_resets_by_name_caches_too() -> None:
    """``Clear-AscendoWebDiscoveryCache`` must reset the new by-name
    caches alongside the existing by-id ones so tests can guarantee a
    clean state.
    """
    text = _read()
    fn = (
        text.split("function Clear-AscendoWebDiscoveryCache", 1)[1]
            .split("function ", 1)[0]
    )
    assert "_AscendoWingetNameCache" in fn, (
        "Clear-AscendoWebDiscoveryCache must reset _AscendoWingetNameCache"
    )
    assert "_AscendoMsstoreNameCache" in fn


def test_owns_hashtable_safe_access_for_legacy_callers() -> None:
    """The eligibility check must use ``ContainsKey`` (not direct
    indexing) when reading the new by-name fields from ``$owns`` so
    older test fixtures that pre-populate ``$owns`` without the new
    keys don't trip a missing-key exception.
    """
    text = _read()
    eligibility_block = text.split(
        "# 4. Owned-by-winget / -msstore", 1
    )[1].split("# 5.", 1)[0]
    assert "owns.ContainsKey('wingetByName')" in eligibility_block, (
        "Use ContainsKey for defensive access to the new key"
    )
    assert "owns.ContainsKey('msstoreByName')" in eligibility_block
