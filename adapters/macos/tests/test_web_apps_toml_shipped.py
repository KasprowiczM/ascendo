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


def test_shipped_registry_brave_uses_sparkle_arm64_appcast() -> None:
    """M5.7.6 (2026-05-24): Brave switched from release_feed-against-
    GitHub to native Sparkle appcast.

    Why the switch: Brave's `releases/latest` on GitHub now ships only
    Android APK assets — the Mac DMG was dropped from GH releases
    entirely, breaking the M5.7.5 download_asset_pattern path with
    handler-exit-28 (no DMG URL resolved). The CANONICAL mac stable
    channel is the vendor's own Sparkle appcast at
    updates.bravesoftware.com/sparkle/Brave-Browser/stable-arm64/
    appcast.xml — gated rollout, lags GH tag by 0–1 patches.

    Real-world failure that drove the fix: operator ran
    `ascendo run --category web --phase apply` on Mac.r12.home,
    sidecar reported `brave: handler exit 28`. Switched in commit
    820dbdb; the Sparkle DMG enclosure flows through the existing
    sparkle handler (download + spctl Gatekeeper verify + atomic
    swap)."""
    reg = WebRegistry.load(SHIPPED, None)
    brave = reg.find("brave")
    assert brave is not None
    assert brave.handler == "sparkle"
    assert brave.appcast_url is not None
    appcast = str(brave.appcast_url)
    assert "updates.bravesoftware.com" in appcast
    assert "stable-arm64" in appcast
    assert appcast.endswith("appcast.xml")
    # Negative: must not still carry the legacy release_feed sub-table.
    assert brave.release_feed is None


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


def test_shipped_registry_has_perplexity_macv3_entry() -> None:
    """Phase B (#B.2): the currently-shipped Perplexity bundle
    (ai.perplexity.macv3, NOT the retired MAS ai.perplexity.mac, NOT
    ai.perplexity.comet) is registered so it is guaranteed to surface
    in the Phase-A Action-required list rather than being silently
    uncovered. builtin handler => zero fake-silent-install risk."""
    reg = WebRegistry.load(SHIPPED, None)
    p = reg.find("perplexity")
    assert p is not None
    assert p.bundle_id == "ai.perplexity.macv3"
    assert p.handler == "builtin"
    assert p.enabled is True


def test_shipped_registry_drops_chatgpt_atlas() -> None:
    """ChatGPT Atlas was uninstalled 2026-08-19 (browser discontinued)."""
    reg = WebRegistry.load(SHIPPED, None)
    assert reg.find("chatgpt-atlas") is None
    slugs = [a.slug for a in reg.apps]
    assert "chatgpt-atlas" not in slugs


def test_shipped_registry_ledger_wallet_display_name() -> None:
    """Vendor renamed Ledger Live.app → Ledger Wallet.app; bundle id stays."""
    reg = WebRegistry.load(SHIPPED, None)
    ledger = reg.find("ledger-live")
    assert ledger is not None
    assert ledger.bundle_id == "com.ledger.live"
    assert ledger.display_name == "Ledger Wallet"


def test_shipped_registry_has_capcut_and_dji() -> None:
    """2026-08-25 inventory sync: CapCut (brew cask) + DJI Assistant 2."""
    reg = WebRegistry.load(SHIPPED, None)
    capcut = reg.find("capcut")
    assert capcut is not None
    assert capcut.bundle_id == "com.lemon.lvoverseas"
    dji = reg.find("dji-assistant")
    assert dji is not None
    assert dji.bundle_id == "DJI.Assistant"
    assert dji.handler == "builtin"
