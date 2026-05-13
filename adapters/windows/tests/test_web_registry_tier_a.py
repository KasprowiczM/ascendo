"""Pydantic schema tests for the Tier-A web apply fields.

Covers:
  * GitHubReleaseConfig.expected_publisher / silent_args / installer_kind /
    kill_processes / display_name_pattern
  * ReleaseFeedConfig with the same fields plus download_url /
    download_path (XOR-validated mutually exclusive)
  * WebAppV1.tier_a_apply field (default False; requires
    windows_uninstall_key; incompatible with handler=builtin)

These are pure-Python mock-free tests -- no pwsh required.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ascendo_windows.web_registry import (
    BuiltinConfig,
    GitHubReleaseConfig,
    ReleaseFeedConfig,
    WebAppV1,
    WebRegistryV2,
)


# ── GitHubReleaseConfig Tier-A fields ────────────────────────────────


def test_github_release_accepts_all_tier_a_fields() -> None:
    cfg = GitHubReleaseConfig(
        repo="obsidianmd/obsidian-releases",
        asset_pattern=r"^Obsidian-[0-9.]+\.exe$",
        expected_publisher="O=Obsidian.md",
        silent_args=["/S"],
        installer_kind="exe",
        kill_processes=["Obsidian"],
        display_name_pattern=r"^Obsidian$",
    )
    assert cfg.expected_publisher == "O=Obsidian.md"
    assert cfg.silent_args == ["/S"]
    assert cfg.installer_kind == "exe"
    assert cfg.kill_processes == ["Obsidian"]
    assert cfg.display_name_pattern == "^Obsidian$"


def test_github_release_tier_a_fields_default_to_none() -> None:
    """All five new fields are optional -- existing TOML rows still parse."""
    cfg = GitHubReleaseConfig(
        repo="user/repo",
        asset_pattern=r"^installer.*\.exe$",
    )
    assert cfg.expected_publisher is None
    assert cfg.silent_args is None
    assert cfg.installer_kind is None
    assert cfg.kill_processes is None
    assert cfg.display_name_pattern is None


def test_github_release_rejects_empty_silent_args() -> None:
    """When silent_args is set explicitly it must contain at least one
    argument; an empty list is ambiguous (use None for auto-detection)."""
    with pytest.raises(ValidationError) as excinfo:
        GitHubReleaseConfig(
            repo="user/repo",
            asset_pattern=r".*",
            silent_args=[],
        )
    assert "silent_args" in str(excinfo.value)


def test_github_release_rejects_empty_kill_processes() -> None:
    with pytest.raises(ValidationError) as excinfo:
        GitHubReleaseConfig(
            repo="user/repo",
            asset_pattern=r".*",
            kill_processes=[],
        )
    assert "kill_processes" in str(excinfo.value)


def test_github_release_rejects_invalid_installer_kind() -> None:
    with pytest.raises(ValidationError):
        GitHubReleaseConfig(
            repo="user/repo",
            asset_pattern=r".*",
            installer_kind="appx",  # not in Literal["exe", "msi"]
        )


def test_github_release_rejects_invalid_display_name_pattern() -> None:
    with pytest.raises(ValidationError) as excinfo:
        GitHubReleaseConfig(
            repo="user/repo",
            asset_pattern=r".*",
            display_name_pattern="[unterminated",
        )
    assert "display_name_pattern" in str(excinfo.value)


def test_github_release_rejects_kill_processes_with_path_separator() -> None:
    """Process names go straight to Get-Process -Name; reject anything with
    path separators or quotes that could let upstream pivot the lookup."""
    with pytest.raises(ValidationError):
        GitHubReleaseConfig(
            repo="user/repo",
            asset_pattern=r".*",
            kill_processes=["..\\..\\evil"],
        )


# ── ReleaseFeedConfig Tier-A fields ──────────────────────────────────


def test_release_feed_accepts_download_url() -> None:
    cfg = ReleaseFeedConfig(
        url="https://example.com/feed.json",
        version_path="version",
        download_url="https://example.com/installer.exe",
        expected_publisher="O=Example",
    )
    assert str(cfg.download_url).startswith("https://example.com/installer.exe")
    assert cfg.expected_publisher == "O=Example"


def test_release_feed_accepts_download_path() -> None:
    cfg = ReleaseFeedConfig(
        url="https://example.com/feed.json",
        version_path="version",
        download_path="downloads.win32.url",
    )
    assert cfg.download_path == "downloads.win32.url"
    assert cfg.download_url is None


def test_release_feed_rejects_download_url_with_download_path() -> None:
    """download_url and download_path are mutually exclusive."""
    with pytest.raises(ValidationError) as excinfo:
        ReleaseFeedConfig(
            url="https://example.com/feed.json",
            version_path="version",
            download_url="https://example.com/i.exe",
            download_path="downloads.url",
        )
    msg = str(excinfo.value)
    assert "mutually exclusive" in msg or "download_url" in msg


def test_release_feed_rejects_http_download_url() -> None:
    """T3: download URL must be HTTPS too."""
    with pytest.raises(ValidationError):
        ReleaseFeedConfig(
            url="https://example.com/feed.json",
            version_path="version",
            download_url="http://example.com/insecure.exe",
        )


def test_release_feed_tier_a_fields_default_to_none() -> None:
    cfg = ReleaseFeedConfig(
        url="https://example.com/feed.json",
        version_path="version",
    )
    assert cfg.download_url is None
    assert cfg.download_path is None
    assert cfg.expected_publisher is None
    assert cfg.silent_args is None
    assert cfg.installer_kind is None
    assert cfg.kill_processes is None
    assert cfg.display_name_pattern is None


# ── WebAppV1.tier_a_apply flag ───────────────────────────────────────


def test_webapp_v1_tier_a_apply_defaults_false() -> None:
    """Critical: Existing TOML rows without tier_a_apply must remain Tier-B."""
    app = WebAppV1(
        slug="brave",
        display_name="Brave Browser",
        handler="github_release",
        windows_uninstall_key="BraveSoftware Brave-Browser",
        github_release=GitHubReleaseConfig(
            repo="brave/brave-browser",
            asset_pattern=r".*\.exe$",
        ),
    )
    assert app.tier_a_apply is False


def test_webapp_v1_tier_a_apply_accepts_true() -> None:
    app = WebAppV1(
        slug="obsidian",
        display_name="Obsidian",
        handler="github_release",
        windows_uninstall_key="Obsidian",
        tier_a_apply=True,
        github_release=GitHubReleaseConfig(
            repo="obsidianmd/obsidian-releases",
            asset_pattern=r"^Obsidian-[0-9.]+\.exe$",
            expected_publisher="O=Obsidian.md",
        ),
    )
    assert app.tier_a_apply is True
    assert app.github_release is not None
    assert app.github_release.expected_publisher == "O=Obsidian.md"


def test_webapp_v1_tier_a_apply_incompatible_with_builtin_handler() -> None:
    """Builtin handler has no candidate version probe; there's no installer
    to download. Setting tier_a_apply=true on a builtin app must error."""
    with pytest.raises(ValidationError) as excinfo:
        WebAppV1(
            slug="discord",
            display_name="Discord",
            handler="builtin",
            windows_uninstall_key="Discord",
            tier_a_apply=True,
            builtin=BuiltinConfig(url="https://discord.com/download"),
        )
    assert "builtin" in str(excinfo.value)


def test_webapp_v1_tier_a_apply_requires_uninstall_key() -> None:
    """Tier-A apply uses windows_uninstall_key for the post-install
    readback. Without it, there's no way to confirm the install landed."""
    with pytest.raises(ValidationError) as excinfo:
        WebAppV1(
            slug="obsidian",
            display_name="Obsidian",
            handler="github_release",
            # windows_uninstall_key intentionally absent
            tier_a_apply=True,
            github_release=GitHubReleaseConfig(
                repo="obsidianmd/obsidian-releases",
                asset_pattern=r".*\.exe$",
            ),
        )
    assert "windows_uninstall_key" in str(excinfo.value)


def test_webapp_v1_tier_a_apply_works_with_release_feed() -> None:
    """release_feed is the other Tier-A handler -- tier_a_apply must work."""
    app = WebAppV1(
        slug="brave",
        display_name="Brave Browser",
        handler="release_feed",
        windows_uninstall_key="BraveSoftware Brave-Browser",
        tier_a_apply=True,
        release_feed=ReleaseFeedConfig(
            url="https://api.github.com/repos/brave/brave-browser/releases/latest",
            version_path="name",
            version_regex=r".*v([0-9.]+).*",
            version_replace=r"\1",
            download_url="https://example.com/brave-installer.exe",
            expected_publisher="O=Brave Software, Inc.",
        ),
    )
    assert app.tier_a_apply is True


# ── TOML round-trip ──────────────────────────────────────────────────


def test_tier_a_apply_round_trips_through_toml(tmp_path) -> None:
    """A TOML registry with a tier_a_apply=true app must parse + retain
    the flag through merge_overrides + the WebRegistryV2.load helper."""
    toml = tmp_path / "registry.toml"
    # NOTE: double-escape the regex \ because Python -> TOML -> Pydantic.
    # `\\\\.` in the Python source becomes `\\.` on disk which TOML reads
    # as `\.` (the regex literal "any character"). Without the second
    # escape the asset_pattern would be `\.exe$` which TOML rejects as
    # an "unescaped backslash" -- the slash is a TOML escape char, not
    # a regex one.
    toml.write_text(
        'schema = "ascendo-web-apps-windows/v1"\n\n'
        "[[app]]\n"
        'slug = "obsidian"\n'
        'display_name = "Obsidian"\n'
        'handler = "github_release"\n'
        'windows_uninstall_key = "Obsidian"\n'
        "tier_a_apply = true\n\n"
        "[app.github_release]\n"
        'repo = "obsidianmd/obsidian-releases"\n'
        'asset_pattern = "^Obsidian-[0-9.]+\\\\.exe$"\n'
        'expected_publisher = "O=Obsidian.md"\n'
        'silent_args = ["/S"]\n'
        'installer_kind = "exe"\n'
        'kill_processes = ["Obsidian"]\n',
        encoding="utf-8",
    )
    reg = WebRegistryV2.load(toml, None)
    app = reg.find("obsidian")
    assert app is not None
    assert app.tier_a_apply is True
    assert app.github_release is not None
    assert app.github_release.expected_publisher == "O=Obsidian.md"
    assert app.github_release.silent_args == ["/S"]
    assert app.github_release.installer_kind == "exe"
    assert app.github_release.kill_processes == ["Obsidian"]


def test_existing_shipped_registry_still_parses() -> None:
    """The shipped registry must continue to parse after the schema
    additions -- catches accidental required-field promotions.

    Tier-A entries are allowed when the entry carries a complete
    silent-install contract (silent_args, installer_kind,
    kill_processes, expected_publisher). Sesja 61 enabled Tier-A
    apply on six well-known installers (vscode-user, keepassxc,
    notepadpp, autohotkey, github-cli, opencode); each must have
    every required field set so the apply path doesn't fall through
    to Tier-B trigger-only.
    """
    from pathlib import Path as _P
    shipped = _P(__file__).resolve().parents[1] / "config" / "web_apps.toml"
    reg = WebRegistryV2.load(shipped, None)
    for app in reg.apps:
        if not app.tier_a_apply:
            continue
        # Tier-A is opt-in. When opted in, the contract requires the
        # full silent-install field set so apply can run unattended.
        assert app.handler in ("github_release", "release_feed"), (
            f"slug={app.slug}: Tier-A apply only supported on "
            f"github_release / release_feed handlers; handler="
            f"{app.handler!r} cannot be Tier-A."
        )
        sub = app.github_release or app.release_feed
        assert sub is not None
        for field_name in ("silent_args", "installer_kind",
                           "kill_processes", "expected_publisher"):
            value = getattr(sub, field_name, None)
            assert value, (
                f"slug={app.slug}: tier_a_apply=true but {field_name} "
                f"is missing. Tier-A apply needs all four fields so "
                f"the install can run unattended + survive Authenticode "
                f"verification."
            )
