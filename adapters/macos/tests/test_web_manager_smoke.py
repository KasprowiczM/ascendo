"""WebManager smoke tests — mocked subprocess."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ascendo_macos.managers.web import WebManager
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.run import Phase, RunInfo, Trigger
from ascendo.models.package import SourceType


def _host_macos() -> HostInfo:
    return HostInfo(hostname="t", os=OperatingSystem.MACOS, os_version="14.0",
                    arch="arm64", user="u", is_elevated=False)


def _host_linux() -> HostInfo:
    return HostInfo(hostname="t", os=OperatingSystem.LINUX_UBUNTU,
                    os_version="22.04", arch="x86_64", user="u", is_elevated=False)


def _mgr() -> WebManager:
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    lib = Path(__file__).resolve().parents[1] / "lib"
    return WebManager(scripts_dir=scripts, lib_dir=lib)


def _run() -> RunInfo:
    return RunInfo(
        id="00000000-0000-0000-0000-000000000000",
        trigger=Trigger.CLI,
        profile="full",
        started_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        dry_run=False,
    )


def test_category_is_web() -> None:
    assert _mgr().category is SourceType.WEB


def test_display_name_set() -> None:
    name = _mgr().display_name
    assert "Web apps" in name


def test_is_available_true_on_macos() -> None:
    assert _mgr().is_available(_host_macos()) is True


def test_is_available_false_on_linux() -> None:
    assert _mgr().is_available(_host_linux()) is False


def test_script_by_phase_covers_all_5_phases() -> None:
    mgr = _mgr()
    expected = {Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP}
    assert set(mgr.SCRIPT_BY_PHASE.keys()) == expected
    for phase, script_rel in mgr.SCRIPT_BY_PHASE.items():
        assert script_rel.startswith("web/")
        assert script_rel.endswith(".sh")


def test_build_argv_dry_run_appends_flag() -> None:
    mgr = _mgr()
    run = RunInfo(
        id="00000000-0000-0000-0000-000000000000",
        trigger=Trigger.CLI, profile="full",
        started_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        dry_run=True,
    )
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=Path("/x/check.sh"),
        run=run, output_dir=Path("/tmp/x"), item_filter=None,
    )
    assert "--dry-run" in argv


def test_build_argv_filter_csv() -> None:
    mgr = _mgr()
    argv = mgr._build_argv(
        bash="/bin/bash", script_path=Path("/x/check.sh"),
        run=_run(), output_dir=Path("/tmp/x"),
        item_filter=["chrome", "brave"],
    )
    idx = argv.index("--filter")
    assert argv[idx + 1] == "chrome,brave"


def test_build_argv_no_filter_when_empty_iterable() -> None:
    mgr = _mgr()
    argv = mgr._build_argv(
        bash="/bin/bash", script_path=Path("/x/check.sh"),
        run=_run(), output_dir=Path("/tmp/x"),
        item_filter=[],
    )
    assert "--filter" not in argv
