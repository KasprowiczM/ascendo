"""Tests for WebRegistry schema v2 (bundle_id-keyed + release_feed)."""
from __future__ import annotations

from pathlib import Path

import pytest
from ascendo_macos.web_registry import WebRegistry


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "apps.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_v2_schema_loads_with_bundle_id_required(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
""")
    reg = WebRegistry.load(p, None)
    assert reg.schema_version == "ascendo-web-apps/v2"
    assert len(reg.apps) == 1
    assert reg.apps[0].bundle_id == "com.brave.Browser"


def test_v1_schema_auto_coerces_to_v2(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v1"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
""")
    reg = WebRegistry.load(p, None)
    assert reg.schema_version == "ascendo-web-apps/v2"
    assert reg.apps[0].slug == "brave"


def test_v2_release_feed_handler_validates(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "warp"
bundle_id = "dev.warp.Warp-Stable"
display_name = "Warp"
handler = "release_feed"

[apps.release_feed]
url = "https://desktop.warp.dev/version.json"
version_path = "latest.darwin.arm64.version"
""")
    reg = WebRegistry.load(p, None)
    app = reg.apps[0]
    assert app.handler == "release_feed"
    assert app.release_feed is not None
    assert str(app.release_feed.url) == "https://desktop.warp.dev/version.json"
    assert app.release_feed.version_path == "latest.darwin.arm64.version"


def test_release_feed_rejects_non_https(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "x"
bundle_id = "com.example.X"
display_name = "X"
handler = "release_feed"

[apps.release_feed]
url = "http://insecure.example.com/feed"
version_path = "version"
""")
    with pytest.raises(Exception):
        WebRegistry.load(p, None)


def test_find_by_bundle_id(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
""")
    reg = WebRegistry.load(p, None)
    app = reg.find_by_bundle_id("com.brave.Browser")
    assert app is not None
    assert app.slug == "brave"
    assert reg.find_by_bundle_id("com.nope.Nope") is None
