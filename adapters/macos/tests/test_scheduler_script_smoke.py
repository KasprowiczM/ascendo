"""Tests for adapters/macos/scripts/scheduler/scheduler.sh.

Real-bash tests with a fake launchctl binary on PATH that records argv
to a log file. No real LaunchAgents written — the script's home dir is
overridden via env vars.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "scheduler" / "scheduler.sh"


def _make_fake_launchctl(tmp_path: Path) -> tuple[Path, Path]:
    """Fake launchctl binary recording each invocation to a log file."""
    log = tmp_path / "launchctl.log"
    binary = tmp_path / "launchctl"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        "exit 0\n"
    )
    os.chmod(binary, 0o755)
    return binary, log


def _run(action: str, *, payload: dict | None, tmp_path: Path,
         fake_home: Path | None = None,
         launchctl: Path | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the driver. Returns (CompletedProcess, output.json path)."""
    output = tmp_path / "result.json"
    argv = ["bash", str(SCRIPT), "--action", action, "--output-path", str(output)]
    if payload is not None:
        payload_path = tmp_path / "payload.json"
        payload_path.write_text(json.dumps(payload))
        argv += ["--payload-path", str(payload_path)]
    env = dict(os.environ)
    if fake_home is not None:
        env["ASCENDO_HOME_OVERRIDE"] = str(fake_home)
    if launchctl is not None:
        env["PATH"] = f"{launchctl.parent}{os.pathsep}{env.get('PATH', '')}"
    res = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    return res, output


def test_unknown_action_exits_2(tmp_path):
    res, _ = _run("bogus", payload=None, tmp_path=tmp_path)
    assert res.returncode == 2
    assert "unknown action" in (res.stderr + res.stdout).lower()


def test_missing_output_path_exits_2(tmp_path):
    res = subprocess.run(
        ["bash", str(SCRIPT), "--action", "list"],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 2


def _run_parse_test(expr: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Source the script and call _parse_expression in a sub-shell.

    The driver exposes _parse_expression as a function; we exercise it
    directly to keep this test focused. Set CAL_HOUR/CAL_MINUTE/etc.
    are echoed so we can assert the parsed values.
    """
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/usr/bin/env bash\n"
        f'export PARSE_EXPR_ONLY=1\n'
        f'. "{SCRIPT}" >/dev/null 2>&1 || true\n'  # source for fn defs
        f'_parse_expression "{expr}"\n'
        f'echo "RC=$?"\n'
        'echo "CAL_HOUR=${CAL_HOUR:-}"\n'
        'echo "CAL_MINUTE=${CAL_MINUTE:-}"\n'
        'echo "CAL_WEEKDAY=${CAL_WEEKDAY:-}"\n'
        'echo "CAL_DAY=${CAL_DAY:-}"\n'
        'echo "CAL_INTERVAL_SEC=${CAL_INTERVAL_SEC:-}"\n'
    )
    return subprocess.run(["bash", str(probe)], capture_output=True, text=True, check=False)


def _parse_probe_output(out: str) -> dict:
    d: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
    return d


def test_parse_daily(tmp_path):
    r = _run_parse_test("DAILY 03:00", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_HOUR"] == "3"
    assert out["CAL_MINUTE"] == "0"
    assert out["CAL_WEEKDAY"] == ""
    assert out["CAL_DAY"] == ""
    assert out["CAL_INTERVAL_SEC"] == ""


def test_parse_weekly_sunday(tmp_path):
    r = _run_parse_test("WEEKLY SUN 03:00", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_HOUR"] == "3"
    assert out["CAL_MINUTE"] == "0"
    assert out["CAL_WEEKDAY"] == "0"


def test_parse_weekly_friday_lowercase(tmp_path):
    r = _run_parse_test("weekly fri 23:30", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_HOUR"] == "23"
    assert out["CAL_MINUTE"] == "30"
    assert out["CAL_WEEKDAY"] == "5"


def test_parse_monthly_default_day_one(tmp_path):
    r = _run_parse_test("MONTHLY 02:15", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_HOUR"] == "2"
    assert out["CAL_MINUTE"] == "15"
    assert out["CAL_DAY"] == "1"


def test_parse_monthly_specific_day(tmp_path):
    r = _run_parse_test("MONTHLY 15 04:00", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_DAY"] == "15"
    assert out["CAL_HOUR"] == "4"


def test_parse_hourly(tmp_path):
    r = _run_parse_test("HOURLY :30", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_MINUTE"] == "30"
    assert out["CAL_HOUR"] == ""


def test_parse_minute_interval(tmp_path):
    r = _run_parse_test("MINUTE 5", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_INTERVAL_SEC"] == "300"


def test_parse_garbage_rejected(tmp_path):
    r = _run_parse_test("YEARLY 2026 1 1 03:00", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "2"


def test_install_writes_plist_and_sidecar(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, log = _make_fake_launchctl(tmp_path)
    payload = {
        "name": "weekly-backup",
        "expression": "WEEKLY SUN 03:00",
        "profile": "safe",
        "enabled": True,
        "description": "weekly safe-profile run",
    }
    res, output = _run("install", payload=payload, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 0, res.stderr + res.stdout
    plist = fake_home / "Library/LaunchAgents/dev.ascendo.weekly-backup.plist"
    sidecar = fake_home / "Library/Application Support/Ascendo/schedules/weekly-backup.json"
    assert plist.exists(), "plist not written"
    assert sidecar.exists(), "description sidecar not written"
    body = plist.read_text()
    assert "<string>dev.ascendo.weekly-backup</string>" in body
    assert "<string>--profile</string>" in body
    assert "<string>safe</string>" in body
    assert "<key>Hour</key>" in body and "<integer>3</integer>" in body
    assert "<key>Minute</key>" in body and "<integer>0</integer>" in body
    assert "<key>Weekday</key>" in body and "<integer>0</integer>" in body
    sidecar_data = json.loads(sidecar.read_text())
    assert sidecar_data["description"] == "weekly safe-profile run"
    assert sidecar_data["expression"] == "WEEKLY SUN 03:00"
    assert sidecar_data["profile"] == "safe"
    assert sidecar_data["enabled"] is True
    assert json.loads(output.read_text()) == {"ok": True}
    log_text = log.read_text()
    assert "bootstrap" in log_text  # launchctl bootstrap was invoked


def test_install_disabled_skips_bootstrap(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, log = _make_fake_launchctl(tmp_path)
    payload = {
        "name": "ad-hoc",
        "expression": "DAILY 04:00",
        "profile": "quick",
        "enabled": False,
        "description": None,
    }
    res, _ = _run("install", payload=payload, tmp_path=tmp_path,
                  fake_home=fake_home, launchctl=binary)
    assert res.returncode == 0
    plist = fake_home / "Library/LaunchAgents/dev.ascendo.ad-hoc.plist"
    assert plist.exists(), "disabled plist still written to disk"
    assert "<key>Disabled</key>" in plist.read_text()
    log_text = log.read_text() if log.exists() else ""
    # bootout runs unconditionally (idempotency), bootstrap MUST NOT appear.
    assert "bootout" in log_text, "bootout should run unconditionally on install for idempotency"
    assert "bootstrap" not in log_text


def test_install_rejects_bad_name(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, _ = _make_fake_launchctl(tmp_path)
    payload = {
        "name": "Has Spaces!",
        "expression": "DAILY 03:00",
        "profile": "safe",
        "enabled": True,
        "description": None,
    }
    res, output = _run("install", payload=payload, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 2
    err = json.loads(output.read_text())
    assert "error" in err
    assert "name" in err["error"].lower()


def test_install_rejects_bad_expression(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, _ = _make_fake_launchctl(tmp_path)
    payload = {
        "name": "broken",
        "expression": "YEARLY 2026 03:00",
        "profile": "safe",
        "enabled": True,
        "description": None,
    }
    res, output = _run("install", payload=payload, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 2
    err = json.loads(output.read_text())
    assert "error" in err
    assert "expression" in err["error"].lower() or "unsupported" in err["error"].lower()


def test_install_rejects_bad_profile(tmp_path):
    """Defense-in-depth: profile content guard rejects shell-special chars."""
    fake_home = tmp_path / "fake_home"
    binary, _ = _make_fake_launchctl(tmp_path)
    payload = {
        "name": "weird-profile",
        "expression": "DAILY 03:00",
        "profile": 'safe"injected',
        "enabled": True,
        "description": None,
    }
    res, output = _run("install", payload=payload, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 2
    err = json.loads(output.read_text())
    assert "error" in err
    assert "profile" in err["error"].lower()


def test_uninstall_removes_plist_and_sidecar(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, _ = _make_fake_launchctl(tmp_path)

    # Install first.
    install_payload = {
        "name": "to-remove",
        "expression": "DAILY 05:00",
        "profile": "safe",
        "enabled": True,
        "description": None,
    }
    _run("install", payload=install_payload, tmp_path=tmp_path,
         fake_home=fake_home, launchctl=binary)
    plist = fake_home / "Library/LaunchAgents/dev.ascendo.to-remove.plist"
    sidecar = fake_home / "Library/Application Support/Ascendo/schedules/to-remove.json"
    assert plist.exists() and sidecar.exists()

    # Now uninstall.
    res, output = _run("uninstall", payload={"name": "to-remove"},
                       tmp_path=tmp_path, fake_home=fake_home,
                       launchctl=binary)
    assert res.returncode == 0, res.stderr
    assert not plist.exists(), "plist still on disk after uninstall"
    assert not sidecar.exists(), "sidecar still on disk after uninstall"
    assert json.loads(output.read_text()) == {"ok": True}


def test_uninstall_idempotent_on_missing(tmp_path):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    binary, _ = _make_fake_launchctl(tmp_path)
    res, output = _run("uninstall", payload={"name": "never-existed"},
                       tmp_path=tmp_path, fake_home=fake_home,
                       launchctl=binary)
    assert res.returncode == 0
    assert json.loads(output.read_text()) == {"ok": True}
