"""End-to-end tests for adapters/macos/scripts/mas/check.sh.

Each test invokes the script with MAS_BIN pointing to a fake script that
emits captured fixture output, then validates the produced sidecar
through Pydantic parse_sidecar().
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
SCRIPT = ADAPTER_ROOT / "scripts" / "mas" / "check.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_mas(tmp_path: Path, behaviour: str) -> Path:
    """Make a fake mas binary with one of:
        signed_out    -> `list`/`outdated` exit 1
        signed_in     -> `list` returns mas-list.txt, `outdated` returns mas-outdated.txt
        no_outdated   -> `list` returns mas-list.txt, `outdated` returns empty
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


def _run_check(fake_mas: Path, output_dir: Path, run_id: str, *extra: str):
    env = dict(os.environ)
    env["MAS_BIN"] = str(fake_mas)
    return subprocess.run(
        ["bash", str(SCRIPT),
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


def test_signed_in_emits_planned_and_up_to_date_items(tmp_path):
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_check(fake, out, rid)
    assert res.returncode == 0, res.stderr
    sidecar = out / rid / "check__mas.json"
    assert sidecar.is_file(), f"sidecar missing\nstdout: {res.stdout}\nstderr: {res.stderr}"

    sc = _parse_sidecar(sidecar)
    assert sc.phase.value == "check"
    assert sc.category.value == "mas"
    statuses = [i.status.value for i in sc.items]
    assert "planned" in statuses        # outdated -> planned
    assert "up_to_date" in statuses     # installed-but-not-outdated -> up_to_date


def test_signed_out_emits_failed_item(tmp_path):
    fake = _make_fake_mas(tmp_path, "signed_out")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_check(fake, out, rid)
    # Phase exits 0; the FAILURE shows up in sidecar status, not exit code
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "check__mas.json")
    assert sc.status.value == "failed"
    assert any(i.id == "mas:not-signed-in" for i in sc.items)


def test_no_outdated_emits_only_up_to_date(tmp_path):
    fake = _make_fake_mas(tmp_path, "no_outdated")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_check(fake, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "check__mas.json")
    statuses = {i.status.value for i in sc.items}
    assert statuses <= {"up_to_date"}


def test_check_with_dry_run_flag_is_no_op_for_check(tmp_path):
    """--dry-run must be accepted (build_argv passes it for all phases)
    but check is already side-effect-free, so behaviour is identical."""
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    env = dict(os.environ); env["MAS_BIN"] = str(fake)
    res = subprocess.run(
        ["bash", str(SCRIPT),
         "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out),
         "--dry-run"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert res.returncode == 0, res.stderr
    sidecar = out / rid / "check__mas.json"
    assert sidecar.is_file()
    # --dry-run must NOT silently short-circuit item emission for check
    sc = _parse_sidecar(sidecar)
    statuses = [i.status.value for i in sc.items]
    assert "planned" in statuses
    assert "up_to_date" in statuses


def test_filter_limits_planned_items(tmp_path):
    """--filter <csv> restricts planned items to listed ids."""
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    env = dict(os.environ); env["MAS_BIN"] = str(fake)
    res = subprocess.run(
        ["bash", str(SCRIPT),
         "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out),
         "--filter", "1153157709"],   # only Keka
        capture_output=True, text=True, env=env, check=False,
    )
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "check__mas.json")
    planned = [i for i in sc.items if i.status.value == "planned"]
    assert all(i.id == "1153157709" for i in planned)
