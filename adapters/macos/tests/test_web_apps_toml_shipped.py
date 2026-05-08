"""Sanity tests for the shipped web_apps.toml."""
from __future__ import annotations

from pathlib import Path

import pytest

from ascendo_macos.web_registry import WebRegistry

SHIPPED = (Path(__file__).resolve().parents[1] / "config" / "web_apps.toml")


def test_shipped_registry_parses() -> None:
    assert SHIPPED.exists()
    reg = WebRegistry.load(SHIPPED, None)
    assert len(reg.apps) >= 20


def test_shipped_registry_has_core_handlers() -> None:
    """Core handlers must be represented. M5.7.5 (v0.4.5): the keystone
    and squirrel handlers are no longer present in the shipped registry
    because every entry that previously used them has been promoted to
    Tier-A (real-candidate detection) — chrome/brave to release_feed,
    gdrive/gemini/comet to omaha, chatgpt/warp/proton-mail/comet to
    sparkle/release_feed/omaha, etc. They remain valid handler choices
    in the schema for user-override registries and future regressions
    (Brave once flipped sparkle->keystone->release_feed); we just
    don't ship any defaults using them. This assertion tracks the
    *Tier-A* handlers that MUST stay represented."""
    reg = WebRegistry.load(SHIPPED, None)
    handlers = {a.handler for a in reg.apps}
    expected = {"sparkle", "github_dmg", "msupdate", "release_feed", "omaha"}
    assert expected.issubset(handlers)


def test_shipped_registry_no_duplicate_slugs() -> None:
    reg = WebRegistry.load(SHIPPED, None)
    slugs = [a.slug for a in reg.apps]
    assert len(slugs) == len(set(slugs)), \
        f"duplicate slugs: {[s for s in slugs if slugs.count(s) > 1]}"


def test_shipped_registry_chrome_uses_release_feed() -> None:
    """M5.7.4 Phase B: Chrome promoted from keystone (Tier-B trigger-only)
    to release_feed (Tier-A) once we discovered the public Chrome Version
    History API at versionhistory.googleapis.com — JSON with
    `versions[0].version` matching CFBundleShortVersionString exactly.
    Apply remains trigger-only (no download_path) so the existing
    GoogleUpdater/Keystone daemon still drives the install."""
    reg = WebRegistry.load(SHIPPED, None)
    chrome = reg.find("chrome")
    assert chrome is not None
    assert chrome.handler == "release_feed"
    assert chrome.release_feed is not None
    assert "versionhistory.googleapis.com" in str(chrome.release_feed.url)
    assert chrome.release_feed.version_path == "versions[0].version"


def test_shipped_registry_brave_uses_release_feed_with_regex() -> None:
    """M5.7.4 Phase B: Brave promoted from keystone to release_feed.
    CFBundleShortVersionString is `<chromium_milestone>.<brave_internal>`
    (e.g. 148.1.90.121 = Chromium 148 + Brave 1.90.121). The GitHub
    release `name` field carries both pieces; version_regex rewrites
    "Release v1.90.121 (Chromium 148.0.7778.96)" -> "148.1.90.121"."""
    reg = WebRegistry.load(SHIPPED, None)
    brave = reg.find("brave")
    assert brave is not None
    assert brave.handler == "release_feed"
    assert brave.release_feed is not None
    assert "api.github.com/repos/brave/brave-browser" in str(brave.release_feed.url)
    assert brave.release_feed.version_path == "name"
    assert brave.release_feed.version_regex is not None
    assert brave.release_feed.version_replace is not None


def test_shipped_registry_docker_uses_sparkle_handler() -> None:
    """M5.7.1: Docker Desktop switched from 'docker' handler (which
    called `docker desktop version` returning the CLI plugin version,
    NOT the .app version) to 'sparkle' against Docker's official
    appcast at desktop.docker.com/mac/main/arm64/appcast.xml."""
    reg = WebRegistry.load(SHIPPED, None)
    docker = reg.find("docker")
    assert docker is not None
    assert docker.handler == "sparkle"
    assert docker.appcast_url is not None
    assert "desktop.docker.com" in str(docker.appcast_url)


def test_shipped_registry_ms365_uses_msupdate_handler() -> None:
    reg = WebRegistry.load(SHIPPED, None)
    ms = reg.find("ms365")
    assert ms is not None
    assert ms.handler == "msupdate"
