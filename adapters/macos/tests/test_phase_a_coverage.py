"""Phase A coverage closeout (M5.7.6, 2026-05-24).

Pins the registry coverage delta added in this session:
  * 5 new entries (antigravity-ide, appcleaner, protonvpn, protondrive, ipmiview)
  * 4 dead entries dropped (opera, macwhisper, notion, notion-calendar)
  * codeedit re-enabled with the correct universal asset pattern
  * com.microsoft.autoupdate2 retagged category=infrastructure
  * `category` field landed on the WebApp schema with default "app"

Regression intent: a future operator must not accidentally re-introduce
the dropped entries or undo the codeedit fix without an updated test.
"""
from pathlib import Path

import pytest

from ascendo_macos.web_registry import WebRegistry


REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "web_apps.toml"
)


@pytest.fixture(scope="module")
def registry() -> WebRegistry:
    return WebRegistry.load(REGISTRY_PATH, None)


def test_registry_loads(registry: WebRegistry) -> None:
    assert len(registry.apps) >= 38
    # All entries must be schema-valid and have unique bundle ids.
    bundles = [a.bundle_id for a in registry.apps]
    assert len(bundles) == len(set(bundles)), "duplicate bundle_id in registry"


@pytest.mark.parametrize(
    "slug,bundle_id,handler",
    [
        ("antigravity-ide", "com.google.antigravity-ide", "release_feed"),
        ("appcleaner", "net.freemacsoft.AppCleaner", "sparkle"),
        ("protonvpn", "ch.protonvpn.mac", "sparkle"),
        ("protondrive", "ch.protonmail.drive", "release_feed"),
        ("ipmiview", "com.supermicro.IPMIView", "builtin"),
    ],
)
def test_new_entries_present(
    registry: WebRegistry, slug: str, bundle_id: str, handler: str
) -> None:
    app = registry.find(slug)
    assert app is not None, f"missing slug {slug!r}"
    assert app.bundle_id == bundle_id
    assert app.handler == handler
    assert app.enabled is True


@pytest.mark.parametrize(
    "slug",
    ["opera", "macwhisper", "notion", "notion-calendar"],
)
def test_dead_entries_dropped(registry: WebRegistry, slug: str) -> None:
    assert registry.find(slug) is None, f"{slug!r} should have been removed"


def test_codeedit_reenabled_universal(registry: WebRegistry) -> None:
    a = registry.find("codeedit")
    assert a is not None
    assert a.enabled is True
    assert a.arch == "universal"
    assert a.asset_pattern == r"^CodeEdit\.dmg$"


def test_msautoupdate_tagged_infrastructure(registry: WebRegistry) -> None:
    a = registry.find("ms365")
    assert a is not None
    assert a.category == "infrastructure"


def test_category_defaults_to_app(registry: WebRegistry) -> None:
    """All other entries must default to category='app'."""
    non_app = [a for a in registry.apps if a.category != "app"]
    assert {a.slug for a in non_app} == {"ms365"}, (
        f"expected only ms365 to be non-app; got {[a.slug for a in non_app]}"
    )
