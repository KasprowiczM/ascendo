"""Mock-based smoke tests for MasManager.

No real mas / sudo / bash invocations — every external call is patched.
Covers the IPC contract (argv shape, env shape, sidecar parse round-trip)
and the elevation handshake (SUDO_ASKPASS env injection on apply only).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo, Trigger
from ascendo_macos.managers.mas import MasManager


# ── fixtures ─────────────────────────────────────────────────

ADAPTER_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mac_host() -> HostInfo:
    return HostInfo(
        hostname="testmac.local", os=OperatingSystem.MACOS,
        os_version="14.5", arch="arm64", user="mk",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def linux_host() -> HostInfo:
    return HostInfo(
        hostname="testlin", os=OperatingSystem.LINUX_OTHER,
        os_version="24.04", arch="x86_64", user="x",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def windows_host() -> HostInfo:
    return HostInfo(
        hostname="testwin", os=OperatingSystem.WINDOWS,
        os_version="11.0", arch="x86_64", user="x",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def run_info() -> RunInfo:
    return RunInfo(
        id=uuid.uuid4(), trigger=Trigger.CLI, profile="default", dry_run=False,
        started_at=datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc),
    )


class _FakeElevation:
    """Minimal IElevation-shaped fake — only the bits MasManager touches."""
    def __init__(self, *, has_pw: bool = False, helper: Path | None = None):
        self._has_pw = has_pw
        self._helper = helper
    def has_password_registered(self) -> bool: return self._has_pw
    def askpass_path(self) -> Path | None: return self._helper


def _make_manager(elev: _FakeElevation = None) -> MasManager:
    return MasManager(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
        elevation=elev or _FakeElevation(),
    )


# ── identity ──────────────────────────────────────────────────

def test_category_is_mas():
    m = _make_manager()
    assert m.category == SourceType.MAS


# ── is_available matrix ───────────────────────────────────────

def test_is_available_false_on_linux(linux_host):
    m = _make_manager()
    assert m.is_available(linux_host) is False


def test_is_available_false_on_windows(windows_host):
    m = _make_manager()
    assert m.is_available(windows_host) is False


def test_is_available_false_when_mas_missing(monkeypatch, mac_host):
    monkeypatch.setattr("ascendo_macos.managers.mas.shutil.which",
                        lambda n: None if n == "mas" else "/usr/local/bin/" + n)
    m = _make_manager()
    assert m.is_available(mac_host) is False


def test_is_available_false_when_jq_missing(monkeypatch, mac_host):
    monkeypatch.setattr("ascendo_macos.managers.mas.shutil.which",
                        lambda n: None if n == "jq" else "/usr/local/bin/" + n)
    m = _make_manager()
    assert m.is_available(mac_host) is False


def test_is_available_false_when_mas_too_old(monkeypatch, mac_host):
    monkeypatch.setattr("ascendo_macos.managers.mas.shutil.which", lambda n: "/usr/local/bin/" + n)
    fake = MagicMock(returncode=0, stdout="3.0.1\n", stderr="")
    monkeypatch.setattr("ascendo_macos.managers.mas.subprocess.run", lambda *a, **kw: fake)
    m = _make_manager()
    assert m.is_available(mac_host) is False


def test_is_available_true_when_all_present_and_recent(monkeypatch, mac_host):
    monkeypatch.setattr("ascendo_macos.managers.mas.shutil.which", lambda n: "/usr/local/bin/" + n)
    fake = MagicMock(returncode=0, stdout="4.3.0\n", stderr="")
    monkeypatch.setattr("ascendo_macos.managers.mas.subprocess.run", lambda *a, **kw: fake)
    m = _make_manager()
    assert m.is_available(mac_host) is True


# ── argv dispatch per phase ───────────────────────────────────

@pytest.mark.parametrize("phase,relpath", [
    (Phase.CHECK,   "scripts/mas/check.sh"),
    (Phase.PLAN,    "scripts/mas/plan.sh"),
    (Phase.APPLY,   "scripts/mas/apply.sh"),
    (Phase.VERIFY,  "scripts/mas/verify.sh"),
    (Phase.CLEANUP, "scripts/mas/cleanup.sh"),
])
def test_run_phase_dispatches_correct_script(phase, relpath, run_info, mac_host):
    captured = {}

    def fake_run_streaming(self, argv, log_path, timeout):
        captured["argv"] = argv
        run_id = run_info.id
        out_dir = Path(argv[argv.index("--output-dir") + 1])
        sidecar_path = out_dir / str(run_id) / f"{phase.value}__mas.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(json.dumps(_minimal_sidecar(phase, run_id)))
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(MasManager, "_run_streaming", fake_run_streaming):
        m = _make_manager()
        m.run_phase(phase, run_info, mac_host)
    assert captured["argv"][1].endswith(relpath), captured["argv"]


# ── apply env injection ───────────────────────────────────────

def test_apply_exports_sudo_askpass_when_password_registered(
    run_info, mac_host, tmp_path,
):
    helper = tmp_path / "askpass-x.sh"
    helper.write_text("#!/usr/bin/env bash\necho secret\n")

    def fake_run_streaming(self, argv, log_path, timeout):
        run_id = run_info.id
        out_dir = Path(argv[argv.index("--output-dir") + 1])
        sidecar_path = out_dir / str(run_id) / "apply__mas.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(json.dumps(_minimal_sidecar(Phase.APPLY, run_id)))
        return MagicMock(returncode=0, stdout="", stderr="")

    elev = _FakeElevation(has_pw=True, helper=helper)
    with patch.object(MasManager, "_run_streaming", fake_run_streaming):
        m = _make_manager(elev)
        m.run_phase(Phase.APPLY, run_info, mac_host)
    assert m._last_env_for_test.get("SUDO_ASKPASS") == str(helper)


def test_apply_does_not_export_sudo_askpass_when_no_password(run_info, mac_host):
    def fake_run_streaming(self, argv, log_path, timeout):
        run_id = run_info.id
        out_dir = Path(argv[argv.index("--output-dir") + 1])
        sidecar_path = out_dir / str(run_id) / "apply__mas.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(json.dumps(_minimal_sidecar(Phase.APPLY, run_id)))
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(MasManager, "_run_streaming", fake_run_streaming):
        m = _make_manager(_FakeElevation(has_pw=False))
        m.run_phase(Phase.APPLY, run_info, mac_host)
    assert "SUDO_ASKPASS" not in m._last_env_for_test


# ── error paths ───────────────────────────────────────────────

def test_run_phase_raises_manager_error_when_no_sidecar(run_info, mac_host):
    """Bash exits non-zero AND no sidecar produced -> ManagerError."""
    def fake_run_streaming(self, argv, log_path, timeout):
        return MagicMock(returncode=2, stdout="boom", stderr="")
    with patch.object(MasManager, "_run_streaming", fake_run_streaming):
        m = _make_manager()
        with pytest.raises(ManagerError):
            m.run_phase(Phase.CHECK, run_info, mac_host)


# ── helpers ───────────────────────────────────────────────────

def _minimal_sidecar(phase: Phase, run_id):
    return {
        "schema": "ascendo/v1",
        "phase": phase.value,
        "category": "mas",
        "run": {
            "id": str(run_id), "trigger": "cli", "profile": "default",
            "dry_run": False, "started_at": "2026-05-03T12:00:00Z",
        },
        "host": {
            "hostname": "testmac.local", "os": "macos", "os_version": "14.5",
            "arch": "arm64", "user": "mk", "is_elevated": False,
        },
        "tool": {"name": "mas", "version": "4.3.0", "binary_path": None},
        "started_at": "2026-05-03T12:00:00Z",
        "finished_at": "2026-05-03T12:00:01Z",
        "status": "success",
        "summary": {"total": 0, "success": 0, "up_to_date": 0, "failed": 0,
                    "skipped": 0, "planned": 0, "partial": 0},
        "items": [], "messages": [],
    }
