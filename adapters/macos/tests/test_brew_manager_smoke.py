"""BrewManager smoke tests -- mock-based, runs on any OS."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo, Trigger

from ascendo_macos.managers.brew import BrewManager

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SIDECAR = ADAPTER_ROOT / "tests" / "fixtures" / "check__brew.json"


def _mac_host(elevated: bool = False) -> HostInfo:
    return HostInfo(
        hostname="macbook.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=elevated,
    )


def _linux_host() -> HostInfo:
    return HostInfo(
        hostname="ubuntu",
        os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04",
        arch="x86_64",
        user="mk",
        is_elevated=False,
    )


def _win_host() -> HostInfo:
    return HostInfo(
        hostname="winbox",
        os=OperatingSystem.WINDOWS,
        os_version="11",
        arch="x86_64",
        user="mk",
        is_elevated=False,
    )


def _run() -> RunInfo:
    return RunInfo(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        trigger=Trigger.CLI,
        profile="default",
        dry_run=False,
        started_at=datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc),
    )


def _mgr(tmp_path: Path) -> BrewManager:
    return BrewManager(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
    )


# -- Identity -------------------------------------------------------------

def test_category_is_brew(tmp_path: Path) -> None:
    assert _mgr(tmp_path).category is SourceType.BREW


def test_display_name_is_homebrew(tmp_path: Path) -> None:
    assert "Homebrew" in _mgr(tmp_path).display_name


# -- Availability matrix --------------------------------------------------

@patch("shutil.which")
def test_is_available_false_on_linux(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: "/usr/bin/" + x
    assert _mgr(tmp_path).is_available(_linux_host()) is False


@patch("shutil.which")
def test_is_available_false_on_windows(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: "/usr/bin/" + x
    assert _mgr(tmp_path).is_available(_win_host()) is False


@patch("ascendo_macos.managers.brew.shutil.which")
def test_is_available_false_when_brew_missing(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: None if x == "brew" else "/usr/bin/jq"
    assert _mgr(tmp_path).is_available(_mac_host()) is False


@patch("ascendo_macos.managers.brew.shutil.which")
def test_is_available_false_when_jq_missing(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: "/opt/homebrew/bin/brew" if x == "brew" else None
    assert _mgr(tmp_path).is_available(_mac_host()) is False


@patch("ascendo_macos.managers.brew.shutil.which")
def test_is_available_true_with_brew_and_jq(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: f"/opt/homebrew/bin/{x}"
    assert _mgr(tmp_path).is_available(_mac_host()) is True


# -- argv shape -----------------------------------------------------------

@pytest.mark.parametrize("phase,script_name", [
    (Phase.CHECK, "check.sh"),
    (Phase.PLAN, "plan.sh"),
    (Phase.APPLY, "apply.sh"),
    (Phase.VERIFY, "verify.sh"),
    (Phase.CLEANUP, "cleanup.sh"),
])
def test_build_argv_per_phase(phase: Phase, script_name: str, tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / script_name,
        run=_run(),
        output_dir=tmp_path,
        item_filter=None,
    )
    assert argv[0] == "/bin/bash"
    assert argv[1].endswith(script_name)
    assert "--run-id" in argv
    assert "--output-dir" in argv


def test_build_argv_omits_dry_run_when_false(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / "check.sh",
        run=_run(),  # dry_run=False
        output_dir=tmp_path,
        item_filter=None,
    )
    assert "--dry-run" not in argv


def test_build_argv_includes_dry_run_when_true(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    run = _run()
    run = run.model_copy(update={"dry_run": True})
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / "apply.sh",
        run=run,
        output_dir=tmp_path,
        item_filter=None,
    )
    assert "--dry-run" in argv


def test_build_argv_passes_filter_csv(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / "apply.sh",
        run=_run(),
        output_dir=tmp_path,
        item_filter=["node", "git", "jq"],
    )
    idx = argv.index("--filter")
    assert argv[idx + 1] == "node,git,jq"


def test_build_argv_omits_empty_filter(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / "apply.sh",
        run=_run(),
        output_dir=tmp_path,
        item_filter=["", "  ", None],  # type: ignore[list-item]
    )
    assert "--filter" not in argv


# -- run_phase end-to-end with mocked subprocess --------------------------

def _populate_fake_sidecar(output_dir: Path, run_id: UUID, phase: Phase) -> None:
    """Helper: drop the fixture sidecar at the path BrewManager will read."""
    target = output_dir / str(run_id) / f"{phase.value}__brew.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(FIXTURE_SIDECAR.read_bytes())


@patch("ascendo_macos.managers.brew.subprocess.Popen")
def test_run_phase_returns_parsed_sidecar(mock_popen: MagicMock, tmp_path: Path) -> None:
    """When the script exits 0 and produces a sidecar, run_phase parses it."""
    captured_argv: dict = {}

    def _popen_side_effect(argv, **kwargs):
        captured_argv["argv"] = argv
        idx = argv.index("--output-dir")
        out_dir = Path(argv[idx + 1])
        run_idx = argv.index("--run-id")
        run_id = UUID(argv[run_idx + 1])
        _populate_fake_sidecar(out_dir, run_id, Phase.CHECK)
        proc = MagicMock()
        proc.stdout.readline.side_effect = [""]
        proc.wait.return_value = 0
        proc.returncode = 0
        proc.kill = MagicMock()
        return proc

    mock_popen.side_effect = _popen_side_effect
    sc = _mgr(tmp_path).run_phase(Phase.CHECK, _run(), _mac_host())
    assert sc.schema_.value == "ascendo/v1"
    assert sc.phase is Phase.CHECK
    assert sc.category is SourceType.BREW


@patch("ascendo_macos.managers.brew.subprocess.Popen")
def test_run_phase_raises_when_no_sidecar(mock_popen: MagicMock, tmp_path: Path) -> None:
    """Script exits non-zero with no sidecar -> ManagerError."""
    proc = MagicMock()
    proc.stdout.readline.side_effect = ["error\n", ""]
    proc.wait.return_value = 30
    proc.returncode = 30
    mock_popen.return_value = proc
    with pytest.raises(ManagerError):
        _mgr(tmp_path).run_phase(Phase.CHECK, _run(), _mac_host())


def test_run_phase_unsupported_raises() -> None:
    """All 5 canonical phases are mapped -- documents the contract."""
    mgr = BrewManager(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
    )
    assert set(mgr.SCRIPT_BY_PHASE) == {
        Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP,
    }
