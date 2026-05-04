"""Mock-based smoke tests for TimeMachineSnapshot (M5.4 Task 8).

Six tests covering:
  a. backend slug
  b. is_available True when tmutil on path
  c. is_available False when tmutil missing
  d. list() returns SnapshotInfo with parsed timestamp
  e. create() raises SnapshotError with APFS explainer
  f. get() filters by snapshot id
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
sys.path.insert(0, str(ADAPTER_ROOT))

from ascendo.interfaces.snapshot import SnapshotError
from ascendo.models.host import HostInfo, OperatingSystem, ElevationMethod
from ascendo_macos.snapshot import TimeMachineSnapshot


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


def _linux_host() -> HostInfo:
    return HostInfo(
        hostname="ubuntu.local",
        os=OperatingSystem.LINUX_OTHER,
        os_version="24.04",
        arch="x86_64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def _snapshot_sidecar(snap_ids: list[str]) -> str:
    """Build a minimal valid ascendo/v1 sidecar with snapshot items."""
    rid = str(uuid.uuid4())
    items = [
        {
            "id": snap_id,
            "name": snap_id,
            "category": "snapshot",
            # Omit current_version + target_version: Pydantic VersionStr
            # requires min_length=1 when present; field is Optional with
            # None default. Snapshots have no version concept.
            "status": "success",
            "source": {"type": "snapshot", "feed": None},
        }
        for snap_id in snap_ids
    ]
    return json.dumps({
        "schema": "ascendo/v1",
        "run": {
            "id": rid,
            "trigger": "cli",
            "profile": "default",
            "dry_run": False,
            "started_at": "2026-05-04T12:00:00Z",
        },
        "host": {
            "hostname": "testmac.local",
            "os": "macos",
            "os_version": "14.5",
            "arch": "arm64",
            "user": "mk",
            "is_elevated": False,
            "elevation_method": "none",
        },
        "tool": {"name": "tmutil", "version": "unknown"},
        "phase": "check",
        "category": "snapshot",
        "started_at": "2026-05-04T12:00:00Z",
        "finished_at": "2026-05-04T12:00:01Z",
        "status": "success",
        "needs_reboot": False,
        "summary": {
            "total": len(items),
            "success": len(items),
            "failed": 0,
            "skipped": 0,
            "up_to_date": 0,
            "planned": 0,
        },
        "items": items,
        "messages": [],
    })


def _make_fake_run(sidecar_json: str, filename: str = "check__snapshot.json"):
    """Return a fake subprocess.run side-effect that writes a sidecar to disk."""
    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out_dir = Path(argv[argv.index("--output-dir") + 1]) / rid
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / filename).write_text(sidecar_json)
        return subprocess.CompletedProcess(argv, 0, "", "")
    return fake_run


# ---------------------------------------------------------------------------
# a. backend slug
# ---------------------------------------------------------------------------

def test_backend_slug_is_time_machine():
    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    assert snap.backend == "time_machine"


# ---------------------------------------------------------------------------
# b. is_available returns True when tmutil is on path
# ---------------------------------------------------------------------------

def test_is_available_returns_true_when_tmutil_on_path():
    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("shutil.which", return_value="/usr/bin/tmutil"):
        result = snap.is_available(_mac_host())
    assert result is True


# ---------------------------------------------------------------------------
# c. is_available returns False when tmutil is missing
# ---------------------------------------------------------------------------

def test_is_available_returns_false_when_tmutil_missing():
    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("shutil.which", return_value=None):
        result = snap.is_available(_mac_host())
    assert result is False


def test_is_available_returns_false_on_non_macos():
    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    # No patch needed — host gate fires before which() check
    result = snap.is_available(_linux_host())
    assert result is False


# ---------------------------------------------------------------------------
# d. list() returns SnapshotInfo with parsed timestamp
# ---------------------------------------------------------------------------

def test_list_returns_snapshotinfo_with_parsed_timestamp():
    snap_id = "com.apple.TimeMachine.2026-05-03-140425.local"
    sidecar_json = _snapshot_sidecar([snap_id])

    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=_make_fake_run(sidecar_json)):
        results = snap.list(_mac_host())

    assert len(results) == 1
    info = results[0]
    assert info.id == snap_id
    assert info.backend == "time_machine"
    # Timestamp parsed from: 2026-05-03-140425 -> 2026-05-03 14:04:25 UTC
    assert info.created_at.year == 2026
    assert info.created_at.month == 5
    assert info.created_at.day == 3
    assert info.created_at.hour == 14
    assert info.created_at.minute == 4
    assert info.created_at.second == 25


# ---------------------------------------------------------------------------
# e. create() raises SnapshotError with APFS explainer
# ---------------------------------------------------------------------------

def test_create_raises_snapshot_error_with_apfs_explainer():
    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with pytest.raises(SnapshotError) as excinfo:
        snap.create(_mac_host(), label="before-upgrade")
    msg = str(excinfo.value).lower()
    # Must mention APFS or auto-managed nature
    assert "apfs" in msg or "auto-managed" in msg or "auto" in msg


# ---------------------------------------------------------------------------
# f. get() filters by snapshot id
# ---------------------------------------------------------------------------

def test_get_filters_by_id():
    snap_id_1 = "com.apple.TimeMachine.2026-05-03-120000.local"
    snap_id_2 = "com.apple.TimeMachine.2026-05-03-140425.local"
    sidecar_json = _snapshot_sidecar([snap_id_1, snap_id_2])

    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=_make_fake_run(sidecar_json)):
        result = snap.get(_mac_host(), snap_id_2)

    assert result is not None
    assert result.id == snap_id_2
    # Second snapshot (14:04:25) should be returned
    assert result.created_at.hour == 14
