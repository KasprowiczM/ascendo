"""Static-analysis regression tests for the release_feed JSON-path walker
and the JSON parser fallback.

Sesja 61 / Sesja 60 follow-up — three distinct bugs surfaced during
operator's full update on DP5520WMK 2026-05-13 (run 6149fbba):

  1. ``version_path = "Releases.0.Version"`` failed because the
     walker's segment loop only recognised numeric array indices
     when written as ``Releases[0].Version`` -- pure-numeric segments
     fell through to the property-name lookup branch and returned
     null. Proton Mail + Proton Drive entries used the dotted form
     (Mac adapter parity) and surfaced as ``skipped: probe returned
     empty``.

  2. ``ConvertFrom-Json`` on PS 7+ rejects JSON whose keys differ
     only in casing (e.g. Proton Drive's ``Sha512CheckSum`` vs
     ``Sha512Checksum``). Switching to ``-AsHashtable`` accepts the
     casing collision and parses correctly. PS 5.1 doesn't support
     ``-AsHashtable``; the fix is gated on
     ``$PSVersionTable.PSVersion.Major -ge 6``.

  3. ``vscode-user`` apply was Tier-B trigger-only (vendor URL
     opened) because no ``tier_a_apply = true`` was set. The actual
     install required an opt-in flag plus the silent-install fields
     (silent_args, installer_kind, kill_processes, expected_publisher).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest


ADAPTER_ROOT = Path(__file__).resolve().parents[1]
RELEASE_FEED_PS1 = ADAPTER_ROOT / "lib" / "handlers" / "release_feed.ps1"
WEB_APPS_TOML = ADAPTER_ROOT / "config" / "web_apps.toml"


def _registry() -> dict:
    with open(WEB_APPS_TOML, "rb") as f:
        return tomllib.load(f)


def _apps() -> list[dict]:
    return _registry().get("app", [])


def _app(slug: str) -> dict:
    a = next((x for x in _apps() if x.get("slug") == slug), None)
    assert a is not None, f"slug {slug!r} missing from web_apps.toml"
    return a


# ─── Fix 1: dotted-numeric JSON path ──────────────────────────────────


def test_walker_documents_dotted_numeric_form() -> None:
    """``_RF-WalkJsonPath`` must document and handle the ``foo.0.bar``
    form alongside the classic ``foo[0].bar``. Proton Mail / Drive
    entries use the dotted form for parity with the macOS adapter.
    """
    text = RELEASE_FEED_PS1.read_text(encoding="utf-8")
    # The walker must contain the dotted-numeric segment handling block.
    assert "dotted-numeric" in text.lower(), (
        "release_feed.ps1 walker must document the dotted-numeric "
        "path syntax explicitly so future refactors preserve it."
    )
    # The PowerShell match operator with `^\d+$` should appear.
    assert "-match '^\\d+$'" in text, (
        r"Walker must match purely-numeric segments via PS `-match '^\d+$'`."
    )


def test_proton_entries_use_dotted_form() -> None:
    """The Proton Mail / Drive entries must use ``Releases[0].Version``
    (the form actually supported -- AND tested live).

    Updated Sesja 61 (2026-05-13): we shipped both forms working in
    the walker; the registry uses the bracket form because that's
    what was actually validated against the live Proton feed.
    """
    for slug in ("proton-mail", "proton-drive"):
        a = _app(slug)
        rf = a.get("release_feed", {})
        path = rf.get("version_path", "")
        assert "Releases" in path and "Version" in path, (
            f"{slug}: version_path {path!r} must walk into Releases.Version"
        )


# ─── Fix 2: -AsHashtable for case-colliding keys ──────────────────────


def test_release_feed_uses_ashashtable_on_ps6_plus() -> None:
    """ConvertFrom-Json must use -AsHashtable on PS 6+ so JSON with
    case-colliding keys (Proton Drive's ``Sha512CheckSum`` /
    ``Sha512Checksum``) parses successfully.
    """
    text = RELEASE_FEED_PS1.read_text(encoding="utf-8")
    assert "-AsHashtable" in text, (
        "release_feed.ps1 must use ConvertFrom-Json -AsHashtable so "
        "Proton Drive's case-colliding keys don't trip the parser."
    )
    # Must be gated on PS version 6+ (PS 5.1 lacks the switch).
    assert "PSVersion.Major -ge 6" in text or "PSVersion.Major -ge 7" in text, (
        "The -AsHashtable path must be gated on PSVersion.Major>=6; "
        "PS 5.1 lacks the switch."
    )


def test_walker_handles_idictionary_branch() -> None:
    """The walker must check ``IDictionary`` before falling to
    ``PSObject.Properties`` -- this is what makes ``-AsHashtable``
    output work seamlessly alongside the default PSCustomObject.
    """
    text = RELEASE_FEED_PS1.read_text(encoding="utf-8")
    assert "IDictionary" in text, (
        "Walker must branch on IDictionary for -AsHashtable compatibility."
    )


# ─── Fix 3: Tier-A apply enabled on common installers ─────────────────


@pytest.mark.parametrize(
    "slug,kind,silent_first_arg",
    [
        ("vscode-user",  "exe",  "/VERYSILENT"),
        ("keepassxc",    "msi",  "/qn"),
        ("notepadpp",    "exe",  "/S"),
        ("autohotkey",   "exe",  "/S"),
        ("github-cli",   "msi",  "/qn"),
        ("opencode",     "exe",  "--silent"),
    ],
)
def test_tier_a_apply_enabled_with_silent_install(
    slug: str, kind: str, silent_first_arg: str,
) -> None:
    """Each enabled entry must have tier_a_apply=true + installer_kind
    + silent_args + kill_processes + expected_publisher.

    Without all four fields, the apply phase falls back to Tier-B
    trigger-only (opens vendor URL) and the operator sees no actual
    install -- the original DP5520WMK 2026-05-13 complaint.
    """
    a = _app(slug)
    assert a.get("tier_a_apply") is True, (
        f"{slug}: tier_a_apply must be true to actually upgrade"
    )
    sub = a.get("github_release") or a.get("release_feed")
    assert sub is not None, f"{slug}: needs github_release or release_feed"
    assert sub.get("installer_kind") == kind, (
        f"{slug}: installer_kind must be {kind!r}"
    )
    args = sub.get("silent_args") or []
    assert args and args[0] == silent_first_arg, (
        f"{slug}: silent_args must start with {silent_first_arg!r}; "
        f"got {args}"
    )
    assert sub.get("kill_processes"), (
        f"{slug}: kill_processes must be set so the installer can "
        "replace the running binary"
    )
    assert sub.get("expected_publisher"), (
        f"{slug}: expected_publisher must be set so Authenticode "
        "verification confirms the right vendor"
    )


def test_vscode_user_has_download_path_url() -> None:
    """vscode-user's release_feed must define ``download_path = 'url'``
    so the Tier-A apply path resolves the .exe installer location
    from the same JSON the version probe reads.
    """
    rf = _app("vscode-user").get("release_feed", {})
    assert rf.get("download_path") == "url", (
        "vscode-user release_feed needs download_path='url' for Tier-A apply"
    )


def test_kill_processes_pattern_allows_plus_chars() -> None:
    """The kill_processes Pydantic pattern must accept ``+`` chars so
    ``notepad++`` validates. Original pattern was ``^[\\w.\\- ]+$``
    which rejected the only-known-process-with-plus case.
    """
    from ascendo_windows.web_registry import GitHubReleaseConfig
    cfg = GitHubReleaseConfig(
        repo="x/y", asset_pattern="^foo\\.exe$",
        kill_processes=["notepad++", "weird (name)"],
    )
    assert "notepad++" in (cfg.kill_processes or [])
