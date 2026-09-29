"""Unit tests for lib/su_classify.py (port of macOS_updates system_updates.py)
and the check/plan/verify phases that use it."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from .su_fakes import su_env

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
LIB = ADAPTER_ROOT / "lib"
SCRIPTS = ADAPTER_ROOT / "scripts" / "softwareupdate"
FIX = ADAPTER_ROOT / "tests" / "fixtures" / "softwareupdate"
US = "\x1f"

sys.path.insert(0, str(LIB))
import su_classify  # noqa: E402


MIXED = (FIX / "mixed-with-major.txt").read_text()


def test_parse_and_classify_softwareupdate_list():
    items = su_classify.parse_softwareupdate_list(MIXED)
    assert [i["label"] for i in items] == [
        "Safari27.0TahoeAuto-27.0", "macOS Tahoe 26.7-25G229", "macOS 27-26A428"]
    assert items[0]["title"] == "Safari" and items[0]["version"] == "27.0"
    assert items[0]["size_kib"] == 249465 and items[0]["recommended"] is True
    assert items[0]["restart"] is False and items[1]["restart"] is True
    cl = su_classify.classify_updates(items, "26.6.2")
    assert [x["label"] for x in cl["other"]] == ["Safari27.0TahoeAuto-27.0"]
    assert [x["label"] for x in cl["same_major"]] == ["macOS Tahoe 26.7-25G229"]
    assert [x["label"] for x in cl["major"]] == ["macOS 27-26A428"]


def test_no_new_software_yields_no_items():
    assert su_classify.parse_softwareupdate_list("Finding available software\nNo new software available.\n") == []
    assert su_classify.parse_softwareupdate_list("") == []


def test_older_or_equal_os_version_is_same_major_not_major():
    items = su_classify.parse_softwareupdate_list(MIXED)
    cl = su_classify.classify_updates(items, "27.1")
    assert cl["major"] == []
    assert len(cl["same_major"]) == 2


def test_unparseable_current_version_fails_safe_to_major():
    items = su_classify.parse_softwareupdate_list(MIXED)
    cl = su_classify.classify_updates(items, "")
    assert [x["label"] for x in cl["major"]] == ["macOS Tahoe 26.7-25G229", "macOS 27-26A428"]
    assert [x["label"] for x in cl["other"]] == ["Safari27.0TahoeAuto-27.0"]


def test_os_major_falls_back_to_title_when_version_missing():
    text = ("* Label: macOS Sequoia 15.1-24B83\n"
            "\tTitle: macOS Sequoia 15.1, Size: 1K, Recommended: YES, Action: restart,\n")
    items = su_classify.parse_softwareupdate_list(text)
    assert items[0]["version"] == ""
    assert su_classify.classify_updates(items, "14.7")["major"][0]["label"] == "macOS Sequoia 15.1-24B83"
    assert su_classify.classify_updates(items, "15.0")["same_major"][0]["label"] == "macOS Sequoia 15.1-24B83"


def test_cli_records_keep_empty_fields_and_order():
    text = ("* Label: Weird Thing-1\n\tTitle: Weird, Size: 1K, Recommended: NO,\n" +
            MIXED.split("software:\n", 1)[1])
    res = subprocess.run([sys.executable, str(LIB / "su_classify.py"), "--current", "26.6.2"],
                         input=text, capture_output=True, text=True, check=True)
    rows = [ln.split(US) for ln in res.stdout.splitlines()]
    assert [r[0] for r in rows] == ["SAME", "OTHER", "OTHER", "MAJOR"]
    weird = next(r for r in rows if r[1] == "Weird Thing-1")
    assert weird == ["OTHER", "Weird Thing-1", "Weird", "", "0", "0"]      # empty version kept
    assert next(r for r in rows if r[1] == "macOS 27-26A428")[4] == "1"


# -- check / plan / verify phases --------------------------------------------------


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


def _run(script: str, ctx: dict, tmp_path: Path, *extra: str):
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = subprocess.run(
        ["bash", str(SCRIPTS / script), "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out), *extra],
        capture_output=True, text=True, env=ctx["env"], check=False,
    )
    phase = script.split(".")[0]
    return res, out, rid, _parse(out / rid / f"{phase}__softwareupdate.json")


@pytest.mark.parametrize("script", ["check.sh", "plan.sh"])
def test_check_plan_major_is_skipped_with_token_not_planned(tmp_path, script):
    ctx = su_env(tmp_path, "mixed-with-major.txt")
    res, _, _, sc = _run(script, ctx, tmp_path)
    assert res.returncode == 0, res.stderr
    st = {i.id: i.status.value for i in sc.items}
    assert st == {"Safari27.0TahoeAuto-27.0": "planned",
                  "macOS Tahoe 26.7-25G229": "planned",
                  "macOS 27-26A428": "skipped"}
    major = next(i for i in sc.items if i.id == "macOS 27-26A428")
    assert major.current_version == "26.6.2" and major.target_version == "27"
    assert any("macOS 27-26A428: skipped" in m.text
               and "ASCENDO-ACTION-REQUIRED reason=major_upgrade cur=26.6.2 cand=27" in m.text
               for m in sc.messages)
    assert sc.needs_reboot is True          # Tahoe 26.7 (same major) restarts


@pytest.mark.parametrize("script", ["check.sh", "plan.sh"])
def test_check_plan_major_only_does_not_set_needs_reboot(tmp_path, script):
    listing = ("Software Update found the following new or updated software:\n"
               "* Label: macOS 27-26A428\n"
               "\tTitle: macOS 27, Version: 27, Size: 1K, Recommended: YES, Action: restart,\n")
    ctx = su_env(tmp_path, listing_text=listing)
    res, _, _, sc = _run(script, ctx, tmp_path)
    assert [i.status.value for i in sc.items] == ["skipped"]
    assert sc.needs_reboot in (False, None)


def test_check_unknown_current_os_never_plans_an_os_update(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt", cur_os="")
    res, _, _, sc = _run("check.sh", ctx, tmp_path)
    st = {i.id: i.status.value for i in sc.items}
    assert st["macOS Tahoe 26.7-25G229"] == "skipped" and st["macOS 27-26A428"] == "skipped"
    assert st["Safari27.0TahoeAuto-27.0"] == "planned"


def test_check_major_planned_when_allowed(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt", ASCENDO_ALLOW_MAJOR_UPGRADE="1")
    res, _, _, sc = _run("check.sh", ctx, tmp_path)
    assert {i.id: i.status.value for i in sc.items}["macOS 27-26A428"] == "planned"


def _apply_sidecar(out: Path, rid: str, ids: list, *, needs_reboot: bool = False) -> None:
    d = out / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "apply__softwareupdate.json").write_text(json.dumps({
        "schema": "ascendo/v1", "phase": "apply", "category": "softwareupdate",
        "needs_reboot": needs_reboot,
        "items": [{"id": i, "status": "success", "resolved_version": "1.0"} for i in ids],
    }))


def _verify(ctx: dict, tmp_path: Path, ids: list, *, needs_reboot: bool = False):
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    _apply_sidecar(out, rid, ids, needs_reboot=needs_reboot)
    res = subprocess.run(
        ["bash", str(SCRIPTS / "verify.sh"), "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out)],
        capture_output=True, text=True, env=ctx["env"], check=False)
    assert res.returncode == 0, res.stderr
    return _parse(out / rid / "verify__softwareupdate.json")


def test_verify_label_with_spaces_gone_is_success(tmp_path):
    ctx = su_env(tmp_path, "no-updates.txt")
    sc = _verify(ctx, tmp_path, ["macOS Tahoe 26.7-25G229"])
    assert {i.id: i.status.value for i in sc.items} == {"macOS Tahoe 26.7-25G229": "success"}


def test_verify_still_listed_non_restart_is_failed(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt")
    sc = _verify(ctx, tmp_path, ["Safari17.4-17.4"])
    assert {i.id: i.status.value for i in sc.items} == {"Safari17.4-17.4": "failed"}


def test_verify_pending_restart_label_after_reboot_flag_is_triggered_not_failed(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt")
    sc = _verify(ctx, tmp_path, ["macOS Tahoe 26.7-25G229"], needs_reboot=True)
    assert {i.id: i.status.value for i in sc.items} == {"macOS Tahoe 26.7-25G229": "triggered"}


def test_verify_list_failure_is_unknown_skipped_never_success(tmp_path):
    ctx = su_env(tmp_path, "no-updates.txt", FAKE_SU_LIST_FAIL="1")
    sc = _verify(ctx, tmp_path, ["Safari17.4-17.4"])
    assert {i.id: i.status.value for i in sc.items} == {"Safari17.4-17.4": "skipped"}
    assert any("unknown" in m.text for m in sc.messages)
