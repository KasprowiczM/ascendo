"""Tests for adapters/macos/scripts/mas/apply.sh.

The mutating phase. Everything runs against fakes (mas_fakes.py): no real mas,
no real sudo, no network. Covered:
  1. --dry-run produces planned items, NEVER invokes sudo
  2. real apply runs `mas upgrade` under sudo, with MAS_NO_AUTO_INDEX=1
     delivered THROUGH sudo (env_reset would strip a plain export)
  3. signed-out fail-fast (no sudo invocation)
  4. --filter restricts upgrades (by id and by name)
  5. success only after a second `mas outdated` measurement
  6. probe failure / timeout = unknown (skipped), never "nothing to upgrade"
  7. opt-in user-session retry (default OFF)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from .mas_fakes import mas_env

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "mas" / "apply.sh"

KEKA = "1153157709"
ONEPASSWORD = "1333542190"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _parse(p: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(p.read_text())
    finally:
        sys.path.pop(0)


def _run_apply(fakes: dict, output_dir: Path, run_id: str, *extra: str,
               env_extra: dict | None = None):
    env = dict(os.environ)
    env.update(fakes["env"])
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(SCRIPT),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir),
         *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def _log(fakes: dict, name: str) -> str:
    p = fakes["state"] / name
    return p.read_text() if p.exists() else ""


def _items(sc) -> dict:
    return {i.id: i.status.value for i in sc.items}


def _messages(sc) -> str:
    return "\n".join(m.text or "" for m in sc.messages)


def test_dry_run_emits_planned_items_no_sudo(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, "--dry-run")
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert {i.status.value for i in sc.items} <= {"planned"}
    assert _log(fakes, "sudo.log") == ""
    assert _log(fakes, "upgrades.log") == ""


def test_real_apply_runs_mas_upgrade_under_sudo_with_no_auto_index(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid)
    assert res.returncode == 0, res.stderr

    # CVE-2025-43411: mas upgrade MUST be wrapped in sudo, and
    # MAS_NO_AUTO_INDEX=1 must ride inside the sudo argv (env_reset).
    sudo_lines = _log(fakes, "sudo.log").strip().splitlines()
    assert len(sudo_lines) == 1, sudo_lines
    assert "env MAS_NO_AUTO_INDEX=1" in sudo_lines[0] and "upgrade" in sudo_lines[0]
    # The fake mas saw: under sudo, with the variable present.
    upgrade_calls = [l for l in _log(fakes, "calls.log").splitlines() if "ARGS=upgrade" in l]
    assert upgrade_calls and all(
        "sudo=1" in l and "MAS_NO_AUTO_INDEX=1" in l for l in upgrade_calls), upgrade_calls


def test_askpass_selects_sudo_dash_a(tmp_path):
    fakes = mas_env(tmp_path)
    askpass = tmp_path / "askpass.sh"
    askpass.write_text("#!/bin/sh\necho pw\n")
    askpass.chmod(0o755)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, env_extra={"SUDO_ASKPASS": str(askpass)})
    assert res.returncode == 0, res.stderr
    assert _log(fakes, "sudo.log").startswith("-A env MAS_NO_AUTO_INDEX=1 ")


def test_signed_out_fail_fast_no_sudo(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, env_extra={"FAKE_MAS_LIST_FAIL": "1"})
    assert res.returncode == 0
    sc = _parse(out / rid / "apply__mas.json")
    assert sc.status.value == "failed"
    assert any(i.id == "mas:not-signed-in" for i in sc.items)
    assert _log(fakes, "sudo.log") == ""


@pytest.mark.parametrize("flt", [KEKA, "Keka"])
def test_filter_restricts_to_listed_id_or_name(tmp_path, flt):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, "--filter", flt)
    assert res.returncode == 0, res.stderr
    upg = _log(fakes, "upgrades.log")
    assert KEKA in upg and ONEPASSWORD not in upg
    assert _items(_parse(out / rid / "apply__mas.json")) == {KEKA: "success"}


def test_success_requires_second_measurement_gone(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert _items(sc) == {ONEPASSWORD: "success", KEKA: "success"}
    outdated_calls = [l for l in _log(fakes, "calls.log").splitlines() if "ARGS=outdated" in l]
    assert len(outdated_calls) == 2, outdated_calls   # before + after the upgrade


def test_rc0_but_still_outdated_is_failed(tmp_path):
    """The 2026-09 WhatsApp failure: sudo mas upgrade exits 0, app is skipped."""
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, env_extra={"FAKE_MAS_KEEP_IDS": KEKA})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert _items(sc) == {ONEPASSWORD: "success", KEKA: "failed"}
    assert "still outdated after sudo mas upgrade (rc=0)" in _messages(sc)
    assert sc.status.value == "partial"


def test_user_retry_is_off_by_default(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _run_apply(fakes, out, rid, env_extra={
        "FAKE_MAS_KEEP_IDS": KEKA, "FAKE_MAS_USER_CLEARS": "1"})
    calls = [l for l in _log(fakes, "calls.log").splitlines() if "ARGS=upgrade" in l]
    assert calls and all("sudo=1" in l for l in calls), calls
    assert len(_log(fakes, "sudo.log").splitlines()) == 1


def test_user_retry_opt_in_runs_without_sudo_and_recovers(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, env_extra={
        "ASCENDO_MAS_USER_RETRY": "1",
        "FAKE_MAS_KEEP_IDS": KEKA, "FAKE_MAS_USER_CLEARS": "1"})
    assert res.returncode == 0, res.stderr
    calls = [l for l in _log(fakes, "calls.log").splitlines() if "ARGS=upgrade" in l]
    user_calls = [l for l in calls if "sudo=0" in l]
    assert len(user_calls) == 1 and user_calls[0].endswith(f"ARGS=upgrade {KEKA}"), calls
    assert "MAS_NO_AUTO_INDEX=1" in user_calls[0]
    # sudo was used for the first pass only (one sudo invocation logged)
    assert len(_log(fakes, "sudo.log").splitlines()) == 1
    sc = _parse(out / rid / "apply__mas.json")
    assert _items(sc) == {ONEPASSWORD: "success", KEKA: "success"}
    assert "user-session retry" in _messages(sc)


def test_user_retry_opt_in_still_outdated_is_failed(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _run_apply(fakes, out, rid, env_extra={
        "ASCENDO_MAS_USER_RETRY": "1", "FAKE_MAS_KEEP_IDS": KEKA})   # user retry doesn't help
    sc = _parse(out / rid / "apply__mas.json")
    assert _items(sc)[KEKA] == "failed"


def test_outdated_probe_failure_is_unknown_not_nothing_to_upgrade(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, env_extra={"FAKE_MAS_OUTDATED_FAIL": "1"})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert _items(sc) == {"mas:outdated-probe": "skipped"}
    assert sc.status.value == "skipped"
    text = _messages(sc)
    assert "unknown" in text and "outdated broke" in text
    assert "nothing to upgrade" not in text
    assert _log(fakes, "sudo.log") == ""


def test_outdated_probe_timeout_is_unknown(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, env_extra={
        "FAKE_MAS_OUTDATED_SLEEP": "30", "ASCENDO_MAS_CHECK_TIMEOUT": "1",
        "ASCENDO_KILL_AFTER": "1"})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert _items(sc) == {"mas:outdated-probe": "skipped"}
    assert "timed out after 1s" in _messages(sc)
    assert _log(fakes, "sudo.log") == ""


def test_remeasure_failure_after_clean_upgrade_is_skipped_unknown(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, env_extra={"FAKE_MAS_OUTDATED_FAIL_AFTER_UPGRADE": "1"})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert set(_items(sc).values()) == {"skipped"}
    assert "outcome unknown" in _messages(sc)


def test_remeasure_failure_after_failed_upgrade_is_failed(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _run_apply(fakes, out, rid, env_extra={
        "FAKE_MAS_OUTDATED_FAIL_AFTER_UPGRADE": "1", "FAKE_MAS_UPGRADE_RC": "5"})
    sc = _parse(out / rid / "apply__mas.json")
    assert set(_items(sc).values()) == {"failed"}


def test_upgrade_timeout_marks_failed_with_reason(tmp_path):
    fakes = mas_env(tmp_path, outdated_text=f"{KEKA} Keka (1.4.2 -> 1.4.3)\n")
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, env_extra={
        "FAKE_MAS_UPGRADE_SLEEP": "30", "ASCENDO_MAS_UPGRADE_TIMEOUT": "1",
        "ASCENDO_KILL_AFTER": "1"})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert _items(sc) == {KEKA: "failed"}
    assert "timed out after 1s" in _messages(sc)


def test_apply_no_outdated_emits_no_items_no_sudo(tmp_path):
    """Probe OK and empty: a genuine "nothing to upgrade" (no sudo)."""
    fakes = mas_env(tmp_path, outdated_text="")
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert sc.items == []
    assert "nothing to upgrade" in _messages(sc)
    assert _log(fakes, "sudo.log") == ""


def test_sanitised_timeout_env_falls_back(tmp_path):
    """Garbage in ASCENDO_MAS_*_TIMEOUT must not break the run."""
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run_apply(fakes, out, rid, env_extra={
        "ASCENDO_MAS_CHECK_TIMEOUT": "abc", "ASCENDO_MAS_UPGRADE_TIMEOUT": "0"})
    assert res.returncode == 0, res.stderr
    assert _items(_parse(out / rid / "apply__mas.json")) == {ONEPASSWORD: "success", KEKA: "success"}

