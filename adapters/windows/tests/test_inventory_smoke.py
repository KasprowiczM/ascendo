"""Smoke tests for :class:`WindowsInventory`.

Mirrors test_winget_manager_smoke.py — mock-based, deliberately does NOT
spawn PowerShell or call winget. Verifies:

* ``list_installed`` shells out, parses the sidecar, and returns Packages.
* ``list_installed`` short-circuits to [] on hosts without winget (no error).
* Script crash with no sidecar -> :class:`ManagerError`.
* Categories filter actually filters.
* ``emit_sidecar`` round-trips through pydantic validation.
* WindowsAdapter.inventory() returns a WindowsInventory.
* WindowsAdapter.capabilities includes INVENTORY.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ascendo.interfaces import AdapterCapability
from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo
from ascendo.models.package import Package, SourceType
from ascendo.models.run import Phase, RunInfo
from ascendo.models.sidecar import Sidecar
from ascendo_windows import WindowsAdapter
from ascendo_windows.inventory import WindowsInventory


# ── Helpers ───────────────────────────────────────────────────────────


def _make_inventory(scripts_dir: Path, lib_dir: Path) -> WindowsInventory:
    """Construct a WindowsInventory pinned to a fake pwsh path so tests
    don't depend on having pwsh installed in the test environment."""
    return WindowsInventory(
        scripts_dir=scripts_dir,
        lib_dir=lib_dir,
        pwsh_path="/usr/bin/true",
        timeout_sec=30,
    )


def _extract_arg(argv: list[str], flag: str) -> str:
    """Find ``flag`` in argv and return the next token."""
    return argv[argv.index(flag) + 1]


def _run_completed_inventory(
    *,
    output_dir_arg: str,
    run_id: str,
    payload: dict[str, Any] | None,
    returncode: int,
) -> subprocess.CompletedProcess[str]:
    """Build a fake CompletedProcess and (optionally) write a sidecar at
    the path WindowsInventory will read from.

    Mirrors what `inventory/list.ps1` does on disk.
    """
    if payload is not None:
        target = Path(output_dir_arg) / run_id / "check__winget.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.CompletedProcess(
        args=["pwsh", "-File", "fake.ps1"],
        returncode=returncode,
        stdout="",
        stderr="",
    )


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_scripts_dir_with_inventory(fake_scripts_dir: Path) -> Path:
    """Extend the conftest fake_scripts_dir with an `inventory/list.ps1`
    placeholder so the manager's existence check passes.

    `fake_scripts_dir` already provides scripts/winget/<phase>.ps1; we
    just add scripts/inventory/list.ps1.
    """
    inv_dir = fake_scripts_dir / "inventory"
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "list.ps1").write_text(
        "# placeholder - mocked away by tests (list.ps1)\n",
        encoding="utf-8",
    )
    return fake_scripts_dir


@pytest.fixture
def inventory_sidecar_payload(
    run_info: RunInfo,
    windows_host: HostInfo,
) -> dict[str, Any]:
    """A valid ascendo/v1 ``check__winget.json`` payload with three
    items, all status='success', mimicking what inventory/list.ps1
    emits.

    All items use category='winget' (the inventory IS of winget packages
    on Windows) — but the dispatcher we test below also feeds in mixed
    categories to exercise the filter path.
    """
    return {
        "schema": "ascendo/v1",
        "run": json.loads(run_info.model_dump_json()),
        "host": json.loads(windows_host.model_dump_json()),
        "tool": {
            "name": "winget",
            "version": "v1.28.240",
            "binary_path": "C:\\winget.exe",
        },
        "phase": Phase.CHECK.value,
        "category": "winget",
        "started_at": "2026-05-01T12:00:00+00:00",
        "finished_at": "2026-05-01T12:00:05+00:00",
        "status": "success",
        "items": [
            {
                "id": "Microsoft.PowerShell",
                "name": "PowerShell",
                "category": "winget",
                "source": {"type": "winget", "feed": "winget", "url": None},
                "current_version": "7.6.0",
                "target_version": None,
                "resolved_version": None,
                "status": "success",
                "exit_code": None,
                "duration_ms": None,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
            {
                "id": "Mozilla.Firefox",
                "name": "Mozilla Firefox",
                "category": "winget",
                "source": {"type": "winget", "feed": "winget", "url": None},
                "current_version": "122.0",
                "target_version": None,
                "resolved_version": None,
                "status": "success",
                "exit_code": None,
                "duration_ms": None,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
            {
                "id": "7zip.7zip",
                "name": "7-Zip",
                "category": "winget",
                "source": {"type": "winget", "feed": "winget", "url": None},
                "current_version": "23.01",
                "target_version": None,
                "resolved_version": None,
                "status": "success",
                "exit_code": None,
                "duration_ms": None,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
        ],
        "summary": {
            "total": 3,
            "success": 3,
            "up_to_date": 0,
            "failed": 0,
            "skipped": 0,
            "planned": 0,
            "partial": 0,
            "duration_ms": None,
            "exit_code": 0,
        },
        "messages": [
            {
                "level": "info",
                "text": "Generated by inventory/list.ps1, not a phase pipeline.",
                "timestamp": None,
            },
        ],
    }


# ── list_installed ────────────────────────────────────────────────────


def test_list_installed_happy_path(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
    inventory_sidecar_payload: dict[str, Any],
) -> None:
    """Happy path: script emits 3 items -> 3 Package objects come back."""
    inv = _make_inventory(fake_scripts_dir_with_inventory, fake_lib_dir)

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        # argv shape sanity: a fresh uuid4 run-id (NOT the orchestrator's)
        # is passed; we extract it from argv to know where the script
        # would write.
        run_id = _extract_arg(argv, "-RunId")
        output_dir = _extract_arg(argv, "-OutputDir")
        # Inventory script always advertises trigger=plugin + profile=inventory.
        assert _extract_arg(argv, "-Trigger") == "plugin"
        assert _extract_arg(argv, "-Profile") == "inventory"
        # Read-only -> no -DryRun.
        assert "-DryRun" not in argv
        # Script path points at scripts/inventory/list.ps1.
        assert str(
            fake_scripts_dir_with_inventory / "inventory" / "list.ps1"
        ) in argv

        # Emit a sidecar at the path WindowsInventory will read from.
        # We have to rewrite the run_id in the payload so it round-trips
        # through Sidecar's pydantic validators (run.id == argv RunId is
        # NOT enforced by the model, but we keep things tidy).
        payload = dict(inventory_sidecar_payload)
        run_block = dict(payload["run"])
        run_block["id"] = run_id
        payload["run"] = run_block
        return _run_completed_inventory(
            output_dir_arg=output_dir,
            run_id=run_id,
            payload=payload,
            returncode=0,
        )

    with patch(
        "ascendo_windows.inventory.shutil.which",
        return_value="C:\\winget.exe",
    ):
        with patch(
            "ascendo_windows.inventory.subprocess.run",
            side_effect=fake_run,
        ):
            packages = inv.list_installed(windows_host)

    assert len(packages) == 3
    ids = [p.id for p in packages]
    assert "Microsoft.PowerShell" in ids
    assert "Mozilla.Firefox" in ids
    assert "7zip.7zip" in ids
    # Each Package has the right category tag.
    assert all(p.category is SourceType.WINGET for p in packages)


def test_list_installed_no_winget(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
) -> None:
    """Host without winget on PATH -> empty list, no error, no spawn."""
    inv = _make_inventory(fake_scripts_dir_with_inventory, fake_lib_dir)

    with patch(
        "ascendo_windows.inventory.shutil.which",
        return_value=None,
    ):
        with patch(
            "ascendo_windows.inventory.subprocess.run",
        ) as run_mock:
            packages = inv.list_installed(windows_host)

    assert packages == []
    run_mock.assert_not_called()


def test_list_installed_non_windows_host_returns_empty(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
    linux_host: HostInfo,
) -> None:
    """Non-Windows host -> empty list, no error, no spawn."""
    inv = _make_inventory(fake_scripts_dir_with_inventory, fake_lib_dir)

    with patch(
        "ascendo_windows.inventory.subprocess.run",
    ) as run_mock:
        packages = inv.list_installed(linux_host)

    assert packages == []
    run_mock.assert_not_called()


def test_list_installed_script_crash_raises(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
) -> None:
    """Script exits non-zero with no sidecar -> ManagerError."""
    inv = _make_inventory(fake_scripts_dir_with_inventory, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv,
            returncode=1,
            stdout="",
            stderr="boom: native crash\n",
        )

    with patch(
        "ascendo_windows.inventory.shutil.which",
        return_value="C:\\winget.exe",
    ):
        with patch(
            "ascendo_windows.inventory.subprocess.run",
            side_effect=fake_run,
        ):
            with pytest.raises(ManagerError) as excinfo:
                inv.list_installed(windows_host)

    msg = str(excinfo.value)
    assert "no sidecar" in msg
    assert "boom: native crash" in msg
    assert "exit code:     1" in msg


def test_list_installed_categories_filter(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
    inventory_sidecar_payload: dict[str, Any],
) -> None:
    """Filter by [SourceType.WINGET] -> only winget items returned.

    We mutate the fixture so it has 3 items with mixed categories: 1
    winget, 1 msstore, 1 appx. The filter should reduce to just the
    winget item.
    """
    payload = dict(inventory_sidecar_payload)
    items = [
        dict(payload["items"][0]),  # winget
        dict(payload["items"][1]),  # winget originally
        dict(payload["items"][2]),  # winget originally
    ]
    items[1]["category"] = "msstore"
    items[1]["source"] = {"type": "msstore", "feed": None, "url": None}
    items[2]["category"] = "appx"
    items[2]["source"] = {"type": "appx", "feed": None, "url": None}
    payload["items"] = items
    # Top-level sidecar.category stays 'winget' — this is the source the
    # script ran against. The per-item categories are what the filter
    # operates on.

    inv = _make_inventory(fake_scripts_dir_with_inventory, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_id = _extract_arg(argv, "-RunId")
        output_dir = _extract_arg(argv, "-OutputDir")
        local_payload = dict(payload)
        run_block = dict(local_payload["run"])
        run_block["id"] = run_id
        local_payload["run"] = run_block
        return _run_completed_inventory(
            output_dir_arg=output_dir,
            run_id=run_id,
            payload=local_payload,
            returncode=0,
        )

    with patch(
        "ascendo_windows.inventory.shutil.which",
        return_value="C:\\winget.exe",
    ):
        with patch(
            "ascendo_windows.inventory.subprocess.run",
            side_effect=fake_run,
        ):
            winget_only = inv.list_installed(
                windows_host, categories=[SourceType.WINGET]
            )

    assert len(winget_only) == 1
    assert winget_only[0].category is SourceType.WINGET
    assert winget_only[0].id == "Microsoft.PowerShell"


def test_list_installed_drops_synthetic_diagnostic_items(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
    inventory_sidecar_payload: dict[str, Any],
) -> None:
    """Items with sentinel IDs like __phase_error__ are dropped from the
    Package list — they are diagnostic markers, not real packages."""
    payload = dict(inventory_sidecar_payload)
    items = list(payload["items"])
    # Inject a synthetic diagnostic item.
    items.append(
        {
            "id": "__phase_error__",
            "name": "inventory error",
            "category": "winget",
            "source": {"type": "winget", "feed": None, "url": None},
            "current_version": None,
            "target_version": None,
            "resolved_version": None,
            "status": "failed",
            "exit_code": None,
            "duration_ms": None,
            "evidence": None,
            "rollback": None,
            "messages": [],
        }
    )
    payload["items"] = items
    payload["summary"] = {
        "total": 4,
        "success": 3,
        "up_to_date": 0,
        "failed": 1,
        "skipped": 0,
        "planned": 0,
        "partial": 0,
        "duration_ms": None,
        "exit_code": 1,
    }
    payload["status"] = "partial"

    inv = _make_inventory(fake_scripts_dir_with_inventory, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_id = _extract_arg(argv, "-RunId")
        output_dir = _extract_arg(argv, "-OutputDir")
        local_payload = dict(payload)
        run_block = dict(local_payload["run"])
        run_block["id"] = run_id
        local_payload["run"] = run_block
        return _run_completed_inventory(
            output_dir_arg=output_dir,
            run_id=run_id,
            payload=local_payload,
            returncode=0,
        )

    with patch(
        "ascendo_windows.inventory.shutil.which",
        return_value="C:\\winget.exe",
    ):
        with patch(
            "ascendo_windows.inventory.subprocess.run",
            side_effect=fake_run,
        ):
            packages = inv.list_installed(windows_host)

    ids = {p.id for p in packages}
    assert "__phase_error__" not in ids
    assert len(packages) == 3


def test_list_installed_timeout_raises(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
) -> None:
    """subprocess timeout -> ManagerError chained from TimeoutExpired."""
    inv = _make_inventory(fake_scripts_dir_with_inventory, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

    with patch(
        "ascendo_windows.inventory.shutil.which",
        return_value="C:\\winget.exe",
    ):
        with patch(
            "ascendo_windows.inventory.subprocess.run",
            side_effect=fake_run,
        ):
            with pytest.raises(ManagerError) as excinfo:
                inv.list_installed(windows_host)

    assert "timed out" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, subprocess.TimeoutExpired)


# ── emit_sidecar ──────────────────────────────────────────────────────


def test_emit_sidecar_round_trip(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
) -> None:
    """emit_sidecar produces a Sidecar that round-trips through pydantic."""
    inv = _make_inventory(fake_scripts_dir_with_inventory, fake_lib_dir)

    packages = [
        Package(id="Microsoft.PowerShell", name="PowerShell", category=SourceType.WINGET),
        Package(id="Mozilla.Firefox", name="Mozilla Firefox", category=SourceType.WINGET),
    ]

    sidecar = inv.emit_sidecar(run_info, windows_host, packages)

    assert isinstance(sidecar, Sidecar)
    assert sidecar.phase is Phase.CHECK
    assert sidecar.category is SourceType.WINGET
    assert sidecar.status.value == "success"
    assert len(sidecar.items) == 2
    assert sidecar.summary.total == 2
    assert sidecar.summary.success == 2

    # Round-trip via JSON to confirm full validation passes.
    raw = sidecar.model_dump_json(by_alias=True)
    payload = json.loads(raw)
    revived = Sidecar.model_validate(payload)
    assert len(revived.items) == 2
    assert revived.items[0].id == "Microsoft.PowerShell"


def test_emit_sidecar_empty_list(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
) -> None:
    """Empty package list still produces a valid sidecar."""
    inv = _make_inventory(fake_scripts_dir_with_inventory, fake_lib_dir)

    sidecar = inv.emit_sidecar(run_info, windows_host, [])

    assert sidecar.summary.total == 0
    assert len(sidecar.items) == 0
    # Round-trip.
    raw = sidecar.model_dump_json(by_alias=True)
    Sidecar.model_validate(json.loads(raw))


# ── Adapter wiring ────────────────────────────────────────────────────


def test_adapter_inventory_returns_windows_inventory_instance() -> None:
    """WindowsAdapter.inventory() must return a WindowsInventory."""
    adapter = WindowsAdapter()
    inv = adapter.inventory()
    assert isinstance(inv, WindowsInventory)


def test_adapter_capabilities_includes_inventory() -> None:
    """INVENTORY must be in adapter.capabilities post-M3.11."""
    adapter = WindowsAdapter()
    assert AdapterCapability.INVENTORY in adapter.capabilities
    # Backward compatibility: PACKAGE_MANAGEMENT still there.
    assert AdapterCapability.PACKAGE_MANAGEMENT in adapter.capabilities


# ── pwsh resolution (parity with WingetManager) ──────────────────────


def test_inventory_resolve_pwsh_uses_override(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
) -> None:
    """Explicit pwsh_path override skips PATH lookup."""
    inv = WindowsInventory(
        scripts_dir=fake_scripts_dir_with_inventory,
        lib_dir=fake_lib_dir,
        pwsh_path="/opt/forced/pwsh",
    )
    assert inv._resolve_pwsh() == "/opt/forced/pwsh"


def test_inventory_resolve_pwsh_raises_if_nothing_found(
    fake_scripts_dir_with_inventory: Path,
    fake_lib_dir: Path,
) -> None:
    """No pwsh / powershell on PATH -> ManagerError."""
    inv = WindowsInventory(
        scripts_dir=fake_scripts_dir_with_inventory,
        lib_dir=fake_lib_dir,
    )
    with patch("ascendo_windows.inventory.shutil.which", return_value=None):
        with pytest.raises(ManagerError) as excinfo:
            inv._resolve_pwsh()
    assert "no PowerShell binary" in str(excinfo.value)
