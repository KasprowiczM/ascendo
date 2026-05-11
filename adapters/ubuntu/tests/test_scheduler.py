"""SystemdScheduler + scheduler.sh tests — mock-based, no live systemd.

The bash driver tests use a fake `systemctl` placed earlier on PATH so
no actual user units are created. The Python class tests mock
`subprocess.run`.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ascendo.interfaces.scheduler import (
    IScheduler,
    ScheduleSpec,
    SchedulerError,
)
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem


ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCHEDULER_SH = ADAPTER_ROOT / "scripts" / "scheduler" / "scheduler.sh"


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def linux_host() -> HostInfo:
    return HostInfo(
        hostname="testlinux", os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04", arch="x86_64", user="mk",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def mac_host() -> HostInfo:
    return HostInfo(
        hostname="mac.local", os=OperatingSystem.MACOS,
        os_version="14.5", arch="arm64", user="mk",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def fake_systemctl(tmp_path: Path) -> Path:
    """Create a fake systemctl script that just exits 0 (records calls in /tmp/sysctl-log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "systemctl"
    log_file = tmp_path / "sysctl.log"
    fake.write_text(
        f'''#!/usr/bin/env bash
echo "$@" >> "{log_file}"
# Special-case "is-enabled" / "is-active" return realistic strings.
case "$2" in
    is-enabled) echo "enabled"; exit 0 ;;
    is-active)  echo "active"; exit 0 ;;
esac
exit 0
'''
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return fake


@pytest.fixture
def driver_env(tmp_path: Path, fake_systemctl: Path) -> dict[str, str]:
    """Sandbox environment for invoking the bash driver."""
    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    (home / ".local" / "share" / "ascendo" / "schedules").mkdir(parents=True)
    return {
        **os.environ,
        "HOME": str(home),
        "ASCENDO_HOME_OVERRIDE": str(home),
        "ASCENDO_XDG_CONFIG_OVERRIDE": str(home / ".config"),
        "ASCENDO_XDG_DATA_OVERRIDE": str(home / ".local" / "share"),
        "ASCENDO_SYSTEMCTL_BIN": str(fake_systemctl),
        "ASCENDO_BIN_OVERRIDE": "/usr/bin/env ascendo",
    }


def _run_driver(env: dict[str, str], action: str, payload: dict | None,
                tmp_path: Path) -> tuple[int, str, dict | None]:
    output = tmp_path / "out.json"
    argv = ["bash", str(SCHEDULER_SH), "--action", action,
            "--output-path", str(output)]
    if payload is not None:
        payload_path = tmp_path / "payload.json"
        payload_path.write_text(json.dumps(payload))
        argv += ["--payload-path", str(payload_path)]
    res = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=15)
    parsed = None
    if output.exists():
        try:
            parsed = json.loads(output.read_text())
        except json.JSONDecodeError:
            parsed = None
    return res.returncode, res.stderr, parsed


# ── DSL parser tests (sourced module, no dispatch) ────────────────────────


def _parse_via_subshell(expr: str) -> tuple[int, dict[str, str]]:
    """Source scheduler.sh with PARSE_EXPR_ONLY=1, then read SD_ globals."""
    code = f'''
set -e
export PARSE_EXPR_ONLY=1
source "{SCHEDULER_SH}"
_parse_expression "{expr}"
echo "RC=$?"
echo "SD_ONCALENDAR=$SD_ONCALENDAR"
echo "SD_ONUNITACTIVE=$SD_ONUNITACTIVE"
'''
    res = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=10)
    out: dict[str, str] = {}
    for line in res.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return res.returncode, out


def test_dsl_daily_emits_oncalendar() -> None:
    rc, out = _parse_via_subshell("DAILY 03:00")
    assert rc == 0
    assert out.get("SD_ONCALENDAR") == "*-*-* 03:00:00"
    assert out.get("SD_ONUNITACTIVE") == ""


def test_dsl_weekly_emits_oncalendar_with_dayname() -> None:
    rc, out = _parse_via_subshell("WEEKLY MON 03:00")
    assert rc == 0
    assert out.get("SD_ONCALENDAR") == "Mon *-*-* 03:00:00"


def test_dsl_monthly_default_day_is_one() -> None:
    rc, out = _parse_via_subshell("MONTHLY 03:00")
    assert rc == 0
    assert out.get("SD_ONCALENDAR") == "*-*-01 03:00:00"


def test_dsl_monthly_explicit_day() -> None:
    rc, out = _parse_via_subshell("MONTHLY 15 04:30")
    assert rc == 0
    assert out.get("SD_ONCALENDAR") == "*-*-15 04:30:00"


def test_dsl_hourly_emits_minute_oncalendar() -> None:
    rc, out = _parse_via_subshell("HOURLY :30")
    assert rc == 0
    assert out.get("SD_ONCALENDAR") == "*-*-* *:30:00"


def test_dsl_minute_emits_onunitactive() -> None:
    rc, out = _parse_via_subshell("MINUTE 5")
    assert rc == 0
    assert out.get("SD_ONUNITACTIVE") == "5min"
    assert out.get("SD_ONCALENDAR") == ""


def test_dsl_invalid_keyword_returns_2() -> None:
    rc, _ = _parse_via_subshell("UNKNOWN 03:00")
    assert rc == 2


def test_dsl_invalid_time_returns_2() -> None:
    rc, _ = _parse_via_subshell("DAILY 25:00")
    assert rc == 2


# ── Bash driver action dispatch ──────────────────────────────────────────


def test_install_emits_unit_files(driver_env, tmp_path) -> None:
    rc, stderr, parsed = _run_driver(
        driver_env, "install",
        {"name": "ascendo-test", "expression": "DAILY 03:00",
         "profile": "full", "enabled": True, "description": "a test"},
        tmp_path,
    )
    assert rc == 0, f"stderr={stderr}"
    assert parsed == {"ok": True, "name": "ascendo-test", "expression": "DAILY 03:00"}

    units_dir = Path(driver_env["ASCENDO_XDG_CONFIG_OVERRIDE"]) / "systemd" / "user"
    assert (units_dir / "ascendo-ascendo-test.service").is_file()
    timer = units_dir / "ascendo-ascendo-test.timer"
    assert timer.is_file()
    timer_text = timer.read_text()
    assert "OnCalendar=*-*-* 03:00:00" in timer_text
    assert "Persistent=true" in timer_text

    sidecar = (Path(driver_env["ASCENDO_XDG_DATA_OVERRIDE"]) / "ascendo"
               / "schedules" / "ascendo-test.json")
    assert sidecar.is_file()
    meta = json.loads(sidecar.read_text())
    assert meta["name"] == "ascendo-test"
    assert meta["expression"] == "DAILY 03:00"
    assert meta["profile"] == "full"
    assert meta["enabled"] is True
    assert meta["description"] == "a test"
    assert "created_at" in meta


def test_install_minute_emits_onunitactive(driver_env, tmp_path) -> None:
    rc, stderr, _ = _run_driver(
        driver_env, "install",
        {"name": "minutely", "expression": "MINUTE 10", "profile": "quick"},
        tmp_path,
    )
    assert rc == 0, stderr
    timer = (Path(driver_env["ASCENDO_XDG_CONFIG_OVERRIDE"]) / "systemd" / "user"
             / "ascendo-minutely.timer")
    text = timer.read_text()
    assert "OnUnitActiveSec=10min" in text
    assert "OnBootSec=1min" in text


def test_uninstall_removes_files(driver_env, tmp_path) -> None:
    # Pre-install
    _run_driver(driver_env, "install",
                {"name": "tobenuked", "expression": "DAILY 02:00", "profile": "full"},
                tmp_path)
    units_dir = Path(driver_env["ASCENDO_XDG_CONFIG_OVERRIDE"]) / "systemd" / "user"
    assert (units_dir / "ascendo-tobenuked.timer").is_file()

    rc, stderr, parsed = _run_driver(
        driver_env, "uninstall", {"name": "tobenuked"}, tmp_path,
    )
    assert rc == 0, stderr
    assert parsed == {"ok": True, "name": "tobenuked"}
    assert not (units_dir / "ascendo-tobenuked.timer").exists()
    assert not (units_dir / "ascendo-tobenuked.service").exists()


def test_list_outputs_schedules_array(driver_env, tmp_path) -> None:
    _run_driver(driver_env, "install",
                {"name": "alpha", "expression": "DAILY 01:00", "profile": "full"},
                tmp_path)
    _run_driver(driver_env, "install",
                {"name": "bravo", "expression": "MINUTE 15", "profile": "quick"},
                tmp_path)
    rc, stderr, parsed = _run_driver(driver_env, "list", None, tmp_path)
    assert rc == 0, stderr
    assert isinstance(parsed, dict)
    assert "schedules" in parsed
    names = sorted(s["name"] for s in parsed["schedules"])
    assert names == ["alpha", "bravo"]
    by_name = {s["name"]: s for s in parsed["schedules"]}
    assert by_name["alpha"]["expression"] == "DAILY 01:00"
    assert by_name["bravo"]["expression"] == "MINUTE 15"
    assert by_name["bravo"]["profile"] == "quick"


def test_get_returns_error_for_unknown_name(driver_env, tmp_path) -> None:
    rc, stderr, parsed = _run_driver(
        driver_env, "get", {"name": "nonexistent"}, tmp_path,
    )
    assert rc == 30, f"stderr={stderr}"
    assert isinstance(parsed, dict)
    assert "error" in parsed
    assert "no such schedule" in parsed["error"]


def test_get_returns_metadata_for_existing(driver_env, tmp_path) -> None:
    _run_driver(driver_env, "install",
                {"name": "real", "expression": "DAILY 04:30", "profile": "safe",
                 "description": "desc"},
                tmp_path)
    rc, stderr, parsed = _run_driver(driver_env, "get", {"name": "real"}, tmp_path)
    assert rc == 0, stderr
    assert parsed["name"] == "real"
    assert parsed["expression"] == "DAILY 04:30"
    assert parsed["profile"] == "safe"
    assert parsed["description"] == "desc"
    assert parsed["enabled"] is True
    # active comes from fake systemctl which echoes "active"
    assert parsed["active"] is True


def test_trigger_starts_service(driver_env, tmp_path) -> None:
    _run_driver(driver_env, "install",
                {"name": "trig", "expression": "DAILY 05:00", "profile": "full"},
                tmp_path)
    rc, stderr, parsed = _run_driver(driver_env, "trigger", {"name": "trig"}, tmp_path)
    assert rc == 0, stderr
    assert parsed == {"ok": True, "name": "trig"}


def test_trigger_unknown_returns_error(driver_env, tmp_path) -> None:
    rc, _stderr, parsed = _run_driver(
        driver_env, "trigger", {"name": "ghost"}, tmp_path,
    )
    assert rc == 30
    assert "error" in parsed


def test_invalid_name_uppercase_rejected(driver_env, tmp_path) -> None:
    rc, _stderr, parsed = _run_driver(
        driver_env, "install",
        {"name": "Foo", "expression": "DAILY 03:00", "profile": "full"},
        tmp_path,
    )
    assert rc == 2
    assert "invalid name" in parsed["error"]


def test_invalid_name_with_space_rejected(driver_env, tmp_path) -> None:
    rc, _stderr, parsed = _run_driver(
        driver_env, "install",
        {"name": "foo bar", "expression": "DAILY 03:00", "profile": "full"},
        tmp_path,
    )
    assert rc == 2


def test_valid_name_with_hyphens_and_digits_accepted(driver_env, tmp_path) -> None:
    rc, stderr, _ = _run_driver(
        driver_env, "install",
        {"name": "ascendo-test-123", "expression": "DAILY 03:00", "profile": "full"},
        tmp_path,
    )
    assert rc == 0, stderr


def test_invalid_profile_rejected(driver_env, tmp_path) -> None:
    rc, _stderr, parsed = _run_driver(
        driver_env, "install",
        {"name": "fine", "expression": "DAILY 03:00", "profile": "bad; rm -rf /"},
        tmp_path,
    )
    assert rc == 2
    assert "invalid profile" in parsed["error"]


# ── Python class smoke tests ──────────────────────────────────────────────


def _make_scheduler():
    from ascendo_ubuntu.managers.scheduler import SystemdScheduler
    return SystemdScheduler(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
    )


def test_backend_slug_is_systemd() -> None:
    s = _make_scheduler()
    assert s.backend == "systemd"


def test_implements_ischeduler() -> None:
    s = _make_scheduler()
    assert isinstance(s, IScheduler)


def test_is_available_linux_with_systemctl(linux_host) -> None:
    s = _make_scheduler()
    with patch("ascendo_ubuntu.managers.scheduler.shutil.which",
               return_value="/bin/systemctl"):
        assert s.is_available(linux_host) is True


def test_is_available_linux_without_systemctl(linux_host) -> None:
    s = _make_scheduler()
    with patch("ascendo_ubuntu.managers.scheduler.shutil.which", return_value=None):
        assert s.is_available(linux_host) is False


def test_is_available_macos_returns_false(mac_host) -> None:
    s = _make_scheduler()
    with patch("ascendo_ubuntu.managers.scheduler.shutil.which",
               return_value="/bin/systemctl"):
        assert s.is_available(mac_host) is False


def test_resolve_bash_returns_path() -> None:
    s = _make_scheduler()
    bash = s._resolve_bash()
    assert bash.endswith("bash")


def test_resolve_bash_uses_override() -> None:
    from ascendo_ubuntu.managers.scheduler import SystemdScheduler
    s = SystemdScheduler(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
        bash_path="/custom/bash",
    )
    assert s._resolve_bash() == "/custom/bash"


def test_invoke_install_passes_payload_path(tmp_path: Path) -> None:
    """Mock subprocess.run; assert argv shape contains --action install + --payload-path."""
    s = _make_scheduler()
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        # write a fake output JSON
        for i, a in enumerate(argv):
            if a == "--output-path":
                Path(argv[i + 1]).write_text('{"ok": true}')
                break
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    spec = ScheduleSpec(name="testname", expression="DAILY 03:00",
                        profile="full", enabled=True)
    host = HostInfo(hostname="x", os=OperatingSystem.LINUX_UBUNTU,
                    os_version="24.04", arch="x86_64", user="mk",
                    is_elevated=False, elevation_method=ElevationMethod.NONE)
    with patch("ascendo_ubuntu.managers.scheduler.subprocess.run",
               side_effect=fake_run):
        s.install(host, spec)

    argv = captured["argv"]
    assert "--action" in argv
    assert argv[argv.index("--action") + 1] == "install"
    assert "--output-path" in argv
    assert "--payload-path" in argv


def test_invoke_raises_on_timeout() -> None:
    s = _make_scheduler()
    host = HostInfo(hostname="x", os=OperatingSystem.LINUX_UBUNTU,
                    os_version="24.04", arch="x86_64", user="mk",
                    is_elevated=False, elevation_method=ElevationMethod.NONE)
    with patch("ascendo_ubuntu.managers.scheduler.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
        with pytest.raises(SchedulerError, match="timed out"):
            s.uninstall(host, "x")


def test_invoke_returns_none_when_no_output(tmp_path: Path) -> None:
    """When output file isn't produced AND rc==0, _invoke returns None."""
    s = _make_scheduler()

    def fake_run(argv, **kwargs):
        # do not write the output file
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    host = HostInfo(hostname="x", os=OperatingSystem.LINUX_UBUNTU,
                    os_version="24.04", arch="x86_64", user="mk",
                    is_elevated=False, elevation_method=ElevationMethod.NONE)
    with patch("ascendo_ubuntu.managers.scheduler.subprocess.run",
               side_effect=fake_run):
        # This call returns None (uninstall doesn't return a typed result)
        s.uninstall(host, "name")


def test_invoke_raises_on_error_payload() -> None:
    s = _make_scheduler()

    def fake_run(argv, **kwargs):
        for i, a in enumerate(argv):
            if a == "--output-path":
                Path(argv[i + 1]).write_text('{"error": "no such schedule: foo"}')
                break
        return subprocess.CompletedProcess(args=argv, returncode=30, stdout="", stderr="")

    host = HostInfo(hostname="x", os=OperatingSystem.LINUX_UBUNTU,
                    os_version="24.04", arch="x86_64", user="mk",
                    is_elevated=False, elevation_method=ElevationMethod.NONE)
    with patch("ascendo_ubuntu.managers.scheduler.subprocess.run",
               side_effect=fake_run):
        with pytest.raises(SchedulerError, match="no such schedule"):
            s.trigger(host, "foo")


def test_list_returns_specs() -> None:
    s = _make_scheduler()

    def fake_run(argv, **kwargs):
        for i, a in enumerate(argv):
            if a == "--output-path":
                Path(argv[i + 1]).write_text(json.dumps({
                    "schedules": [
                        {"name": "a", "expression": "DAILY 03:00",
                         "profile": "full", "enabled": True,
                         "description": None},
                        {"name": "b", "expression": "MINUTE 5",
                         "profile": "quick", "enabled": False},
                    ]
                }))
                break
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    host = HostInfo(hostname="x", os=OperatingSystem.LINUX_UBUNTU,
                    os_version="24.04", arch="x86_64", user="mk",
                    is_elevated=False, elevation_method=ElevationMethod.NONE)
    with patch("ascendo_ubuntu.managers.scheduler.subprocess.run",
               side_effect=fake_run):
        specs = s.list(host)
    assert len(specs) == 2
    assert specs[0].name == "a"
    assert specs[1].profile == "quick"
    assert specs[1].enabled is False


def test_get_returns_none_on_unknown() -> None:
    s = _make_scheduler()

    def fake_run(argv, **kwargs):
        for i, a in enumerate(argv):
            if a == "--output-path":
                Path(argv[i + 1]).write_text('{"error": "no such schedule: x"}')
                break
        return subprocess.CompletedProcess(args=argv, returncode=30, stdout="", stderr="")

    host = HostInfo(hostname="x", os=OperatingSystem.LINUX_UBUNTU,
                    os_version="24.04", arch="x86_64", user="mk",
                    is_elevated=False, elevation_method=ElevationMethod.NONE)
    with patch("ascendo_ubuntu.managers.scheduler.subprocess.run",
               side_effect=fake_run):
        result = s.get(host, "x")
    assert result is None
