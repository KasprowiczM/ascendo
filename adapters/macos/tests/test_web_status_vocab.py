"""Task 12: status vocabulary + version_cmp3 migration + verify Tier-B + launch tracking.

Everything is hermetic: fake tools on PATH (Sandbox), temp Applications root,
temp HOME / state dir. Nothing touches /Applications, the network or the real
~/.ascendo.
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

import pytest

from .test_web_phase_check_plan import SCRIPTS as WEB_SCRIPTS
from .test_web_phase_check_plan import _make_fake_app, _read_sidecar, _run_phase
from .test_web_vendor_install import BID, Sandbox, make_app, script

ADAPTER = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER.parents[1]
LIB = ADAPTER / "lib"
SCRIPTS = ADAPTER / "scripts"


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


# ----------------------------------------------------------------------------
# ascendo_version_rel + classify() migration (npm / pip): unparseable => unknown
# ----------------------------------------------------------------------------

def _bash(snippet: str) -> str:
    r = subprocess.run(["bash", "-c", f'. "{LIB}/ascendo_json.sh"; {snippet}'],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.mark.parametrize("cand,inst,rel", [
    ("1.2.0", "1.1.0", "newer"), ("1.1.0", "1.1.0", "equal"), ("1.0", "1.0.0", "equal"),
    ("1.0.0", "1.1.0", "older"), ("nightly", "1.0", "unknown"), ("", "1.0", "unknown"),
])
def test_version_rel_is_three_way_and_never_guesses(cand, inst, rel):
    assert _bash(f'ascendo_version_rel "{cand}" "{inst}"') == rel


CLASSIFY_SCRIPTS = ["npm/check.sh", "npm/plan.sh", "npm/apply.sh",
                    "pip/check.sh", "pip/plan.sh", "pip/apply.sh"]


def _classify(rel_script: str, installed: str, latest: str) -> str:
    text = (SCRIPTS / rel_script).read_text()
    m = re.search(r"^classify\(\) \{.*?^\}", text, re.S | re.M)
    assert m, rel_script
    return _bash(f'{m.group(0)}\nclassify "{installed}" "{latest}"')


@pytest.mark.parametrize("rel_script", CLASSIFY_SCRIPTS)
def test_classify_uses_three_way_and_flags_unparseable(rel_script):
    assert _classify(rel_script, "1.0.0", "1.0.1") == "planned"
    assert _classify(rel_script, "1.0.1", "1.0.0") == "up_to_date"
    assert _classify(rel_script, "2.0", "2.0") == "up_to_date"
    assert _classify(rel_script, "", "1.0") == "missing"
    # NB: an empty `latest` (failed probe) still reads up_to_date in classify(); that
    # pre-existing GC5 gap is tracked in the task report, not enshrined here.
    assert _classify(rel_script, "dev-build", "nightly") == "unknown_version"
    assert _classify(rel_script, "1.0", "latest") == "unknown_version"


# ----------------------------------------------------------------------------
# web check / plan: non-feed handlers (release_feed) with an unparseable pair
# ----------------------------------------------------------------------------

def _release_feed_registry(tmp_path: Path, installed: str, vendor: str):
    _make_fake_app(tmp_path, "RFApp", installed, bundle_id="com.example.rfapp")
    feed = tmp_path / "feed.json"
    feed.write_text(json.dumps({"v": vendor}))
    curl = tmp_path / "fakecurl"
    curl.write_text('#!/bin/sh\ncat "$ASCENDO_TEST_FEED"\n')
    curl.chmod(0o755)
    reg = tmp_path / "registry.toml"
    reg.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "rfapp"\nbundle_id = "com.example.rfapp"\n'
        'display_name = "RFApp"\nhandler = "release_feed"\n'
        f'app_path = "{tmp_path}/Applications/RFApp.app"\n'
        '[app.release_feed]\nurl = "https://feed.test/x.json"\nversion_path = "v"\n')
    return reg, {"ASCENDO_WEB_CURL_BIN": str(curl), "ASCENDO_TEST_FEED": str(feed)}


@pytest.mark.parametrize("inst,vendor", [("1.0", "latest"), ("nightly", "1.0"), ("1.0", "nightly")])
@pytest.mark.parametrize("phase", ["check", "plan", "apply"])
def test_release_feed_unparseable_versions_are_skipped_unknown_version(tmp_path, phase, inst, vendor):
    """Both directions, and independent of string order / the prerelease heuristic
    (`_should_skip_upgrade` must not turn garbage into up_to_date)."""
    reg, env = _release_feed_registry(tmp_path, inst, vendor)
    env = {**env, "ASCENDO_SUDO_WARM_DISABLE": "1"}
    out = tmp_path / "out"
    r, run_id = _run_phase(phase, reg, out, env_extra=env)
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id, phase)
    (item,) = sc["items"]
    assert item["status"] == "skipped" and not item.get("target_version"), sc
    assert "unknown_version" in [m.get("code") for m in sc["messages"]]


def test_release_feed_normal_pair_still_plans(tmp_path):
    reg, env = _release_feed_registry(tmp_path, "1.0", "1.1")
    out = tmp_path / "out"
    r, run_id = _run_phase("check", reg, out, env_extra=env)
    (item,) = _read_sidecar(out, run_id, "check")["items"]
    assert (item["status"], item["target_version"]) == ("planned", "1.1")


# ----------------------------------------------------------------------------
# feed_stale message shape (delta v1.5.4 row 15)
# ----------------------------------------------------------------------------

def test_feed_stale_message_matches_upstream_wording():
    out = _bash('. "' + str(LIB) + '/ascendo_web.sh"; _web_feed_stale_message warp 0.9 0.8')
    assert out == "warp: vendor feed 0.8 is older than installed 0.9 | ASCENDO-STATUS code=feed_stale"


# ----------------------------------------------------------------------------
# Firefox Developer Edition: a new-major beta is not skipped forever (Spec H)
# ----------------------------------------------------------------------------

def _firefox_registry(tmp_path: Path, installed: str):
    """A registry holding the SHIPPED firefox-dev entry (only bundle id / app_path pinned to a tmp bundle)."""
    shipped = (ADAPTER / "config" / "web_apps.toml").read_text()
    m = re.search(r'(\[\[app\]\]\nslug = "firefox-dev".*?)(?=\n# \u2500|\n\[\[app\]\])', shipped, re.S)
    assert m
    app = _make_fake_app(tmp_path, "Firefox Developer Edition", installed,
                         bundle_id="org.mozilla.firefoxdeveloperedition")
    block = m.group(1).replace('handler = "release_feed"\n',
                               f'handler = "release_feed"\napp_path = "{app}"\n', 1)
    reg = tmp_path / "registry.toml"
    reg.write_text('schema = "ascendo-web-apps/v1"\n' + block + "\n")
    return reg


@pytest.mark.parametrize("installed,feed,status,target", [
    ("156.0", "157.0b1", "planned", "157.0"),        # new-major beta: used to be skipped forever
    ("156.0", "156.0b3", "up_to_date", "156.0"),     # same base: current
    ("156.0", "156.0", "up_to_date", "156.0"),
])
def test_firefox_dev_compares_the_stripped_base(tmp_path, installed, feed, status, target):
    reg = _firefox_registry(tmp_path, installed)
    body = tmp_path / "ff.json"
    body.write_text(json.dumps({"FIREFOX_DEVEDITION": feed}))
    curl = tmp_path / "fakecurl"
    curl.write_text('#!/bin/sh\ncat "$ASCENDO_TEST_FEED"\n')
    curl.chmod(0o755)
    out = tmp_path / "out"
    r, run_id = _run_phase("check", reg, out, env_extra={
        "ASCENDO_WEB_CURL_BIN": str(curl), "ASCENDO_TEST_FEED": str(body)})
    assert r.returncode == 0, r.stderr
    (item,) = _read_sidecar(out, run_id, "check")["items"]
    assert (item["status"], item.get("target_version")) == (status, target)


# ----------------------------------------------------------------------------
# verify.sh helpers
# ----------------------------------------------------------------------------

def _registry(sb: Sandbox) -> Path:
    app = sb.apps / "Example.app"
    reg = sb.tmp / "registry.toml"
    reg.write_text(
        'schema = "ascendo-web-apps/v1"\n[[app]]\nslug = "vt"\n'
        f'bundle_id = "{BID}"\ndisplay_name = "Example"\nhandler = "squirrel"\napp_path = "{app}"\n')
    return reg


def _apply_sidecar(run_dir: Path, *, status="triggered", current="1.0", target=None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    item = {"id": "web:vt", "name": "web:vt", "status": status, "current_version": current,
            "source": {"type": "web", "feed": "squirrel"}}
    if target:
        item["target_version"] = target
    (run_dir / "apply__web.json").write_text(json.dumps({"items": [item]}))


def _verify(sb: Sandbox, run_dir: Path, *, output_dir: Path | None = None, **extra):
    out = output_dir if output_dir is not None else run_dir.parent
    run_id = run_dir.name
    sleeps = sb.log / "sleep.log"
    # NOT named `sleep`: a PATH-shadowing stub would also swallow the 0.2 s ticks of
    # ascendo_run_with_timeout's fallback loop, so its 20 s deadline "elapsed" instantly
    # and killed the osascript stub before it ran (failed only under CPU load).
    script(sb.bin / "sleep_stub", f'echo "$*" >> "{sleeps}"\nexit 0\n')
    base = dict(ASCENDO_WEB_REGISTRY_PATH=_registry(sb), ASCENDO_WEB_USER_REGISTRY_PATH="",
                ASCENDO_WEB_SLEEP_BIN=sb.bin / "sleep_stub", ASCENDO_WEB_SEEN_FILE=sb.tmp / "seen.tsv",
                ASCENDO_RUNS_DIR=sb.tmp / "no-runs", PYTHONPATH=f"{REPO_ROOT}/core:{ADAPTER}")
    base.update(extra)
    env = sb.env(**base)
    res = subprocess.run(["bash", str(WEB_SCRIPTS / "verify.sh"), "--run-id", run_id, "--trigger", "cli",
                          "--profile", "full", "--output-dir", str(out)],
                         capture_output=True, text=True, env=env)
    vp = out / run_id / "verify__web.json"
    return res, json.loads(vp.read_text()), (sleeps.read_text().splitlines() if sleeps.exists() else [])


def _texts(sc): return " || ".join(m["text"] for m in sc["messages"])
def _codes(sc): return [m.get("code") for m in sc["messages"]]


# ---- sidecar lookup via the canonical run dir ------------------------------

def test_verify_finds_the_apply_sidecar_in_the_canonical_runs_dir(sb):
    make_app(sb.apps, ver="5.0")
    runs = sb.tmp / "runs"
    rid = "rid-canon"
    _apply_sidecar(runs / rid, status="success", current="1.0")
    own = sb.tmp / "verify-own"                  # verify's own tempdir: empty
    (own / rid).mkdir(parents=True)
    res, sc, _ = _verify(sb, own / rid, output_dir=own, ASCENDO_RUNS_DIR=runs,
                         ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    assert res.returncode == 0, res.stderr
    assert [i["id"] for i in sc["items"]] == ["web:vt"], sc
    assert str(runs / rid / "apply__web.json") in _texts(sc)        # which path was used is logged


def test_verify_falls_back_to_home_ascendo_runs(sb):
    make_app(sb.apps, ver="5.0")
    rid = "rid-home"
    _apply_sidecar(sb.tmp / "home" / ".ascendo" / "runs" / rid, status="success", current="1.0")
    own = sb.tmp / "verify-own"
    (own / rid).mkdir(parents=True)
    res, sc, _ = _verify(sb, own / rid, output_dir=own, ASCENDO_RUNS_DIR="",
                         ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    assert [i["id"] for i in sc["items"]] == ["web:vt"], (sc, res.stderr)


# ---- Tier-B classification ---------------------------------------------------

def test_verify_tier_b_changed_version_is_success_updated(sb):
    make_app(sb.apps, ver="5.0")
    rd = sb.tmp / "out" / "r1"
    _apply_sidecar(rd)
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    (item,) = sc["items"]
    assert item["status"] == "success" and item["current_version"] == "5.0", sc
    assert "updated" in _codes(sc) and "1.0 -> 5.0" in _texts(sc)


def test_verify_tier_b_unchanged_without_candidate_is_triggered_unverified_not_failed(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "r2"
    _apply_sidecar(rd)
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    (item,) = sc["items"]
    assert item["status"] == "triggered", sc
    assert "unverified" in _codes(sc) and "ASCENDO-STATUS code=unverified" in _texts(sc)
    assert sc["status"] != "failed"


def test_verify_tier_b_unchanged_with_newer_candidate_is_behind_skipped(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "r3"
    _apply_sidecar(rd, target="3.0")
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    (item,) = sc["items"]
    assert item["status"] == "skipped", sc
    assert "behind" in _codes(sc) and "ASCENDO-ACTION-REQUIRED reason=behind" in _texts(sc)


def test_verify_tier_b_unchanged_for_longer_than_stale_days_fails(sb):
    make_app(sb.apps, ver="1.0")
    (sb.tmp / "seen.tsv").write_text(f"vt\t1.0\t{100_000_000 - 60 * 86400}\n")
    rd = sb.tmp / "out" / "r4"
    _apply_sidecar(rd)
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0", ASCENDO_WEB_NOW_EPOCH="100000000")
    (item,) = sc["items"]
    assert item["status"] == "failed", sc
    assert "60 days" in _texts(sc)
    assert "ASCENDO-ACTION-REQUIRED reason=unverified" in _texts(sc)    # stale => promoted by the report


def test_verify_tier_b_stale_days_bound_is_configurable_and_default_45(sb):
    make_app(sb.apps, ver="1.0")
    (sb.tmp / "seen.tsv").write_text(f"vt\t1.0\t{100_000_000 - 30 * 86400}\n")
    rd = sb.tmp / "out" / "r5"
    _apply_sidecar(rd)
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0", ASCENDO_WEB_NOW_EPOCH="100000000")
    assert sc["items"][0]["status"] == "triggered"                       # 30 d < default 45
    rd2 = sb.tmp / "out" / "r6"
    _apply_sidecar(rd2)
    res, sc, _ = _verify(sb, rd2, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0", ASCENDO_WEB_NOW_EPOCH="100000000",
                         ASCENDO_WEB_STALE_DAYS="10")
    assert sc["items"][0]["status"] == "failed"                          # 30 d > 10


def test_verify_records_first_seen_and_a_changed_version_resets_the_clock(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "r7"
    _apply_sidecar(rd)
    _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0", ASCENDO_WEB_NOW_EPOCH="500")
    assert (sb.tmp / "seen.tsv").read_text().split() == ["vt", "1.0", "500"]
    _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0", ASCENDO_WEB_NOW_EPOCH="900")
    assert (sb.tmp / "seen.tsv").read_text().split() == ["vt", "1.0", "500"]     # unchanged: kept
    make_app(sb.apps, ver="2.0")
    _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0", ASCENDO_WEB_NOW_EPOCH="1200")
    assert (sb.tmp / "seen.tsv").read_text().split() == ["vt", "2.0", "1200"]    # new version: reset


# ---- settle only when something was launched / triggered ----------------------

def test_verify_does_not_settle_when_nothing_is_triggered_or_launched(sb):
    make_app(sb.apps, ver="5.0")
    rd = sb.tmp / "out" / "r8"
    _apply_sidecar(rd, status="success", current="1.0")                  # a synchronous success
    res, sc, sleeps = _verify(sb, rd)
    assert [x for x in sleeps if float(x) >= 1] == [], sleeps


def test_verify_settles_for_a_triggered_item(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "r9"
    _apply_sidecar(rd)
    res, sc, sleeps = _verify(sb, rd)
    assert [x for x in sleeps if float(x) >= 1] == ["30"], sleeps


# ---- toolkit-launched app quit ----------------------------------------------------

def _tracking_tools(sb: Sandbox, *, running="true", front=False, osa_flips=True):
    """lsappinfo (list/find/front) + osascript that records quit calls and (optionally) stops the app."""
    state = sb.tmp / "app.running"
    state.write_text(running)
    script(sb.bin / "lsappinfo", f'''
case "$1" in
  list) if [ "$(cat "{state}")" = true ]; then echo 'bundleID="{BID}"'; else echo 'bundleID="com.apple.finder"'; fi ;;
  find) echo 'ASN:0x0-0x1234-"Example":' ;;
  front) {'echo "ASN:0x0-0x1234:"' if front else 'echo "ASN:0x0-0x9999:"'} ;;
esac
exit 0
''')
    script(sb.bin / "osascript", f'''
echo "osascript $*" >> "{sb.log}/osascript.log"
{'echo false > "' + str(state) + '"' if osa_flips else ':'}
exit 0
''')


def _track_file(rd: Path, *bids: str, stamp: int = 1) -> None:
    """Launch record: "<bundle id>\t<launch epoch>" per line (epoch 1 = long ago)."""
    (rd / "toolkit_launched.txt").write_text("".join(f"{b}\t{stamp}\n" for b in bids))


def test_verify_quits_only_the_apps_ascendo_launched(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "q1"
    _apply_sidecar(rd)
    _track_file(rd, BID)
    _tracking_tools(sb)
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    log = sb.read_log("osascript.log")
    assert len(log) == 1 and f'tell application id "{BID}" to quit' in log[0], (log, res.stderr)
    assert sb.forbidden() == []


def test_verify_never_quits_an_app_ascendo_did_not_launch(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "q2"
    _apply_sidecar(rd)                                   # no toolkit_launched.txt: the user had it open
    _tracking_tools(sb)
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    assert sb.read_log("osascript.log") == []
    _track_file(rd, "com.other.app")                     # tracked, but a different app
    _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    assert sb.read_log("osascript.log") == [] or all(BID not in l for l in sb.read_log("osascript.log"))


def test_verify_does_not_quit_a_frontmost_app_and_ignores_invalid_ids(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "q3"
    _apply_sidecar(rd)
    _track_file(rd, BID, 'bad id";do shell script "x')
    _tracking_tools(sb, front=True)
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    assert sb.read_log("osascript.log") == [], "frontmost app (the user is using it) must not be quit"
    assert "app_in_foreground" in _codes(sc)


def test_verify_reports_app_still_running_when_quit_does_not_take(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "q4"
    _apply_sidecar(rd)
    _track_file(rd, BID)
    _tracking_tools(sb, osa_flips=False)
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0", ASCENDO_WEB_QUIT_WAIT="2")
    assert "app_still_running" in _codes(sc)
    assert sc["items"][0]["status"] == "triggered"        # item status untouched


def test_verify_finds_the_track_file_in_the_canonical_runs_dir(sb):
    make_app(sb.apps, ver="1.0")
    runs = sb.tmp / "runs"
    rid = "q5"
    _apply_sidecar(runs / rid)
    _track_file(runs / rid, BID)
    own = sb.tmp / "verify-own"
    (own / rid).mkdir(parents=True)
    _tracking_tools(sb)
    _verify(sb, own / rid, output_dir=own, ASCENDO_RUNS_DIR=runs, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0")
    assert len(sb.read_log("osascript.log")) == 1


# ---- apply records only launches of apps that were explicitly idle -----------------

def _squirrel_feed_registry(sb: Sandbox) -> Path:
    app = sb.apps / "Example.app"
    reg = sb.tmp / "registry.toml"
    reg.write_text(
        'schema = "ascendo-web-apps/v1"\n[[app]]\nslug = "vt"\n'
        f'bundle_id = "{BID}"\ndisplay_name = "Example"\nhandler = "squirrel"\napp_path = "{app}"\n'
        '[app.vendor_feed]\nkind = "json"\nurl = "https://downloads.claude.ai/r.json"\n'
        'arg = "claude_releases"\nartifact = "zip"\nallowed_host = "downloads.claude.ai"\n'
        'direct_first = false\n')
    return reg


def _apply_launch(sb: Sandbox, ls_state: str):
    from .test_web_phase_vendor_truth import CLAUDE_BODY
    make_app(sb.apps, ver="1.0")
    sb.set_ls(ls_state)
    script(sb.bin / "open", f'echo "open $*" >> "{sb.log}/open.log"\nexit 0\n')
    script(sb.bin / "sleep_stub", "exit 0\n")
    feeds = sb.tmp / "feeds"
    feeds.mkdir(exist_ok=True)
    (feeds / "vt.feed").write_text(CLAUDE_BODY)
    runs = sb.tmp / "runs"
    out = sb.tmp / "out"
    env = sb.env(ASCENDO_WEB_REGISTRY_PATH=_squirrel_feed_registry(sb), ASCENDO_WEB_USER_REGISTRY_PATH="",
                 ASCENDO_SUDO_WARM_DISABLE="1", ASCENDO_WEB_VENDOR_FEED_DIR=feeds,
                 ASCENDO_WEB_SLEEP_BIN=sb.bin / "sleep_stub", ASCENDO_WEB_DOCKER_BIN=sb.tmp / "no-docker",
                 ASCENDO_WEB_ALLOW_GUI="1", ASCENDO_WEB_STAGE_WAIT="1", ASCENDO_RUNS_DIR=runs,
                 PYTHONPATH=f"{REPO_ROOT}/core:{ADAPTER}")
    run_id = str(uuid.uuid4())
    res = subprocess.run(["bash", str(WEB_SCRIPTS / "apply.sh"), "--run-id", run_id, "--trigger", "cli",
                          "--profile", "full", "--output-dir", str(out)],
                         capture_output=True, text=True, env=env)
    return res, run_id, out, runs


def test_apply_records_a_launch_only_when_the_app_was_explicitly_idle(sb):
    res, run_id, out, runs = _apply_launch(sb, "false")
    assert sb.read_log("open.log"), res.stderr
    rec = (out / run_id / "toolkit_launched.txt").read_text().split()
    assert rec[0] == BID and rec[1].isdigit()                  # bundle id + launch epoch
    assert (runs / run_id / "toolkit_launched.txt").read_text().split() == rec   # for verify's own tempdir


def test_apply_does_not_launch_or_record_when_running_or_unknown(sb):
    for state in ("true", "unknown"):
        res, run_id, out, runs = _apply_launch(sb, state)
        assert sb.read_log("open.log") == [], state
        assert not (out / run_id / "toolkit_launched.txt").exists(), state
        assert not (runs / run_id / "toolkit_launched.txt").exists(), state


# ---- settle window before any quit; builtin never recorded --------------------------

def test_verify_does_not_quit_before_the_settle_window_elapses(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "s1"
    _apply_sidecar(rd)
    _track_file(rd, BID, stamp=990)
    _tracking_tools(sb)
    res, sc, _ = _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0", ASCENDO_WEB_NOW_EPOCH="1000",
                         ASCENDO_WEB_STAGE_WAIT="90")
    assert sb.read_log("osascript.log") == []
    assert "settle_pending" in _codes(sc)
    assert sc["items"][0]["status"] == "triggered"                        # no false success


def test_verify_quits_once_the_settle_window_has_elapsed(sb):
    make_app(sb.apps, ver="1.0")
    rd = sb.tmp / "out" / "s2"
    _apply_sidecar(rd)
    _track_file(rd, BID, stamp=900)
    _tracking_tools(sb)
    _verify(sb, rd, ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE="0", ASCENDO_WEB_NOW_EPOCH="1000", ASCENDO_WEB_STAGE_WAIT="90")
    assert len(sb.read_log("osascript.log")) == 1


def test_builtin_handler_launch_is_never_recorded_for_quit(sb):
    """`builtin` opens the app FOR the user (Help -> Check for Updates)."""
    make_app(sb.apps, ver="1.0")
    sb.set_ls("false")
    script(sb.bin / "open", f'echo "open $*" >> "{sb.log}/open.log"\nexit 0\n')
    track = sb.tmp / "track.txt"
    r = sb.bash(
        f'. "{LIB}/handlers/builtin.sh"; builtin_apply vt \'{{"app_path":"{sb.apps}/Example.app",'
        f'"bundle_id":"{BID}","display_name":"Example","handler":"builtin"}}\'',
        ASCENDO_WEB_LAUNCH_TRACK_FILE=track, ASCENDO_SAFE_MODE="false")
    assert sb.read_log("open.log"), r.stderr
    assert not track.exists()


def test_web_manager_exports_the_run_base_dir_as_ascendo_runs_dir(tmp_path):
    from ascendo.orchestrator.stream_log import stream_log_context
    from ascendo_macos.managers.web import WebManager
    from ascendo.models.run import Phase

    mgr = WebManager(scripts_dir=SCRIPTS, lib_dir=LIB)
    base = tmp_path / "runs-x"
    with stream_log_context(base / "rid-1" / "_stream.log"):
        assert mgr._build_env(Phase.VERIFY)["ASCENDO_RUNS_DIR"] == str(base)
    assert mgr._build_env(Phase.VERIFY).get("ASCENDO_RUNS_DIR") != str(base)   # unbound: not forced
