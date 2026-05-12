"""Static-analysis tests verifying the PowerShell-side bufdir wiring.

These tests don't spawn pwsh — they read the .psm1 / .ps1 source files
and grep for the expected param declarations + invocations. That gives
the contract a fast, cross-platform regression check without needing
PowerShell installed on the CI host.

Companion to test_salvage.py (which covers the Python helpers + the
WingetManager integration end-to-end with a mocked subprocess).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LIB_DIR = _REPO_ROOT / "adapters" / "windows" / "lib"
_SCRIPTS_DIR = _REPO_ROOT / "adapters" / "windows" / "scripts"


def _read(path: Path) -> str:
    assert path.exists(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


def test_ascendojson_psm1_supports_bufdir_param() -> None:
    """AscendoJson.psm1 exposes -BufDir on New-Sidecar."""
    text = _read(_LIB_DIR / "AscendoJson.psm1")

    # Locate the New-Sidecar function body and confirm the BufDir
    # parameter is declared in its param block.
    fn = re.search(
        r"function\s+New-Sidecar\s*\{(.*?)^\}",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert fn is not None, "New-Sidecar function not found in AscendoJson.psm1"
    body = fn.group(1)
    assert re.search(
        r"\[Parameter\(\)\]\s*\[string\]\s*\$BufDir",
        body,
    ), "New-Sidecar must declare [Parameter()] [string] $BufDir"


def test_ascendojson_psm1_appends_items_to_bufdir() -> None:
    """Add-SidecarItem appends to items.jsonl via Append-AscendoJsonLine."""
    text = _read(_LIB_DIR / "AscendoJson.psm1")
    # Add-SidecarItem must reference Append-AscendoJsonLine with the
    # items.jsonl filename.
    assert "Append-AscendoJsonLine" in text, (
        "Append-AscendoJsonLine helper missing from AscendoJson.psm1"
    )
    fn = re.search(
        r"function\s+Add-SidecarItem\s*\{(.*?)^\}",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert fn is not None, "Add-SidecarItem function not found"
    body = fn.group(1)
    assert "items.jsonl" in body, (
        "Add-SidecarItem must append to items.jsonl"
    )
    assert "Append-AscendoJsonLine" in body, (
        "Add-SidecarItem must call Append-AscendoJsonLine"
    )


def test_ascendojson_psm1_appends_messages_to_bufdir() -> None:
    """Add-SidecarMessage appends to messages.jsonl via Append-AscendoJsonLine."""
    text = _read(_LIB_DIR / "AscendoJson.psm1")
    fn = re.search(
        r"function\s+Add-SidecarMessage\s*\{(.*?)^\}",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert fn is not None, "Add-SidecarMessage function not found"
    body = fn.group(1)
    assert "messages.jsonl" in body, (
        "Add-SidecarMessage must append to messages.jsonl"
    )
    assert "Append-AscendoJsonLine" in body, (
        "Add-SidecarMessage must call Append-AscendoJsonLine"
    )


def test_ascendojson_psm1_save_sidecar_cleans_bufdir() -> None:
    """Save-Sidecar removes the bufdir on success (unless KEEP env var)."""
    text = _read(_LIB_DIR / "AscendoJson.psm1")
    fn = re.search(
        r"function\s+Save-Sidecar\s*\{(.*?)^\}",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert fn is not None, "Save-Sidecar function not found"
    body = fn.group(1)
    assert "ASCENDO_KEEP_BUFDIR" in body, (
        "Save-Sidecar must honour $env:ASCENDO_KEEP_BUFDIR=1 escape hatch"
    )
    assert "Remove-Item" in body and "-Recurse" in body, (
        "Save-Sidecar must recursively remove the bufdir after success"
    )


def test_winget_check_passes_bufdir() -> None:
    """winget/check.ps1 declares -BufDir and forwards it to New-Sidecar."""
    text = _read(_SCRIPTS_DIR / "winget" / "check.ps1")
    # Param block has $BufDir.
    assert re.search(
        r"\[Parameter\(\)\]\s*\[string\]\s*\$BufDir",
        text,
    ), "winget/check.ps1 must declare [Parameter()] [string] $BufDir"
    # New-Sidecar is invoked with the BufDir key in its splat.
    assert "newSidecarArgs['BufDir']" in text or "newSidecarArgs.BufDir" in text, (
        "winget/check.ps1 must forward $BufDir into New-Sidecar's splat"
    )
