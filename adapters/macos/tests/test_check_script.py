"""Real-brew test for adapters/macos/scripts/brew/check.sh.

Runs the phase script directly (not through Python adapter), verifies the
sidecar lands at the expected path with the right shape.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "brew" / "check.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_script_exists_and_executable() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "check.sh not executable"


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("brew") is None or shutil.which("jq") is None,
    reason="real brew + jq on macOS required",
)
def test_check_emits_valid_sidecar(tmp_path: Path) -> None:
    run_id = str(uuid.uuid4())
    out_dir = tmp_path / "runs"
    res = subprocess.run(
        [
            "bash", str(SCRIPT),
            "--run-id", run_id,
            "--trigger", "cli",
            "--profile", "default",
            "--output-dir", str(out_dir),
        ],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode in (0, 1), (
        f"unexpected exit: {res.returncode}\n{res.stderr}\n{res.stdout}"
    )

    sidecar_path = out_dir / run_id / "check__brew.json"
    assert sidecar_path.is_file(), (
        f"missing {sidecar_path}\n{res.stdout}\n{res.stderr}"
    )

    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        sc = parse_sidecar(sidecar_path.read_text())
    finally:
        sys.path.pop(0)

    # The Sidecar model uses schema_ (trailing underscore) because "schema" is
    # a reserved alias in Pydantic v2; the JSON field is "schema".
    assert sc.schema_.value == "ascendo/v1"
    assert sc.phase.value == "check"
    assert sc.category.value == "brew"
    # summary.total may be 0 if nothing outdated; phase still success.
    assert sc.summary.total >= 0
    assert sc.tool.name == "brew"


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("brew") is None or shutil.which("jq") is None,
    reason="real brew + jq on macOS required",
)
def test_apply_dry_run_emits_planned_items(tmp_path: Path) -> None:
    """apply.sh with --dry-run emits status=planned, no real upgrade."""
    APPLY = ADAPTER_ROOT / "scripts" / "brew" / "apply.sh"
    run_id = str(uuid.uuid4())
    out_dir = tmp_path / "runs"
    res = subprocess.run(
        [
            "bash", str(APPLY),
            "--run-id", run_id,
            "--trigger", "cli",
            "--profile", "default",
            "--output-dir", str(out_dir),
            "--dry-run",
        ],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode in (0, 1), (
        f"unexpected exit: {res.returncode}\n{res.stderr}\n{res.stdout}"
    )
    sidecar_path = out_dir / run_id / "apply__brew.json"
    assert sidecar_path.is_file(), (
        f"missing {sidecar_path}\n{res.stdout}\n{res.stderr}"
    )
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        sc = parse_sidecar(sidecar_path.read_text())
    finally:
        sys.path.pop(0)
    # In dry-run mode, NO item should have status in success/failed (those
    # are mutation outcomes); planned/up_to_date are valid.
    for it in sc.items:
        assert it.status.value in {"planned", "up_to_date", "skipped"}, (
            f"unexpected status {it.status.value} for {it.id} in dry-run"
        )
