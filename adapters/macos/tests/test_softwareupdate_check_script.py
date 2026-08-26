"""Tests for adapters/macos/scripts/softwareupdate/check.sh.

Six integration tests using a fake softwareupdate binary fed canned fixtures.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "softwareupdate" / "check.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures" / "softwareupdate"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_su(tmp_path: Path, *, fixture_name: str) -> Path:
    """Fake softwareupdate binary returning the canned fixture for `-l`."""
    fixture = (FIX / fixture_name).read_text()
    p = tmp_path / "fake_softwareupdate"
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
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _parse(p: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(p.read_text())
    finally:
        sys.path.pop(0)


def _run(script: Path, su: Path, output_dir: Path, run_id: str, *extra: str):
    env = dict(os.environ)
    env["SU_BIN"] = str(su)
    return subprocess.run(
        ["bash", str(script),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir),
         *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def test_no_updates_emits_zero_items_status_success(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="no-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__softwareupdate.json")
    assert sc.status.value == "success"
    assert sc.items == []


def test_incremental_updates_emits_two_planned_items(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__softwareupdate.json")
    assert len(sc.items) == 2
    assert {i.id for i in sc.items} == {
        "Safari17.4-17.4",
        "XProtectPlistConfigData_10_15-2174",
    }
    for item in sc.items:
        assert item.status.value == "planned"
        assert item.source.type.value == "softwareupdate"


def test_restart_required_marks_needs_reboot(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="restart-required.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__softwareupdate.json")
    assert len(sc.items) == 2
    assert sc.needs_reboot is True
    macos_item = next(i for i in sc.items if i.id.startswith("macOS Sonoma"))
    assert macos_item.current_version == "14.7.1"


def test_per_item_metadata_safari(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 0
    sc = _parse(out / rid / "check__softwareupdate.json")
    safari = next(i for i in sc.items if "Safari" in i.id)
    assert safari.current_version == "17.4"
    assert safari.target_version == "17.4"
    assert safari.source.type.value == "softwareupdate"


def test_softwareupdate_failure_exits_30(tmp_path):
    su = tmp_path / "broken_su"
    su.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = '--help' ] || [ \"$1\" = '-h' ]; then\n"
        "    echo 'broken-fake'\n"
        "    exit 0\n"
        "fi\n"
        "echo 'broken' >&2\n"
        "exit 1\n"
    )
    os.chmod(su, 0o755)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 30
    sc = _parse(out / rid / "check__softwareupdate.json")
    assert sc.status.value == "failed"
    assert any(m.level.value == "error" for m in sc.messages)


def test_required_args_validation(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="no-updates.txt")
    env = dict(os.environ)
    env["SU_BIN"] = str(su)
    res = subprocess.run(
        ["bash", str(SCRIPT), "--run-id", "x"],  # missing other required args
        capture_output=True, text=True, env=env, check=False,
    )
    assert res.returncode == 2


def test_softwareupdate_scripts_include_config_data_flag() -> None:
    for name in ("check.sh", "plan.sh", "apply.sh", "verify.sh"):
        text = (ADAPTER_ROOT / "scripts" / "softwareupdate" / name).read_text(
            encoding="utf-8"
        )
        assert "--include-config-data" in text, name
