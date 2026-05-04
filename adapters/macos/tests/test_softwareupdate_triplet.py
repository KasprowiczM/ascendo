"""Integration tests for adapters/macos/scripts/softwareupdate/{plan,verify,cleanup}.sh.

Each test invokes the script with SU_BIN pointing to a fake binary that
emits captured fixture output, then validates the produced sidecar through
Pydantic parse_sidecar().
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SU = ADAPTER_ROOT / "scripts" / "softwareupdate"
FIX = ADAPTER_ROOT / "tests" / "fixtures" / "softwareupdate"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_su(tmp_path: Path, *, fixture_name: str, exit_code: int = 0) -> Path:
    """Make a fake softwareupdate binary returning canned fixture output for -l.

    If exit_code != 0, the -l invocation exits with that code (simulating failure).
    The --help/-h path always succeeds so tool-version detection works.
    """
    if exit_code != 0:
        # Broken: --help succeeds (version detection), -l fails
        body = (
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = '--help' ] || [ \"$1\" = '-h' ]; then\n"
            "    echo 'softwareupdate test-fake'\n"
            "    exit 0\n"
            "fi\n"
            f"echo 'broken su' >&2\n"
            f"exit {exit_code}\n"
        )
    else:
        fixture = (FIX / fixture_name).read_text()
        body = (
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = '--help' ] || [ \"$1\" = '-h' ]; then\n"
            "    echo 'softwareupdate test-fake'\n"
            "    exit 0\n"
            "fi\n"
            "if [ \"$1\" = '-l' ] || [ \"$1\" = '--list' ]; then\n"
            f"    cat <<'EOF_SU'\n{fixture}\nEOF_SU\n"
            "    exit 0\n"
            "fi\n"
            "exit 1\n"
        )
    p = tmp_path / "fake_softwareupdate"
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _make_apply_sidecar(run_dir: Path, success_ids: list) -> Path:
    """Write a minimal apply__softwareupdate.json with given ids marked success."""
    run_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = run_dir / "apply__softwareupdate.json"
    items = [
        {"id": id_, "status": "success", "resolved_version": "1.0.0"}
        for id_ in success_ids
    ]
    data = {
        "schema": "ascendo/v1",
        "phase": "apply",
        "category": "softwareupdate",
        "items": items,
    }
    sidecar_path.write_text(json.dumps(data))
    return sidecar_path


def _run_script(script: Path, fake_su: Path, output_dir: Path, run_id: str, *extra: str):
    env = dict(os.environ)
    env["SU_BIN"] = str(fake_su)
    return subprocess.run(
        ["bash", str(script),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir),
         *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def _parse_sidecar(path: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(path.read_text())
    finally:
        sys.path.pop(0)


# ---------------------------------------------------------------------------
# plan.sh tests
# ---------------------------------------------------------------------------

def test_plan_emits_only_planned_items(tmp_path):
    """plan.sh with incremental-updates fixture: exactly 2 planned items, no up_to_date."""
    fake = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_script(SCRIPTS_SU / "plan.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr

    sidecar = out / rid / "plan__softwareupdate.json"
    assert sidecar.is_file(), f"sidecar missing\nstdout: {res.stdout}\nstderr: {res.stderr}"

    sc = _parse_sidecar(sidecar)
    assert sc.phase.value == "plan"
    assert sc.category.value == "softwareupdate"
    assert len(sc.items) == 2
    statuses = {i.status.value for i in sc.items}
    assert "planned" in statuses
    assert "up_to_date" not in statuses


def test_plan_failure_emits_failed_item(tmp_path):
    """plan.sh when softwareupdate -l exits non-zero: sidecar emitted with failed item."""
    fake = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt", exit_code=1)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_script(SCRIPTS_SU / "plan.sh", fake, out, rid)
    # Script should exit 30 (tool failure)
    assert res.returncode == 30, f"expected exit 30, got {res.returncode}\nstderr: {res.stderr}"

    sidecar = out / rid / "plan__softwareupdate.json"
    assert sidecar.is_file(), f"sidecar missing\nstdout: {res.stdout}\nstderr: {res.stderr}"

    sc = _parse_sidecar(sidecar)
    assert sc.status.value == "failed"
    assert any(m.level.value == "error" for m in sc.messages)


# ---------------------------------------------------------------------------
# verify.sh tests
# ---------------------------------------------------------------------------

def test_verify_reads_sibling_apply_sidecar(tmp_path):
    """verify.sh: applied item no longer in softwareupdate -l -> verify success."""
    # fake_su returns no-updates (all applied, nothing left)
    fake = _make_fake_su(tmp_path, fixture_name="no-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    # Synthesise apply sidecar with one successful item
    _make_apply_sidecar(out / rid, ["Safari17.4-17.4"])

    res = _run_script(SCRIPTS_SU / "verify.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr

    sidecar = out / rid / "verify__softwareupdate.json"
    assert sidecar.is_file(), f"sidecar missing\nstdout: {res.stdout}\nstderr: {res.stderr}"

    sc = _parse_sidecar(sidecar)
    assert sc.phase.value == "verify"
    success_ids = [i.id for i in sc.items if i.status.value == "success"]
    assert "Safari17.4-17.4" in success_ids


def test_verify_softnoop_when_no_apply_sidecar(tmp_path):
    """verify.sh: when apply sidecar absent, emits success sidecar with zero items + info message."""
    fake = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    # Do NOT create apply sidecar

    res = _run_script(SCRIPTS_SU / "verify.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr

    sidecar = out / rid / "verify__softwareupdate.json"
    assert sidecar.is_file(), f"sidecar missing\nstdout: {res.stdout}\nstderr: {res.stderr}"

    sc = _parse_sidecar(sidecar)
    assert sc.phase.value == "verify"
    assert sc.status.value == "success"
    assert sc.items == []
    # Should have at least one info message explaining the no-op
    assert len(sc.messages) >= 1
    assert any(m.level.value == "info" for m in sc.messages)


# ---------------------------------------------------------------------------
# cleanup.sh tests
# ---------------------------------------------------------------------------

def test_cleanup_emits_success_zero_items(tmp_path):
    """cleanup.sh: no-op, emits success sidecar + info message + zero items."""
    fake = _make_fake_su(tmp_path, fixture_name="no-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_script(SCRIPTS_SU / "cleanup.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr

    sidecar = out / rid / "cleanup__softwareupdate.json"
    assert sidecar.is_file(), f"sidecar missing\nstdout: {res.stdout}\nstderr: {res.stderr}"

    sc = _parse_sidecar(sidecar)
    assert sc.phase.value == "cleanup"
    assert sc.status.value == "success"
    assert sc.items == []
    assert len(sc.messages) >= 1


def test_cleanup_dry_run_is_identical(tmp_path):
    """cleanup.sh --dry-run: identical to normal run (no-op either way)."""
    fake = _make_fake_su(tmp_path, fixture_name="no-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_script(SCRIPTS_SU / "cleanup.sh", fake, out, rid, "--dry-run")
    assert res.returncode == 0, res.stderr

    sidecar = out / rid / "cleanup__softwareupdate.json"
    assert sidecar.is_file()

    sc = _parse_sidecar(sidecar)
    assert sc.status.value == "success"
    assert sc.items == []
