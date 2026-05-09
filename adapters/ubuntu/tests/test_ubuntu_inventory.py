"""Mock-based smoke tests for UbuntuInventory + bash list.sh integration."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ADAPTER_ROOT / "scripts"
LIB_DIR = ADAPTER_ROOT / "lib"
LIST_SH = SCRIPTS_DIR / "inventory" / "list.sh"

from ascendo.interfaces.package_manager import ManagerError  # noqa: E402
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem  # noqa: E402
from ascendo.models.package import SourceType  # noqa: E402
from ascendo.models.run import RunInfo, Trigger  # noqa: E402
from ascendo.models.sidecar import parse_sidecar  # noqa: E402

from ascendo_ubuntu.inventory import UbuntuInventory  # noqa: E402


def _ubuntu_host() -> HostInfo:
    return HostInfo(
        hostname="testbox",
        os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04",
        arch="x86_64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def _minimal_sidecar(items_json: list[dict]) -> str:
    rid = str(uuid.uuid4())
    return json.dumps({
        "schema": "ascendo/v1",
        "run": {"id": rid, "trigger": "cli", "profile": "default",
                "dry_run": False, "started_at": "2026-05-09T12:00:00Z"},
        "host": {"hostname": "x", "os": "linux_ubuntu", "os_version": "24.04",
                 "arch": "x86_64", "user": "mk", "is_elevated": False,
                 "elevation_method": "none"},
        "tool": {"name": "ubuntu-inventory", "version": "1.0"},
        "phase": "check", "category": "inventory",
        "started_at": "2026-05-09T12:00:00Z",
        "finished_at": "2026-05-09T12:00:01Z",
        "status": "success",
        "summary": {"total": len(items_json), "success": 0, "failed": 0,
                    "skipped": 0, "up_to_date": len(items_json),
                    "planned": 0},
        "items": items_json,
        "messages": [],
    })


def _item(name: str, src: str, version: str = "1.0") -> dict:
    return {
        "id": f"{src}:{name}",
        "name": name,
        "category": "inventory",
        "source": {"type": src, "feed": src},
        "current_version": version,
        "target_version": version,
        "status": "up_to_date",
    }


# ── Argv shape ──────────────────────────────────────────────────────────────


def test_inventory_calls_bash_with_correct_args() -> None:
    """list_installed builds the canonical bash argv."""
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        # Don't write a sidecar — assert before we even hit the read.
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = UbuntuInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError):
            inv.list_installed(_ubuntu_host())

    argv = captured["argv"]
    assert argv[0] in ("/bin/bash",) or argv[0].endswith("bash")
    assert argv[1] == str(LIST_SH)
    assert "--run-id" in argv
    assert "--trigger" in argv
    assert argv[argv.index("--trigger") + 1] == Trigger.PLUGIN.value
    assert "--profile" in argv
    assert argv[argv.index("--profile") + 1] == "default"
    assert "--output-dir" in argv


# ── Sidecar -> Package conversion ──────────────────────────────────────────


def test_inventory_parses_sidecar_into_packages() -> None:
    """Hand-crafted sidecar on disk -> Package list."""
    items = [
        _item("firefox", "apt", "131.0+build1"),
        _item("code", "snap", "1.95.3"),
        _item("com.spotify.Client", "flatpak", "1.2.45"),
        _item("ripgrep", "brew_formula", "14.1.1"),
        _item("typescript", "npm", "5.6.3"),
        _item("requests", "pip", "2.32.3"),
    ]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__inventory.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = UbuntuInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        packages = inv.list_installed(_ubuntu_host())

    assert len(packages) == 6
    by_id = {p.id: p for p in packages}
    assert by_id["apt:firefox"].installed_version == "131.0+build1"
    assert by_id["apt:firefox"].category is SourceType.APT
    assert by_id["snap:code"].category is SourceType.SNAP
    assert by_id["flatpak:com.spotify.Client"].category is SourceType.FLATPAK
    assert by_id["brew_formula:ripgrep"].category is SourceType.BREW_FORMULA
    assert by_id["npm:typescript"].category is SourceType.NPM
    assert by_id["pip:requests"].category is SourceType.PIP


# ── Missing sidecar / failure paths ────────────────────────────────────────


def test_inventory_handles_missing_sidecar_returns_empty() -> None:
    """Script returns 0 but writes no sidecar -> ManagerError."""
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = UbuntuInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError) as excinfo:
            inv.list_installed(_ubuntu_host())
    assert "sidecar" in str(excinfo.value).lower()


def test_inventory_handles_subprocess_failure_returns_empty() -> None:
    """Bash exits non-zero AND writes no sidecar -> ManagerError (does not raise raw OSError)."""
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 30, "", "boom")

    inv = UbuntuInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError):
            inv.list_installed(_ubuntu_host())


def test_inventory_timeout_raises_managererror() -> None:
    """Subprocess timeout -> ManagerError, not raw TimeoutExpired."""
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 300)

    inv = UbuntuInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR, timeout_sec=300)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError) as excinfo:
            inv.list_installed(_ubuntu_host())
    assert "timed out" in str(excinfo.value).lower()


# ── Filter ─────────────────────────────────────────────────────────────────


def test_inventory_filters_by_category() -> None:
    """list_installed(categories=["apt"]) returns only APT items."""
    items = [
        _item("firefox", "apt"),
        _item("code", "snap"),
        _item("ripgrep", "brew_formula"),
    ]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__inventory.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = UbuntuInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        packages = inv.list_installed(_ubuntu_host(), categories=[SourceType.APT])

    assert len(packages) == 1
    assert packages[0].id == "apt:firefox"


# ── Non-Linux short-circuit ────────────────────────────────────────────────


def test_inventory_non_linux_returns_empty() -> None:
    """On macOS/Windows, list_installed returns [] without shelling out."""
    inv = UbuntuInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    mac_host = HostInfo(
        hostname="x", os=OperatingSystem.MACOS, os_version="14.5",
        arch="arm64", user="x", is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )
    # Should NOT call subprocess.run at all on a non-Linux host.
    with patch("subprocess.run") as run_mock:
        out = inv.list_installed(mac_host)
    assert out == []
    run_mock.assert_not_called()


# ── End-to-end bash test (Linux-only; smoke on macOS too since brew works) ─


def test_list_sh_emits_valid_sidecar(tmp_path: Path) -> None:
    """Run the real list.sh end-to-end; verify it emits a parseable sidecar.

    This works even on macOS (brew/pip will respond, dpkg/snap/flatpak get
    skipped gracefully). On Linux, all six sources are exercised. Skipped
    only if neither python3 nor bash is on PATH (unlikely).
    """
    if shutil.which("bash") is None:
        pytest.skip("bash not on PATH")
    if shutil.which("python3") is None:
        pytest.skip("python3 not on PATH")

    rid = str(uuid.uuid4())
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    res = subprocess.run(
        [
            "bash", str(LIST_SH),
            "--run-id", rid,
            "--trigger", "cli",
            "--profile", "quick",
            "--output-dir", str(out_dir),
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert res.returncode == 0, f"list.sh exited {res.returncode}; stderr: {res.stderr}"

    sidecar_path = out_dir / rid / "check__inventory.json"
    assert sidecar_path.is_file(), "list.sh did not write the expected sidecar"

    payload = json.loads(sidecar_path.read_text())
    assert payload["schema"] == "ascendo/v1"
    assert payload["phase"] == "check"
    assert payload["category"] == "inventory"

    # Pydantic validation round-trip.
    sidecar = parse_sidecar(payload)
    assert sidecar.status.value in ("success", "partial", "skipped")
    # At least the per-source status messages should be present (6 sources).
    assert len(sidecar.messages) >= 1


# ── Adapter wiring ─────────────────────────────────────────────────────────


def test_ubuntu_adapter_inventory_returns_ubuntu_inventory() -> None:
    """UbuntuAdapter.inventory() now returns a real UbuntuInventory."""
    from ascendo_ubuntu import UbuntuAdapter

    a = UbuntuAdapter()
    inv = a.inventory()
    assert inv is not None
    assert isinstance(inv, UbuntuInventory)
    # Cached singleton.
    assert a.inventory() is inv
    # scripts_dir points at the adapter-local tree, not the legacy top-level.
    assert "adapters/ubuntu/scripts" in str(inv.scripts_dir).replace("\\", "/")
