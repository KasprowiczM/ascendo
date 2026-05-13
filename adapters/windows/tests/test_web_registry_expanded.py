"""Regression tests for the Sesja 60 web registry expansion.

Operator observation on DP5520WMK (2026-05-13 after Sesja 59 fixes):
``ascendo build-inventory`` correctly populated all 8 categories, but
the web category showed 108 auto-discovered apps with status=ok and
ZERO actionable updates. Root cause: the shipped curated registry had
only 10 apps (Brave, Obsidian, Notion, OBS, Discord, Slack, Zoom,
Cursor, GitHub Desktop, brave-nightly), and NONE were installed on
the operator's machine -> 0 curated matches -> 0 items in plan/apply.

Sesja 60 ships:
  1. ``Get-WebInstalledVersion`` extended with a DisplayName fallback
     so curated entries can use friendly registry DisplayNames
     instead of guessing the exact subkey name.
  2. Registry pattern relaxed to allow the punctuation real Windows
     DisplayNames carry (``+``, ``(``, ``)``, etc.).
  3. 10 new curated entries for common dev tools: KeePassXC,
     Notepad++, AutoHotkey, rclone, GitHub CLI, OpenCode, Tuta Mail,
     VS Code (User), Proton Mail, Proton Drive.
  4. apply.ps1 emits a clear top-level info message when 0 curated
     apps matched the host, telling the operator what to do next.

These tests pin the new entries and the relaxed pattern so a future
refactor can't silently drop them.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest


ADAPTER_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ADAPTER_ROOT / "config" / "web_apps.toml"
APPLY_PS1 = ADAPTER_ROOT / "scripts" / "web" / "apply.ps1"
WEB_PSM1 = ADAPTER_ROOT / "lib" / "AscendoWeb.psm1"


def _parsed_registry() -> dict:
    with open(REGISTRY, "rb") as f:
        return tomllib.load(f)


def _slugs() -> list[str]:
    return [a["slug"] for a in _parsed_registry().get("app", [])]


@pytest.mark.parametrize(
    "slug",
    [
        "keepassxc", "notepadpp", "autohotkey", "rclone", "github-cli",
        "opencode", "tuta-mail", "vscode-user", "proton-mail", "proton-drive",
    ],
)
def test_sesja60_curated_entries_present(slug: str) -> None:
    """The 10 Sesja 60 entries must remain in the shipped registry."""
    assert slug in _slugs(), (
        f"Curated entry '{slug}' missing from web_apps.toml. Sesja 60 "
        "added these to cover the operator's installed apps."
    )


def test_curated_count_grew_from_10_to_20() -> None:
    """The registry should have >=20 entries (10 shipped + 10 Sesja 60).
    Catches a future trim that would re-introduce the operator's bug.
    """
    count = len(_parsed_registry().get("app", []))
    assert count >= 20, (
        f"Registry shrunk to {count} apps. Sesja 60 shipped 10 new "
        "entries; if any are removed, document why in commit message."
    )


@pytest.mark.parametrize(
    "slug,expected_handler",
    [
        ("keepassxc", "github_release"),
        ("notepadpp", "github_release"),
        ("autohotkey", "github_release"),
        ("rclone", "github_release"),
        ("github-cli", "github_release"),
        ("opencode", "github_release"),
        ("tuta-mail", "github_release"),
        ("vscode-user", "release_feed"),
        ("proton-mail", "release_feed"),
        ("proton-drive", "release_feed"),
    ],
)
def test_sesja60_entries_are_tier_a(slug: str, expected_handler: str) -> None:
    """Each Sesja 60 entry must have a Tier-A handler (real probe).
    builtin would defeat the purpose -- the operator wants real
    candidate-version detection, not just a "click to download" link.
    """
    apps = _parsed_registry().get("app", [])
    entry = next((a for a in apps if a.get("slug") == slug), None)
    assert entry is not None, f"{slug} entry missing"
    assert entry["handler"] == expected_handler, (
        f"{slug}: expected handler={expected_handler!r}, "
        f"got {entry['handler']!r}"
    )


@pytest.mark.parametrize(
    "slug,key_substring",
    [
        # Each entry's windows_uninstall_key must mention the friendly
        # display-name shape (case-insensitive) so the
        # Get-WebInstalledVersion fallback finds it via the DisplayName
        # cache when the registry subkey is a GUID or version-suffixed.
        ("keepassxc", "keepassxc"),
        ("notepadpp", "notepad++"),
        ("autohotkey", "autohotkey"),
        ("rclone", "rclone"),
        ("github-cli", "github cli"),
        ("opencode", "opencode"),
        ("tuta-mail", "tuta mail"),
        ("vscode-user", "visual studio code"),
        ("proton-mail", "proton mail"),
        ("proton-drive", "proton drive"),
    ],
)
def test_entries_have_friendly_uninstall_keys(slug: str, key_substring: str) -> None:
    apps = _parsed_registry().get("app", [])
    entry = next((a for a in apps if a.get("slug") == slug), None)
    assert entry is not None
    key = (entry.get("windows_uninstall_key") or "").lower()
    assert key_substring in key, (
        f"{slug}: windows_uninstall_key {entry.get('windows_uninstall_key')!r} "
        f"should contain {key_substring!r} so the DisplayName fallback hits."
    )


def test_get_web_installed_version_has_display_name_fallback() -> None:
    """``Get-WebInstalledVersion`` source must include the DisplayName
    fallback scan added by Sesja 60.
    """
    text = WEB_PSM1.read_text(encoding="utf-8")
    assert "_AscendoWebDisplayNameCache" in text, (
        "DisplayName cache helper must remain in AscendoWeb.psm1 -- it's "
        "what lets curated entries use friendly display names instead of "
        "exact registry subkey names."
    )
    # The fallback must scan all 3 standard Uninstall roots.
    assert "WOW6432Node" in text
    assert "HKCU:" in text


def test_apply_ps1_emits_zero_curated_info_message() -> None:
    """apply.ps1 must emit a clear top-level info message when 0 items
    were processed -- prevents the operator confusion observed on
    DP5520WMK 2026-05-13.
    """
    text = APPLY_PS1.read_text(encoding="utf-8")
    assert "Web apply emitted 0 items" in text, (
        "apply.ps1 must explain when no curated apps matched. Operator "
        "previously saw an empty items[] with no explanation."
    )


def test_windows_uninstall_key_pattern_allows_real_displaynames() -> None:
    """The Pydantic schema pattern must accept real Windows DisplayNames
    containing parentheses, plus, and other punctuation that the
    original pattern (``[\\w.\\-\\{\\} ]+``) rejected.
    """
    from ascendo_windows.web_registry import WebAppV1
    from pydantic import ValidationError

    accepting_cases = [
        "Notepad++ (64-bit x64)",
        "Mozilla Firefox (x64 en-US)",
        "Microsoft Visual Studio Code (User)",
        "Visual Studio Build Tools 2022",
        "AutoHotkey",
        "{12345678-1234-1234-1234-123456789ABC}",
    ]
    for value in accepting_cases:
        WebAppV1(
            slug="testslug", display_name="Test", handler="builtin",
            windows_uninstall_key=value,
            builtin={"url": "https://example.com/"},
        )

    rejecting_cases = [
        "danger;rm -rf /",
        "bad`backtick",
        "evil$injection",
        "and&command",
        "or|pipe",
    ]
    for value in rejecting_cases:
        with pytest.raises(ValidationError):
            WebAppV1(
                slug="testslug", display_name="Test", handler="builtin",
                windows_uninstall_key=value,
                builtin={"url": "https://example.com/"},
            )


def test_registry_validates_clean() -> None:
    """The shipped registry must round-trip through the Pydantic
    validator without errors. Catches typos in TOML structure.
    """
    from ascendo_windows.web_registry import WebRegistryV2

    payload = _parsed_registry()
    WebRegistryV2.model_validate(payload)
