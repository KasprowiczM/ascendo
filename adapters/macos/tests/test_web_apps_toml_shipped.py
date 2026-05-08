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
    """Core handlers must be represented. M5.7.1: Docker switched from
    'docker' (CLI plugin version probe was wrong) to 'sparkle' (real
    appcast at desktop.docker.com). 'release_feed' added for vendor
    JSON/YAML probes (VSCode/Notion/Ledger/Firefox-Dev/Zoom)."""
    reg = WebRegistry.load(SHIPPED, None)
    handlers = {a.handler for a in reg.apps}
    expected = {"sparkle", "github_dmg", "keystone", "squirrel",
                "msupdate", "release_feed"}
    assert expected.issubset(handlers)


def test_shipped_registry_no_duplicate_slugs() -> None:
    reg = WebRegistry.load(SHIPPED, None)
    slugs = [a.slug for a in reg.apps]
    assert len(slugs) == len(set(slugs)), \
        f"duplicate slugs: {[s for s in slugs if slugs.count(s) > 1]}"


def test_shipped_registry_chrome_is_keystone() -> None:
    reg = WebRegistry.load(SHIPPED, None)
    chrome = reg.find("chrome")
    assert chrome is not None
    assert chrome.handler == "keystone"


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
