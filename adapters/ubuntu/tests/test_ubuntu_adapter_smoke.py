"""UbuntuAdapter smoke tests — mock-based, runs on any OS."""
from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from ascendo.interfaces import AdapterCapability
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo, Trigger

from ascendo_ubuntu import UbuntuAdapter


# ── Identity ─────────────────────────────────────────────────────────────


def test_adapter_identity() -> None:
    a = UbuntuAdapter()
    assert a.name == "ubuntu"
    assert a.display_name == "Ubuntu / Debian"
    assert a.tier == 1


# ── Capabilities ─────────────────────────────────────────────────────────


def test_capabilities_includes_package_management_and_inventory() -> None:
    """The adapter declares PACKAGE_MANAGEMENT | INVENTORY at minimum."""
    a = UbuntuAdapter()
    caps = a.capabilities
    assert AdapterCapability.PACKAGE_MANAGEMENT in caps
    assert AdapterCapability.INVENTORY in caps


def test_capabilities_includes_snapshots() -> None:
    """SNAPSHOTS landed via TimeshiftSnapshot wiring."""
    a = UbuntuAdapter()
    assert AdapterCapability.SNAPSHOTS in a.capabilities


def test_capabilities_includes_elevation() -> None:
    """ELEVATION landed via LinuxElevation wiring."""
    a = UbuntuAdapter()
    assert AdapterCapability.ELEVATION in a.capabilities


def test_capabilities_includes_scheduling() -> None:
    """SCHEDULING landed via SystemdScheduler wiring."""
    a = UbuntuAdapter()
    assert AdapterCapability.SCHEDULING in a.capabilities


def test_scheduler_returns_systemd_scheduler_singleton() -> None:
    from ascendo_ubuntu.managers.scheduler import SystemdScheduler

    a = UbuntuAdapter()
    s1 = a.scheduler()
    s2 = a.scheduler()
    assert isinstance(s1, SystemdScheduler)
    assert s1 is s2


# ── Sub-interface accessors ──────────────────────────────────────────────


def test_unsupported_accessors_return_none() -> None:
    """source remains unimplemented for now (reserved for M6)."""
    a = UbuntuAdapter()
    assert a.source() is None


def test_elevation_returns_linux_elevation_singleton() -> None:
    """elevation() returns a cached LinuxElevation instance."""
    from ascendo_ubuntu.managers.elevation import LinuxElevation

    a = UbuntuAdapter()
    e1 = a.elevation()
    e2 = a.elevation()
    assert isinstance(e1, LinuxElevation)
    assert e1 is e2


def test_snapshot_returns_timeshift_snapshot_singleton() -> None:
    from ascendo_ubuntu.snapshot import TimeshiftSnapshot

    a = UbuntuAdapter()
    s1 = a.snapshot()
    s2 = a.snapshot()
    assert isinstance(s1, TimeshiftSnapshot)
    assert s1 is s2


def test_inventory_returns_singleton() -> None:
    from ascendo_ubuntu.inventory import UbuntuInventory

    a = UbuntuAdapter()
    i1 = a.inventory()
    i2 = a.inventory()
    assert isinstance(i1, UbuntuInventory)
    assert i1 is i2


# ── package_managers ─────────────────────────────────────────────────────


@pytest.fixture
def linux_host() -> HostInfo:
    from ascendo.models.host import ElevationMethod

    return HostInfo(
        hostname="mk-uP5520",
        os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04",
        arch="x86_64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def test_package_managers_returns_canonical_order(linux_host: HostInfo) -> None:
    """package_managers() returns the canonical-order managers:
    apt → snap → brew → npm → pip → flatpak → web → drivers.
    """
    a = UbuntuAdapter()
    mgrs = a.package_managers(linux_host)
    expected = [
        SourceType.APT,
        SourceType.SNAP,
        SourceType.BREW,
        SourceType.NPM,
        SourceType.PIP,
        SourceType.FLATPAK,
        SourceType.WEB,
        SourceType.DRIVERS,
    ]
    assert [m.category for m in mgrs] == expected


def test_package_manager_class_names(linux_host: HostInfo) -> None:
    """Each manager has the expected class name."""
    a = UbuntuAdapter()
    mgrs = a.package_managers(linux_host)
    names = [type(m).__name__ for m in mgrs]
    assert "WebManager" in names
    assert "AptManager" in names
    assert "DriversManager" in names


def test_each_manager_has_display_name(linux_host: HostInfo) -> None:
    a = UbuntuAdapter()
    for m in a.package_managers(linux_host):
        assert isinstance(m.display_name, str)
        assert len(m.display_name) > 0


# ── is_available ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "manager_name,binary",
    [
        ("AptManager", "apt-get"),
        ("SnapManager", "snap"),
        ("NpmManager", "npm"),
        ("PipManager", "pip3"),
        ("FlatpakManager", "flatpak"),
    ],
)
def test_is_available_true_when_binary_resolves(
    linux_host: HostInfo, manager_name: str, binary: str
) -> None:
    """is_available returns True on Linux when the underlying CLI is on PATH."""
    a = UbuntuAdapter()
    mgrs = {type(m).__name__: m for m in a.package_managers(linux_host)}
    mgr = mgrs[manager_name]
    with patch("shutil.which", return_value=f"/usr/bin/{binary}"):
        assert mgr.is_available(linux_host) is True


@pytest.mark.parametrize(
    "manager_name",
    ["AptManager", "SnapManager", "NpmManager", "PipManager", "FlatpakManager"],
)
def test_is_available_false_when_binary_missing(
    linux_host: HostInfo, manager_name: str
) -> None:
    a = UbuntuAdapter()
    mgrs = {type(m).__name__: m for m in a.package_managers(linux_host)}
    mgr = mgrs[manager_name]
    with patch("shutil.which", return_value=None), \
            patch("pathlib.Path.is_file", return_value=False):
        assert mgr.is_available(linux_host) is False


def test_is_available_false_off_linux(linux_host: HostInfo) -> None:
    """All managers return False when host.os is not linux_*."""
    from ascendo.models.host import ElevationMethod
    mac_host = HostInfo(
        hostname="mac.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )
    a = UbuntuAdapter()
    for mgr in a.package_managers(linux_host):
        # Even with binaries present on PATH, non-Linux hosts return False.
        with patch("shutil.which", return_value="/usr/bin/whatever"):
            assert mgr.is_available(mac_host) is False, (
                f"{type(mgr).__name__} returned True on macOS host"
            )


def test_brew_is_available_via_linuxbrew_prefix(linux_host: HostInfo) -> None:
    """BrewManager falls back to /home/linuxbrew/.linuxbrew/bin/brew when
    `brew` isn't on PATH."""
    from ascendo_ubuntu.managers.brew import BrewManager
    a = UbuntuAdapter()
    brew_mgr = next(m for m in a.package_managers(linux_host) if isinstance(m, BrewManager))
    with patch("shutil.which", return_value=None), \
            patch("ascendo_ubuntu.managers.brew._LINUXBREW_PREFIX") as mock_prefix:
        mock_prefix.is_file.return_value = True
        assert brew_mgr.is_available(linux_host) is True


def test_drivers_is_available_on_linux_only(linux_host: HostInfo) -> None:
    """DriversManager is available on any linux_* host (no CLI gate)."""
    from ascendo_ubuntu.managers.drivers import DriversManager
    a = UbuntuAdapter()
    drv = next(m for m in a.package_managers(linux_host) if isinstance(m, DriversManager))
    # Linux: True regardless of which binaries are present.
    with patch("shutil.which", return_value=None):
        assert drv.is_available(linux_host) is True


# ── run_phase ────────────────────────────────────────────────────────────


def _make_run() -> RunInfo:
    from datetime import datetime, timezone

    return RunInfo(
        id=uuid4(),
        trigger=Trigger.CLI,
        profile="full",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
    )


def _legacy_v1_payload(*, category: str, started_at: str, ended_at: str) -> dict:
    """Build a minimal legacy ascendo/v1 sidecar payload."""
    return {
        "schema": "ascendo/v1",
        "kind": "check",
        "category": category,
        "host": "mk-uP5520",
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_code": 0,
        "summary": {"ok": 0, "warn": 0, "err": 0},
        "items": [],
        "diagnostics": [],
        "log_path": "logs/runs/test/check.log",
        "needs_reboot": False,
        "advisory": [],
    }


def test_run_phase_invokes_bash_with_correct_env(
    linux_host: HostInfo, tmp_path: Path
) -> None:
    """run_phase spawns bash with JSON_OUT/LOG_FILE/ORCH_RUN_ID/etc env."""
    from ascendo_ubuntu.managers.apt import AptManager

    run = _make_run()
    started_at = run.started_at.isoformat()
    ended_at = started_at  # zero-duration is fine

    # Create a fake scripts dir layout
    scripts_dir = tmp_path / "scripts"
    lib_dir = tmp_path / "lib"
    (scripts_dir / "apt").mkdir(parents=True)
    lib_dir.mkdir()
    fake_script = scripts_dir / "apt" / "check.sh"
    fake_script.write_text("#!/usr/bin/env bash\necho 'fake'\n")

    captured_env: dict[str, str] = {}

    def fake_subprocess_run(argv, env=None, **kwargs):  # noqa: ARG001
        # Capture env so test can assert on it.
        if env:
            captured_env.update(env)
        # Write a minimal legacy v1 sidecar to JSON_OUT.
        sidecar_path = Path(env["JSON_OUT"])
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(
            json.dumps(_legacy_v1_payload(
                category="apt",
                started_at=started_at,
                ended_at=ended_at,
            ))
        )
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    mgr = AptManager(
        scripts_dir=scripts_dir,
        lib_dir=lib_dir,
        repo_root=tmp_path,
        bash_path="/bin/bash",
    )
    with patch("ascendo_ubuntu.managers._base.subprocess.run", side_effect=fake_subprocess_run):
        sc = mgr.run_phase(Phase.CHECK, run, linux_host)

    # Sidecar parsed back into ascendo/v1 model
    assert sc.phase is Phase.CHECK
    assert sc.category is SourceType.APT

    # Env variables passed correctly
    assert "JSON_OUT" in captured_env
    assert captured_env["JSON_OUT"].endswith("apt/check.json")
    assert "LOG_FILE" in captured_env
    assert captured_env["ORCH_RUN_ID"] == str(run.id)
    assert captured_env["ORCH_DRY_RUN"] == "0"
    assert captured_env["ORCH_PROFILE"] == "full"


def test_run_phase_dry_run_sets_env_flag(
    linux_host: HostInfo, tmp_path: Path
) -> None:
    from datetime import datetime, timezone

    from ascendo_ubuntu.managers.apt import AptManager

    run = RunInfo(
        id=uuid4(),
        trigger=Trigger.CLI,
        profile="full",
        dry_run=True,
        started_at=datetime.now(timezone.utc),
    )
    started_at = run.started_at.isoformat()

    scripts_dir = tmp_path / "scripts"
    lib_dir = tmp_path / "lib"
    (scripts_dir / "apt").mkdir(parents=True)
    lib_dir.mkdir()
    (scripts_dir / "apt" / "check.sh").write_text("#!/usr/bin/env bash\n")

    captured_env: dict[str, str] = {}

    def fake_run(argv, env=None, **kwargs):  # noqa: ARG001
        if env:
            captured_env.update(env)
        Path(env["JSON_OUT"]).parent.mkdir(parents=True, exist_ok=True)
        Path(env["JSON_OUT"]).write_text(
            json.dumps(_legacy_v1_payload(
                category="apt", started_at=started_at, ended_at=started_at,
            ))
        )
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    mgr = AptManager(scripts_dir=scripts_dir, lib_dir=lib_dir, bash_path="/bin/bash")
    with patch("ascendo_ubuntu.managers._base.subprocess.run", side_effect=fake_run):
        mgr.run_phase(Phase.CHECK, run, linux_host)
    assert captured_env["ORCH_DRY_RUN"] == "1"


def test_run_phase_resolves_correct_script_per_phase(
    linux_host: HostInfo, tmp_path: Path
) -> None:
    """The script path includes the phase name (check.sh / apply.sh / ...)."""
    from ascendo_ubuntu.managers.snap import SnapManager

    run = _make_run()
    scripts_dir = tmp_path / "scripts"
    lib_dir = tmp_path / "lib"
    (scripts_dir / "snap").mkdir(parents=True)
    lib_dir.mkdir()
    for phase in ("check", "plan", "apply", "verify", "cleanup"):
        (scripts_dir / "snap" / f"{phase}.sh").write_text("#!/usr/bin/env bash\n")

    captured_argv: list[str] = []

    def fake_run(argv, env=None, **kwargs):  # noqa: ARG001
        captured_argv.extend(argv)
        Path(env["JSON_OUT"]).parent.mkdir(parents=True, exist_ok=True)
        Path(env["JSON_OUT"]).write_text(
            json.dumps(_legacy_v1_payload(
                category="snap",
                started_at=run.started_at.isoformat(),
                ended_at=run.started_at.isoformat(),
            ))
        )
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    mgr = SnapManager(scripts_dir=scripts_dir, lib_dir=lib_dir, bash_path="/bin/bash")
    for phase in (Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP):
        captured_argv.clear()
        with patch("ascendo_ubuntu.managers._base.subprocess.run", side_effect=fake_run):
            mgr.run_phase(phase, run, linux_host)
        # argv[0] is bash, argv[1] is the script path
        assert captured_argv[1].endswith(f"snap/{phase.value}.sh")


# ── adapter_factory integration ──────────────────────────────────────────


def test_adapter_factory_resolves_ubuntu() -> None:
    """AdapterRegistry.discover() must find UbuntuAdapter for LINUX_UBUNTU."""
    from ascendo.adapter_factory import AdapterRegistry

    reg = AdapterRegistry()
    reg.discover()
    cls = reg.get(OperatingSystem.LINUX_UBUNTU)
    assert cls is not None, (
        "AdapterRegistry.discover() did not register an ubuntu adapter. "
        "Ensure ascendo-ubuntu is installed (pip install -e adapters/ubuntu --no-deps) "
        "OR the direct-import fallback path in adapter_factory works."
    )
    assert cls.__name__ == "UbuntuAdapter"


def test_adapter_via_select_adapter() -> None:
    from ascendo.adapter_factory import select_adapter

    a = select_adapter(OperatingSystem.LINUX_UBUNTU)
    assert isinstance(a, UbuntuAdapter)
    assert a.tier == 1
    assert a.name == "ubuntu"


# ── health_check ─────────────────────────────────────────────────────────


def test_health_check_reports_required_keys() -> None:
    """health_check returns at minimum the expected component keys."""
    a = UbuntuAdapter()
    h = a.health_check()
    expected_keys = {
        "apt", "snap", "brew", "npm", "pip", "flatpak", "fwupd",
        "bash", "ascendo_lib", "ascendo_scripts",
    }
    assert expected_keys.issubset(h.keys()), (
        f"missing keys: {expected_keys - h.keys()}"
    )


def test_health_check_includes_timeshift() -> None:
    """timeshift component lands when snapshots wire in."""
    a = UbuntuAdapter()
    assert "timeshift" in a.health_check()


def test_health_check_includes_systemctl() -> None:
    """systemctl component lands when scheduling wires in."""
    a = UbuntuAdapter()
    assert "systemctl" in a.health_check()


def test_health_check_includes_sudo() -> None:
    """sudo component lands when elevation wires in."""
    a = UbuntuAdapter()
    assert "sudo" in a.health_check()


def test_health_check_has_at_least_eleven_components() -> None:
    """Component count grew from 10 → 11 (timeshift) → 12 (sudo)."""
    a = UbuntuAdapter()
    assert len(a.health_check()) >= 11


def test_health_check_values_are_strings() -> None:
    a = UbuntuAdapter()
    for key, value in a.health_check().items():
        assert isinstance(value, str), f"health_check[{key!r}] is not a string"
        assert any(
            value.startswith(prefix)
            for prefix in ("ok", "degraded", "unavailable", "error")
        ), f"health_check[{key!r}] = {value!r} does not start with a known status"


# ── detect_host (Linux-only live test) ───────────────────────────────────


def test_detect_host_returns_linux_on_linux() -> None:
    if platform.system() != "Linux":
        pytest.skip("requires Linux")
    h = UbuntuAdapter().detect_host()
    assert h.os.value.startswith("linux_")
    assert h.hostname
    assert h.arch in {"x86_64", "aarch64", "arm64", "i686"}


def test_detect_host_is_cached() -> None:
    if platform.system() != "Linux":
        pytest.skip("requires Linux")
    a = UbuntuAdapter()
    h1 = a.detect_host()
    h2 = a.detect_host()
    assert h1 is h2
