"""Sanity tests for the shipped web_apps.toml."""
from __future__ import annotations

from pathlib import Path

import pytest

from ascendo.models.deduplication import AppSourcesRegistry
from ascendo_macos.web_registry import WebRegistry

_ADAPTER = Path(__file__).resolve().parents[1]
SHIPPED = _ADAPTER / "config" / "web_apps.toml"
SOURCES = _ADAPTER / "config" / "macos_app_sources.toml"

# Canonical brew_cask overlap from macOS_updates APPLICATIONS.md §4c (2026-08-25).
# blackhole-2ch is brew-only (no web duplicate, no sources row).
_BREW_PREFERRED_CASKS = {
    "brave-browser",
    "perplexity",
    "lm-studio",
    "protonvpn",
    "zoom",
    "megasync",
    "appcleaner",
    "obsidian",
    "spotify",
    "inkscape",
    "capcut",
}


def test_shipped_registry_parses() -> None:
    assert SHIPPED.exists()
    reg = WebRegistry.load(SHIPPED, None)
    assert len(reg.apps) >= 20


def test_shipped_registry_has_core_handlers() -> None:
    """Handlers that must stay represented after macOS_updates channel lock.

    Omaha is not shipped: Chrome/Drive use Keystone; Gemini/Comet use
    squirrel (silent_launch). Sparkle remains for RDM / Proton Drive /
    AppCleaner / ProtonVPN (brew skip still owns the last two at apply).
    """
    reg = WebRegistry.load(SHIPPED, None)
    handlers = {a.handler for a in reg.apps}
    expected = {
        "sparkle",
        "github_dmg",
        "msupdate",
        "release_feed",
        "keystone",
        "squirrel",
        "docker",
        "builtin",
    }
    assert expected.issubset(handlers)
    assert "omaha" not in handlers


def test_shipped_registry_no_duplicate_slugs() -> None:
    reg = WebRegistry.load(SHIPPED, None)
    slugs = [a.slug for a in reg.apps]
    assert len(slugs) == len(set(slugs)), \
        f"duplicate slugs: {[s for s in slugs if slugs.count(s) > 1]}"


def test_shipped_registry_chrome_uses_keystone() -> None:
    """macOS_updates channel is Google Keystone, not Version History API."""
    reg = WebRegistry.load(SHIPPED, None)
    chrome = reg.find("chrome")
    assert chrome is not None
    assert chrome.handler == "keystone"
    assert chrome.ksadmin_product_id == "com.google.Chrome"
    assert chrome.release_feed is None


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


def test_shipped_registry_docker_uses_docker_cli_handler() -> None:
    """macOS_updates channel is `docker desktop update --quiet`, not Sparkle."""
    reg = WebRegistry.load(SHIPPED, None)
    docker = reg.find("docker")
    assert docker is not None
    assert docker.handler == "docker"
    assert docker.appcast_url is None


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


def test_sources_has_no_claude_desktop_brew_row() -> None:
    """Claude Desktop is web/silent_launch in macOS_updates, not brew_cask.

    The leftover [[app]] id=claude with brew=claude made the deduplicator
    treat a web-only app as a brew+web duplicate.
    """
    text = SOURCES.read_text(encoding="utf-8")
    assert 'id = "claude"' not in text
    assert 'brew = "claude"' not in text
    registry = AppSourcesRegistry.load(SOURCES)
    assert all(app.id != "claude" for app in registry.apps)


def test_sources_brew_tokens_match_canonical_cask_set() -> None:
    """preferred_order brew,web rows must be exactly the brew_cask overlap set."""
    registry = AppSourcesRegistry.load(SOURCES)
    brew_tokens = set()
    for app in registry.apps:
        assert app.preferred_order[0] == "brew", app.id
        brew_tokens.add(app.sources["brew"])
    assert brew_tokens == _BREW_PREFERRED_CASKS


# macOS_updates internet_app_methods.txt → Ascendo web handler.
# brew_cask apps are NOT updated by the web category (Homebrew owns them).
_SISTER_WEB_HANDLERS = {
    "chrome": "keystone",
    "gdrive": "keystone",
    "firefox-dev": "release_feed",
    "claude": "squirrel",
    "chatgpt": "squirrel",
    "codex": "squirrel",
    "warp": "squirrel",
    "gemini": "squirrel",
    "comet": "squirrel",
    "antigravity": "squirrel",
    "antigravity-ide": "squirrel",
    "opencode": "squirrel",
    "cursor": "squirrel",
    "proton-mail": "squirrel",
    "keepassxc": "github_dmg",
    "codeedit": "github_dmg",
    "trezor-suite": "github_dmg",
    "vscode": "release_feed",
    "ledger-live": "release_feed",
    "docker": "docker",
    "rdm": "sparkle",
    "protondrive": "sparkle",
    "ms365": "msupdate",
    "ms-word": "msupdate",
    "ms-excel": "msupdate",
    "ms-powerpoint": "msupdate",
    "ms-outlook": "msupdate",
    "ms-onenote": "msupdate",
    "ms-teams": "msupdate",
    "ipmiview": "builtin",
    "dji-assistant": "builtin",
}

_BREW_OWNED_WEB_SLUGS = {
    "brave",
    "perplexity",
    "lm-studio",
    "protonvpn",
    "zoom",
    "megasync",
    "appcleaner",
    "obsidian",
    "spotify",
    "inkscape",
    "capcut",
}


def test_sources_brew_preferred_web_bundle_ids_cover_cask_overlap() -> None:
    """Web check/plan/apply must skip these bundle IDs — brew owns the cask."""
    registry = AppSourcesRegistry.load(SOURCES)
    ids = registry.brew_preferred_web_bundle_ids()
    by_slug = {app.id: app.sources["web"] for app in registry.apps}
    assert ids == set(by_slug.values())
    assert "us.zoom.xos" in ids
    assert "com.brave.Browser" in ids


def test_web_handlers_match_macos_updates_channels() -> None:
    """Same product must not use a second installer (DMG/Sparkle) vs sister."""
    reg = WebRegistry.load(SHIPPED, None)
    by_slug = {a.slug: a for a in reg.apps}
    for slug, handler in _SISTER_WEB_HANDLERS.items():
        app = by_slug.get(slug)
        assert app is not None, f"missing slug {slug}"
        assert app.handler == handler, f"{slug}: {app.handler} != {handler}"
    for slug in _BREW_OWNED_WEB_SLUGS:
        app = by_slug.get(slug)
        assert app is not None, f"missing brew-owned slug {slug}"
        assert app.bundle_id in AppSourcesRegistry.load(SOURCES).brew_preferred_web_bundle_ids()


def test_firefox_dev_downloads_official_mozilla_dmg() -> None:
    """Sister iu_firefox_developer_edition uses download.mozilla.org, not GitHub."""
    reg = WebRegistry.load(SHIPPED, None)
    ff = next(a for a in reg.apps if a.slug == "firefox-dev")
    assert ff.release_feed is not None
    assert ff.release_feed.download_url is not None
    assert "download.mozilla.org" in str(ff.release_feed.download_url)
    assert "firefox-devedition" in str(ff.release_feed.download_url)


def test_chrome_and_gdrive_use_keystone_product_ids() -> None:
    reg = WebRegistry.load(SHIPPED, None)
    chrome = next(a for a in reg.apps if a.slug == "chrome")
    gdrive = next(a for a in reg.apps if a.slug == "gdrive")
    assert chrome.ksadmin_product_id == "com.google.Chrome"
    assert gdrive.ksadmin_product_id == "com.google.drivefs"


def test_ledger_download_path_picks_dmg_not_index() -> None:
    """Ledger latest-mac.yml files[0]=zip, files[1] is often a blockmap.

    files[dmg].url must select the first files[].url ending in .dmg so
    sha512 is paired with the DMG, not the zip (macOS_updates 2026-08-25).
    """
    reg = WebRegistry.load(SHIPPED, None)
    ledger = reg.find("ledger-live")
    assert ledger is not None
    assert ledger.release_feed is not None
    assert ledger.release_feed.download_path == "files[dmg].url"


def test_teams_notes_document_msupdate_cannot_install() -> None:
    """Microsoft: do not use msupdate --install --apps TEAMS21 (learn.microsoft.com 2025-08-20)."""
    reg = WebRegistry.load(SHIPPED, None)
    teams = reg.find("ms-teams")
    assert teams is not None
    notes = (teams.notes or "").lower()
    assert "msupdate" in notes
    assert "teams21" in notes
    assert "cannot" in notes or "do not" in notes or "not admin" in notes


def test_retired_slugs_stay_gone() -> None:
    reg = WebRegistry.load(SHIPPED, None)
    slugs = {a.slug for a in reg.apps}
    for slug in ("chatgpt-atlas", "opera", "macwhisper", "notion", "notion-calendar"):
        assert slug not in slugs


def test_mas_ipad_apps_are_not_web_registry_rows() -> None:
    """UniFi / WiFiman / Picsart are mas_ipad in macOS_updates, not web handlers."""
    reg = WebRegistry.load(SHIPPED, None)
    slugs = {a.slug for a in reg.apps}
    names = {a.display_name.lower() for a in reg.apps}
    assert "unifi" not in slugs
    assert "wifiman" not in slugs
    assert "picsart" not in slugs
    assert not any("unifi" in n for n in names)
    assert not any("wifiman" in n for n in names)
    assert not any("picsart" in n for n in names)


def test_rf_walk_json_files_dmg_picks_dmg_not_zip() -> None:
    """Pytest coverage for Ledger files[dmg].url (same fixture as the bash harness)."""
    import subprocess
    import shutil

    if shutil.which("bash") is None:
        pytest.skip("bash required")
    handler = _ADAPTER / "lib" / "handlers" / "release_feed.sh"
    yml = (
        "version: 4.17.1\n"
        "files:\n"
        "  - url: ledger-live-desktop-4.17.1-mac.zip\n"
        "    sha512: AAA\n"
        "  - url: ledger-live-desktop-4.17.1-mac.blockmap\n"
        "    sha512: BBB\n"
        "  - url: ledger-live-desktop-4.17.1-mac.dmg\n"
        "    sha512: CCC\n"
    )
    script = f'. "{handler}" && _rf_walk_json "$1" "files[dmg].url"'
    res = subprocess.run(
        ["bash", "-c", script, "inline", yml],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ledger-live-desktop-4.17.1-mac.dmg"


def test_rdm_and_protondrive_sparkle_read_sufeedurl() -> None:
    """Sister sparkle_appcast has no static feed URL in the registry."""
    reg = WebRegistry.load(SHIPPED, None)
    rdm = reg.find("rdm")
    drive = reg.find("protondrive")
    assert rdm is not None and drive is not None
    assert rdm.handler == "sparkle" and rdm.appcast_url is None
    assert drive.handler == "sparkle" and drive.appcast_url is None
