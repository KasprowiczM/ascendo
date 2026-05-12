"""Smoke tests for DellDriverManager (the dcu-cli plugin wrapper).

Hermetic — mock subprocess + shutil.which so the tests run on any
platform without Dell hardware / dcu-cli installed.

The plugin proper lives at ``plugins/dell-driver-update/windows/*.ps1``;
DellDriverManager makes it visible to the orchestrator alongside winget /
msstore / npm / pip / web / arp / windows_update.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo, Trigger
from ascendo_windows.managers.dell import DellDriverManager, _find_dcu_cli

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = REPO_ROOT / "plugins" / "dell-driver-update"
LIB_DIR = REPO_ROOT / "adapters" / "windows" / "lib"


@pytest.fixture
def host_windows() -> HostInfo:
    """Synthetic Windows HostInfo for is_available + run_phase calls."""
    return HostInfo(
        hostname="testbox",
        os=OperatingSystem.WINDOWS,
        os_version="11 Pro 26200",
        arch="x86_64",
        user="tester",
        is_elevated=False,
    )


@pytest.fixture
def host_linux() -> HostInfo:
    return HostInfo(
        hostname="testbox",
        os=OperatingSystem.LINUX_OTHER,
        os_version="Ubuntu 24.04",
        arch="x86_64",
        user="tester",
        is_elevated=False,
    )


@pytest.fixture
def run() -> RunInfo:
    return RunInfo(
        id=uuid4(),
        trigger=Trigger.CLI,
        profile="safe",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def manager() -> DellDriverManager:
    return DellDriverManager(plugin_dir=PLUGIN_DIR, lib_dir=LIB_DIR)


# ── Identity ────────────────────────────────────────────────────────────


def test_category_is_plugin(manager: DellDriverManager) -> None:
    assert manager.category is SourceType.PLUGIN


def test_display_name_mentions_dell(manager: DellDriverManager) -> None:
    name = manager.display_name
    assert "Dell" in name


def test_plugin_dir_resolves_to_repo_plugin(manager: DellDriverManager) -> None:
    assert manager._plugin_dir.name == "dell-driver-update"
    # The 5 phase scripts must all exist on disk for this manager to be
    # usable. Treat missing scripts as a smoke regression.
    for phase in ("check", "plan", "apply", "verify", "cleanup"):
        assert (manager._plugin_dir / "windows" / f"{phase}.ps1").is_file()


def test_script_by_phase_covers_all_5(manager: DellDriverManager) -> None:
    expected = {Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP}
    assert set(manager.SCRIPT_BY_PHASE) == expected
    for phase, rel in manager.SCRIPT_BY_PHASE.items():
        assert rel.startswith("windows/")
        assert rel.endswith(".ps1")


# ── is_available ────────────────────────────────────────────────────────


def test_is_available_off_windows_returns_false(
    manager: DellDriverManager,
    host_linux: HostInfo,
) -> None:
    assert manager.is_available(host_linux) is False


def test_is_available_on_windows_with_dcu(
    manager: DellDriverManager,
    host_windows: HostInfo,
) -> None:
    # Force _find_dcu_cli to return a fake path.
    fake = Path(r"C:\fake\dcu-cli.exe")
    with patch(
        "ascendo_windows.managers.dell._find_dcu_cli",
        return_value=fake,
    ):
        assert manager.is_available(host_windows) is True


def test_is_available_on_windows_without_dcu(
    manager: DellDriverManager,
    host_windows: HostInfo,
) -> None:
    with patch(
        "ascendo_windows.managers.dell._find_dcu_cli",
        return_value=None,
    ):
        assert manager.is_available(host_windows) is False


# ── _find_dcu_cli ───────────────────────────────────────────────────────


def test_find_dcu_cli_returns_none_when_nothing_resolves() -> None:
    """When neither PATH nor known install dirs have dcu-cli, return None."""
    with patch("shutil.which", return_value=None), patch.object(
        Path, "is_file", lambda self: False
    ):
        assert _find_dcu_cli() is None


def test_find_dcu_cli_uses_path_first() -> None:
    """If shutil.which finds dcu-cli, return that without checking install dirs."""
    fake_path = r"C:\Users\tester\dcu-cli.exe"
    with patch("shutil.which", return_value=fake_path):
        result = _find_dcu_cli()
        assert result is not None
        assert str(result) == fake_path


# ── _build_argv ─────────────────────────────────────────────────────────


def test_build_argv_includes_run_id_as_string(
    manager: DellDriverManager, run: RunInfo
) -> None:
    """Regression: run.id is uuid.UUID; argv must contain str(uuid), not UUID."""
    argv = manager._build_argv(
        pwsh="pwsh.exe",
        script_path=Path("C:/foo/check.ps1"),
        run=run,
        output_dir=Path("C:/tmp/out"),
        item_filter=None,
    )
    rid_idx = argv.index("-RunId")
    assert isinstance(argv[rid_idx + 1], str)
    assert argv[rid_idx + 1] == str(run.id)


def test_build_argv_adds_dryrun_switch_when_requested(
    manager: DellDriverManager
) -> None:
    """When run.dry_run is True, argv contains '-DryRun' switch (no value)."""
    run = RunInfo(
        id=uuid4(),
        trigger=Trigger.CLI,
        profile="safe",
        dry_run=True,
        started_at=datetime.now(timezone.utc),
    )
    argv = manager._build_argv(
        pwsh="pwsh.exe",
        script_path=Path("C:/foo/check.ps1"),
        run=run,
        output_dir=Path("C:/tmp/out"),
        item_filter=None,
    )
    assert "-DryRun" in argv


def test_build_argv_omits_dryrun_switch_when_not_requested(
    manager: DellDriverManager, run: RunInfo
) -> None:
    argv = manager._build_argv(
        pwsh="pwsh.exe",
        script_path=Path("C:/foo/check.ps1"),
        run=run,
        output_dir=Path("C:/tmp/out"),
        item_filter=None,
    )
    assert "-DryRun" not in argv


def test_build_argv_item_filter_becomes_comma_joined(
    manager: DellDriverManager, run: RunInfo
) -> None:
    argv = manager._build_argv(
        pwsh="pwsh.exe",
        script_path=Path("C:/foo/apply.ps1"),
        run=run,
        output_dir=Path("C:/tmp/out"),
        item_filter=["nvidia-driver", " bios-update "],
    )
    idx = argv.index("-ItemFilter")
    assert argv[idx + 1] == "nvidia-driver,bios-update"


# ── run_phase guards ────────────────────────────────────────────────────


def test_run_phase_rejects_unsupported_phase(
    manager: DellDriverManager, run: RunInfo, host_windows: HostInfo
) -> None:
    # Construct a fake "SOURCE" phase by abusing Phase's enum; since there
    # are only 5 phases and they're all supported, this codepath is
    # currently dead but the test pins the contract.
    bogus_phases = [p for p in Phase if p not in manager.SCRIPT_BY_PHASE]
    if not bogus_phases:
        pytest.skip("DellDriverManager already supports every Phase value")
    with pytest.raises(ValueError, match="not supported"):
        manager.run_phase(bogus_phases[0], run, host_windows)


# ── Adapter wiring ──────────────────────────────────────────────────────


def test_adapter_wires_dell_manager_in_position_6() -> None:
    """DellDriverManager must be slot 6 of 8 (between web and registry_arp)."""
    from ascendo_windows.adapter import WindowsAdapter

    a = WindowsAdapter()
    host = a.detect_host()
    mgrs = a.package_managers(host)
    cats = [m.category.value for m in mgrs]
    # Position-agnostic existence check (more robust than strict ordering):
    assert "plugin" in cats, f"plugin category missing from {cats}"
    # Position assertion (the run_tag_release ordering convention).
    # Dell sits between web and registry_arp by design.
    assert cats == [
        "winget", "msstore", "npm", "pip", "web",
        "plugin", "registry_arp", "windows_update",
    ]


def test_run_phase_apply_returns_skipped_when_not_elevated(
    manager: DellDriverManager, run: RunInfo, host_windows: HostInfo
) -> None:
    """Bug from the first real Dell apply run: dcu-cli /applyUpdates returns
    exit code 6 ("Application requires elevated privileges") when invoked
    without Administrator. The plugin originally mapped all non-{0,1,5,500}
    exit codes to ``failed``, which made ``--category plugin --phase apply``
    on a non-elevated PowerShell abort the run with `! aborted after
    phase apply`.

    Fix: Python preflight short-circuits with a ``skipped`` sidecar before
    even spawning dcu-cli when ``host.is_elevated == False`` and phase
    is not cleanup. Validates that the user gets a clear "needs
    Administrator" message instead of a confusing apply-failure.
    """
    # host_windows has is_elevated=False by default in this fixture.
    assert host_windows.is_elevated is False

    # Force is_available to return True so the manager attempts run_phase.
    with patch(
        "ascendo_windows.managers.dell._find_dcu_cli",
        return_value=Path(r"C:\fake\dcu-cli.exe"),
    ):
        sc = manager.run_phase(Phase.APPLY, run, host_windows)

    assert sc.status.value == "skipped"
    assert sc.phase is Phase.APPLY
    assert sc.category is SourceType.PLUGIN
    assert len(sc.items) == 1
    assert sc.items[0].id == "dell:<needs-admin>"
    assert sc.items[0].status.value == "skipped"
    # The clear "needs Administrator" warning surfaces on both the
    # phase-level messages AND the item's messages.
    phase_text = " ".join(m.text for m in sc.messages)
    assert "Administrator" in phase_text
    assert "elevated" in phase_text.lower()


def test_run_phase_check_returns_skipped_when_not_elevated(
    manager: DellDriverManager, run: RunInfo, host_windows: HostInfo
) -> None:
    """Same preflight applies to check / plan / verify (every dcu-cli
    subcommand needs Administrator). Mirror of the apply test for the
    check phase which is the most-exercised one.
    """
    with patch(
        "ascendo_windows.managers.dell._find_dcu_cli",
        return_value=Path(r"C:\fake\dcu-cli.exe"),
    ):
        sc = manager.run_phase(Phase.CHECK, run, host_windows)
    assert sc.status.value == "skipped"
    assert sc.items[0].id == "dell:<needs-admin>"


def test_run_phase_cleanup_runs_even_when_not_elevated(
    manager: DellDriverManager, run: RunInfo, host_windows: HostInfo
) -> None:
    """Cleanup phase doesn't need elevation (the plugin's cleanup.ps1 is a
    no-op). Preflight must NOT short-circuit it -- the script should be
    spawned normally.
    """
    # Patch _find_dcu_cli + _resolve_pwsh to ensure preflight is the only
    # thing that could intercept. Then patch subprocess.run to simulate
    # a clean cleanup invocation. If preflight short-circuited cleanup,
    # subprocess.run would never be called -- this test asserts it IS.
    fake_pwsh = r"C:\fake\pwsh.exe"
    called: list[bool] = []

    def fake_subprocess_run(*args, **kwargs):
        called.append(True)
        # Caller will then try to read the sidecar from the bufdir; we
        # haven't created one, so a ManagerError "no sidecar" raise is
        # expected and acceptable for this test's scope.
        from unittest.mock import MagicMock
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with patch(
        "ascendo_windows.managers.dell._find_dcu_cli",
        return_value=Path(r"C:\fake\dcu-cli.exe"),
    ), patch.object(
        DellDriverManager, "_resolve_pwsh", return_value=fake_pwsh
    ), patch(
        "ascendo_windows.managers.dell.subprocess.run",
        side_effect=fake_subprocess_run,
    ):
        # We expect ManagerError because subprocess.run won't actually
        # write the sidecar -- but the IMPORTANT assertion is that
        # subprocess.run was reached (i.e. preflight didn't short-circuit).
        from ascendo.interfaces.package_manager import ManagerError
        try:
            manager.run_phase(Phase.CLEANUP, run, host_windows)
        except ManagerError:
            pass

    assert called, "cleanup phase must NOT be short-circuited by the preflight"


def test_adapter_health_check_reports_dcu() -> None:
    """`ascendo doctor` must include a `dcu` row in its output dict."""
    from ascendo_windows.adapter import WindowsAdapter

    a = WindowsAdapter()
    health = a.health_check()
    assert "dcu" in health, f"dcu probe missing from {list(health)}"
    # The value starts with one of ok / degraded / unavailable / error
    # (matches the existing convention in this file).
    assert health["dcu"].split(":", 1)[0].strip() in {
        "ok", "degraded", "unavailable", "error",
    }
