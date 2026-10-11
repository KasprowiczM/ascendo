"""sparkle.sh handler tests."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"


def _run(snippet: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    full = textwrap.dedent(f"""\
        set -eo pipefail
        source {LIB}/ascendo_web.sh
        source {LIB}/handlers/sparkle.sh
        {snippet}
    """)
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)


SAMPLE_APPCAST = """<?xml version="1.0"?>
<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
<channel>
  <item>
    <title>Brave 1.95.0</title>
    <enclosure url="https://updates.bravesoftware.com/Brave-1.95.0.dmg"
               sparkle:shortVersionString="1.95.0"
               sparkle:version="195000"
               type="application/octet-stream"/>
  </item>
  <item>
    <enclosure url="https://updates.bravesoftware.com/Brave-1.94.0.dmg"
               sparkle:shortVersionString="1.94.0"/>
  </item>
</channel></rss>"""


def test_sparkle_check_extracts_latest_version(tmp_path: Path) -> None:
    appcast_file = tmp_path / "appcast.xml"
    appcast_file.write_text(SAMPLE_APPCAST)
    cfg = json.dumps({
        "slug": "brave",
        "appcast_url": f"file://{appcast_file}",
    })
    snippet = f"sparkle_check 'brave' {json.dumps(cfg)!r}"
    r = _run(snippet)
    assert r.returncode == 0
    assert r.stdout.strip() == "1.95.0"


def test_sparkle_check_returns_empty_on_unreachable() -> None:
    cfg = json.dumps({
        "slug": "brave",
        "appcast_url": "https://nonexistent.invalid/appcast.xml",
    })
    snippet = f"sparkle_check 'brave' {json.dumps(cfg)!r} || true"
    r = _run(snippet)
    # Empty output is the "unknown" signal
    assert r.stdout.strip() == ""


def test_sparkle_apply_uses_apply_cli_argv_when_set() -> None:
    cfg = json.dumps({
        "slug": "test",
        "apply_cli_argv": ["/bin/echo", "ok-from-cli"],
    })
    snippet = f"sparkle_apply 'test' {json.dumps(cfg)!r}"
    r = _run(snippet)
    assert r.returncode == 0
    assert "ok-from-cli" in r.stdout


def test_sparkle_apply_falls_back_to_dmg_when_no_cli(tmp_path: Path) -> None:
    cfg = json.dumps({
        "slug": "test",
        "appcast_url": "https://nonexistent.invalid/appcast.xml",
    })
    snippet = (
        f"sparkle_apply 'test' {json.dumps(cfg)!r} || rc=$?\n"
        "echo \"rc=$rc\""
    )
    r = _run(snippet)
    # Non-zero rc expected (download or enclosure-extract fail)
    assert "rc=" in r.stdout
    assert "rc=0" not in r.stdout


# ---------------------------------------------------------------------------
# Vendor-truth parser parity (Spec D): rdm short->build fallback, channel and
# minimumSystemVersion filters, arm64/x86 filter kept, no regex guessing.
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vendor_feeds"
HERMETIC = {"ASCENDO_WEB_HOST_OS_VERSION": "27.0", "ASCENDO_WEB_HOST_ARCH": "arm64"}


def _check_fixture(name: str, env: dict | None = None) -> str:
    cfg = json.dumps({"slug": "t", "appcast_url": f"file://{FIXTURES / name}"})
    r = _run(f"sparkle_check t {json.dumps(cfg)!r}", {**HERMETIC, **(env or {})})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_sparkle_check_rdm_build_only_items_parse_to_real_newest_version() -> None:
    assert _check_fixture("rdm_build_only.xml") == "2026.3.0.5"


def test_sparkle_check_upstream_rdm_ascending_fixture_picks_max_not_first() -> None:
    assert _check_fixture("rdm_ascending.xml") == "2023.1.12.0"


def test_sparkle_check_ignores_beta_channel_item() -> None:
    assert _check_fixture("protonvpn_channels.xml") == "6.5.1"


def test_sparkle_check_skips_item_needing_newer_macos() -> None:
    assert _check_fixture("sparkle_min_os.xml") == "1.2.0"
    assert _check_fixture("sparkle_min_os.xml", {"ASCENDO_WEB_HOST_OS_VERSION": "99.5"}) == "2.0.0"


def test_sparkle_check_keeps_arch_filter_per_host_arch() -> None:
    assert _check_fixture("sparkle_arch_mixed.xml") == "2.0.0"
    assert _check_fixture("sparkle_arch_mixed.xml", {"ASCENDO_WEB_HOST_ARCH": "x86_64"}) == "3.0.0"
    assert _check_fixture("sparkle_arch_url_only.xml") == "4.0.0"


def test_sparkle_check_garbage_xml_is_empty_not_a_regex_guess(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text('<rss><item sparkle:shortVersionString="9.9.9"><enclosure url="https://e/x.dmg"')
    cfg = json.dumps({"slug": "t", "appcast_url": f"file://{bad}"})
    r = _run(f"sparkle_check t {json.dumps(cfg)!r}", HERMETIC)
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_sparkle_apply_installs_the_arch_matching_enclosure(tmp_path: Path) -> None:
    cfg = json.dumps({"slug": "t", "app_path": "/Applications/T.app",
                      "appcast_url": f"file://{FIXTURES / 'sparkle_arch_mixed.xml'}"})
    snippet = ('_web_install_dmg() { echo "INSTALL $1 $2 $3"; }\n'
               f"sparkle_apply t {json.dumps(cfg)!r}")
    r = _run(snippet, HERMETIC)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "INSTALL t https://example.test/App-2.0.0-arm64.zip /Applications/T.app"
    r = _run(snippet, {**HERMETIC, "ASCENDO_WEB_HOST_ARCH": "x86_64"})
    assert "App-3.0.0-x86_64.zip" in r.stdout


def test_sparkle_apply_url_with_spaces_is_percent_encoded() -> None:
    cfg = json.dumps({"slug": "t", "app_path": "/Applications/T.app",
                      "appcast_url": f"file://{FIXTURES / 'rdm_build_only.xml'}"})
    snippet = ('_web_install_dmg() { echo "INSTALL $2"; }\n'
               f"sparkle_apply t {json.dumps(cfg)!r}")
    r = _run(snippet, HERMETIC)
    assert r.stdout.strip() == "INSTALL https://cdn.devolutions.net/RemoteDesktopManager%202026.3.0.5.dmg"
