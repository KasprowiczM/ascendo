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


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
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


def test_v1_schema_emits_deprecation_warning(tmp_path: Path) -> None:
    """Spec §5.1: a single deprecation message per load when v1 is seen."""
    import warnings as _warnings
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v1"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
""")
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        WebRegistry.load(p, None)
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1
    assert "ascendo-web-apps/v1" in str(deprecations[0].message)


def test_release_feed_version_regex_pair_loads(tmp_path: Path) -> None:
    """M5.7.4: version_regex + version_replace round-trip when supplied together."""
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "warp"
bundle_id = "dev.warp.Warp-Stable"
display_name = "Warp"
handler = "release_feed"

[apps.release_feed]
url = "https://releases.warp.dev/channel_versions.json"
version_path = "stable.version"
version_regex = "^v(.+)\\\\.stable_(.+)$"
version_replace = "\\\\1.\\\\2"
http_timeout_s = 8
""")
    reg = WebRegistry.load(p, None)
    app = reg.apps[0]
    assert app.handler == "release_feed"
    assert app.release_feed is not None
    assert app.release_feed.version_regex == r"^v(.+)\.stable_(.+)$"
    assert app.release_feed.version_replace == r"\1.\2"


def test_release_feed_version_regex_xor_rejected(tmp_path: Path) -> None:
    """M5.7.4: regex without replace (or vice versa) is a config typo."""
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "warp"
bundle_id = "dev.warp.Warp-Stable"
display_name = "Warp"
handler = "release_feed"

[apps.release_feed]
url = "https://releases.warp.dev/channel_versions.json"
version_path = "stable.version"
version_regex = "^v(.+)$"
""")
    with pytest.raises(Exception) as exc:
        WebRegistry.load(p, None)
    assert "version_regex" in str(exc.value) or "version_replace" in str(exc.value)


def test_release_feed_version_regex_invalid_pattern_rejected(tmp_path: Path) -> None:
    """M5.7.4: a malformed regex must fail at registry load time."""
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "warp"
bundle_id = "dev.warp.Warp-Stable"
display_name = "Warp"
handler = "release_feed"

[apps.release_feed]
url = "https://releases.warp.dev/channel_versions.json"
version_path = "stable.version"
version_regex = "[unclosed"
version_replace = "x"
""")
    with pytest.raises(Exception) as exc:
        WebRegistry.load(p, None)
    assert "regex" in str(exc.value).lower()


def test_release_feed_download_asset_pattern_loads(tmp_path: Path) -> None:
    """M5.7.5: download_asset_pattern lets release_feed apply pick a
    DMG out of GitHub's assets[] by name regex (Brave use case —
    ~30 platform assets per release; we want only universal DMG)."""
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "release_feed"

[apps.release_feed]
url = "https://api.github.com/repos/brave/brave-browser/releases/latest"
version_path = "name"
download_asset_pattern = "^Brave-Browser-universal\\\\.dmg$"
http_timeout_s = 12
""")
    reg = WebRegistry.load(p, None)
    app = reg.apps[0]
    assert app.handler == "release_feed"
    assert app.release_feed is not None
    assert app.release_feed.download_asset_pattern == r"^Brave-Browser-universal\.dmg$"
    assert app.release_feed.download_path is None


def test_release_feed_download_asset_pattern_xor_download_path(tmp_path: Path) -> None:
    """M5.7.5: download_asset_pattern + download_path together is ambiguous."""
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "release_feed"

[apps.release_feed]
url = "https://api.github.com/repos/brave/brave-browser/releases/latest"
version_path = "name"
download_path = "assets[0].browser_download_url"
download_asset_pattern = "^Brave-Browser-universal\\\\.dmg$"
""")
    with pytest.raises(Exception) as exc:
        WebRegistry.load(p, None)
    msg = str(exc.value).lower()
    assert "download_asset_pattern" in msg and "download_path" in msg


def test_release_feed_download_asset_pattern_invalid_regex_rejected(tmp_path: Path) -> None:
    """M5.7.5: a malformed asset regex must fail at registry load time."""
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "release_feed"

[apps.release_feed]
url = "https://api.github.com/repos/brave/brave-browser/releases/latest"
version_path = "name"
download_asset_pattern = "[unclosed"
""")
    with pytest.raises(Exception) as exc:
        WebRegistry.load(p, None)
    assert "regex" in str(exc.value).lower()


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


# ---------------------------------------------------------------- vendor_feed

def _app_with_feed(handler: str, feed_body: str, extra: str = "") -> str:
    return f"""
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "demo"
bundle_id = "com.example.demo"
display_name = "Demo"
handler = "{handler}"
{extra}

[apps.vendor_feed]
{feed_body}
"""


_JSON_FEED = '''kind = "json"
url = "https://downloads.claude.ai/releases/darwin/universal/RELEASES.json"
arg = "claude_releases"
artifact = "zip"
allowed_host = "downloads.claude.ai"
'''


def test_vendor_feed_loads_on_squirrel_and_docker(tmp_path: Path) -> None:
    for handler in ("squirrel", "docker"):
        reg = WebRegistry.load(_write(tmp_path, _app_with_feed(handler, _JSON_FEED)), None)
        vf = reg.apps[0].vendor_feed
        assert vf is not None
        assert (vf.kind, vf.arg, vf.artifact, vf.allowed_host, vf.direct_first) == (
            "json", "claude_releases", "zip", "downloads.claude.ai", False)


def test_vendor_feed_dump_reaches_bash_as_nested_json(tmp_path: Path) -> None:
    import json
    reg = WebRegistry.load(_write(tmp_path, _app_with_feed("squirrel", _JSON_FEED + "direct_first = true\n")), None)
    dumped = json.loads(json.dumps(reg.apps[0].model_dump(mode="json")))
    assert dumped["vendor_feed"]["url"].startswith("https://downloads.claude.ai/")
    assert dumped["vendor_feed"]["direct_first"] is True


@pytest.mark.parametrize("handler,extra", [
    ("sparkle", ""),
    ("builtin", ""),
    ("github_dmg", 'github_repo = "a/b"\nasset_pattern = "x"'),
    ("keystone", 'ksadmin_product_id = "p"'),
])
def test_vendor_feed_rejected_on_other_handlers(tmp_path: Path, handler: str, extra: str) -> None:
    with pytest.raises(Exception, match="vendor_feed"):
        WebRegistry.load(_write(tmp_path, _app_with_feed(handler, _JSON_FEED, extra)), None)


def test_vendor_feed_unknown_json_selector_rejected(tmp_path: Path) -> None:
    bad = _JSON_FEED.replace("claude_releases", "no_such_selector")
    with pytest.raises(Exception, match="selector"):
        WebRegistry.load(_write(tmp_path, _app_with_feed("squirrel", bad)), None)


def test_vendor_feed_selector_list_matches_vendor_feeds_module() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    import vendor_feeds
    from ascendo_macos.web_registry import VENDOR_FEED_JSON_SELECTORS
    assert set(VENDOR_FEED_JSON_SELECTORS) == set(vendor_feeds.JSON_SELECTORS)


def test_vendor_feed_kv_requires_prefix(tmp_path: Path) -> None:
    kv = '''kind = "kv"
url = "https://devolutions.net/productinfo.htm"
arg = "-"
artifact = "dmg"
allowed_host = "cdn.devolutions.net"
'''
    with pytest.raises(Exception, match="kv"):
        WebRegistry.load(_write(tmp_path, _app_with_feed("squirrel", kv)), None)


def test_vendor_feed_artifact_requires_allowed_host(tmp_path: Path) -> None:
    bad = _JSON_FEED.replace('allowed_host = "downloads.claude.ai"', 'allowed_host = "-"')
    with pytest.raises(Exception, match="allowed_host"):
        WebRegistry.load(_write(tmp_path, _app_with_feed("squirrel", bad)), None)


def test_vendor_feed_verify_only_artifact_dash_requires_host_dash(tmp_path: Path) -> None:
    bad = '''kind = "json"
url = "https://releases.warp.dev/channel_versions.json"
arg = "warp_channels"
artifact = "-"
allowed_host = "releases.warp.dev"
'''
    with pytest.raises(Exception, match="allowed_host"):
        WebRegistry.load(_write(tmp_path, _app_with_feed("squirrel", bad)), None)
    ok = bad.replace('allowed_host = "releases.warp.dev"', 'allowed_host = "-"')
    reg = WebRegistry.load(_write(tmp_path, _app_with_feed("squirrel", ok)), None)
    assert reg.apps[0].vendor_feed.artifact == "-"


def test_vendor_feed_rejects_http_url_and_unknown_keys(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        WebRegistry.load(_write(tmp_path, _app_with_feed(
            "squirrel", _JSON_FEED.replace("https://", "http://"))), None)
    with pytest.raises(Exception):
        WebRegistry.load(_write(tmp_path, _app_with_feed(
            "squirrel", _JSON_FEED + 'bogus = 1\n')), None)


def test_vendor_feed_rejects_bad_host_characters(tmp_path: Path) -> None:
    bad = _JSON_FEED.replace('allowed_host = "downloads.claude.ai"', 'allowed_host = "a b/c"')
    with pytest.raises(Exception):
        WebRegistry.load(_write(tmp_path, _app_with_feed("squirrel", bad)), None)


# ------------------------------------------------------------ chromium_updater

_CU_APP = '''
schema = "ascendo-web-apps/v2"

[[app]]
slug = "chrome"
bundle_id = "com.google.Chrome"
display_name = "Google Chrome"
handler = "chromium_updater"
{extra}
'''


def test_chromium_updater_defaults(tmp_path: Path) -> None:
    reg = WebRegistry.load(_write(tmp_path, _CU_APP.format(extra='''
[app.chromium_updater]
appid = "com.google.chrome"
version_history_url = "https://versionhistory.googleapis.com/v1/chrome/platforms/mac_arm64/channels/stable/versions/all/releases?filter=endtime=none"
''')), None)
    cu = reg.apps[0].chromium_updater
    assert cu is not None and cu.appid == "com.google.chrome"
    assert cu.updater_bin is None and cu.updater_log is None
    assert cu.wake_args == ["--wake-all"]
    assert cu.history_max_age_h == 6
    assert cu.wait_s == 45
    assert str(cu.version_history_url).startswith("https://versionhistory.googleapis.com/")


def test_chromium_updater_requires_subtable(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="chromium_updater"):
        WebRegistry.load(_write(tmp_path, _CU_APP.format(extra="")), None)


def test_chromium_updater_wait_clamped_by_schema(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        WebRegistry.load(_write(tmp_path, _CU_APP.format(extra='''
[app.chromium_updater]
appid = "com.google.chrome"
wait_s = 500
''')), None)


def test_chromium_updater_rejects_http_version_history(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        WebRegistry.load(_write(tmp_path, _CU_APP.format(extra='''
[app.chromium_updater]
appid = "com.google.chrome"
version_history_url = "http://example.com/vh"
''')), None)


def test_chromium_updater_rejects_unknown_field(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        WebRegistry.load(_write(tmp_path, _CU_APP.format(extra='''
[app.chromium_updater]
appid = "com.google.chrome"
bogus = 1
''')), None)


def test_chromium_updater_subtable_rejected_on_other_handlers(tmp_path: Path) -> None:
    body = '''
schema = "ascendo-web-apps/v2"

[[app]]
slug = "x"
bundle_id = "com.x.X"
display_name = "X"
handler = "builtin"

[app.chromium_updater]
appid = "com.x"
'''
    with pytest.raises(Exception, match="chromium_updater"):
        WebRegistry.load(_write(tmp_path, body), None)


def test_keystone_remains_valid_for_user_overrides(tmp_path: Path) -> None:
    body = '''
schema = "ascendo-web-apps/v2"

[[app]]
slug = "chrome"
bundle_id = "com.google.Chrome"
display_name = "Google Chrome"
handler = "keystone"
ksadmin_product_id = "com.google.Chrome"
'''
    reg = WebRegistry.load(_write(tmp_path, body), None)
    assert reg.apps[0].handler == "keystone"


def test_vendor_feed_rejected_on_chromium_updater(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="vendor_feed"):
        WebRegistry.load(_write(tmp_path, _CU_APP.format(extra='''
[app.chromium_updater]
appid = "com.google.chrome"

[app.vendor_feed]
kind = "yml"
url = "https://example.com/latest.yml"
''')), None)
