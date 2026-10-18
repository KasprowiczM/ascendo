"""chromium_updater handler (Spec C): Chrome, Google Drive, Gemini, Comet.

Port of macOS_updates tests/test_chromium_updaters.py (15) onto the Ascendo handler.
Everything runs against fakes: fixture updater logs / updater_history.jsonl, fake
updater executables, a fake curl for VersionHistory. No real updater, network,
browser launch or /Applications access; waits are 0-2 s.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from .omaha_fakes import (FIXTURES, fake_curl_serving, hermetic_env, history_text,
                          make_app, make_exe, response_line, run_bash, set_app_version)

GOOGLE_LOG_REL = "Library/Application Support/Google/GoogleUpdater/updater.log"
GOOGLE_BIN_REL = ("Library/Application Support/Google/GoogleUpdater/Current/"
                  "GoogleUpdater.app/Contents/MacOS/GoogleUpdater")


class World:
    """Per-test sandbox: HOME, apps dir, fake bin dir, run dir, google user log."""

    def __init__(self, tmp: Path, extra: dict | None = None):
        self.tmp = tmp
        self.apps = tmp / "Applications"
        self.apps.mkdir()
        self.out = tmp / "out"
        self.run_id = "run1"
        (self.out / self.run_id).mkdir(parents=True)
        self.env = hermetic_env(tmp, extra)
        self.home = Path(self.env["HOME"])
        self.user_log = self.home / GOOGLE_LOG_REL
        self.user_log.parent.mkdir(parents=True)
        self.user_log.write_text("")
        self.sys_log = tmp / "sys" / "updater.log"
        self.sys_log.parent.mkdir()
        self.sys_log.write_text("")
        self.calls = tmp / "calls.log"
        self.env.update({
            "ASCENDO_GOOGLE_USER_LOG": str(self.user_log),
            "ASCENDO_GOOGLE_SYS_LOG": str(self.sys_log),
            "ASCENDO_GOOGLE_USER_UPDATER": "",
            "ASCENDO_GOOGLE_SYS_UPDATER": "",
            "ASCENDO_GOOGLE_LEGACY_AGENT": "",
        })
        # Browser-launch tripwires: none of these may ever run.
        for name in ("open", "osascript", "killall", "pkill"):
            make_exe(tmp / "fakebin" / name, f'echo "{name} $@" >> "{tmp}/forbidden.log"\n')

    def app(self, name: str, short: str, build: str | None = None) -> Path:
        return make_app(self.apps, name, short, build)

    def updater(self, body: str = "", name: str = "GoogleUpdater") -> Path:
        """User-scope fake GoogleUpdater: logs its args, then runs `body`."""
        path = self.tmp / "user_bin" / name
        make_exe(path, f'echo "$@" >> "{self.calls}"\n{body}\nexit 0\n')
        self.env["ASCENDO_GOOGLE_USER_UPDATER"] = str(path)
        return path

    def vh(self, fixture: str = "versionhistory.json") -> None:
        self.env["ASCENDO_WEB_CURL_BIN"] = str(fake_curl_serving(self.tmp, FIXTURES / fixture))

    def cfg(self, slug: str, appid: str, app: Path, display: str, *, vh: bool = False,
            comet: bool = False) -> str:
        sub: dict = {"appid": appid}
        if vh:
            sub["version_history_url"] = ("https://versionhistory.googleapis.com/v1/chrome/platforms/"
                                          "mac_arm64/channels/stable/versions/all/releases?filter=endtime=none")
        if comet:
            sub["updater_bin"] = [str(self.tmp / "comet" / "CometUpdater")]
            sub["updater_log"] = [str(self.tmp / "comet" / "updater.log")]
        return json.dumps({"slug": slug, "bundle_id": f"x.{slug}", "display_name": display,
                           "handler": "chromium_updater", "app_path": str(app),
                           "chromium_updater": sub})

    def run(self, snippet: str):
        pre = f'OUTPUT_DIR="{self.out}"; RUN_ID="{self.run_id}"\n'
        return run_bash(pre + snippet, self.env)

    def forbidden_calls(self) -> str:
        f = self.tmp / "forbidden.log"
        return f.read_text() if f.exists() else ""

    def calls_text(self) -> str:
        return self.calls.read_text() if self.calls.exists() else ""


@pytest.fixture
def w(tmp_path: Path) -> World:
    return World(tmp_path)


def _write_history(w: World, events, age_s: float = 1800, log: Path | None = None) -> None:
    log = log or w.user_log
    (log.parent / "updater_history.jsonl").write_text(history_text(events, time.time() - age_s))


def _check(w: World, cfg: str):
    note = w.tmp / "note.txt"
    r = w.run(f"""
        export ASCENDO_WEB_PROBE_NOTE_FILE='{note}'
        out=$(chromium_updater_check x '{cfg}')
        echo "CAND=$out"
    """)
    return r, (note.read_text() if note.exists() else "")


# ------------------------------- check (read-only) -------------------------------

def test_check_history_proof_echoes_installed_and_notes_current_vendor(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    _write_history(w, [("com.google.geminimacos", "T1", "NO_UPDATE", "1.119.2.914")])
    r, note = _check(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert r.returncode == 0, r.stderr
    assert "CAND=1.119.2.914" in r.stdout
    code, text = note.rstrip("\n").split("\t", 1)
    assert code == "current_vendor"
    assert "verified by updater at " in text


def test_check_drive_compares_short_and_build_version(w: World) -> None:
    """Drive registers 131.0.2.0 while the bundle says short 131.0 / build 131.0.2."""
    app = w.app("Google Drive", "131.0", "131.0.2")
    _write_history(w, [("com.google.drivefs", "T1", "NO_UPDATE", "131.0.2.0")])
    r, note = _check(w, w.cfg("gdrive", "com.google.drivefs", app, "Google Drive"))
    assert "CAND=131.0" in r.stdout
    assert note.startswith("current_vendor\t")


def test_check_history_rotated_old_file_is_read(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    (w.user_log.parent / "updater_history.jsonl.old").write_text(history_text(
        [("com.google.geminimacos", "T1", "NO_UPDATE", "1.119.2.914")], time.time() - 600))
    r, _ = _check(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert "CAND=1.119.2.914" in r.stdout


def test_check_stale_or_mismatched_history_is_not_proof(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    cfg = w.cfg("gemini", "com.google.geminimacos", app, "Gemini")
    _write_history(w, [("com.google.geminimacos", "T1", "NO_UPDATE", "1.119.2.914")], age_s=8 * 3600)
    r, note = _check(w, cfg)
    assert r.stdout.strip() == "CAND=" and note == ""
    _write_history(w, [("com.google.geminimacos", "T1", "NO_UPDATE", "1.116.5.889")], age_s=60)
    r, note = _check(w, cfg)
    assert r.stdout.strip() == "CAND=" and note == ""


def test_check_chrome_installed_ge_public_is_current_without_wake(w: World) -> None:
    app = w.app("Google Chrome", "154.0.8037.58")
    w.vh()
    updater = w.updater()
    r, note = _check(w, w.cfg("chrome", "com.google.chrome", app, "Google Chrome", vh=True))
    assert "CAND=154.0.8037.58" in r.stdout
    assert note.startswith("current_verified\t")
    assert updater.exists() and w.calls_text() == "", "check must never wake the updater"


def test_check_chrome_newer_installed_than_public_also_current(w: World) -> None:
    app = w.app("Google Chrome", "155.0.1.1")
    w.vh()
    r, note = _check(w, w.cfg("chrome", "com.google.chrome", app, "Google Chrome", vh=True))
    assert "CAND=155.0.1.1" in r.stdout and note.startswith("current_verified\t")


def test_check_chrome_behind_public_or_unreachable_feed_is_empty(w: World) -> None:
    app = w.app("Google Chrome", "153.0.8010.53")
    cfg = w.cfg("chrome", "com.google.chrome", app, "Google Chrome", vh=True)
    w.vh()
    r, note = _check(w, cfg)
    assert r.stdout.strip() == "CAND=" and note == ""            # vendor_opaque as today
    w.env["ASCENDO_WEB_CURL_BIN"] = str(w.tmp / "fakebin" / "curl-fail")
    r, note = _check(w, cfg)
    assert r.stdout.strip() == "CAND=" and note == ""


def test_check_non_chrome_never_uses_version_history(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    w.vh()
    r, note = _check(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert r.stdout.strip() == "CAND=" and note == ""


# ------------------------------- apply: proof / no wake -------------------------------

def _apply(w: World, cfg: str, slug: str = "x") -> "tuple":
    r = w.run(f"""
        _web_outcome_reset
        chromium_updater_apply {slug} '{cfg}'
        echo "RC=$?"
        echo "OUTCOME=$ASCENDO_WEB_OUTCOME"
        echo "MSG=$ASCENDO_WEB_OUTCOME_MSG"
    """)
    assert r.returncode == 0, r.stderr
    out = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return r, out


def test_apply_proven_current_does_not_wake(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    _write_history(w, [("com.google.geminimacos", "T1", "NO_UPDATE", "1.119.2.914")])
    w.updater()
    _, o = _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert o["RC"] == "0" and o["OUTCOME"] == "current_vendor"
    assert "verified by updater at " in o["MSG"]
    assert w.calls_text() == ""
    assert not (w.out / w.run_id / "_omaha_init.txt").exists()


def test_apply_chrome_installed_ge_public_is_current_verified_without_wake(w: World) -> None:
    app = w.app("Google Chrome", "154.0.8037.58")
    w.vh()
    w.updater()
    _, o = _apply(w, w.cfg("chrome", "com.google.chrome", app, "Google Chrome", vh=True))
    assert o["OUTCOME"] == "current_verified" and w.calls_text() == ""


# ------------------------------- apply: wake + evaluate -------------------------------

def test_apply_noupdate_is_current_vendor(w: World) -> None:
    app = w.app("Gemini", "1.116.5.889")
    fixture = (FIXTURES / "googleupdater.log").read_text()
    w.updater(f"cat >> '{w.user_log}' <<'EOF'\n{fixture}EOF")
    _, o = _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert o["RC"] == "0" and o["OUTCOME"] == "current_vendor"
    assert "--wake-all" in w.calls_text()
    assert w.forbidden_calls() == ""


def test_apply_chrome_noupdate_with_newer_public_is_rollout_hold(w: World) -> None:
    app = w.app("Google Chrome", "153.0.8010.53")
    fixture = (FIXTURES / "googleupdater.log").read_text()
    w.updater(f"cat >> '{w.user_log}' <<'EOF'\n{fixture}EOF")
    w.vh()
    _, o = _apply(w, w.cfg("chrome", "com.google.chrome", app, "Google Chrome", vh=True))
    assert o["RC"] == "0" and o["OUTCOME"] == "rollout_hold"
    assert "153.0.8010.53" in o["MSG"] and "154.0.8037.58" in o["MSG"]
    assert w.forbidden_calls() == ""


def test_apply_update_ok_and_version_changes_is_updated(w: World) -> None:
    app = w.app("Google Drive", "124.0")
    w.env["ASCENDO_OMAHA_VERIFY_WAIT"] = "3"
    w.env["ASCENDO_OMAHA_VERIFY_STEP"] = "1"
    plist = app / "Contents" / "Info.plist"
    w.updater(
        f"cat >> '{w.user_log}' <<'EOF'\n{response_line('com.google.drivefs', 'ok')}EOF\n"
        f"sed -i '' 's/124.0/125.0.0.0/' '{plist}'")
    _, o = _apply(w, w.cfg("gdrive", "com.google.drivefs", app, "Google Drive"))
    assert o["RC"] == "0" and o["OUTCOME"] == "updated"
    assert "125.0.0.0" in o["MSG"]


def test_apply_update_ok_but_version_unchanged_is_unverified(w: World) -> None:
    app = w.app("Google Drive", "124.0")
    w.env["ASCENDO_OMAHA_VERIFY_WAIT"] = "1"
    w.env["ASCENDO_OMAHA_VERIFY_STEP"] = "1"
    w.updater(f"cat >> '{w.user_log}' <<'EOF'\n{response_line('com.google.drivefs', 'ok')}EOF")
    _, o = _apply(w, w.cfg("gdrive", "com.google.drivefs", app, "Google Drive"))
    assert o["RC"] == "0" and o["OUTCOME"] == "unverified"
    assert "in progress" in o["MSG"]


def test_apply_no_response_falls_back_to_history_written_during_wake(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    hist = w.user_log.parent / "updater_history.jsonl"
    events = history_text([("com.google.geminimacos", "T1", "NO_UPDATE", "1.119.2.914")],
                          time.time() - 5)
    w.updater(f"cat > '{hist}' <<'EOF'\n{events}EOF")
    _, o = _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert o["OUTCOME"] == "current_vendor"


def test_apply_no_response_chrome_behind_public_is_rollout_hold(w: World) -> None:
    app = w.app("Google Chrome", "153.0.8010.53")
    w.updater()                      # wakes fine, writes nothing
    w.vh()
    _, o = _apply(w, w.cfg("chrome", "com.google.chrome", app, "Google Chrome", vh=True))
    assert o["RC"] == "0" and o["OUTCOME"] == "rollout_hold"


def test_apply_no_response_no_feed_is_unverified_triggered(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    w.updater()
    _, o = _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert o["RC"] == "0" and o["OUTCOME"] == "unverified"


def test_apply_waits_for_its_own_appid_not_another_apps_response(w: World) -> None:
    """A Drive noupdate must not end the wait for Chrome: Chrome's arrives 1 s later."""
    app = w.app("Gemini", "1.119.2.914")
    w.env["ASCENDO_OMAHA_WAIT"] = "5"
    w.updater(
        f"cat >> '{w.user_log}' <<'EOF'\n{response_line('com.google.drivefs')}EOF\n"
        f"( sleep 1; cat >> '{w.user_log}' <<'EOF2'\n{response_line('com.google.geminimacos')}EOF2\n"
        f") >/dev/null 2>&1 &")
    _, o = _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert o["OUTCOME"] == "current_vendor"


def test_apply_reads_log_increment_across_rotation(w: World) -> None:
    """The updater rotates updater.log -> .old and starts a shorter new log mid-wait:
    the response in the new file is still found (size < init => read .old tail + new)."""
    app = w.app("Gemini", "1.119.2.914")
    w.user_log.write_text("X" * 600 + "\n")
    w.updater(
        f"mv '{w.user_log}' '{w.user_log}.old'\n"
        f"cat >> '{w.user_log}' <<'EOF'\n{response_line('com.google.geminimacos')}EOF")
    _, o = _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert o["OUTCOME"] == "current_vendor"


# ------------------------------- wake order / failures -------------------------------

def test_wake_prefers_user_updater_and_skips_system_and_legacy(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    w.updater()
    sys_calls = w.tmp / "sys_calls.log"
    w.env["ASCENDO_GOOGLE_SYS_UPDATER"] = str(make_exe(w.tmp / "sys_bin" / "GoogleUpdater",
                                                       f'echo sys >> "{sys_calls}"\n'))
    legacy_calls = w.tmp / "legacy_calls.log"
    w.env["ASCENDO_GOOGLE_LEGACY_AGENT"] = str(make_exe(w.tmp / "legacy" / "Agent",
                                                        f'echo legacy >> "{legacy_calls}"\n'))
    _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert "--wake-all" in w.calls_text()
    assert not sys_calls.exists() and not legacy_calls.exists()


def test_wake_falls_to_system_updater_when_user_one_fails(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    make_exe(w.tmp / "user_bin" / "GoogleUpdater", f'echo user >> "{w.calls}"\nexit 1\n')
    w.env["ASCENDO_GOOGLE_USER_UPDATER"] = str(w.tmp / "user_bin" / "GoogleUpdater")
    w.env["ASCENDO_GOOGLE_SYS_UPDATER"] = str(make_exe(w.tmp / "sys_bin" / "GoogleUpdater",
                                                       f'echo "sys $@" >> "{w.calls}"\n'))
    _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert w.calls_text().splitlines()[0] == "user"
    assert "sys --wake-all" in w.calls_text()


def test_wake_uses_legacy_agent_only_when_no_updater_exists(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    w.env["ASCENDO_GOOGLE_LEGACY_AGENT"] = str(make_exe(
        w.tmp / "legacy" / "Agent", f'echo "legacy $@" >> "{w.calls}"\n'))
    _, o = _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert "legacy --runMode ondemand" in w.calls_text()
    assert o["RC"] == "0" and o["OUTCOME"] == "unverified"


def test_wake_failure_is_error_soft_not_failed(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    _, o = _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))   # no updater at all
    assert o["RC"] == "0" and o["OUTCOME"] == "wake_failed"
    assert "no updater" in o["MSG"].lower()

    (w.tmp / "second").mkdir()
    w2 = World(w.tmp / "second")
    app2 = w2.app("Gemini", "1.119.2.914")
    make_exe(w2.tmp / "user_bin" / "GoogleUpdater", "exit 7\n")
    w2.env["ASCENDO_GOOGLE_USER_UPDATER"] = str(w2.tmp / "user_bin" / "GoogleUpdater")
    _, o2 = _apply(w2, w2.cfg("gemini", "com.google.geminimacos", app2, "Gemini"))
    assert o2["RC"] == "0" and o2["OUTCOME"] == "wake_failed"
    assert "7" in o2["MSG"]


# ------------------------------- shared window / Comet -------------------------------

def test_three_google_apps_share_one_wake_and_one_window(w: World) -> None:
    gem = w.app("Gemini", "1.119.2.914")
    drv = w.app("Google Drive", "125.0.0.0")
    chr_ = w.app("Google Chrome", "153.0.8010.53")
    w.env["ASCENDO_OMAHA_WAIT"] = "2"
    sleeps = w.tmp / "sleeps.log"
    w.env["ASCENDO_WEB_SLEEP_BIN"] = str(make_exe(w.tmp / "fakebin" / "sleepy",
                                                  f'echo "$1" >> "{sleeps}"\n/bin/sleep "$1"\n'))
    w.updater()      # writes no response: every app waits the (shared) window
    cfgs = [w.cfg("gemini", "com.google.geminimacos", gem, "Gemini"),
            w.cfg("gdrive", "com.google.drivefs", drv, "Google Drive"),
            w.cfg("chrome", "com.google.chrome", chr_, "Google Chrome")]
    t0 = time.time()
    r = w.run("\n".join(f"_web_outcome_reset; chromium_updater_apply s '{c}'" for c in cfgs))
    elapsed = time.time() - t0
    assert r.returncode == 0, r.stderr
    assert len(w.calls_text().splitlines()) == 1, "updater must be woken once per run"
    state = (w.out / w.run_id / "_omaha_init.txt").read_text().split()
    assert len(state) == 3 and all(s.isdigit() for s in state)
    total_sleep = sum(int(x) for x in sleeps.read_text().split()) if sleeps.exists() else 0
    assert total_sleep <= 2, f"waited {total_sleep}s, shared window is 2s"
    assert elapsed < 6


def test_comet_uses_its_own_updater_and_log_and_per_app_state(w: World) -> None:
    app = w.app("Comet", "153.0.8010.191")
    comet_calls = w.tmp / "comet_calls.log"
    comet_log = w.tmp / "comet" / "updater.log"
    make_exe(w.tmp / "comet" / "CometUpdater",
             f'echo "$@" >> "{comet_calls}"\n'
             f'cat >> "{comet_log}" <<\'EOF\'\n{response_line("ai.perplexity.comet")}EOF\n')
    comet_log.write_text("")
    # GoogleUpdater is a tripwire here: must never run for Comet.
    w.env["ASCENDO_GOOGLE_USER_UPDATER"] = str(make_exe(
        w.tmp / "user_bin" / "GoogleUpdater", f'echo google >> "{w.calls}"\n'))
    _, o = _apply(w, w.cfg("comet", "ai.perplexity.comet", app, "Comet", comet=True), slug="comet")
    assert o["OUTCOME"] == "current_vendor"
    assert "--wake-all" in comet_calls.read_text()
    assert w.calls_text() == ""
    assert (w.out / w.run_id / "_omaha_init_comet.txt").exists()
    assert not (w.out / w.run_id / "_omaha_init.txt").exists()


def test_comet_history_next_to_its_log_skips_wake(w: World) -> None:
    app = w.app("Comet", "153.0.8010.191")
    comet_calls = w.tmp / "comet_calls.log"
    make_exe(w.tmp / "comet" / "CometUpdater", f'echo "$@" >> "{comet_calls}"\n')
    (w.tmp / "comet" / "updater.log").write_text("")
    (w.tmp / "comet" / "updater_history.jsonl").write_text(history_text(
        [("ai.perplexity.comet", "T1", "NO_UPDATE", "153.0.8010.191")], time.time() - 600))
    _, o = _apply(w, w.cfg("comet", "ai.perplexity.comet", app, "Comet", comet=True), slug="comet")
    assert o["OUTCOME"] == "current_vendor" and not comet_calls.exists()


def test_increment_reader_handles_rotation(w: World) -> None:
    log = w.tmp / "updater.log"
    (w.tmp / "updater.log.old").write_text("A" * 100 + "STATUS_IN_OLD\n")
    log.write_text("NEW_HEADER_LINE\n")
    r = w.run(f"_cu_read_increment '{log}' 100")
    assert "STATUS_IN_OLD" in r.stdout and "NEW_HEADER_LINE" in r.stdout
    log.write_text("0123456789ABC")
    assert w.run(f"_cu_read_increment '{log}' 10").stdout == "ABC"
    assert w.run(f"_cu_read_increment '{w.tmp}/missing.log' 10").stdout == ""


def test_handler_never_launches_or_quits_anything(w: World) -> None:
    app = w.app("Gemini", "1.119.2.914")
    w.updater(f"cat >> '{w.user_log}' <<'EOF'\n{response_line('com.google.geminimacos', 'ok')}EOF")
    _check(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    _apply(w, w.cfg("gemini", "com.google.geminimacos", app, "Gemini"))
    assert w.forbidden_calls() == ""
