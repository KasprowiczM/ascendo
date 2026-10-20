"""Contract tests for bin/check-version-lockstep.sh.

Verifies:
- Sibling version match exits 0 and prints both versions
- Sibling version mismatch exits 1 and prints mismatch details
- Missing sibling version file exits 0 and outputs 'skipped'
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "bin" / "check-version-lockstep.sh"


def test_lockstep_script_exists_and_executable() -> None:
    assert SCRIPT_PATH.is_file(), f"Expected script at {SCRIPT_PATH}"
    assert os.access(SCRIPT_PATH, os.X_OK), f"Script {SCRIPT_PATH} is not executable"


def test_lockstep_match_exits_0(tmp_path: Path) -> None:
    fake_sibling_dir = tmp_path / "macOS_updates"
    fake_sibling_dir.mkdir()
    version_file = fake_sibling_dir / "VERSION"
    version_file.write_text("1.5.5\n", encoding="utf-8")

    res = subprocess.run(
        ["bash", str(SCRIPT_PATH), str(version_file)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert res.returncode == 0
    assert "Ascendo version:       1.5.5" in res.stdout
    assert "macOS_updates version: 1.5.5" in res.stdout
    assert "PASSED" in res.stdout


def test_lockstep_mismatch_exits_1(tmp_path: Path) -> None:
    fake_sibling_dir = tmp_path / "macOS_updates"
    fake_sibling_dir.mkdir()
    version_file = fake_sibling_dir / "VERSION"
    version_file.write_text("1.5.4\n", encoding="utf-8")

    res = subprocess.run(
        ["bash", str(SCRIPT_PATH), str(version_file)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert res.returncode == 1
    assert "Ascendo version:       1.5.5" in res.stdout
    assert "macOS_updates version: 1.5.4" in res.stdout
    assert "MISMATCH" in res.stderr or "MISMATCH" in res.stdout


def test_lockstep_missing_sibling_exits_0_and_skips(tmp_path: Path) -> None:
    non_existent = tmp_path / "nonexistent" / "VERSION"

    res = subprocess.run(
        ["bash", str(SCRIPT_PATH), str(non_existent)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert res.returncode == 0
    combined_output = (res.stdout + res.stderr).lower()
    assert "skipped" in combined_output
