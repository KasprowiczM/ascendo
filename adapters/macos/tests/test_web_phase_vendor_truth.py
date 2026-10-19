"""check / plan / apply phases for feed-backed squirrel + docker apps, and the
rc 1 => soft rule for the installer-backed handlers (Task 11).

End to end through the real phase scripts against the Task-10 sandbox: fake
curl / hdiutil / spctl / codesign / mv / ditto / lsappinfo / open / sleep, a temp
"Applications" root, the vendor feed served from ASCENDO_WEB_VENDOR_FEED_DIR.
Nothing touches /Applications or the network; `open` is a logging stub (fails by
default => any unexpected launch shows up in forbidden.log).
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from .test_web_vendor_install import BID, SCRIPTS, Sandbox, make_app, script, zip_of
from .test_web_phase_check_plan import _make_fake_app

ADAPTER = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER.parents[1]
FIX = Path(__file__).resolve().parent / "fixtures" / "vendor_feeds"

CLAUDE_BODY = (FIX / "claude_releases.json").read_text()      # 2.7032.0 -> downloads.claude.ai/...zip
CLAUDE_ZIP_PART = "Claude-6c468ab6ed862a68c9555cce34f11186c35f526d.zip"
DOCKER_BODY = (FIX / "docker_attr.xml").read_text()            # 4.92.0 -> desktop.docker.com/.../Docker.dmg


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def registry(sb: Sandbox, *, handler="squirrel", slug="vt", direct_first=False, feed: dict | None = None,
             extra: str = "") -> Path:
    app = sb.apps / "Example.app"
    lines = ['schema = "ascendo-web-apps/v1"', "[[app]]", f'slug = "{slug}"', f'bundle_id = "{BID}"',
             'display_name = "Example"', f'handler = "{handler}"', f'app_path = "{app}"']
    if extra:
        lines.append(extra)
    if feed is None:
        feed = {"kind": "json", "url": "https://downloads.claude.ai/r.json", "arg": "claude_releases",
                "artifact": "zip", "allowed_host": "downloads.claude.ai"}
    if feed:
        lines += ["[app.vendor_feed]"] + [f'{k} = "{v}"' for k, v in feed.items()]
        lines.append(f"direct_first = {'true' if direct_first else 'false'}")
    reg = sb.tmp / "registry.toml"
    reg.write_text("\n".join(lines) + "\n")
    return reg


def stub_open(sb: Sandbox, bumps_to: str | None = None) -> Path:
    """`open` that records every call; optionally rewrites the installed version
    (a native updater that installs while the app is launched)."""
    app = sb.apps / "Example.app" / "Contents" / "Info.plist"
    bump = f'/usr/bin/sed -i "" "s|<string>1.0</string>|<string>{bumps_to}</string>|" "{app}"\n' if bumps_to else ""
    return script(sb.bin / "open", f'echo "open $*" >> "{sb.log}/open.log"\n{bump}exit 0\n')


def stub_sleep(sb: Sandbox) -> Path:
    return script(sb.bin / "sleep_stub", f'echo "$*" >> "{sb.log}/sleep.log"\nexit 0\n')


def run_apply(sb: Sandbox, reg: Path, feed_body: str | None = None, feed_slug: str = "vt", **extra):
    feeds = sb.tmp / "feeds"
    feeds.mkdir(exist_ok=True)
    if feed_body is not None:
        (feeds / f"{feed_slug}.feed").write_text(feed_body)
    out = sb.tmp / "out"
    env = sb.env(ASCENDO_WEB_REGISTRY_PATH=reg, ASCENDO_WEB_USER_REGISTRY_PATH="",
                 ASCENDO_SUDO_WARM_DISABLE="1", ASCENDO_WEB_VENDOR_FEED_DIR=feeds,
                 ASCENDO_WEB_SLEEP_BIN=sb.bin / "sleep_stub",
                 ASCENDO_WEB_DOCKER_BIN=sb.tmp / "no-docker",
                 PYTHONPATH=f"{REPO_ROOT}/core:{ADAPTER}", **extra)
    (sb.bin / "sleep_stub").exists() or stub_sleep(sb)
    run_id = str(uuid.uuid4())
    res = subprocess.run(["bash", str(SCRIPTS / "apply.sh"), "--run-id", run_id, "--trigger", "cli",
                          "--profile", "full", "--output-dir", str(out)],
                         capture_output=True, text=True, env=env)
    return res, json.loads((out / run_id / "apply__web.json").read_text())


def item_of(sc: dict) -> dict:
    (item,) = sc["items"]
    return item


def texts(sc: dict) -> str:
    return " || ".join(m["text"] for m in sc["messages"])


def codes(sc: dict) -> list:
    return [m.get("code") for m in sc["messages"]]


def prime_claude(sb: Sandbox, *, new_team="ABCDE12345"):
    """Installed 1.0 + the vendor zip containing 2.7032.0 (same bundle id, same team)."""
    make_app(sb.apps, ver="1.0", payload="old")
    new_parent = sb.fx / "new"
    new_parent.mkdir(exist_ok=True)
    new = make_app(new_parent, ver="2.7032.0", payload="new", team=new_team)
    sb.serve(CLAUDE_ZIP_PART, zip_of(sb.fx / "Claude.zip", new))


def calls(sb: Sandbox, name: str) -> list[str]:
    return sb.read_log(name)


# ============================================================================
# apply.sh: feed-backed squirrel
# ============================================================================

def test_apply_direct_first_idle_installs_verified_artifact_without_launch(sb):
    prime_claude(sb)
    stub_open(sb)
    res, sc = run_apply(sb, registry(sb, direct_first=True), CLAUDE_BODY)
    assert res.returncode == 0, res.stderr
    item = item_of(sc)
    assert item["status"] == "success", (sc, res.stderr)
    assert item["current_version"] == "2.7032.0" and item["target_version"] == "2.7032.0"
    assert sb.payload() == "new" and sb.leftovers() == []
    assert calls(sb, "open.log") == [], "direct-first must not launch the app"
    assert sb.forbidden() == []


def test_apply_non_direct_first_default_safe_mode_installs_without_launching(sb):
    """apply.sh forces ASCENDO_SAFE_MODE=true unless ASCENDO_WEB_ALLOW_GUI=1: the native
    launch cycle is skipped and the verified vendor artifact installs the idle app."""
    prime_claude(sb)
    stub_open(sb)
    res, sc = run_apply(sb, registry(sb, direct_first=False), CLAUDE_BODY)
    assert item_of(sc)["status"] == "success", (sc, res.stderr)
    assert sb.payload() == "new"
    assert calls(sb, "open.log") == []


def test_apply_running_app_is_needs_restart_and_untouched(sb):
    prime_claude(sb)
    stub_open(sb)
    sb.set_ls("true")
    res, sc = run_apply(sb, registry(sb, direct_first=True), CLAUDE_BODY)
    item = item_of(sc)
    assert item["status"] == "skipped", sc
    assert "ASCENDO-ACTION-REQUIRED reason=needs_restart cur=1.0 cand=2.7032.0" in texts(sc)
    assert "needs_restart" in codes(sc)
    assert sb.payload() == "old" and sb.leftovers() == []
    assert calls(sb, "open.log") == [] and calls(sb, "curl.log") == []
    assert sc["status"] != "failed"


def test_apply_unknown_running_state_is_needs_restart(sb):
    prime_claude(sb)
    stub_open(sb)
    sb.set_ls("unknown")
    res, sc = run_apply(sb, registry(sb, direct_first=True), CLAUDE_BODY)
    assert item_of(sc)["status"] == "skipped"
    assert "reason=needs_restart" in texts(sc)
    assert sb.payload() == "old" and calls(sb, "open.log") == []


def test_apply_installer_deferral_rc4_is_needs_restart(sb):
    """Idle at the first probe, running by the installer's own probe."""
    prime_claude(sb)
    stub_open(sb)
    sb.set_ls("false", "true")
    res, sc = run_apply(sb, registry(sb, direct_first=True), CLAUDE_BODY)
    assert item_of(sc)["status"] == "skipped", (sc, res.stderr)
    assert "reason=needs_restart" in texts(sc)
    assert sb.payload() == "old" and calls(sb, "open.log") == []


@pytest.mark.parametrize("installed,expect_status,expect_text", [
    ("2.7032.0", "up_to_date", None),
    ("3.0", "skipped", "feed_stale"),
    ("dev-build", "skipped", "unknown_version"),
])
def test_apply_equal_older_unknown_never_launch_or_install(sb, installed, expect_status, expect_text):
    make_app(sb.apps, ver=installed, payload="old")
    stub_open(sb)
    res, sc = run_apply(sb, registry(sb, direct_first=False), CLAUDE_BODY)
    item = item_of(sc)
    assert item["status"] == expect_status, sc
    if expect_text:
        assert expect_text in texts(sc) and expect_text in codes(sc)
        assert not item.get("target_version")
    assert sb.payload() == "old"
    assert calls(sb, "open.log") == [] and calls(sb, "curl.log") == []


def test_apply_feed_unreachable_is_skipped_probe_unavailable(sb):
    make_app(sb.apps, ver="1.0", payload="old")
    stub_open(sb)
    res, sc = run_apply(sb, registry(sb), None)               # no feed file => unreachable
    item = item_of(sc)
    assert item["status"] == "skipped" and "probe_unavailable" in texts(sc)
    assert calls(sb, "open.log") == [] and sb.payload() == "old"


def test_apply_native_launch_cycle_observes_the_update(sb):
    make_app(sb.apps, ver="1.0", payload="old")
    stub_open(sb, bumps_to="2.7032.0")
    res, sc = run_apply(sb, registry(sb, direct_first=False), CLAUDE_BODY,
                        ASCENDO_WEB_ALLOW_GUI="1", ASCENDO_WEB_STAGE_WAIT="3")
    item = item_of(sc)
    assert item["status"] == "success", (sc, res.stderr)
    assert item["current_version"] == "2.7032.0"
    assert len(calls(sb, "open.log")) == 1 and "-gjF" in calls(sb, "open.log")[0]
    assert calls(sb, "sleep.log") == [] and calls(sb, "curl.log") == []


def test_apply_native_cycle_that_does_nothing_reports_launched_and_never_installs(sb):
    """M1: Ascendo launched the app hidden and its updater did nothing. The app is now
    running because of us, so no vendor-direct install follows (it would only fail with
    a misleading "quit the app"). The item is `triggered` and says so."""
    prime_claude(sb)
    stub_open(sb)
    stub_sleep(sb)
    # A real lsappinfo: idle until `open` has been called, running afterwards.
    script(sb.bin / "lsappinfo",
           f'''if [ -f "{sb.log}/open.log" ]; then echo 'bundleID="com.apple.finder"'; echo 'bundleID="{BID}"';
else echo 'bundleID="com.apple.finder"'; fi
exit 0
''')
    res, sc = run_apply(sb, registry(sb, direct_first=False), CLAUDE_BODY,
                        ASCENDO_WEB_ALLOW_GUI="1", ASCENDO_WEB_STAGE_WAIT="2")
    item = item_of(sc)
    assert item["status"] == "triggered", (sc, res.stderr)
    assert "launched" in codes(sc) and "launched the app hidden" in texts(sc)
    assert "needs_restart" not in codes(sc) and "reason=needs_restart" not in texts(sc)
    assert len(calls(sb, "open.log")) == 1
    assert calls(sb, "sleep.log") == ["1", "1"]
    assert calls(sb, "curl.log") == []          # no vendor download after the launch
    assert sb.payload() == "old" and sc["status"] != "failed"


def test_apply_direct_download_failure_safe_mode_is_skipped_not_failed(sb):
    prime_claude(sb)
    stub_open(sb)
    res, sc = run_apply(sb, registry(sb, direct_first=True), CLAUDE_BODY, FAKE_CURL_RC="22")
    item = item_of(sc)
    assert item["status"] == "skipped", (sc, res.stderr)
    assert "error_soft" in codes(sc) and "reason=download_error" in texts(sc)
    assert sb.payload() == "old" and calls(sb, "open.log") == []
    assert sc["status"] != "failed"


def test_apply_direct_download_failure_retains_native_fallback_as_triggered(sb):
    prime_claude(sb)
    stub_open(sb)
    res, sc = run_apply(sb, registry(sb, direct_first=True), CLAUDE_BODY, FAKE_CURL_RC="22",
                        ASCENDO_WEB_ALLOW_GUI="1")
    item = item_of(sc)
    assert item["status"] == "triggered", (sc, res.stderr)
    assert "error_soft" in codes(sc)
    assert len(calls(sb, "open.log")) == 1
    assert sb.payload() == "old"


def test_apply_hard_install_failure_stays_failed(sb):
    prime_claude(sb, new_team="ZZZZZ99999")                    # Team-ID mismatch => rc 3
    stub_open(sb)
    res, sc = run_apply(sb, registry(sb, direct_first=True), CLAUDE_BODY)
    item = item_of(sc)
    assert item["status"] == "failed", (sc, res.stderr)
    assert sb.payload() == "old" and calls(sb, "open.log") == []


def test_apply_vendor_direct_kill_switch_reports_behind(sb):
    prime_claude(sb)
    stub_open(sb)
    res, sc = run_apply(sb, registry(sb, direct_first=False), CLAUDE_BODY, ASCENDO_WEB_VENDOR_DIRECT="0")
    assert item_of(sc)["status"] == "skipped"
    assert "reason=behind cur=1.0 cand=2.7032.0" in texts(sc)
    assert sb.payload() == "old" and calls(sb, "open.log") == [] and calls(sb, "curl.log") == []


def test_apply_staged_rollout_never_installs_directly(sb):
    make_app(sb.apps, ver="1.0", payload="old")
    stub_open(sb)
    body = json.dumps({"Releases": [{"CategoryName": "Stable", "Version": "2.0", "RolloutProportion": 0.2,
                                     "File": [{"Identifier": "Apple Disk Image",
                                               "Url": "https://proton.me/update.dmg",
                                               "Sha512CheckSum": "abc"}]}]})
    reg = registry(sb, direct_first=True, feed={"kind": "json", "url": "https://proton.me/v.json",
                                                "arg": "proton_releases", "artifact": "dmg",
                                                "allowed_host": "proton.me"})
    res, sc = run_apply(sb, reg, body)
    item = item_of(sc)
    assert item["status"] == "skipped", sc
    assert "rollout_hold" in codes(sc) and "reason=" not in texts(sc)    # soft, not action-required
    assert calls(sb, "curl.log") == [] and calls(sb, "open.log") == []


def test_apply_feedless_squirrel_is_unchanged_triggered(sb):
    make_app(sb.apps, ver="1.0", payload="old")
    stub_open(sb)
    reg = registry(sb, feed={})
    res, sc = run_apply(sb, reg)
    assert item_of(sc)["status"] == "triggered", (sc, res.stderr)
    assert len(calls(sb, "open.log")) == 1


# ============================================================================
# apply.sh: Docker
# ============================================================================

def docker_body(newest: str, older: str) -> str:
    items = "".join(
        f'<item><enclosure url="https://desktop.docker.com/mac/main/arm64/{v}/Docker.dmg" '
        f'sparkle:shortVersionString="{v}" sparkle:version="{n}" type="application/octet-stream"/></item>'
        for n, v in enumerate((older, newest)))
    return ('<?xml version="1.0"?><rss version="2.0" '
            'xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>'
            + items + "</channel></rss>")


def prime_docker(sb: Sandbox, installed="4.91.0", new_ver="4.92.0"):
    make_app(sb.apps, ver=installed, payload="old")
    new_parent = sb.fx / "new"
    new_parent.mkdir(exist_ok=True)
    new = make_app(new_parent, ver=new_ver, payload="new")
    sb.serve(f"/mac/main/arm64/{new_ver}/Docker.dmg", sb.dmg_of(new, name=f"Docker-{new_ver}.dmg"))


DOCKER_FEED = {"kind": "sparkle", "url": "https://desktop.docker.com/mac/main/arm64/appcast.xml", "arg": "-",
               "artifact": "dmg", "allowed_host": "desktop.docker.com"}


def docker_cli_stub(sb: Sandbox) -> Path:
    return script(sb.bin / "docker", f'echo "docker $*" >> "{sb.log}/docker.log"\n'
                  'echo "Docker Desktop CLI plugin version: v0.4.4"\nexit 1\n')


def test_apply_docker_idle_installs_verified_dmg_never_the_cli(sb):
    prime_docker(sb)
    docker_cli_stub(sb)
    stub_open(sb)
    reg = registry(sb, handler="docker", slug="docker", feed=DOCKER_FEED)
    res, sc = run_apply(sb, reg, DOCKER_BODY, feed_slug="docker")
    item = item_of(sc)
    assert item["status"] == "success", (sc, res.stderr)
    assert item["current_version"] == "4.92.0"
    assert sb.payload() == "new"
    assert calls(sb, "docker.log") == [] and calls(sb, "open.log") == []


def test_apply_docker_cli_plugin_version_does_not_hide_an_update(sb):
    """P0 regression: `docker desktop version` v0.4.4 vs Docker.app 4.93.0 made the
    probe say 'up to date'. The feed (4.94.0) must be applied."""
    prime_docker(sb, installed="4.93.0", new_ver="4.94.0")
    docker_cli_stub(sb)
    reg = registry(sb, handler="docker", slug="docker", feed=DOCKER_FEED)
    res, sc = run_apply(sb, reg, docker_body("4.94.0", "4.93.0"), feed_slug="docker")
    assert item_of(sc)["status"] == "success", (sc, res.stderr)
    assert sb.payload() == "new"
    assert calls(sb, "docker.log") == []


def test_apply_docker_running_is_needs_restart_and_never_touched(sb):
    prime_docker(sb)
    docker_cli_stub(sb)
    sb.set_ls("true")
    reg = registry(sb, handler="docker", slug="docker", feed=DOCKER_FEED)
    res, sc = run_apply(sb, reg, DOCKER_BODY, feed_slug="docker")
    assert item_of(sc)["status"] == "skipped"
    assert "reason=needs_restart cur=4.91.0 cand=4.92.0" in texts(sc)
    assert sb.payload() == "old" and calls(sb, "docker.log") == []


def test_apply_docker_feed_unreachable_is_skipped_never_up_to_date(sb):
    prime_docker(sb)
    docker_cli_stub(sb)
    reg = registry(sb, handler="docker", slug="docker", feed=DOCKER_FEED)
    res, sc = run_apply(sb, reg, None, feed_slug="docker")
    item = item_of(sc)
    assert item["status"] == "skipped" and "probe_unavailable" in texts(sc)
    assert calls(sb, "docker.log") == [] and sb.payload() == "old"


def test_apply_docker_stale_feed_is_feed_stale(sb):
    prime_docker(sb, installed="4.93.0")
    reg = registry(sb, handler="docker", slug="docker", feed=DOCKER_FEED)
    res, sc = run_apply(sb, reg, DOCKER_BODY, feed_slug="docker")
    assert item_of(sc)["status"] == "skipped" and "feed_stale" in codes(sc)


def test_apply_docker_download_failure_is_soft_and_never_launches_docker(sb):
    prime_docker(sb)
    docker_cli_stub(sb)
    stub_open(sb)
    reg = registry(sb, handler="docker", slug="docker", feed=DOCKER_FEED)
    res, sc = run_apply(sb, reg, DOCKER_BODY, feed_slug="docker", FAKE_CURL_RC="22",
                        ASCENDO_WEB_ALLOW_GUI="1")
    assert item_of(sc)["status"] == "skipped" and "error_soft" in codes(sc)
    assert calls(sb, "open.log") == [] and calls(sb, "docker.log") == []


# ============================================================================
# apply.sh: Ruling 29 - installer rc 1 is soft for the installer-backed handlers
# ============================================================================

APPCAST = """<?xml version="1.0"?>
<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>
<item><enclosure url="https://dl.example.com/App.zip" sparkle:shortVersionString="2.0" sparkle:version="2"
 type="application/octet-stream"/></item></channel></rss>"""


def sparkle_registry(sb: Sandbox) -> Path:
    app = sb.apps / "Example.app"
    reg = sb.tmp / "registry.toml"
    reg.write_text('schema = "ascendo-web-apps/v1"\n[[app]]\nslug = "example"\n'
                   f'bundle_id = "{BID}"\ndisplay_name = "Example"\nhandler = "sparkle"\n'
                   f'app_path = "{app}"\nappcast_url = "https://dl.example.com/appcast.xml"\n')
    (sb.fx / "appcast.xml").write_text(APPCAST)
    sb.serve("appcast.xml", sb.fx / "appcast.xml")           # the archive URL is NOT served => curl 22
    return reg


def test_sparkle_download_failure_is_skipped_error_soft_not_failed(sb):
    make_app(sb.apps, ver="1.0", payload="old")
    stub_open(sb)
    res, sc = run_apply(sb, sparkle_registry(sb), feed_slug="example")
    item = item_of(sc)
    assert item["status"] == "skipped", (sc, res.stderr)
    assert "error_soft" in codes(sc) and "reason=download_error" in texts(sc)
    assert sc["status"] != "failed"
    assert calls(sb, "open.log") == [], "safe mode never launches"


def test_sparkle_download_failure_launches_once_when_allowed_and_idle(sb):
    make_app(sb.apps, ver="1.0", payload="old")
    stub_open(sb)
    res, sc = run_apply(sb, sparkle_registry(sb), feed_slug="example", ASCENDO_WEB_ALLOW_GUI="1")
    assert item_of(sc)["status"] == "triggered", (sc, res.stderr)
    assert "error_soft" in codes(sc)
    assert len(calls(sb, "open.log")) == 1 and "-gjF" in calls(sb, "open.log")[0]


def test_sparkle_download_failure_does_not_launch_a_running_app(sb):
    make_app(sb.apps, ver="1.0", payload="old")
    stub_open(sb)
    # probe #1 (defer gate) and #2 (installer, before download) idle; the user opens the app
    # while the download fails; probe #3 (fallback launch gate) sees it running.
    sb.set_ls("false", "false", "true")
    res, sc = run_apply(sb, sparkle_registry(sb), feed_slug="example", ASCENDO_WEB_ALLOW_GUI="1")
    assert item_of(sc)["status"] == "skipped", (sc, res.stderr)
    assert "error_soft" in codes(sc)
    assert calls(sb, "open.log") == []


def test_sparkle_hard_install_failure_still_failed(sb):
    make_app(sb.apps, ver="1.0", payload="old")
    new_parent = sb.fx / "new"
    new_parent.mkdir()
    sb.serve("App.zip", zip_of(sb.fx / "App.zip", make_app(new_parent, ver="2.0", payload="new",
                                                          team="ZZZZZ99999")))
    stub_open(sb)
    res, sc = run_apply(sb, sparkle_registry(sb), feed_slug="example")
    assert item_of(sc)["status"] == "failed", (sc, res.stderr)


# ============================================================================
# check.sh / plan.sh: feed-backed squirrel + docker
# ============================================================================

def _phase(phase: str, tmp_path: Path, reg_text: str, feed_slug: str, feed_body: str | None,
           extra_env: dict | None = None):
    reg = tmp_path / "registry.toml"
    reg.write_text(reg_text)
    feeds = tmp_path / "feeds"
    feeds.mkdir(exist_ok=True)
    if feed_body is not None:
        (feeds / f"{feed_slug}.feed").write_text(feed_body)
    env = {"ASCENDO_WEB_VENDOR_FEED_DIR": str(feeds), "ASCENDO_WEB_HOST_OS_VERSION": "27.0",
           "ASCENDO_WEB_HOST_ARCH": "arm64", "ASCENDO_WEB_DOCKER_BIN": str(tmp_path / "no-docker"),
           **(extra_env or {})}
    from .test_web_phase_check_plan import _read_sidecar, _run_phase
    out = tmp_path / "out"
    r, run_id = _run_phase(phase, reg, out, env_extra=env)
    assert r.returncode == 0, r.stderr
    return _read_sidecar(out, run_id, phase)


def _squirrel_reg(tmp_path: Path, installed: str, *, slug="vt", bundle="com.example.vt",
                  handler="squirrel", feed: str | None = None) -> str:
    app = _make_fake_app(tmp_path, "VtApp", installed, bundle_id=bundle)
    feed = feed if feed is not None else ('[app.vendor_feed]\nkind = "json"\nurl = "https://downloads.claude.ai/r.json"\n'
                    'arg = "claude_releases"\nartifact = "zip"\nallowed_host = "downloads.claude.ai"\n')
    return ('schema = "ascendo-web-apps/v1"\n[[app]]\n'
            f'slug = "{slug}"\nbundle_id = "{bundle}"\ndisplay_name = "VtApp"\nhandler = "{handler}"\n'
            f'app_path = "{app}"\n{feed}')


@pytest.mark.parametrize("installed,status,target,code", [
    ("1.0.0", "planned", "2.7032.0", None),
    ("2.7032.0", "up_to_date", "2.7032.0", None),
    ("3.0.0", "skipped", None, "feed_stale"),
    ("dev-build", "skipped", None, "unknown_version"),
])
def test_check_squirrel_with_feed_is_classified_three_way(tmp_path, installed, status, target, code):
    sc = _phase("check", tmp_path, _squirrel_reg(tmp_path, installed), "vt", CLAUDE_BODY)
    item = sc["items"][0]
    assert item["status"] == status, sc
    assert item.get("target_version") == target
    if code:
        assert code in [m.get("code") for m in sc["messages"]]
        assert "vendor_opaque" not in texts(sc)


def test_check_squirrel_with_unreachable_feed_is_probe_unavailable_not_vendor_opaque(tmp_path):
    sc = _phase("check", tmp_path, _squirrel_reg(tmp_path, "1.0.0"), "vt", None)
    item = sc["items"][0]
    assert item["status"] == "skipped"
    assert "probe_unavailable" in texts(sc) and "vendor_opaque" not in texts(sc)


def test_check_feedless_squirrel_keeps_vendor_opaque(tmp_path):
    reg = _squirrel_reg(tmp_path, "1.0.0", feed="")
    sc = _phase("check", tmp_path, reg, "vt", None)
    assert sc["items"][0]["status"] == "skipped" and "vendor_opaque" in texts(sc)


@pytest.mark.parametrize("installed,expect_planned", [
    ("1.0.0", True), ("2.7032.0", False), ("3.0.0", False), ("dev-build", False),
])
def test_plan_squirrel_with_feed_plans_only_when_the_feed_is_newer(tmp_path, installed, expect_planned):
    sc = _phase("plan", tmp_path, _squirrel_reg(tmp_path, installed), "vt", CLAUDE_BODY)
    assert [i["status"] for i in sc["items"]] == (["planned"] if expect_planned else [])
    if expect_planned:
        assert sc["items"][0]["target_version"] == "2.7032.0"


DOCKER_REG_FEED = ('[app.vendor_feed]\nkind = "sparkle"\nurl = "https://desktop.docker.com/mac/main/arm64/appcast.xml"\n'
                   'arg = "-"\nartifact = "dmg"\nallowed_host = "desktop.docker.com"\n')


def _docker_shim(tmp_path: Path) -> Path:
    d = tmp_path / "dockerbin"
    d.mkdir(exist_ok=True)
    p = d / "docker"
    p.write_text('#!/bin/sh\necho "Docker Desktop CLI plugin version: v0.4.4"\n')
    p.chmod(0o755)
    return d


def test_check_docker_regression_cli_plugin_version_no_longer_means_up_to_date(tmp_path):
    shim = _docker_shim(tmp_path)
    reg = _squirrel_reg(tmp_path, "4.93.0", slug="docker", bundle="com.docker.docker", handler="docker",
                        feed=DOCKER_REG_FEED)
    sc = _phase("check", tmp_path, reg, "docker", docker_body("4.94.0", "4.93.0"),
                {"PATH": f"{shim}:{os.environ['PATH']}"})
    item = sc["items"][0]
    assert (item["status"], item["current_version"], item["target_version"]) == ("planned", "4.93.0", "4.94.0")


def test_plan_docker_with_feed_does_not_need_the_docker_cli(tmp_path):
    reg = _squirrel_reg(tmp_path, "4.91.0", slug="docker", bundle="com.docker.docker", handler="docker",
                        feed=DOCKER_REG_FEED)
    sc = _phase("plan", tmp_path, reg, "docker", DOCKER_BODY)
    assert [(i["status"], i["target_version"]) for i in sc["items"]] == [("planned", "4.92.0")]


def test_check_docker_unreachable_feed_is_skipped_probe_unavailable(tmp_path):
    reg = _squirrel_reg(tmp_path, "4.91.0", slug="docker", bundle="com.docker.docker", handler="docker",
                        feed=DOCKER_REG_FEED)
    sc = _phase("check", tmp_path, reg, "docker", None)
    assert sc["items"][0]["status"] == "skipped" and "probe_unavailable" in texts(sc)


@pytest.mark.parametrize("installed,expect", [("2.0", "up_to_date"), ("3.0", "skipped")])
def test_sparkle_equal_and_stale_do_not_launch(tmp_path, installed, expect):
    """Port of test_sparkle_equal_and_stale_do_not_launch: a current or stale Sparkle
    feed must not launch the app (apply classifies before any handler runs)."""
    sb = Sandbox(tmp_path)
    make_app(sb.apps, ver=installed, payload="old")
    stub_open(sb)
    res, sc = run_apply(sb, sparkle_registry(sb), feed_slug="example", ASCENDO_WEB_ALLOW_GUI="1")
    assert item_of(sc)["status"] == expect, sc
    assert calls(sb, "open.log") == [] and sb.payload() == "old"
    assert calls(sb, "curl.log") and not [c for c in calls(sb, "curl.log") if "App.zip" in c]


# ============================================================================
# verify.sh (I2): real app path + measured verification of feed-backed items
# ============================================================================

def run_verify(sb: Sandbox, reg: Path, run_dir: Path, **extra):
    """Run verify.sh against the apply sidecar produced by run_apply in `run_dir`."""
    out = run_dir.parent
    run_id = run_dir.name
    # verify.sh waits through `_web_sleep` -> $ASCENDO_WEB_SLEEP_BIN (a PATH `sleep`
    # stub is never consulted: it would leave the "must not sleep" asserts blind).
    stub = script(sb.bin / "verify_sleep_stub", f'echo "$*" >> "{sb.log}/verify_sleep.log"\nexit 0\n')
    assert stub.exists()
    env = sb.env(ASCENDO_WEB_REGISTRY_PATH=reg, ASCENDO_WEB_USER_REGISTRY_PATH="",
                 ASCENDO_WEB_SLEEP_BIN=stub,
                 PYTHONPATH=f"{REPO_ROOT}/core:{ADAPTER}", **extra)
    res = subprocess.run(["bash", str(SCRIPTS / "verify.sh"), "--run-id", run_id, "--trigger", "cli",
                          "--profile", "full", "--output-dir", str(out)],
                         capture_output=True, text=True, env=env)
    return res, json.loads((out / run_id / "verify__web.json").read_text())


def _only_run_dir(sb: Sandbox) -> Path:
    (d,) = [p for p in (sb.tmp / "out").iterdir() if p.is_dir()]
    return d


def test_verify_feed_backed_squirrel_success_is_measured_not_slept_not_escalated(sb):
    """Ruling 35: an apply `success` for a feed-backed squirrel app verifies by measuring
    the REAL bundle (registry app_path, not /Applications/web:<slug>.app), with no
    30 s squirrel sleep and no escalation to failed."""
    prime_claude(sb)
    stub_open(sb)
    reg = registry(sb, direct_first=True)
    res, sc = run_apply(sb, reg, CLAUDE_BODY)
    assert item_of(sc)["status"] == "success", (sc, res.stderr)
    vres, vsc = run_verify(sb, reg, _only_run_dir(sb))
    item = item_of(vsc)
    assert item["status"] == "success", (vsc, vres.stderr)
    assert item["current_version"] == "2.7032.0" and item["target_version"] == "2.7032.0"
    assert "verified by measurement" in texts(vsc)
    assert calls(sb, "verify_sleep.log") == [], "feed-backed squirrel verify must not sleep"
    assert "failed" not in [i["status"] for i in vsc["items"]]


def test_verify_feed_backed_success_but_bundle_still_old_is_failed(sb):
    prime_claude(sb)
    stub_open(sb)
    reg = registry(sb, direct_first=True)
    res, sc = run_apply(sb, reg, CLAUDE_BODY)
    assert item_of(sc)["status"] == "success"
    # Roll the bundle back after apply: the measurement must catch it.
    make_app(sb.apps, ver="1.0", payload="rolled-back")
    vres, vsc = run_verify(sb, reg, _only_run_dir(sb))
    item = item_of(vsc)
    assert item["status"] == "failed", vsc
    assert "not the vendor's 2.7032.0" in texts(vsc)
    assert calls(sb, "verify_sleep.log") == []


def test_verify_feed_backed_triggered_stays_triggered_when_not_yet_applied(sb):
    """The `launched` apply item (Ascendo started the app hidden): verify keeps it
    `triggered` with a pending note. It is unverified, not a failure."""
    prime_claude(sb)
    stub_open(sb)
    stub_sleep(sb)
    reg = registry(sb, direct_first=False)
    res, sc = run_apply(sb, reg, CLAUDE_BODY, ASCENDO_WEB_ALLOW_GUI="1", ASCENDO_WEB_STAGE_WAIT="1")
    assert item_of(sc)["status"] == "triggered", (sc, res.stderr)
    vres, vsc = run_verify(sb, reg, _only_run_dir(sb))
    item = item_of(vsc)
    assert item["status"] == "triggered", vsc
    assert "triggered_pending" in texts(vsc) and "unverified" in texts(vsc)
    assert "unverified" in codes(vsc) and "ASCENDO-STATUS code=unverified" in texts(vsc)
    # no settle wait (the sub-second polling of the running-state probe is not one)
    assert [c for c in calls(sb, "verify_sleep.log") if float(c) >= 1] == []
    assert sc["status"] != "failed" and vsc["status"] != "failed"


def test_verify_feed_backed_triggered_is_confirmed_when_the_version_arrives(sb):
    prime_claude(sb)
    stub_open(sb)
    stub_sleep(sb)
    reg = registry(sb, direct_first=False)
    res, sc = run_apply(sb, reg, CLAUDE_BODY, ASCENDO_WEB_ALLOW_GUI="1", ASCENDO_WEB_STAGE_WAIT="1")
    assert item_of(sc)["status"] == "triggered"
    make_app(sb.apps, ver="2.7032.0", payload="updated-by-native-updater")   # the app updated itself
    vres, vsc = run_verify(sb, reg, _only_run_dir(sb))
    assert item_of(vsc)["status"] == "success", vsc
    assert "verified by measurement" in texts(vsc)


def test_verify_feedless_squirrel_resolves_the_real_path(sb):
    """Legacy (feedless) squirrel keeps its async semantics, on the registry's real app
    path: a changed version is `success` (code updated), an unchanged one `triggered`
    (code unverified) -- never a bogus-path 'failed'. ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE=0
    skips the 30 s wait."""
    make_app(sb.apps, ver="5.0", payload="updated")
    reg = registry(sb, feed={})                            # squirrel, no [app.vendor_feed]
    out = sb.tmp / "out"
    run_dir = out / "rid-feedless"
    run_dir.mkdir(parents=True)
    (run_dir / "apply__web.json").write_text(json.dumps({"items": [{
        "id": "web:vt", "name": "web:vt", "status": "triggered", "current_version": "1.0",
        "source": {"type": "web", "feed": "squirrel"}}]}))
    vres, vsc = run_verify(sb, reg, run_dir, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    item = item_of(vsc)
    assert item["status"] == "success" and "updated (1.0 -> 5.0)" in texts(vsc), vsc
    assert "updated" in codes(vsc)
    # unchanged => unverified (Task 12): not failed until it has been unchanged for the stale bound
    make_app(sb.apps, ver="1.0", payload="old")
    vres, vsc = run_verify(sb, reg, run_dir, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0",
                           ASCENDO_WEB_SEEN_FILE=sb.tmp / "seen.tsv")
    assert item_of(vsc)["status"] == "triggered" and "unverified" in codes(vsc), vsc


# ============================================================================
# apply.sh --dry-run for feed-backed items (never launches, classifies on the feed)
# ============================================================================

def run_apply_dry(sb: Sandbox, reg: Path, feed_body: str, **extra):
    feeds = sb.tmp / "feeds"
    feeds.mkdir(exist_ok=True)
    (feeds / "vt.feed").write_text(feed_body)
    stub_sleep(sb)
    out = sb.tmp / "out"
    env = sb.env(ASCENDO_WEB_REGISTRY_PATH=reg, ASCENDO_WEB_USER_REGISTRY_PATH="",
                 ASCENDO_SUDO_WARM_DISABLE="1", ASCENDO_WEB_VENDOR_FEED_DIR=feeds,
                 ASCENDO_WEB_SLEEP_BIN=sb.bin / "sleep_stub", ASCENDO_WEB_DOCKER_BIN=sb.tmp / "no-docker",
                 PYTHONPATH=f"{REPO_ROOT}/core:{ADAPTER}", **extra)
    run_id = str(uuid.uuid4())
    res = subprocess.run(["bash", str(SCRIPTS / "apply.sh"), "--run-id", run_id, "--trigger", "cli",
                          "--profile", "full", "--output-dir", str(out), "--dry-run"],
                         capture_output=True, text=True, env=env)
    return res, json.loads((out / run_id / "apply__web.json").read_text())


def test_vendor_truth_dry_run_reports_update_available_without_launch(sb):
    """Port of upstream test_vendor_truth_dry_run_reports_update_available_without_launch."""
    prime_claude(sb)
    stub_open(sb)
    res, sc = run_apply_dry(sb, registry(sb), CLAUDE_BODY, ASCENDO_WEB_ALLOW_GUI="1")
    item = item_of(sc)
    assert item["status"] == "planned", (sc, res.stderr)
    assert item["current_version"] == "1.0" and item["target_version"] == "2.7032.0"
    assert "Update available 1.0 -> 2.7032.0" in texts(sc)
    assert calls(sb, "open.log") == [] and calls(sb, "sleep.log") == []
    assert not [c for c in calls(sb, "curl.log") if ".zip" in c]      # no artifact download
    assert sb.payload() == "old" and sb.forbidden() == []


@pytest.mark.parametrize("installed,expect", [("2.7032.0", "up_to_date"), ("3.0", "skipped")])
def test_dry_run_equal_or_older_feed_is_not_planned_and_never_launches(sb, installed, expect):
    make_app(sb.apps, ver=installed, payload="old")
    stub_open(sb)
    res, sc = run_apply_dry(sb, registry(sb), CLAUDE_BODY, ASCENDO_WEB_ALLOW_GUI="1")
    assert item_of(sc)["status"] == expect, sc
    assert calls(sb, "open.log") == [] and sb.payload() == "old"
