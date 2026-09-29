"""Homebrew safety: never close user apps, Xcode licence gate, outdated rc
handling, SUDO_ASKPASS for the manager.

Everything runs against fake binaries on PATH (brew, pgrep, osascript, pkill,
xcode-select, xcodebuild). No real brew/Xcode/app is ever touched.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
LIB = ADAPTER_ROOT / "lib" / "ascendo_brew.sh"
JSON_LIB = ADAPTER_ROOT / "lib" / "ascendo_json.sh"
SCRIPTS = ADAPTER_ROOT / "scripts" / "brew"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="bash + jq required",
)

OUTDATED = {
    "formulae": [],
    "casks": [
        {"name": ["brave-browser"], "installed_versions": "1.0", "current_version": "2.0"},
        {"name": ["slack"], "installed_versions": "3.0", "current_version": "4.0"},
    ],
}


def _write_exec(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return path


class Env:
    """Fake-binary sandbox: bin dir, call logs, env for the child."""

    def __init__(self, tmp: Path, *, outdated_rc: int = 0, outdated_stderr: str = "",
                 outdated: dict | None = None, running_marker: str | None = None,
                 xcode_path: str | None = None, xcodebuild_rc: int = 0) -> None:
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir(parents=True)
        self.brew_log = tmp / "brew.log"
        self.kill_log = tmp / "kill.log"
        self.apps = tmp / "Applications"
        self.apps.mkdir()
        (self.apps / "Brave Browser.app").mkdir()
        (self.apps / "Slack.app").mkdir()
        payload = json.dumps(outdated if outdated is not None else OUTDATED)
        (tmp / "outdated.json").write_text(payload)
        (tmp / "outdated.err").write_text(outdated_stderr)
        _write_exec(self.bin / "brew", f'''
echo "$@" >> "{self.brew_log}"
case "$1" in
  --version) echo "Homebrew 9.9.9" ;;
  --prefix) echo "{tmp}/prefix" ;;
  outdated)
    cat "{tmp}/outdated.json"
    cat "{tmp}/outdated.err" >&2
    exit {outdated_rc} ;;
  list) exit 0 ;;
  upgrade) echo "==> upgraded $*"; exit 0 ;;
  cleanup) exit 0 ;;
esac
exit 0
''')
        # pgrep: "running" only for patterns mentioning the marker app.
        marker = running_marker or "__nothing_running__"
        _write_exec(self.bin / "pgrep", f'''
echo "pgrep $*" >> "{self.kill_log}"
case "$*" in *"{marker}"*) exit 0 ;; esac
exit 1
''')
        for name in ("osascript", "pkill", "kill", "killall"):
            _write_exec(self.bin / name, f'echo "{name} $*" >> "{self.kill_log}"\nexit 0\n')
        if xcode_path is not None:
            _write_exec(self.bin / "xcode-select", f'echo "{xcode_path}"\n')
            _write_exec(self.bin / "xcodebuild",
                        f'echo "xcodebuild $*" >> "{tmp}/xcodebuild.log"\nexit {xcodebuild_rc}\n')
        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.bin}:{self.env['PATH']}"
        self.env["ASCENDO_BREW_APPLICATIONS_DIR"] = str(self.apps)
        self.env.pop("ASCENDO_STREAM_LOG", None)

    def brew_calls(self) -> list[str]:
        return self.brew_log.read_text().splitlines() if self.brew_log.exists() else []

    def brew_work_calls(self) -> list[str]:
        """brew invocations other than the `--version` metadata probe."""
        return [c for c in self.brew_calls() if c != "--version"]

    def kill_calls(self) -> list[str]:
        if not self.kill_log.exists():
            return []
        return [ln for ln in self.kill_log.read_text().splitlines() if not ln.startswith("pgrep")]

    def run_phase(self, phase: str, *extra: str):
        out = self.tmp / "out"
        rid = str(uuid.uuid4())
        res = subprocess.run(
            ["bash", str(SCRIPTS / f"{phase}.sh"), "--run-id", rid, "--trigger", "cli",
             "--profile", "default", "--output-dir", str(out), *extra],
            capture_output=True, text=True, env=self.env, check=False,
        )
        return res, json.loads((out / rid / f"{phase}__brew.json").read_text())


# ── never close a running app ────────────────────────────────────────────────

def test_running_cask_is_skipped_never_killed_and_brew_not_called(tmp_path):
    env = Env(tmp_path, running_marker="Brave Browser")
    res, sc = env.run_phase("apply")
    assert res.returncode == 0, res.stderr

    by_id = {i["id"]: i for i in sc["items"]}
    assert by_id["brave-browser"]["status"] == "skipped"
    # The candidate must survive the deferral: apply outranks check in the
    # inventory flush, so a dropped target would hide the outdated cask.
    assert by_id["brave-browser"]["current_version"] == "1.0"
    assert by_id["brave-browser"]["target_version"] == "2.0"
    assert by_id["slack"]["status"] == "success"

    # Nothing was quit or killed; brew never touched the running cask.
    assert env.kill_calls() == []
    upgrades = [c for c in env.brew_calls() if c.startswith("upgrade")]
    assert upgrades == ["upgrade --cask slack"]

    msg = next(m for m in sc["messages"] if m.get("code") == "CASK_APP_RUNNING")
    assert msg["text"].startswith("brave-browser: ")
    assert "ASCENDO-ACTION-REQUIRED reason=app_in_use cur=1.0 cand=2.0" in msg["text"]


def test_idle_cask_is_upgraded_and_still_no_kill(tmp_path):
    env = Env(tmp_path)  # nothing running
    res, sc = env.run_phase("apply")
    assert res.returncode == 0, res.stderr
    assert {i["status"] for i in sc["items"]} == {"success"}
    assert "upgrade --cask brave-browser" in env.brew_calls()
    assert env.kill_calls() == []


def test_dry_run_never_kills_or_upgrades(tmp_path):
    env = Env(tmp_path, running_marker="Brave Browser")
    res, sc = env.run_phase("apply", "--dry-run")
    assert res.returncode == 0, res.stderr
    assert {i["status"] for i in sc["items"]} == {"planned"}
    assert not [c for c in env.brew_calls() if c.startswith("upgrade")]
    assert env.kill_calls() == []


def test_kill_helper_is_gone_and_scripts_never_reference_kill():
    lib = LIB.read_text()
    assert "ascendo_brew_kill_cask_apps" not in lib
    for script in SCRIPTS.glob("*.sh"):
        text = script.read_text()
        for needle in ("pkill", "osascript", "kill_cask_apps", "killall"):
            assert needle not in text, f"{script.name} references {needle}"
    # The library only mentions them in comments describing the guarantee.
    for line in lib.splitlines():
        code = line.split("#", 1)[0]
        assert "pkill" not in code and "osascript" not in code


def test_app_running_probe_escapes_path_and_reports_state(tmp_path):
    env = Env(tmp_path, running_marker="Brave Browser")
    script = f'. "{LIB}"; ascendo_brew_cask_app_running brave-browser; echo "brave=$?"; ' \
             f'ascendo_brew_cask_app_running slack; echo "slack=$?"; ' \
             f'ascendo_brew_cask_app_running no-such-cask; echo "none=$?"'
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env.env)
    assert res.stdout.split() == ["brave=0", "slack=1", "none=1"], res.stdout + res.stderr
    pgrep_lines = [ln for ln in env.kill_log.read_text().splitlines() if ln.startswith("pgrep")]
    assert any(r"Brave Browser\.app/Contents/" in ln for ln in pgrep_lines)


# ── Xcode licence gate ───────────────────────────────────────────────────────

XCODE_APP = "/Applications/Xcode.app/Contents/Developer"
CLT = "/Library/Developer/CommandLineTools"


def _license_ok(env: Env) -> int:
    res = subprocess.run(["bash", "-c", f'. "{LIB}"; ascendo_brew_xcode_license_ok'],
                         capture_output=True, text=True, env=env.env)
    return res.returncode


def test_license_ok_helper_matrix(tmp_path):
    assert _license_ok(Env(tmp_path / "a", xcode_path=XCODE_APP, xcodebuild_rc=69)) == 1
    assert _license_ok(Env(tmp_path / "b", xcode_path=XCODE_APP, xcodebuild_rc=0)) == 0
    # CLT selected: xcodebuild is never consulted.
    clt = Env(tmp_path / "c", xcode_path=CLT, xcodebuild_rc=69)
    assert _license_ok(clt) == 0
    assert not (clt.tmp / "xcodebuild.log").exists()


@pytest.mark.parametrize("phase", ["check", "plan", "apply", "cleanup"])
def test_unaccepted_licence_gates_every_phase_before_any_brew_call(tmp_path, phase):
    env = Env(tmp_path, xcode_path=XCODE_APP, xcodebuild_rc=69)
    res, sc = env.run_phase(phase)
    assert res.returncode == 10, res.stderr
    assert env.brew_work_calls() == [], "brew must never run while the licence is unaccepted"
    assert sc["summary"]["exit_code"] == 10
    assert sc["status"] == "skipped"
    # No synthetic item: it would surface as a fake brew package in the
    # inventory. The exit-10 / zero-item sidecar resolves to `skipped`.
    assert sc["items"] == []
    assert sc["summary"]["total"] == 0
    msg = next(m for m in sc["messages"] if m.get("code") == "XCODE_LICENSE")
    assert "sudo xcodebuild -license accept" in msg["text"]
    assert "reason=xcode_license" in msg["text"]
    # Never auto-accepted: only the read-only `-license check` was run.
    calls = (env.tmp / "xcodebuild.log").read_text().splitlines()
    assert calls and all(c == "xcodebuild -license check" for c in calls)


def test_verify_is_gated_when_apply_sidecar_exists(tmp_path):
    env = Env(tmp_path, xcode_path=XCODE_APP, xcodebuild_rc=69)
    out = env.tmp / "out"
    rid = str(uuid.uuid4())
    (out / rid).mkdir(parents=True)
    (out / rid / "apply__brew.json").write_text(json.dumps({"items": []}))
    res = subprocess.run(
        ["bash", str(SCRIPTS / "verify.sh"), "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out)],
        capture_output=True, text=True, env=env.env)
    assert res.returncode == 10
    assert env.brew_work_calls() == []


@pytest.mark.parametrize("xcode_path,rc", [(CLT, 69), (XCODE_APP, 0)])
def test_accepted_or_clt_licence_lets_brew_run(tmp_path, xcode_path, rc):
    env = Env(tmp_path, xcode_path=xcode_path, xcodebuild_rc=rc)
    res, sc = env.run_phase("check")
    assert res.returncode == 0, res.stderr
    assert any(c.startswith("outdated") for c in env.brew_calls())
    assert not any(m.get("code") == "XCODE_LICENSE" for m in sc["messages"])


# ── brew outdated rc handling ────────────────────────────────────────────────

def _outdated_json(env: Env) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{LIB}"; ascendo_brew_outdated_json'],
                          capture_output=True, text=True, env=env.env)


def test_outdated_rc1_without_error_line_is_success(tmp_path):
    env = Env(tmp_path, outdated_rc=1, outdated_stderr="==> Downloading Homebrew API data\n")
    res = _outdated_json(env)
    assert res.returncode == 0
    assert {c["name"][0] for c in json.loads(res.stdout)["casks"]} == {"brave-browser", "slack"}


def test_outdated_rc1_with_error_line_is_failure_with_reason(tmp_path):
    env = Env(tmp_path, outdated_rc=1,
              outdated_stderr="Error: A full Xcode licence is required\n")
    res = _outdated_json(env)
    assert res.returncode == 1
    assert json.loads(res.stdout) == {"formulae": [], "casks": []}
    assert "Error: A full Xcode licence is required" in res.stderr


def test_outdated_other_rc_is_failure(tmp_path):
    env = Env(tmp_path, outdated_rc=2, outdated_stderr="boom\n")
    assert _outdated_json(env).returncode == 2


def test_phase_check_succeeds_on_rc1_and_reports_real_error_otherwise(tmp_path):
    ok = Env(tmp_path / "ok", outdated_rc=1, outdated_stderr="==> chatter\n")
    res, sc = ok.run_phase("check")
    assert res.returncode == 0
    assert sum(1 for i in sc["items"] if i["status"] == "planned") == 2

    bad = Env(tmp_path / "bad", outdated_rc=1,
              outdated_stderr="==> chatter\nError: tap is broken\n")
    res, sc = bad.run_phase("check")
    assert res.returncode == 30
    msg = next(m for m in sc["messages"] if m.get("code") == "BREW_OUTDATED_FAIL")
    assert "Error: tap is broken" in msg["text"], "real stderr reason must reach the sidecar"
    assert "unexpectedly" not in msg["text"]


@pytest.mark.parametrize("phase", ["plan", "apply"])
def test_other_phases_surface_the_stderr_reason(tmp_path, phase):
    env = Env(tmp_path, outdated_rc=1, outdated_stderr="Error: network down\n")
    res, sc = env.run_phase(phase)
    assert res.returncode == 30
    msg = next(m for m in sc["messages"] if m.get("code") == "BREW_OUTDATED_FAIL")
    assert "network down" in msg["text"]


# ── SUDO_ASKPASS for the manager ─────────────────────────────────────────────

class _FakeElevation:
    def __init__(self, *, has_pw: bool, helper: Path | None):
        self._has_pw, self._helper = has_pw, helper

    def has_password_registered(self) -> bool:
        return self._has_pw

    def askpass_path(self) -> Path | None:
        return self._helper


def _mgr(elevation=None):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    from ascendo_macos.managers.brew import BrewManager
    return BrewManager(scripts_dir=ADAPTER_ROOT / "scripts", lib_dir=ADAPTER_ROOT / "lib",
                       elevation=elevation)


def test_manager_injects_askpass_for_apply_only(monkeypatch):
    from ascendo.models.run import Phase
    monkeypatch.delenv("SUDO_ASKPASS", raising=False)
    helper = Path("/tmp/askpass-helper.sh")
    m = _mgr(_FakeElevation(has_pw=True, helper=helper))
    assert m._build_env(Phase.APPLY)["SUDO_ASKPASS"] == str(helper)
    for phase in (Phase.CHECK, Phase.PLAN, Phase.VERIFY, Phase.CLEANUP):
        assert "SUDO_ASKPASS" not in m._build_env(phase)


def test_manager_no_askpass_without_cached_password_or_elevation(monkeypatch):
    from ascendo.models.run import Phase
    monkeypatch.delenv("SUDO_ASKPASS", raising=False)
    assert "SUDO_ASKPASS" not in _mgr(_FakeElevation(has_pw=False, helper=Path("/x")))._build_env(Phase.APPLY)
    assert "SUDO_ASKPASS" not in _mgr(None)._build_env(Phase.APPLY)
    assert "SUDO_ASKPASS" not in _mgr(_FakeElevation(has_pw=True, helper=None))._build_env(Phase.APPLY)


def test_run_phase_passes_askpass_env_to_child(monkeypatch, tmp_path):
    """The env built for APPLY is what actually reaches the spawn."""
    from datetime import datetime, timezone
    from uuid import UUID
    from ascendo.models.host import HostInfo, OperatingSystem
    from ascendo.models.run import Phase, RunInfo, Trigger
    from unittest.mock import patch

    monkeypatch.delenv("SUDO_ASKPASS", raising=False)
    m = _mgr(_FakeElevation(has_pw=True, helper=Path("/tmp/askpass-helper.sh")))
    host = HostInfo(hostname="h", os=OperatingSystem.MACOS, os_version="14", arch="arm64",
                    user="u", is_elevated=False)
    run = RunInfo(id=UUID("00000000-0000-0000-0000-000000000009"), trigger=Trigger.CLI,
                  profile="default", dry_run=False,
                  started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    seen: dict = {}

    def fake_run(self, argv, log_path, timeout, *, env):
        seen["env"] = env
        raise OSError("stop here")

    with patch.object(type(m), "_run_streaming", fake_run):
        with pytest.raises(Exception):
            m.run_phase(Phase.APPLY, run, host)
    assert seen["env"]["SUDO_ASKPASS"] == "/tmp/askpass-helper.sh"


# ── exit-10 (precondition failed) status rule in _json_emit ───────────────────

def _finalize(tmp_path: Path, *, exit_code: int, add_item: bool = False) -> dict:
    helper = ADAPTER_ROOT / "lib" / "_json_emit.py"
    buf, out = tmp_path / "buf", tmp_path / "out.json"

    def run(*args: str) -> None:
        subprocess.run([sys.executable, str(helper), *args], check=True, capture_output=True, text=True)

    run("init", "--bufdir", str(buf), "--phase", "check", "--category", "brew",
        "--run-id", "00000000-0000-0000-0000-000000000001", "--trigger", "cli",
        "--profile-name", "default", "--tool-name", "brew", "--tool-version", "1",
        "--started-at", "2026-05-03T12:00:00Z", "--host-name", "h", "--host-os", "macos",
        "--host-os-version", "14", "--host-arch", "arm64", "--host-user", "u",
        "--host-is-elevated", "false")
    if add_item:
        run("add-item", "--bufdir", str(buf), "--id", "x", "--status", "up_to_date",
            "--source-type", "brew")
    run("finalize", "--bufdir", str(buf), "--out", str(out), "--exit-code", str(exit_code),
        "--ended-at", "2026-05-03T12:00:01Z")
    return json.loads(out.read_text())


def test_exit_10_with_no_items_is_skipped_not_success(tmp_path):
    sc = _finalize(tmp_path, exit_code=10)
    assert sc["status"] == "skipped" and sc["summary"]["exit_code"] == 10
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    from ascendo.models.sidecar import parse_sidecar
    parse_sidecar(json.dumps(sc))  # zero-item skipped sidecar stays schema-valid


def test_exit_0_with_no_items_and_exit_10_with_items_are_unchanged(tmp_path):
    assert _finalize(tmp_path / "a", exit_code=0)["status"] == "success"
    assert _finalize(tmp_path / "b", exit_code=10, add_item=True)["status"] == "success"
