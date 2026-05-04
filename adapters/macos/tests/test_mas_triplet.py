"""Integration tests for adapters/macos/scripts/mas/{plan,verify,cleanup}.sh.

Each test invokes the script with MAS_BIN pointing to a fake script that
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
SCRIPTS_MAS = ADAPTER_ROOT / "scripts" / "mas"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_mas(tmp_path: Path, behaviour: str) -> Path:
    """Make a fake mas binary.

    behaviours:
      signed_out   -> all subcommands exit 1
      signed_in    -> list returns mas-list.txt, outdated returns mas-outdated.txt
      no_outdated  -> list returns mas-list.txt, outdated exits 0 (empty)
    """
    p = tmp_path / "fake_mas"
    list_text = (FIX / "mas-list.txt").read_text()
    out_text = (FIX / "mas-outdated.txt").read_text()
    if behaviour == "signed_out":
        body = "#!/usr/bin/env bash\nexit 1\n"
    elif behaviour == "signed_in":
        body = (
            "#!/usr/bin/env bash\n"
            "case \"$1\" in\n"
            f"  list)     cat <<'EOF_LIST'\n{list_text}EOF_LIST\n            ;;\n"
            f"  outdated) cat <<'EOF_OUT'\n{out_text}EOF_OUT\n            ;;\n"
            "  version)  echo '4.3.0' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
    elif behaviour == "no_outdated":
        body = (
            "#!/usr/bin/env bash\n"
            "case \"$1\" in\n"
            f"  list)     cat <<'EOF_LIST'\n{list_text}EOF_LIST\n            ;;\n"
            "  outdated) exit 0 ;;\n"
            "  version)  echo '4.3.0' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
    else:
        raise ValueError(behaviour)
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _run_script(script: Path, fake_mas: Path, output_dir: Path, run_id: str, *extra: str):
    env = dict(os.environ)
    env["MAS_BIN"] = str(fake_mas)
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
    """plan.sh must emit planned items but NO up_to_date items."""
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_script(SCRIPTS_MAS / "plan.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr
    sidecar = out / rid / "plan__mas.json"
    assert sidecar.is_file(), f"sidecar missing\nstdout: {res.stdout}\nstderr: {res.stderr}"

    sc = _parse_sidecar(sidecar)
    assert sc.phase.value == "plan"
    assert sc.category.value == "mas"
    statuses = {i.status.value for i in sc.items}
    assert "planned" in statuses
    assert "up_to_date" not in statuses


def test_plan_no_outdated_emits_success_no_items(tmp_path):
    """plan.sh with nothing outdated: success sidecar, zero items."""
    fake = _make_fake_mas(tmp_path, "no_outdated")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_script(SCRIPTS_MAS / "plan.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "plan__mas.json")
    assert sc.items == [] or all(i.status.value != "up_to_date" for i in sc.items)


# ---------------------------------------------------------------------------
# verify.sh tests
# ---------------------------------------------------------------------------

def _make_apply_sidecar(run_dir: Path, success_ids: list[str]) -> Path:
    """Write a minimal apply__mas.json with given ids marked success."""
    run_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = run_dir / "apply__mas.json"
    items = [{"id": id_, "status": "success", "resolved_version": "1.0.0"} for id_ in success_ids]
    # Minimal ascendo/v1 sidecar structure enough for verify.sh's python3 inline
    data = {
        "schema": "ascendo/v1",
        "phase": "apply",
        "category": "mas",
        "items": items,
    }
    sidecar_path.write_text(json.dumps(data))
    return sidecar_path


def test_verify_marks_success_when_no_longer_outdated(tmp_path):
    """verify.sh: item that was applied and is no longer outdated -> success."""
    # fake mas: signed in, no outdated apps (all applied successfully)
    fake = _make_fake_mas(tmp_path, "no_outdated")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    _make_apply_sidecar(out / rid, ["1333542190"])  # 1Password
    res = _run_script(SCRIPTS_MAS / "verify.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "verify__mas.json")
    assert sc.phase.value == "verify"
    success_ids = [i.id for i in sc.items if i.status.value == "success"]
    assert "1333542190" in success_ids


def test_verify_marks_failed_when_still_outdated(tmp_path):
    """verify.sh: item that is still outdated after apply -> failed."""
    # fake mas: signed in, both packages still outdated
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    _make_apply_sidecar(out / rid, ["1333542190", "1153157709"])
    res = _run_script(SCRIPTS_MAS / "verify.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "verify__mas.json")
    failed_ids = [i.id for i in sc.items if i.status.value == "failed"]
    assert "1333542190" in failed_ids
    assert "1153157709" in failed_ids


def test_verify_soft_noop_without_apply_sidecar(tmp_path):
    """verify.sh: when apply sidecar absent, emits success sidecar with info message."""
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    # Do NOT create apply sidecar
    res = _run_script(SCRIPTS_MAS / "verify.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "verify__mas.json")
    assert sc.phase.value == "verify"
    assert sc.status.value == "success"
    assert sc.items == []


# ---------------------------------------------------------------------------
# cleanup.sh tests
# ---------------------------------------------------------------------------

def test_cleanup_emits_success_sidecar_with_info_message(tmp_path):
    """cleanup.sh: no-op, emits success sidecar + one info message + zero items."""
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_script(SCRIPTS_MAS / "cleanup.sh", fake, out, rid)
    assert res.returncode == 0, res.stderr
    sidecar = out / rid / "cleanup__mas.json"
    assert sidecar.is_file(), f"sidecar missing\nstdout: {res.stdout}\nstderr: {res.stderr}"

    sc = _parse_sidecar(sidecar)
    assert sc.phase.value == "cleanup"
    assert sc.status.value == "success"
    assert sc.items == []
    assert len(sc.messages) >= 1
    assert any("cleanup" in (m.text or "").lower() or "caches" in (m.text or "").lower()
               for m in sc.messages)
