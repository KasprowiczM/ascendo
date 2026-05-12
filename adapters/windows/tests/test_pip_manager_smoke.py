"""PipManager smoke tests - mock-based, runs on any OS."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo
from ascendo.models.run import Phase, RunInfo
from ascendo_windows.managers.pip import PipManager


# -- Helpers ---------------------------------------------------------------


def _make(scripts_dir: Path, lib_dir: Path) -> PipManager:
    return PipManager(
        scripts_dir=scripts_dir,
        lib_dir=lib_dir,
        pwsh_path="/usr/bin/true",
        timeout_sec=30,
    )


def _run_completed(
    *,
    output_dir_arg: str,
    run_id: str,
    payload: dict[str, Any] | None,
    returncode: int,
    phase_value: str = "check",
) -> subprocess.CompletedProcess[str]:
    if payload is not None:
        target = Path(output_dir_arg) / run_id / f"{phase_value}__pip.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.CompletedProcess(
        args=["pwsh", "-File", "fake.ps1"],
        returncode=returncode,
        stdout="ok\n",
        stderr="",
    )


def _extract_output_dir(argv: list[str]) -> str:
    return argv[argv.index("-OutputDir") + 1]


def _pip_sidecar_payload(run, host) -> dict[str, Any]:
    return {
        "schema": "ascendo/v1",
        "run": json.loads(run.model_dump_json()),
        "host": json.loads(host.model_dump_json()),
        "tool": {
            "name": "pip",
            "version": "24.2",
            "binary_path": "C:\\Python312\\Scripts\\pip.exe",
        },
        "phase": Phase.CHECK.value,
        "category": "pip",
        "started_at": "2026-05-01T12:00:00Z",
        "finished_at": "2026-05-01T12:00:05Z",
        "status": "success",
        "items": [
            {
                "id": "ruff",
                "name": "ruff",
                "category": "pip",
                "source": {"type": "pip", "feed": None, "url": None},
                "current_version": "0.6.0",
                "target_version": "0.7.0",
                "resolved_version": None,
                "status": "planned",
                "exit_code": None,
                "duration_ms": None,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
            {
                "id": "black",
                "name": "black",
                "category": "pip",
                "source": {"type": "pip", "feed": None, "url": None},
                "current_version": "24.8.0",
                "target_version": "24.8.0",
                "resolved_version": None,
                "status": "up_to_date",
                "exit_code": None,
                "duration_ms": None,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
        ],
        "summary": {
            "total": 2,
            "success": 0,
            "up_to_date": 1,
            "failed": 0,
            "skipped": 0,
            "planned": 1,
            "partial": 0,
            "duration_ms": None,
            "exit_code": 0,
        },
        "messages": [],
    }


@pytest.fixture
def pip_check_payload(run_info, windows_host) -> dict[str, Any]:
    return _pip_sidecar_payload(run_info, windows_host)


# -- Identity --------------------------------------------------------------


def test_category_is_pip(fake_scripts_dir, fake_lib_dir):
    from ascendo.models.package import SourceType
    assert _make(fake_scripts_dir, fake_lib_dir).category is SourceType.PIP


def test_display_name_mentions_python_and_pip(fake_scripts_dir, fake_lib_dir):
    name = _make(fake_scripts_dir, fake_lib_dir).display_name.lower()
    assert "python" in name
    assert "pip" in name


# -- is_available ----------------------------------------------------------


def test_is_available_returns_false_on_non_windows(
    fake_scripts_dir, fake_lib_dir, linux_host,
):
    """Non-Windows host -> False even if pip is on PATH."""
    manager = _make(fake_scripts_dir, fake_lib_dir)
    with patch("ascendo_windows.managers.pip.shutil.which",
               return_value="/usr/bin/pip"):
        assert manager.is_available(linux_host) is False


def test_is_available_true_when_pip3_on_path(
    fake_scripts_dir, fake_lib_dir, windows_host,
):
    """Windows + pip3 on PATH -> True."""
    manager = _make(fake_scripts_dir, fake_lib_dir)
    candidates = {"pip3": "C:\\Python312\\Scripts\\pip3.exe"}
    with patch(
        "ascendo_windows.managers.pip.shutil.which",
        side_effect=lambda name: candidates.get(name),
    ):
        assert manager.is_available(windows_host) is True


def test_is_available_true_when_py_launcher_on_path(
    fake_scripts_dir, fake_lib_dir, windows_host,
):
    """Windows + py launcher on PATH -> True (fallback path)."""
    manager = _make(fake_scripts_dir, fake_lib_dir)
    candidates = {"py": "C:\\Windows\\py.exe"}
    with patch(
        "ascendo_windows.managers.pip.shutil.which",
        side_effect=lambda name: candidates.get(name),
    ):
        assert manager.is_available(windows_host) is True


def test_is_available_false_when_no_python_anywhere(
    fake_scripts_dir, fake_lib_dir, windows_host,
):
    manager = _make(fake_scripts_dir, fake_lib_dir)
    with patch("ascendo_windows.managers.pip.shutil.which", return_value=None):
        assert manager.is_available(windows_host) is False


# -- Phase dispatch --------------------------------------------------------


def test_run_phase_unknown_phase_raises(
    fake_scripts_dir, fake_lib_dir, run_info, windows_host,
):
    manager = _make(fake_scripts_dir, fake_lib_dir)
    bad = MagicMock(spec=Phase)
    bad.value = "wat"
    with pytest.raises(ManagerError, match="does not support phase"):
        manager.run_phase(bad, run_info, windows_host)


@pytest.mark.parametrize(
    ("phase", "script_name"),
    [
        (Phase.CHECK, "check.ps1"),
        (Phase.PLAN, "plan.ps1"),
        (Phase.APPLY, "apply.ps1"),
        (Phase.VERIFY, "verify.ps1"),
        (Phase.CLEANUP, "cleanup.ps1"),
    ],
)
def test_run_phase_dispatches_correct_script_per_phase(
    phase, script_name, fake_scripts_dir, fake_lib_dir, run_info,
    windows_host, pip_check_payload,
):
    """All 5 phases dispatch to the matching .ps1 under scripts/pip/."""
    (fake_scripts_dir / "pip").mkdir(parents=True, exist_ok=True)
    for ph in ("check", "plan", "apply", "verify", "cleanup"):
        (fake_scripts_dir / "pip" / f"{ph}.ps1").write_text(
            f"# placeholder pip/{ph}.ps1\n", encoding="utf-8",
        )

    manager = _make(fake_scripts_dir, fake_lib_dir)
    payload = dict(pip_check_payload)
    payload["phase"] = phase.value

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **_: Any):
        captured["argv"] = argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=payload,
            returncode=0,
            phase_value=phase.value,
        )

    with patch.object(
        manager,
        "_run_streaming",
        side_effect=lambda argv, **kw: fake_run(argv),
    ):
        sidecar = manager.run_phase(phase, run_info, windows_host)

    assert str(fake_scripts_dir / "pip" / script_name) in captured["argv"]
    assert sidecar.phase is phase
    assert sidecar.category.value == "pip"


# -- _build_argv shape -----------------------------------------------------


def test_build_argv_includes_dry_run_switch_when_requested(
    fake_scripts_dir, fake_lib_dir, run_info,
):
    """RunInfo.dry_run=True -> '-DryRun' switch token present."""
    dry_run = run_info.model_copy(update={"dry_run": True})
    manager = _make(fake_scripts_dir, fake_lib_dir)
    argv = manager._build_argv(
        pwsh="/usr/bin/true",
        script_path=fake_scripts_dir / "pip" / "check.ps1",
        run=dry_run,
        output_dir=fake_scripts_dir / "out",
        item_filter=None,
    )
    assert "-DryRun" in argv


def test_build_argv_omits_dry_run_when_false(
    fake_scripts_dir, fake_lib_dir, run_info,
):
    manager = _make(fake_scripts_dir, fake_lib_dir)
    argv = manager._build_argv(
        pwsh="/usr/bin/true",
        script_path=fake_scripts_dir / "pip" / "check.ps1",
        run=run_info,
        output_dir=fake_scripts_dir / "out",
        item_filter=None,
    )
    assert "-DryRun" not in argv


def test_build_argv_passes_item_filter_as_comma_list(
    fake_scripts_dir, fake_lib_dir, run_info,
):
    manager = _make(fake_scripts_dir, fake_lib_dir)
    argv = manager._build_argv(
        pwsh="/usr/bin/true",
        script_path=fake_scripts_dir / "pip" / "apply.ps1",
        run=run_info,
        output_dir=fake_scripts_dir / "out",
        item_filter=["ruff", "  uv  ", "", "  "],
    )
    assert "-ItemFilter" in argv
    idx = argv.index("-ItemFilter")
    assert argv[idx + 1] == "ruff,uv"


def test_build_argv_omits_filter_when_item_filter_empty(
    fake_scripts_dir, fake_lib_dir, run_info,
):
    manager = _make(fake_scripts_dir, fake_lib_dir)
    argv = manager._build_argv(
        pwsh="/usr/bin/true",
        script_path=fake_scripts_dir / "pip" / "apply.ps1",
        run=run_info,
        output_dir=fake_scripts_dir / "out",
        item_filter=[],
    )
    assert "-ItemFilter" not in argv


# -- run_phase end-to-end with mocked subprocess ---------------------------


def test_run_phase_sidecar_round_trip(
    fake_scripts_dir, fake_lib_dir, run_info, windows_host, pip_check_payload,
):
    """Script emits a sidecar; we get it back parsed correctly."""
    (fake_scripts_dir / "pip").mkdir(parents=True, exist_ok=True)
    (fake_scripts_dir / "pip" / "check.ps1").write_text(
        "# placeholder\n", encoding="utf-8",
    )
    manager = _make(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **_: Any):
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=pip_check_payload,
            returncode=0,
        )

    with patch.object(
        manager,
        "_run_streaming",
        side_effect=lambda argv, **kw: fake_run(argv),
    ):
        sidecar = manager.run_phase(Phase.CHECK, run_info, windows_host)

    assert sidecar.category.value == "pip"
    assert sidecar.phase is Phase.CHECK
    assert len(sidecar.items) == 2
    assert sidecar.items[0].id == "ruff"


def test_run_phase_missing_sidecar_raises(
    fake_scripts_dir, fake_lib_dir, run_info, windows_host,
):
    """Script exits without writing a sidecar -> ManagerError."""
    (fake_scripts_dir / "pip").mkdir(parents=True, exist_ok=True)
    (fake_scripts_dir / "pip" / "check.ps1").write_text("#\n", encoding="utf-8")
    manager = _make(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **_: Any):
        return subprocess.CompletedProcess(
            args=argv, returncode=1, stdout="", stderr="boom\n",
        )

    with patch.object(
        manager,
        "_run_streaming",
        side_effect=lambda argv, **kw: fake_run(argv),
    ):
        with pytest.raises(ManagerError, match="no sidecar"):
            manager.run_phase(Phase.CHECK, run_info, windows_host)
