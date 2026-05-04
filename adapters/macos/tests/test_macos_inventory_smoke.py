"""Mock-based smoke tests for MacOSInventory."""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
sys.path.insert(0, str(ADAPTER_ROOT))

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo, OperatingSystem, ElevationMethod
from ascendo.models.run import Phase, RunInfo, Trigger
from ascendo.models.package import SourceType
from ascendo_macos.inventory import MacOSInventory


SCRIPTS_DIR = ADAPTER_ROOT / "scripts"
LIB_DIR = ADAPTER_ROOT / "lib"


def _mac_host() -> HostInfo:
    return HostInfo(
        hostname="testmac.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def _minimal_sidecar(items_json: list[dict]) -> str:
    """Build a minimal valid ascendo/v1 sidecar JSON string with the given items."""
    rid = str(uuid.uuid4())
    return json.dumps({
        "schema": "ascendo/v1",
        "run": {"id": rid, "trigger": "cli", "profile": "default",
                "dry_run": False, "started_at": "2026-05-04T12:00:00Z"},
        "host": {"hostname": "x", "os": "macos", "os_version": "14.5",
                 "arch": "arm64", "user": "mk", "is_elevated": False,
                 "elevation_method": "none"},
        "tool": {"name": "system_profiler", "version": "1.0"},
        "phase": "check", "category": "inventory",
        "started_at": "2026-05-04T12:00:00Z",
        "finished_at": "2026-05-04T12:00:01Z",
        "status": "success",
        "summary": {"total": len(items_json), "success": 0, "failed": 0,
                    "skipped": 0, "up_to_date": len(items_json),
                    "planned": 0},
        "items": items_json,
        "messages": [],
    })


def _item(name: str, src: str, version: str = "1.0",
          path: str | None = None) -> dict:
    return {
        "id": name,
        "name": name,
        "category": src,
        "current_version": version,
        "resolved_version": version,
        "status": "up_to_date",
        "source": {"type": src, "feed": path or f"/Applications/{name}.app"},
    }


def test_identity_and_paths():
    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    assert inv.SCRIPT_REL == "inventory/list.sh"
    assert inv.scripts_dir == SCRIPTS_DIR


def test_list_installed_returns_packages_from_sidecar():
    items = [_item("Safari", "system"), _item("Amphetamine", "mas"),
             _item("Inkscape", "brew"), _item("Firefox", "web")]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        # Simulate the bash script writing its sidecar to disk.
        # Find the run-id in argv (--run-id <uuid>)
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__inventory.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        packages = inv.list_installed(_mac_host())
    assert len(packages) == 4
    sources = {p.source.type for p in packages}
    assert sources == {SourceType.SYSTEM, SourceType.MAS,
                       SourceType.BREW, SourceType.WEB}


def test_list_installed_categories_filter():
    items = [_item("Safari", "system"), _item("Amphetamine", "mas")]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__inventory.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        packages = inv.list_installed(_mac_host(), categories=["mas"])
    assert len(packages) == 1
    assert packages[0].source.type == SourceType.MAS


def test_list_installed_missing_sidecar_raises_managererror():
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError) as excinfo:
            inv.list_installed(_mac_host())
    assert "sidecar" in str(excinfo.value).lower()


def test_list_installed_script_exit_30_raises_managererror():
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 30, "", "system_profiler crashed")

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError):
            inv.list_installed(_mac_host())


def test_list_installed_timeout_raises_managererror():
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 300)

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR, timeout_sec=300)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError) as excinfo:
            inv.list_installed(_mac_host())
    assert "timeout" in str(excinfo.value).lower() or "timed" in str(excinfo.value).lower()


def test_list_installed_non_macos_returns_empty():
    """Inventory on a Linux/Windows host returns [] cleanly (no shell-out)."""
    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    linux_host = HostInfo(
        hostname="x", os=OperatingSystem.LINUX_OTHER, os_version="24.04",
        arch="x86_64", user="x", is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )
    packages = inv.list_installed(linux_host)
    assert packages == []


def test_emit_sidecar_returns_valid_sidecar():
    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    run = RunInfo(id=str(uuid.uuid4()), trigger=Trigger.CLI,
                  profile="default", dry_run=False,
                  started_at="2026-05-04T12:00:00Z")
    host = _mac_host()
    # Build a single Package via list_installed using a fake run pipeline
    items = [_item("Safari", "system")]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__inventory.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("subprocess.run", side_effect=fake_run):
        packages = inv.list_installed(host)
    sidecar = inv.emit_sidecar(run, host, packages)
    assert sidecar.schema_.value == "ascendo/v1"
    assert sidecar.phase.value == "check"
    assert sidecar.category.value == "inventory"
    assert len(sidecar.items) == 1


def test_list_installed_unknown_category_logs_and_skips(caplog):
    """Unknown category strings are logged at WARNING and skipped, not silent-pass."""
    items = [_item("Safari", "system"), _item("Amphetamine", "mas")]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__inventory.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        with caplog.at_level("WARNING"):
            packages = inv.list_installed(_mac_host(), categories=["systm"])  # typo
    # Unknown category yields an empty filter set -> no packages match
    assert packages == []
    # A WARNING was emitted naming the unknown category
    assert any(
        "unknown category" in rec.message.lower()
        for rec in caplog.records
    )
