"""Smoke tests for MSStoreManager + ArpManager.

Both managers inherit from WingetManager, so the bulk of the IPC
machinery (subprocess spawn, argv build, sidecar parse) is already
exercised by ``test_winget_manager_smoke.py``. These tests cover only
the bits that diverge:

* identity (``category`` / ``display_name``)
* the per-phase script path
* availability probing (ArpManager doesn't need winget on PATH)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ascendo_windows.managers.arp import ArpManager
from ascendo_windows.managers.msstore import MSStoreManager
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.run import Phase


def _make_host(os_: OperatingSystem) -> HostInfo:
    return HostInfo(
        hostname="testbox",
        os=os_,
        os_version="10",
        arch="x86_64",
        user="tester",
        is_elevated=False,
    )


# ── Identity ───────────────────────────────────────────────────────────────


def test_msstore_identity(tmp_path: Path) -> None:
    m = MSStoreManager(scripts_dir=tmp_path / "scripts", lib_dir=tmp_path / "lib")
    assert m.category.value == "msstore"
    assert "msstore" in m.display_name.lower()


def test_arp_identity(tmp_path: Path) -> None:
    m = ArpManager(scripts_dir=tmp_path / "scripts", lib_dir=tmp_path / "lib")
    assert m.category.value == "registry_arp"
    assert "registry" in m.display_name.lower() or "arp" in m.display_name.lower() \
           or "remove" in m.display_name.lower()


# ── Script path mapping ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("phase", "expected_dir"),
    [
        (Phase.CHECK,   "msstore"),
        (Phase.PLAN,    "msstore"),
        (Phase.APPLY,   "msstore"),
        (Phase.VERIFY,  "msstore"),
        (Phase.CLEANUP, "msstore"),
    ],
)
def test_msstore_script_dir(tmp_path: Path, phase: Phase, expected_dir: str) -> None:
    m = MSStoreManager(scripts_dir=tmp_path, lib_dir=tmp_path)
    assert m.SCRIPT_BY_PHASE[phase].startswith(f"{expected_dir}/")


@pytest.mark.parametrize(
    ("phase", "expected_dir"),
    [
        (Phase.CHECK,   "arp"),
        (Phase.PLAN,    "arp"),
        (Phase.APPLY,   "arp"),
        (Phase.VERIFY,  "arp"),
        (Phase.CLEANUP, "arp"),
    ],
)
def test_arp_script_dir(tmp_path: Path, phase: Phase, expected_dir: str) -> None:
    m = ArpManager(scripts_dir=tmp_path, lib_dir=tmp_path)
    assert m.SCRIPT_BY_PHASE[phase].startswith(f"{expected_dir}/")


# ── Availability ──────────────────────────────────────────────────────────


def test_msstore_unavailable_on_non_windows(tmp_path: Path) -> None:
    m = MSStoreManager(scripts_dir=tmp_path, lib_dir=tmp_path)
    assert m.is_available(_make_host(OperatingSystem.LINUX_UBUNTU)) is False
    assert m.is_available(_make_host(OperatingSystem.MACOS)) is False


def test_arp_unavailable_on_non_windows(tmp_path: Path) -> None:
    m = ArpManager(scripts_dir=tmp_path, lib_dir=tmp_path)
    assert m.is_available(_make_host(OperatingSystem.LINUX_UBUNTU)) is False
    assert m.is_available(_make_host(OperatingSystem.MACOS)) is False


def test_arp_available_on_windows_without_winget(tmp_path: Path) -> None:
    """ArpManager scans the registry directly and does NOT depend on winget.

    Even if ``shutil.which('winget')`` returns None, ArpManager must still
    advertise itself as available on Windows hosts. That's what
    differentiates it from MSStoreManager (which DOES require winget).
    """
    m = ArpManager(scripts_dir=tmp_path, lib_dir=tmp_path)
    with patch("ascendo_windows.managers.winget.shutil.which", return_value=None):
        assert m.is_available(_make_host(OperatingSystem.WINDOWS)) is True


def test_msstore_unavailable_without_winget(tmp_path: Path) -> None:
    """MSStoreManager routes through winget --source msstore — without
    winget on PATH it must report unavailable on Windows too."""
    m = MSStoreManager(scripts_dir=tmp_path, lib_dir=tmp_path)
    with patch("ascendo_windows.managers.winget.shutil.which", return_value=None):
        assert m.is_available(_make_host(OperatingSystem.WINDOWS)) is False


# ── Adapter wiring ─────────────────────────────────────────────────────────


def test_windows_adapter_includes_msstore_and_arp() -> None:
    """WindowsAdapter.package_managers() must surface both new managers."""
    from ascendo_windows.adapter import WindowsAdapter

    adapter = WindowsAdapter()
    host = _make_host(OperatingSystem.WINDOWS)
    managers = adapter.package_managers(host)
    categories = {m.category.value for m in managers}
    assert "winget" in categories
    assert "msstore" in categories
    assert "registry_arp" in categories
    assert "windows_update" in categories
