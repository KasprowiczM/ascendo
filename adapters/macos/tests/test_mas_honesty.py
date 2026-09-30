"""mas check / plan / verify honesty: a probe that fails or times out is UNKNOWN
(`skipped` with a reason) -- never "nothing pending" and never `success`.

All against the shared fakes (mas_fakes.py); no real mas/sudo/network.
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

from .mas_fakes import mas_env

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ADAPTER_ROOT / "scripts" / "mas"


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


def _run(script: str, fakes: dict, out: Path, rid: str, *extra: str,
         env_extra: dict | None = None):
    env = dict(os.environ)
    env.update(fakes["env"])
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(SCRIPTS / script), "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out), *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def _msgs(sc) -> str:
    return "\n".join(m.text or "" for m in sc.messages)


@pytest.mark.parametrize("script,phase", [("check.sh", "check"), ("plan.sh", "plan")])
def test_outdated_probe_failure_is_not_clean(tmp_path, script, phase):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run(script, fakes, out, rid, env_extra={"FAKE_MAS_OUTDATED_FAIL": "1"})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / f"{phase}__mas.json")
    assert [(i.id, i.status.value) for i in sc.items] == [("mas:outdated-probe", "skipped")]
    assert sc.status.value == "skipped"          # not success
    assert "up_to_date" not in {i.status.value for i in sc.items}
    text = _msgs(sc)
    assert "mas outdated exited 1" in text and "outdated broke" in text and "unknown" in text


@pytest.mark.parametrize("script,phase", [("check.sh", "check"), ("plan.sh", "plan")])
def test_outdated_probe_timeout_is_reported_with_the_bound(tmp_path, script, phase):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run(script, fakes, out, rid, env_extra={
        "FAKE_MAS_OUTDATED_SLEEP": "30", "ASCENDO_MAS_CHECK_TIMEOUT": "1",
        "ASCENDO_KILL_AFTER": "1"})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / f"{phase}__mas.json")
    assert [i.id for i in sc.items] == ["mas:outdated-probe"]
    assert "timed out after 1s" in _msgs(sc)


def test_sign_in_probe_timeout_is_unknown_not_signed_out(tmp_path):
    """`mas list` hanging is not "not signed in": distinct item, no sign-in advice."""
    fakes = mas_env(tmp_path)
    # a `list` that hangs: make the fake sleep via a wrapper env knob
    (Path(fakes["env"]["FAKE_MAS_STATE"]) / "list.txt").write_text("")
    out, rid = tmp_path / "out", str(uuid.uuid4())
    # Reuse OUTDATED_SLEEP-style knob for list via a tiny wrapper mas
    wrapper = tmp_path / "slow_mas"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = list ]; then sleep 30; exit 0; fi\n"
        f"exec {fakes['env']['MAS_BIN']} \"$@\"\n")
    wrapper.chmod(0o755)
    fakes["env"]["MAS_BIN"] = str(wrapper)
    res = _run("check.sh", fakes, out, rid, env_extra={
        "ASCENDO_MAS_CHECK_TIMEOUT": "1", "ASCENDO_KILL_AFTER": "1"})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__mas.json")
    assert [(i.id, i.status.value) for i in sc.items] == [("mas:probe-unknown", "skipped")]
    assert "Not signed into" not in _msgs(sc)


def test_check_default_uses_plain_outdated_never_accurate(tmp_path):
    fakes = mas_env(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _run("check.sh", fakes, out, rid)
    calls = (fakes["state"] / "calls.log").read_text()
    assert "--accurate" not in calls
    assert "ARGS=outdated\n" in calls


def test_check_with_empty_outdated_and_healthy_probe_is_clean(tmp_path):
    """Control: a SUCCESSFUL empty probe is a genuine "nothing pending"."""
    fakes = mas_env(tmp_path, outdated_text="")
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run("check.sh", fakes, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__mas.json")
    assert sc.status.value == "success"
    assert {i.status.value for i in sc.items} == {"up_to_date"}


# -- verify ------------------------------------------------------------------


def _apply_sidecar(run_dir: Path, ids: list[str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "apply__mas.json").write_text(json.dumps({
        "schema": "ascendo/v1", "phase": "apply", "category": "mas",
        "items": [{"id": i, "status": "success", "resolved_version": "1.0.0"} for i in ids],
    }))


def test_verify_cannot_remeasure_is_skipped_not_success(tmp_path):
    fakes = mas_env(tmp_path, outdated_text="")
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _apply_sidecar(out / rid, ["1333542190", "1153157709"])
    res = _run("verify.sh", fakes, out, rid, env_extra={"FAKE_MAS_OUTDATED_FAIL": "1"})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "verify__mas.json")
    assert {i.id: i.status.value for i in sc.items} == {
        "1333542190": "skipped", "1153157709": "skipped"}
    assert "cannot re-measure" in _msgs(sc)


def test_verify_measured_gone_is_success(tmp_path):
    fakes = mas_env(tmp_path, outdated_text="")
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _apply_sidecar(out / rid, ["1333542190"])
    res = _run("verify.sh", fakes, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "verify__mas.json")
    assert {i.id: i.status.value for i in sc.items} == {"1333542190": "success"}
