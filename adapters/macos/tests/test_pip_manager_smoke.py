"""PipManager smoke tests — mock-based, runs on any OS."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo, Trigger

from ascendo_macos.managers.pip import PipManager


@pytest.fixture
def mac_host() -> HostInfo:
    return HostInfo(
        hostname="mac.local",
        os=OperatingSystem.MACOS,
        os_version="15.0",
        arch="arm64",
        user="mk",
        is_elevated=False,
    )


@pytest.fixture
def linux_host() -> HostInfo:
    return HostInfo(
        hostname="linux",
        os=OperatingSystem.LINUX_OTHER,
        os_version="6.5",
        arch="x86_64",
        user="mk",
        is_elevated=False,
    )


def _make() -> PipManager:
    return PipManager(
        scripts_dir=Path("/tmp/scripts"),
        lib_dir=Path("/tmp/lib"),
        bash_path="/bin/bash",
    )


# ── Identity ─────────────────────────────────────────────────────────────


def test_pip_manager_category_is_pip():
    assert _make().category is SourceType.PIP


def test_pip_manager_display_name_includes_python():
    name = _make().display_name.lower()
    assert "python" in name
    # Must mention pip somewhere — it's the entire purpose of this manager.
    assert "pip" in name


# ── is_available ─────────────────────────────────────────────────────────


def test_is_available_returns_false_on_non_macos(linux_host):
    assert _make().is_available(linux_host) is False


def test_is_available_true_when_pip_resolves(mac_host):
    """When the bash helper prints a pip path, is_available is True."""
    with patch("ascendo_macos.managers.pip.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(
            returncode=0, stdout="/opt/homebrew/bin/pip\n", stderr=""
        )
        assert _make().is_available(mac_host) is True


def test_is_available_false_when_pip_missing(mac_host):
    """When the bash helper prints empty (no pip), is_available is False."""
    with patch("ascendo_macos.managers.pip.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert _make().is_available(mac_host) is False


# ── Phase dispatch ───────────────────────────────────────────────────────


def test_run_phase_unknown_phase_raises(mac_host):
    from ascendo.interfaces.package_manager import ManagerError

    m = _make()
    bad = MagicMock(spec=Phase)
    bad.value = "wat"
    with pytest.raises(ManagerError, match="does not support phase"):
        m.run_phase(bad, _run(), mac_host)


@pytest.mark.parametrize("phase,expected_script", [
    (Phase.CHECK,   "pip/check.sh"),
    (Phase.PLAN,    "pip/plan.sh"),
    (Phase.APPLY,   "pip/apply.sh"),
    (Phase.VERIFY,  "pip/verify.sh"),
    (Phase.CLEANUP, "pip/cleanup.sh"),
])
def test_run_phase_dispatches_correct_script_per_phase(phase, expected_script, mac_host):
    """SCRIPT_BY_PHASE maps each Phase enum to the correct relative path."""
    m = _make()
    assert m.SCRIPT_BY_PHASE[phase] == expected_script


def _run(dry: bool = False) -> RunInfo:
    from datetime import datetime, timezone
    from uuid import uuid4
    return RunInfo(
        id=uuid4(),
        trigger=Trigger.CLI,
        profile="full",
        dry_run=dry,
        started_at=datetime.now(timezone.utc),
    )


# ── _build_argv shape ────────────────────────────────────────────────────


def test_argv_includes_dry_run_when_run_dry_run_set():
    m = _make()
    run = _run(dry=True)
    argv = m._build_argv(
        bash="/bin/bash",
        script_path=Path("/tmp/scripts/pip/check.sh"),
        run=run,
        output_dir=Path("/tmp/out"),
        item_filter=None,
    )
    assert "--dry-run" in argv


def test_argv_omits_dry_run_when_run_dry_run_false():
    m = _make()
    run = _run(dry=False)
    argv = m._build_argv(
        bash="/bin/bash",
        script_path=Path("/tmp/scripts/pip/check.sh"),
        run=run,
        output_dir=Path("/tmp/out"),
        item_filter=None,
    )
    assert "--dry-run" not in argv


def test_argv_includes_filter_when_item_filter_set():
    m = _make()
    run = _run()
    argv = m._build_argv(
        bash="/bin/bash",
        script_path=Path("/tmp/scripts/pip/apply.sh"),
        run=run,
        output_dir=Path("/tmp/out"),
        item_filter=["ruff", "uv"],
    )
    i = argv.index("--filter")
    assert argv[i + 1] == "ruff,uv"


def test_argv_omits_filter_when_item_filter_empty():
    m = _make()
    run = _run()
    argv = m._build_argv(
        bash="/bin/bash",
        script_path=Path("/tmp/scripts/pip/apply.sh"),
        run=run,
        output_dir=Path("/tmp/out"),
        item_filter=[],
    )
    assert "--filter" not in argv


def test_argv_strips_whitespace_in_filter():
    m = _make()
    run = _run()
    argv = m._build_argv(
        bash="/bin/bash",
        script_path=Path("/tmp/scripts/pip/apply.sh"),
        run=run,
        output_dir=Path("/tmp/out"),
        item_filter=["  ruff  ", "uv", "   "],
    )
    i = argv.index("--filter")
    assert argv[i + 1] == "ruff,uv"
