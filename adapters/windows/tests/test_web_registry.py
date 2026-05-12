"""Pydantic schema + shipped TOML sanity tests for the Windows web_registry.

Mirrors the macOS test_web_apps_toml_shipped.py + test_web_registry_v2.py
suite but adapted for the smaller Windows handler set (github_release,
release_feed, builtin) and slug-keyed (not bundle_id-keyed) overrides.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ascendo_windows.web_registry import (
    BuiltinConfig,
    GitHubReleaseConfig,
    ReleaseFeedConfig,
    WebAppV1,
    WebRegistryV2,
    merge_overrides,
)


SHIPPED = (
    Path(__file__).resolve().parents[1] / "config" / "web_apps.toml"
)


# ── Shipped TOML ─────────────────────────────────────────────────────


def test_shipped_registry_parses() -> None:
    assert SHIPPED.exists(), f"missing shipped registry: {SHIPPED}"
    reg = WebRegistryV2.load(SHIPPED, None)
    assert reg.schema_version == "ascendo-web-apps-windows/v1"
    assert len(reg.apps) >= 5


def test_shipped_registry_has_min_entries() -> None:
    """v1 baseline must ship at least 10 curated apps so operators have
    enough coverage to validate the WebManager end-to-end."""
    reg = WebRegistryV2.load(SHIPPED, None)
    assert len(reg.apps) >= 10


def test_shipped_registry_no_duplicate_slugs() -> None:
    reg = WebRegistryV2.load(SHIPPED, None)
    slugs = [a.slug for a in reg.apps]
    assert len(slugs) == len(set(slugs)), \
        f"duplicate slugs: {[s for s in slugs if slugs.count(s) > 1]}"


def test_shipped_registry_handlers_in_allowed_set() -> None:
    """Every shipped handler must be one of the three v1 Windows handlers
    (github_release, release_feed, builtin). The Pydantic Literal will
    have rejected anything else at load time; this is the assertion that
    the registry actually covers our supported set."""
    reg = WebRegistryV2.load(SHIPPED, None)
    handlers = {a.handler for a in reg.apps}
    allowed = {"github_release", "release_feed", "builtin"}
    assert handlers.issubset(allowed)
    assert handlers, "registry has zero apps"


def test_shipped_registry_brave_uses_release_feed() -> None:
    """Brave on Windows uses the same release_feed + version_regex
    technique as macOS: the GitHub release `name` field carries both
    Chromium milestone and Brave internal version, regex rewrites into
    chromium.brave_internal shape matching DisplayVersion."""
    reg = WebRegistryV2.load(SHIPPED, None)
    brave = reg.find("brave")
    assert brave is not None
    assert brave.handler == "release_feed"
    assert brave.release_feed is not None
    assert "api.github.com/repos/brave/brave-browser" in str(brave.release_feed.url)
    assert brave.release_feed.version_path == "name"
    assert brave.release_feed.version_regex is not None
    assert brave.release_feed.version_replace is not None


def test_shipped_registry_obsidian_uses_github_release() -> None:
    reg = WebRegistryV2.load(SHIPPED, None)
    obs = reg.find("obsidian")
    assert obs is not None
    assert obs.handler == "github_release"
    assert obs.github_release is not None
    assert obs.github_release.repo == "obsidianmd/obsidian-releases"
    # asset_pattern restricts to the .exe installer (not the .zip / .asar.gz).
    assert ".exe" in obs.github_release.asset_pattern


def test_shipped_registry_notion_uses_release_feed_text() -> None:
    """Notion's Windows update manifest is Electron-builder YAML. Using
    format=text + version_regex avoids pulling in a YAML parser."""
    reg = WebRegistryV2.load(SHIPPED, None)
    notion = reg.find("notion")
    assert notion is not None
    assert notion.handler == "release_feed"
    assert notion.release_feed is not None
    assert notion.release_feed.format == "text"
    assert "notion-static.com/latest.yml" in str(notion.release_feed.url)
    assert notion.release_feed.version_regex is not None


def test_shipped_registry_xor_enforcement_on_handler_subtables() -> None:
    """Every shipped app must have exactly its handler's sub-table set.
    The Pydantic validator catches violations; we still want a runtime
    assertion in case a future refactor accidentally loosens the check.
    """
    reg = WebRegistryV2.load(SHIPPED, None)
    for app in reg.apps:
        present = []
        if app.github_release is not None:
            present.append("github_release")
        if app.release_feed is not None:
            present.append("release_feed")
        if app.builtin is not None:
            present.append("builtin")
        assert present == [app.handler], (
            f"slug={app.slug}: handler={app.handler!r} but sub-tables {present!r} "
            f"present (expected exactly [{app.handler!r}])"
        )


def test_shipped_registry_https_only() -> None:
    """T3 threat-model mitigation: every URL must be HTTPS. Pydantic
    HttpsUrl rejects http:// at load time; this is the runtime double-check.
    """
    reg = WebRegistryV2.load(SHIPPED, None)
    for app in reg.apps:
        if app.github_release is not None:
            pass  # repo only; URL composed at runtime against api.github.com (https)
        if app.release_feed is not None:
            assert str(app.release_feed.url).startswith("https://"), app.slug
        if app.builtin is not None:
            assert str(app.builtin.url).startswith("https://"), app.slug


# ── Pydantic schema validation ───────────────────────────────────────


def test_schema_rejects_handler_without_matching_subtable() -> None:
    """Setting handler=github_release without [apps.github_release] must
    raise a ValidationError naming the required-by-handler condition."""
    with pytest.raises(ValidationError) as excinfo:
        WebAppV1(
            slug="testapp",
            display_name="Test App",
            handler="github_release",
        )
    assert "github_release" in str(excinfo.value)


def test_schema_rejects_handler_with_wrong_subtable() -> None:
    """Setting handler=github_release while supplying [apps.release_feed]
    must raise — catches accidental copy-paste from another row."""
    with pytest.raises(ValidationError):
        WebAppV1(
            slug="testapp",
            display_name="Test App",
            handler="github_release",
            release_feed=ReleaseFeedConfig(
                url="https://example.com/feed.json",
                version_path="version",
            ),
        )


def test_schema_rejects_release_feed_json_without_version_path() -> None:
    """format=json requires version_path; the schema must reject."""
    with pytest.raises(ValidationError):
        ReleaseFeedConfig(
            url="https://example.com/feed.json",
            format="json",
        )


def test_schema_rejects_release_feed_text_without_version_regex() -> None:
    """format=text requires version_regex; the schema must reject."""
    with pytest.raises(ValidationError):
        ReleaseFeedConfig(
            url="https://example.com/feed.txt",
            format="text",
        )


def test_schema_rejects_invalid_regex() -> None:
    with pytest.raises(ValidationError):
        ReleaseFeedConfig(
            url="https://example.com/feed.json",
            version_path="v",
            version_regex="[",  # unbalanced
            version_replace=r"\1",
        )


def test_schema_rejects_regex_without_replace_xor() -> None:
    with pytest.raises(ValidationError):
        ReleaseFeedConfig(
            url="https://example.com/feed.json",
            version_path="v",
            version_regex="(.+)",
            # version_replace missing
        )


def test_schema_rejects_http_url() -> None:
    """T3: http:// must be rejected (HTTPS-only)."""
    with pytest.raises(ValidationError):
        BuiltinConfig(url="http://example.com/download")


def test_schema_accepts_minimal_builtin_app() -> None:
    """Builtin apps are the trivial happy path: handler + builtin sub-table
    + slug + display_name."""
    app = WebAppV1(
        slug="discord",
        display_name="Discord",
        handler="builtin",
        builtin=BuiltinConfig(url="https://discord.com/download"),
    )
    assert app.handler == "builtin"
    assert str(app.builtin.url).startswith("https://discord.com/download")


def test_schema_rejects_invalid_slug() -> None:
    with pytest.raises(ValidationError):
        WebAppV1(
            slug="Bad Slug!",  # uppercase + space + ! all rejected
            display_name="X",
            handler="builtin",
            builtin=BuiltinConfig(url="https://example.com/"),
        )


def test_github_release_compiles_asset_pattern() -> None:
    """Bad regex must be rejected at schema load time."""
    with pytest.raises(ValidationError):
        GitHubReleaseConfig(
            repo="user/repo",
            asset_pattern="[unterminated",  # invalid regex
        )


# ── Override merge ───────────────────────────────────────────────────


def test_merge_overrides_user_wins_by_slug(tmp_path: Path) -> None:
    shipped = WebRegistryV2(
        schema="ascendo-web-apps-windows/v1",
        app=[
            WebAppV1(
                slug="brave",
                display_name="Brave (shipped)",
                handler="builtin",
                builtin=BuiltinConfig(url="https://brave.com/download/"),
            ),
            WebAppV1(
                slug="obsidian",
                display_name="Obsidian (shipped)",
                handler="builtin",
                builtin=BuiltinConfig(url="https://obsidian.md/download"),
            ),
        ],
    )
    user = WebRegistryV2(
        schema="ascendo-web-apps-windows/v1",
        app=[
            WebAppV1(
                slug="brave",
                display_name="Brave (user override)",
                handler="builtin",
                builtin=BuiltinConfig(url="https://laplace.brave.com/"),
            ),
            WebAppV1(
                slug="custom-app",
                display_name="Custom App",
                handler="builtin",
                builtin=BuiltinConfig(url="https://example.com/custom"),
            ),
        ],
    )
    merged = merge_overrides(shipped, user)
    by_slug = {a.slug: a for a in merged.apps}
    assert by_slug["brave"].display_name == "Brave (user override)"
    assert by_slug["obsidian"].display_name == "Obsidian (shipped)"
    assert "custom-app" in by_slug
    assert len(merged.apps) == 3


def test_load_with_user_override_via_files(tmp_path: Path) -> None:
    shipped_path = tmp_path / "shipped.toml"
    shipped_path.write_text(
        'schema = "ascendo-web-apps-windows/v1"\n\n'
        '[[app]]\n'
        'slug = "brave"\n'
        'display_name = "Brave (shipped)"\n'
        'handler = "builtin"\n\n'
        '[app.builtin]\n'
        'url = "https://brave.com/download/"\n',
        encoding="utf-8",
    )
    user_path = tmp_path / "user.toml"
    user_path.write_text(
        'schema = "ascendo-web-apps-windows/v1"\n\n'
        '[[app]]\n'
        'slug = "brave"\n'
        'display_name = "Brave (user)"\n'
        'handler = "builtin"\n\n'
        '[app.builtin]\n'
        'url = "https://laplace.brave.com/"\n',
        encoding="utf-8",
    )
    reg = WebRegistryV2.load(shipped_path, user_path)
    brave = reg.find("brave")
    assert brave is not None
    assert brave.display_name == "Brave (user)"
