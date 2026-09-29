"""--filter routing: mas matches id OR name, softwareupdate takes many labels,
managers only forward --filter to the phases whose scripts act on it.

Background (Task 2 of the macOS_updates parity plan): the dashboard derives
an inclusion filter from the user's exclusions. check/plan emit the mas
DISPLAY NAME as the item id while apply matched numeric ids, so with any
exclusion configured ``mas apply`` upgraded nothing and exited 0. The
softwareupdate manager kept only the FIRST filter entry (usually a brew/mas
name -> "filter LABEL did not match"), and verify/cleanup scripts crashed
on ``--filter`` ("unknown arg").
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.run import Phase, RunInfo, Trigger
from ascendo_macos.managers.brew import BrewManager
from ascendo_macos.managers.mas import MasManager
from ascendo_macos.managers.softwareupdate import SoftwareUpdateManager

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ADAPTER_ROOT / "scripts"
LIB = ADAPTER_ROOT / "lib"
FIX = ADAPTER_ROOT / "tests" / "fixtures"

MAS_OUTDATED = (FIX / "mas-outdated.txt").read_text()
# 1333542190 = "1Password 7 — Password Manager"; 1153157709 = "Keka"
ONEPASSWORD = "1Password 7 — Password Manager"


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


# -- fakes -----------------------------------------------------------------


def _fake_mas(tmp_path: Path, upgrade_log: Path | None = None) -> Path:
    list_text = (FIX / "mas-list.txt").read_text()
    upg = f"echo \"$@\" >> {upgrade_log}\n" if upgrade_log else ""
    p = tmp_path / "fake_mas"
    p.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$1\" in\n"
        f"  list) cat <<'EOF_L'\n{list_text}EOF_L\n ;;\n"
        f"  outdated) cat <<'EOF_O'\n{MAS_OUTDATED}EOF_O\n ;;\n"
        "  version) echo 4.3.0 ;;\n"
        f"  upgrade) {upg} ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    os.chmod(p, 0o755)
    return p


def _fake_sudo(tmp_path: Path, log: Path) -> Path:
    p = tmp_path / "bin" / "sudo"
    p.parent.mkdir(exist_ok=True)
    p.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {log}\n"
        "if [ \"$1\" = '-A' ]; then shift; fi\n"
        "\"$@\"\n"
    )
    os.chmod(p, 0o755)
    return p


def _fake_su(tmp_path: Path, fixture: str = "incremental-updates.txt") -> Path:
    text = (FIX / "softwareupdate" / fixture).read_text()
    p = tmp_path / "fake_softwareupdate"
    p.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = '--help' ] || [ \"$1\" = '-h' ]; then echo fake-su; exit 0; fi\n"
        "if [ \"$1\" = '-l' ] || [ \"$1\" = '--list' ]; then\n"
        f"    cat <<'EOF_SU'\n{text}\nEOF_SU\n    exit 0\n"
        "fi\n"
        "echo \"simulated install: $@\"\nexit 0\n"
    )
    os.chmod(p, 0o755)
    return p


def _run(script: Path, env_extra: dict, out: Path, rid: str, *extra: str):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(script), "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out), *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def _mas_env(tmp_path: Path, upgrade_log: Path | None = None):
    sudo_log = tmp_path / "sudo.log"
    sudo = _fake_sudo(tmp_path, sudo_log)
    return {
        "MAS_BIN": str(_fake_mas(tmp_path, upgrade_log)),
        "PATH": f"{sudo.parent}:{os.environ['PATH']}",
    }, sudo_log


# -- mas_filter_match (helper) ---------------------------------------------


def _match(csv: str, id_: str, name: str = "") -> bool:
    res = subprocess.run(
        ["bash", "-c", f'. "{LIB}/ascendo_mas.sh"; mas_filter_match "$1" "$2" "$3"',
         "_", csv, id_, name],
        capture_output=True, text=True,
    )
    return res.returncode == 0


def test_mas_filter_match_semantics():
    assert _match("", "1", "A")                       # empty = no filter
    assert _match("1153157709", "1153157709", "Keka")  # id
    assert _match("Keka", "1153157709", "Keka")        # name
    assert _match("x,Keka,y", "1153157709", "Keka")    # name in the middle
    assert not _match("Kek", "1153157709", "Keka")     # no substring match
    assert not _match("10", "1011", "")                # id not substring
    assert _match(ONEPASSWORD, "1333542190", ONEPASSWORD)  # spaces/em-dash
    assert not _match("Keka", "1", "")                 # name given only in csv


# -- mas scripts: id OR name -------------------------------------------------


@pytest.mark.parametrize("flt", ["1153157709", "Keka"])
def test_mas_apply_dry_run_filter_by_id_and_by_name(tmp_path, flt):
    env, sudo_log = _mas_env(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "mas" / "apply.sh", env, out, rid, "--dry-run", "--filter", flt)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert [i.id for i in sc.items] == ["1153157709"], [i.id for i in sc.items]
    assert not sudo_log.exists() or sudo_log.read_text() == ""


def test_mas_apply_real_filter_by_name_upgrades_only_that_app(tmp_path):
    """The reported bug: with a NAME filter mas apply upgraded nothing."""
    upgrade_log = tmp_path / "upgrade.log"
    env, _ = _mas_env(tmp_path, upgrade_log)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "mas" / "apply.sh", env, out, rid, "--filter", "Keka")
    assert res.returncode == 0, res.stderr
    upg = upgrade_log.read_text() if upgrade_log.exists() else ""
    assert "1153157709" in upg
    assert "1333542190" not in upg
    sc = _parse(out / rid / "apply__mas.json")
    assert [(i.id, i.status.value) for i in sc.items] == [("1153157709", "success")]


def test_mas_apply_filter_name_with_spaces_and_dash(tmp_path):
    upgrade_log = tmp_path / "upgrade.log"
    env, _ = _mas_env(tmp_path, upgrade_log)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "mas" / "apply.sh", env, out, rid,
               "--filter", f"{ONEPASSWORD},SomeOtherApp")
    assert res.returncode == 0, res.stderr
    upg = upgrade_log.read_text()
    assert "1333542190" in upg and "1153157709" not in upg


@pytest.mark.parametrize("script", ["check.sh", "plan.sh"])
@pytest.mark.parametrize("flt", ["1153157709", "Keka"])
def test_mas_check_plan_filter_by_id_and_name(tmp_path, script, flt):
    env, _ = _mas_env(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "mas" / script, env, out, rid, "--filter", flt)
    assert res.returncode == 0, res.stderr
    phase = script.split(".")[0]
    sc = _parse(out / rid / f"{phase}__mas.json")
    planned = [i for i in sc.items if i.status.value == "planned"]
    assert len(planned) == 1
    # check emits the display name as the item id, plan the numeric id.
    assert planned[0].id == ("Keka" if phase == "check" else "1153157709")


@pytest.mark.parametrize("script", ["verify.sh", "cleanup.sh"])
def test_mas_verify_cleanup_tolerate_filter(tmp_path, script):
    env, _ = _mas_env(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "mas" / script, env, out, rid, "--filter", "Keka,foo")
    assert res.returncode == 0, res.stderr
    assert (out / rid / f"{script.split('.')[0]}__mas.json").is_file()


# -- softwareupdate scripts --------------------------------------------------


def _su_env(tmp_path: Path, fixture: str = "incremental-updates.txt"):
    sudo_log = tmp_path / "sudo.log"
    sudo = _fake_sudo(tmp_path, sudo_log)
    return {
        "SU_BIN": str(_fake_su(tmp_path, fixture)),
        "PATH": f"{sudo.parent}:{os.environ['PATH']}",
    }, sudo_log


def test_su_apply_dry_run_matches_every_label_in_csv(tmp_path):
    env, _ = _su_env(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "softwareupdate" / "apply.sh", env, out, rid, "--dry-run",
               "--filter", "Copilot,Safari17.4-17.4,XProtectPlistConfigData_10_15-2174")
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__softwareupdate.json")
    assert sorted(i.id for i in sc.items) == [
        "Safari17.4-17.4", "XProtectPlistConfigData_10_15-2174",
    ]


def test_su_apply_second_label_alone_is_matched(tmp_path):
    """The old manager kept only the FIRST entry; a later label must work."""
    env, _ = _su_env(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "softwareupdate" / "apply.sh", env, out, rid, "--dry-run",
               "--filter", "XProtectPlistConfigData_10_15-2174")
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__softwareupdate.json")
    assert [i.id for i in sc.items] == ["XProtectPlistConfigData_10_15-2174"]


def test_su_apply_real_passes_all_matched_labels_and_R(tmp_path):
    env, sudo_log = _su_env(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "softwareupdate" / "apply.sh", env, out, rid,
               "--filter", "Safari17.4-17.4,XProtectPlistConfigData_10_15-2174,Nope-1")
    assert res.returncode == 0, res.stderr
    lines = sudo_log.read_text().strip().splitlines()
    inst = [ln for ln in lines if " -i " in (" " + ln + " ")]
    assert inst, lines
    line = inst[-1]
    assert "Safari17.4-17.4" in line and "XProtectPlistConfigData_10_15-2174" in line
    assert "Nope-1" not in line       # only PENDING labels are installed
    assert " -R " in (" " + line + " ")
    assert " -ia " not in line and " -a " not in (" " + line + " ")


def test_su_apply_app_names_select_nothing_and_never_install(tmp_path):
    """A filter of brew/mas app names must not degrade to 'install all'."""
    env, sudo_log = _su_env(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "softwareupdate" / "apply.sh", env, out, rid,
               "--filter", "Copilot,Amphetamine")
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__softwareupdate.json")
    assert sc.items == []
    assert any("nothing selected" in m.text for m in sc.messages)
    assert not sudo_log.exists() or sudo_log.read_text() == ""


@pytest.mark.parametrize("script", ["check.sh", "plan.sh", "verify.sh", "cleanup.sh"])
def test_su_readonly_and_cleanup_scripts_tolerate_filter(tmp_path, script):
    env, _ = _su_env(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPTS / "softwareupdate" / script, env, out, rid, "--filter", "x,y")
    assert res.returncode == 0, res.stderr
    assert (out / rid / f"{script.split('.')[0]}__softwareupdate.json").is_file()


# -- managers: --filter only for phases that act on it -----------------------


def _run_info() -> RunInfo:
    from datetime import datetime, timezone

    return RunInfo(
        id=uuid.uuid4(), trigger=Trigger.CLI, profile="full", dry_run=False,
        started_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )


def _argv(mgr, phase: Phase, flt, tmp_path: Path, **kw):
    script = mgr._scripts_dir / mgr.SCRIPT_BY_PHASE[phase]
    return mgr._build_argv(
        bash="/bin/bash", script_path=script, run=_run_info(),
        output_dir=tmp_path, item_filter=flt, phase=phase, **kw,
    )


def _mas_mgr(tmp_path):
    return MasManager(scripts_dir=SCRIPTS, lib_dir=LIB, elevation=MagicMock())


def _su_mgr():
    return SoftwareUpdateManager(scripts_dir=SCRIPTS, lib_dir=LIB, elevation=MagicMock())


def _brew_mgr():
    return BrewManager(scripts_dir=SCRIPTS, lib_dir=LIB)


@pytest.mark.parametrize("phase,expected", [
    (Phase.CHECK, True), (Phase.PLAN, True), (Phase.APPLY, True),
    (Phase.VERIFY, False), (Phase.CLEANUP, False),
])
def test_mas_and_brew_forward_filter_only_where_scripts_act_on_it(tmp_path, phase, expected):
    for mgr in (_mas_mgr(tmp_path), _brew_mgr()):
        argv = _argv(mgr, phase, ["Keka", "1Password"], tmp_path)
        assert ("--filter" in argv) is expected, (type(mgr).__name__, phase)
        if expected:
            assert argv[argv.index("--filter") + 1] == "Keka,1Password"


def test_mas_build_argv_without_explicit_phase_infers_it_from_the_script(tmp_path):
    mgr = _mas_mgr(tmp_path)
    script = mgr._scripts_dir / "mas" / "verify.sh"
    argv = mgr._build_argv(
        bash="/bin/bash", script_path=script, run=_run_info(),
        output_dir=tmp_path, item_filter=["Keka"],
    )
    assert "--filter" not in argv


@pytest.mark.parametrize("phase", [Phase.CHECK, Phase.PLAN, Phase.VERIFY, Phase.CLEANUP])
def test_su_manager_never_sends_filter_outside_apply(tmp_path, phase):
    argv = _argv(_su_mgr(), phase, ["Safari17.4-17.4"], tmp_path)
    assert "--filter" not in argv


def test_su_manager_forwards_all_labels_not_just_the_first(tmp_path):
    argv = _argv(_su_mgr(), Phase.APPLY,
                 ["Copilot", "Safari17.4-17.4", "macOS Sequoia 15.1-24B83"], tmp_path)
    assert argv[argv.index("--filter") + 1] == "Safari17.4-17.4,macOS Sequoia 15.1-24B83"


def test_su_manager_app_names_only_are_kept_raw_so_script_selects_nothing(tmp_path):
    argv = _argv(_su_mgr(), Phase.APPLY, ["Copilot", "Amphetamine"], tmp_path)
    assert argv[argv.index("--filter") + 1] == "Copilot,Amphetamine"


def test_su_manager_no_filter_when_empty_or_none(tmp_path):
    assert "--filter" not in _argv(_su_mgr(), Phase.APPLY, None, tmp_path)
    assert "--filter" not in _argv(_su_mgr(), Phase.APPLY, ["", "  "], tmp_path)


# -- action-required token helper -------------------------------------------


def test_action_required_token_helper_matches_report_regex():
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.orchestrator.report import ACTION_RE
    finally:
        sys.path.pop(0)
    res = subprocess.run(
        ["bash", "-c",
         f'. "{LIB}/ascendo_json.sh"; ascendo_action_required_token major_upgrade "15.1" ""; '
         f'printf "\\n"; ascendo_action_required_token "needs restart" "1 2" "3"'],
        capture_output=True, text=True, check=True,
    )
    first, second = res.stdout.splitlines()
    m = ACTION_RE.search(first)
    assert m and m.groups() == ("major_upgrade", "15.1", "?")
    m2 = ACTION_RE.search(second)          # whitespace collapsed -> one token
    assert m2 and m2.groups() == ("needs_restart", "1_2", "3")
