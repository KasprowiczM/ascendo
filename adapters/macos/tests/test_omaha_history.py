"""Omaha proof from the Chromium updater's structured history (port of macOS_updates
tests/test_omaha_history.py) plus the omaha_history.py CLI used by chromium_updater.sh.

GoogleUpdater 156 and CometUpdater log the Omaha response on a line of its own below
the ``[pid:tid:MMDD/HHMMSS...]`` prefix; ``updater_history.jsonl`` carries the same
outcome structurally. These tests pin both readers and the bash-facing CLI.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

from omaha_history import (  # noqa: E402
    omaha_history_status,
    omaha_recent_status,
    resolve_cfg,
)

from .omaha_fakes import history_text, win_us as _win_us  # noqa: E402,F401

NOW = 1_790_609_000.0  # fixed "now" for the pure-function tests
CLI = LIB / "omaha_history.py"


def test_recent_noupdate_matching_installed_is_proof() -> None:
    text = history_text([("com.google.geminimacos", "T1", "NO_UPDATE", "1.119.2.914")], NOW - 3 * 3600)
    res = omaha_history_status(text, "com.google.geminimacos", ["1.119.2.914"], 6, now=NOW)
    assert res is not None
    assert res[0] == "noupdate"
    assert res[2] == "1.119.2.914"
    assert res[1] == datetime.datetime.fromtimestamp(NOW - 3 * 3600).strftime("%H:%M")


def test_trailing_zero_components_compare_equal() -> None:
    """Drive registers 131.0.2.0 while its bundle says 131.0.2."""
    text = history_text([("com.google.drivefs", "T1", "NO_UPDATE", "131.0.2.0")], NOW - 600)
    res = omaha_history_status(text, "com.google.drivefs", ["131.0", "131.0.2"], 6, now=NOW)
    assert res and res[0] == "noupdate"


def test_stale_evidence_is_rejected() -> None:
    text = history_text([("com.google.drivefs", "T1", "NO_UPDATE", "131.0.2.0")], NOW - 7 * 3600)
    assert omaha_history_status(text, "com.google.drivefs", ["131.0.2"], 6, now=NOW) is None


def test_version_mismatch_is_not_proof() -> None:
    text = history_text([("com.google.geminimacos", "T1", "NO_UPDATE", "1.116.5.889")], NOW - 60)
    assert omaha_history_status(text, "com.google.geminimacos", ["1.119.2.914"], 6, now=NOW) is None


def test_latest_event_wins() -> None:
    older = history_text([("com.google.chrome", "T1", "NO_UPDATE", "153.0.8010.53")], NOW - 5 * 3600)
    newer = history_text([("com.google.chrome", "T2", "UPDATED", "154.0.8037.58")], NOW - 3600)
    res = omaha_history_status(older + newer, "com.google.chrome", ["154.0.8037.58"], 6, now=NOW)
    assert res and res[0] == "updated"


def test_other_apps_and_garbage_are_ignored() -> None:
    text = "not json\n" + history_text([("com.google.chrome", "T1", "NO_UPDATE", "154.0")], NOW - 60)
    assert omaha_history_status(text, "com.google.drivefs", ["131.0"], 6, now=NOW) is None
    assert omaha_history_status("", "com.google.chrome", ["154.0"], 6, now=NOW) is None


def test_update_error_is_not_proof() -> None:
    text = history_text([("ai.perplexity.comet", "T1", "UPDATE_ERROR", "153.0.8010.191")], NOW - 60)
    assert omaha_history_status(text, "ai.perplexity.comet", ["153.0.8010.191"], 6, now=NOW) is None


def test_response_on_line_after_prefix_is_timestamped() -> None:
    now = datetime.datetime(2026, 9, 28, 17, 38, 0)
    log = (
        "[29873:16872739:0928/141044.149349:VERBOSE2:components/update_client/"
        "request_sender.cc:151] Omaha response received: )]}'\n"
        '{"response":{"apps":[{"appid":"com.google.geminimacos","status":"ok",'
        '"updatecheck":{"status":"noupdate"}}]}}\n'
    )
    assert omaha_recent_status(log, "com.google.geminimacos", 6, now=now) == ("noupdate", "14:10")


# --------------------------- CLI (what bash calls) ---------------------------

def _cli(*args: str, stdin: str = "", env: dict | None = None) -> str:
    e = {k: v for k, v in os.environ.items() if not k.startswith("ASCENDO_")}
    e.update(env or {})
    r = subprocess.run([sys.executable, str(CLI), *args], input=stdin,
                       capture_output=True, text=True, env=e)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_cli_history_reads_files_from_stdin(tmp_path: Path) -> None:
    f = tmp_path / "updater_history.jsonl"
    f.write_text(history_text([("com.google.drivefs", "T1", "NO_UPDATE", "131.0.2.0")],
                              time.time() - 1800), encoding="utf-8")
    out = _cli("history", "com.google.drivefs", "6", "131.0", "131.0.2", stdin=f"{f}\n")
    status, hhmm, version = out.strip().split("\t")
    assert status == "noupdate" and version == "131.0.2.0" and len(hhmm) == 5
    # a version that does not match proves nothing
    assert _cli("history", "com.google.drivefs", "6", "9.9", stdin=f"{f}\n").strip() == ""


def test_cli_last_recent_and_vh() -> None:
    log = ('[1:2:0923/094646.163000:V:x.cc:200] {"response":{"apps":[{"appid":"com.google.chrome",'
           '"status":"ok","updatecheck":{"status":"noupdate"}}]}}\n')
    assert _cli("last", "com.google.chrome", stdin=log).strip() == "noupdate"
    assert _cli("last", "com.google.drivefs", stdin=log).strip() == ""
    vh = (LIB.parent / "tests" / "fixtures" / "vendor_feeds" / "versionhistory.json").read_text()
    assert _cli("vh", stdin=vh).strip() == "154.0.8037.58"
    assert _cli("vh", stdin="garbage").strip() == ""


def test_cfg_google_defaults_and_env_overrides(tmp_path: Path) -> None:
    app = json.dumps({"slug": "gdrive", "chromium_updater": {"appid": "com.google.drivefs"}})
    env = {"ASCENDO_WEB_CFG": app, "HOME": str(tmp_path)}
    bins = _cli("cfg", "updater_bin", env=env).splitlines()
    assert bins[0] == str(tmp_path / "Library/Application Support/Google/GoogleUpdater/Current/"
                          "GoogleUpdater.app/Contents/MacOS/GoogleUpdater")
    assert bins[1].startswith("/Library/Application Support/Google/GoogleUpdater/Current/")
    logs = _cli("cfg", "updater_log", env=env).splitlines()
    assert logs[0].endswith("GoogleUpdater/updater.log") and logs[1].startswith("/Library/")
    assert _cli("cfg", "wake_args", env=env).strip() == "--wake-all"
    assert _cli("cfg", "wait_s", env=env).strip() == "45"
    assert _cli("cfg", "history_max_age_h", env=env).strip() == "6.0"
    assert _cli("cfg", "google", env=env).strip() == "1"
    assert _cli("cfg", "legacy_agent", env=env).strip().endswith("GoogleSoftwareUpdateAgent")

    over = {**env, "ASCENDO_GOOGLE_USER_UPDATER": "/u/up", "ASCENDO_GOOGLE_SYS_UPDATER": "",
            "ASCENDO_GOOGLE_USER_LOG": "/u/log", "ASCENDO_GOOGLE_SYS_LOG": "/s/log",
            "ASCENDO_OMAHA_WAIT": "2", "ASCENDO_OMAHA_MAX_AGE_H": "3"}
    assert _cli("cfg", "updater_bin", env=over).splitlines() == ["/u/up"]   # empty = disabled
    assert _cli("cfg", "updater_log", env=over).splitlines() == ["/u/log", "/s/log"]
    assert _cli("cfg", "wait_s", env=over).strip() == "2"
    assert _cli("cfg", "history_max_age_h", env=over).strip() == "3.0"


def test_cfg_wait_is_clamped_and_comet_is_not_google(tmp_path: Path) -> None:
    app = {"chromium_updater": {"appid": "ai.perplexity.comet",
                                "updater_bin": ["~/Comet/CometUpdater"],
                                "updater_log": ["~/Comet/updater.log"], "wait_s": 999}}
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)
    try:
        cfg = resolve_cfg(app)
    finally:
        if old is not None:
            os.environ["HOME"] = old
    assert cfg["wait_s"] == 180
    assert cfg["google"] == "0"
    assert cfg["updater_bin"] == [str(tmp_path / "Comet/CometUpdater")]
    assert cfg["legacy_agent"] == ""


@pytest.mark.parametrize("bad", ["abc", "-5", ""])
def test_cfg_bad_wait_env_falls_back(bad: str) -> None:
    app = json.dumps({"chromium_updater": {"appid": "x"}})
    out = _cli("cfg", "wait_s", env={"ASCENDO_WEB_CFG": app, "ASCENDO_OMAHA_WAIT": bad})
    assert out.strip() in ("45", "0")
