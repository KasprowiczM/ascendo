"""Mock-based smoke tests for LaunchdScheduler.

No real launchctl / bash invocations — every external call is patched.
Covers identity, OS gate, JSON-IPC argv shape, error paths.
"""
from __future__ import annotations

import json
import shutil
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


def _make_scheduler():
    from ascendo_macos.managers.scheduler import LaunchdScheduler
    return LaunchdScheduler(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
    )


def test_backend_slug_is_launchd():
    s = _make_scheduler()
    assert s.backend == "launchd"


def test_implements_ischeduler():
    s = _make_scheduler()
    assert isinstance(s, IScheduler)


def test_is_available_macos_with_launchctl(mac_host):
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/launchctl"):
        assert s.is_available(mac_host) is True


def test_is_available_macos_without_launchctl(mac_host):
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which", return_value=None):
        assert s.is_available(mac_host) is False


def test_is_available_linux_returns_false(linux_host):
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/launchctl"):
        assert s.is_available(linux_host) is False
