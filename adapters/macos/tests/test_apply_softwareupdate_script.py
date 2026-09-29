"""Tests for adapters/macos/scripts/softwareupdate/apply.sh.

The mutating phase. NEVER runs a real softwareupdate: ``su_fakes`` provides a
fake softwareupdate (canned ``-l`` listing that drops installed labels and
records every install), a fake ``sudo`` that logs its argv, and a fake
``sw_vers`` that pins the installed macOS version (26.6.2 by default).

Hard rules under test: every install keeps ``-R``; never ``-ia`` / ``-a`` /
bare ``-r``; a major macOS upgrade is never installed automatically; success
only after a post-install remeasure; restart labels are batched last and are
deferred under the safe profile.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from .su_fakes import su_env

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "softwareupdate" / "apply.sh"

MAJOR_LABEL = "macOS 27-26A428"
TAHOE = "macOS Tahoe 26.7-25G229"
SAFARI = "Safari27.0TahoeAuto-27.0"


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


def _run(ctx: dict, tmp_path: Path, *extra: str, profile: str = "default"):
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = subprocess.run(
        ["bash", str(SCRIPT), "--run-id", rid, "--trigger", "cli",
         "--profile", profile, "--output-dir", str(out), *extra],
        capture_output=True, text=True, env=ctx["env"], check=False,
    )
    side = out / rid / "apply__softwareupdate.json"
    return res, (_parse(side) if side.exists() else None)


def _subdir(tmp_path: Path) -> Path:
    """A second, independent sandbox (own fakes/state/out) inside the test."""
    sub = tmp_path / "second"
    sub.mkdir()
    return sub


def _status(sc) -> dict:
    return {i.id: i.status.value for i in sc.items}


def _sudo_lines(ctx: dict) -> list:
    p = ctx["sudo_log"]
    return p.read_text().strip().splitlines() if p.exists() else []


def _has_flag(line: str, flag: str) -> bool:
    return f" {flag} " in f" {line} "


# -- basics ---------------------------------------------------------------------


def test_dry_run_emits_planned_items_no_sudo(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt")
    res, sc = _run(ctx, tmp_path, "--dry-run")
    assert res.returncode == 0, res.stderr
    assert {i.status.value for i in sc.items} == {"planned"}
    assert _sudo_lines(ctx) == []
    assert ctx["installs"]() == []


def test_real_apply_installs_each_label_individually_with_R(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 0, res.stderr
    installs = ctx["installs"]()
    assert installs == [
        "-i|Safari17.4-17.4|-R|--verbose|--include-config-data",
        "-i|XProtectPlistConfigData_10_15-2174|-R|--verbose|--include-config-data",
    ]
    for line in _sudo_lines(ctx):
        assert _has_flag(line, "-R")
        assert not _has_flag(line, "-r") and not _has_flag(line, "-a") and not _has_flag(line, "-ia")
    assert _status(sc) == {"Safari17.4-17.4": "success",
                           "XProtectPlistConfigData_10_15-2174": "success"}


def test_all_flag_stays_label_based_and_never_dash_ia(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt")
    res, sc = _run(ctx, tmp_path, "--all")
    assert res.returncode == 0, res.stderr
    for line in ctx["installs"]():
        assert "-a" not in line.split("|") and "-ia" not in line.split("|")
        assert "-R" in line.split("|")
    assert len(ctx["installs"]()) == 2


def test_filter_passes_label_to_softwareupdate(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt")
    res, sc = _run(ctx, tmp_path, "--filter", "Safari17.4-17.4")
    assert res.returncode == 0, res.stderr
    assert ctx["installs"]() == ["-i|Safari17.4-17.4|-R|--verbose|--include-config-data"]
    assert _status(sc) == {"Safari17.4-17.4": "success"}


def test_no_updates_exit_0_no_sudo(tmp_path):
    ctx = su_env(tmp_path, "no-updates.txt")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 0, res.stderr
    assert sc.items == []
    assert _sudo_lines(ctx) == []


def test_softwareupdate_l_failure_exit_30_no_sudo(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt", FAKE_SU_LIST_FAIL="1")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 30
    assert sc.status.value == "failed"
    assert any(m.level.value == "error" for m in sc.messages)
    assert _sudo_lines(ctx) == []


def test_listing_probe_runs_in_C_locale(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt", LANG="pl_PL.UTF-8", LC_ALL="pl_PL.UTF-8")
    _run(ctx, tmp_path, "--dry-run")
    calls = (ctx["state"] / "list_calls.log").read_text()
    assert "LANG=C LC_ALL=C" in calls and "--include-config-data" in calls


# -- restart handling -------------------------------------------------------------


def test_restart_required_exits_75(tmp_path):
    ctx = su_env(tmp_path, "restart-required.txt")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 75, res.stderr
    assert sc.needs_reboot is True
    assert set(_status(sc).values()) == {"success"}


def test_restart_labels_batched_in_one_call_last(tmp_path):
    ctx = su_env(tmp_path, "two-restart.txt")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 75, res.stderr
    installs = ctx["installs"]()
    # non-restart label first (own call), then ONE batch with both restart labels.
    assert installs == [
        "-i|Safari17.5-17.5|-R|--verbose|--include-config-data",
        f"-i|{TAHOE}|Firmware Update-1.0|-R|--verbose|--include-config-data",
    ]
    assert sc.needs_reboot is True


def test_safe_profile_defers_restart_labels_even_when_pinned(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt")
    res, sc = _run(ctx, tmp_path, "--filter", TAHOE, profile="safe")
    assert res.returncode == 0, res.stderr
    assert ctx["installs"]() == []
    assert _status(sc) == {TAHOE: "skipped"}
    text = " ".join(m.text for m in sc.messages)
    assert "ASCENDO-ACTION-REQUIRED reason=needs_restart" in text
    assert sc.needs_reboot in (False, None)


def test_safe_profile_still_installs_non_restart_labels(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt")
    res, sc = _run(ctx, tmp_path, profile="safe")
    assert res.returncode == 0, res.stderr
    assert ctx["installs"]() == [f"-i|{SAFARI}|-R|--verbose|--include-config-data"]
    st = _status(sc)
    assert st[SAFARI] == "success" and st[TAHOE] == "skipped" and st[MAJOR_LABEL] == "skipped"


def test_safe_mode_env_defers_restart_labels(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt", ASCENDO_SAFE_MODE="true")
    res, sc = _run(ctx, tmp_path, "--filter", TAHOE)
    assert ctx["installs"]() == []
    assert _status(sc) == {TAHOE: "skipped"}


# -- major macOS upgrades --------------------------------------------------------


def test_major_upgrade_never_installed_and_never_dash_ia(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 75, res.stderr    # Tahoe 26.7 (same major) restarts
    log = "\n".join(_sudo_lines(ctx))
    assert MAJOR_LABEL not in log
    assert " -ia" not in f" {log}" and not any(_has_flag(l, "-a") for l in _sudo_lines(ctx))
    assert _status(sc)[MAJOR_LABEL] == "skipped"
    major = next(i for i in sc.items if i.id == MAJOR_LABEL)
    assert major.current_version == "26.6.2" and major.target_version == "27"
    assert any("ASCENDO-ACTION-REQUIRED reason=major_upgrade cur=26.6.2 cand=27" in m.text
               for m in sc.messages)
    assert ctx["installs"]() == [
        f"-i|{SAFARI}|-R|--verbose|--include-config-data",
        f"-i|{TAHOE}|-R|--verbose|--include-config-data",
    ]


def test_major_pinned_explicitly_is_still_never_installed(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt")
    res, sc = _run(ctx, tmp_path, "--filter", MAJOR_LABEL)
    assert res.returncode == 0, res.stderr
    assert ctx["installs"]() == []
    assert _status(sc) == {MAJOR_LABEL: "skipped"}


def test_major_installed_only_with_allow_flag_and_keeps_R(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt", ASCENDO_ALLOW_MAJOR_UPGRADE="1")
    res, sc = _run(ctx, tmp_path, "--filter", MAJOR_LABEL)
    assert res.returncode == 75, res.stderr
    assert ctx["installs"]() == [f"-i|{MAJOR_LABEL}|-R|--verbose|--include-config-data"]


# -- selection / labels with spaces ------------------------------------------------


def test_items_not_requested_are_not_success(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt")
    res, sc = _run(ctx, tmp_path, "--filter", SAFARI)
    assert res.returncode == 0, res.stderr
    assert list(_status(sc)) == [SAFARI]      # nothing else reported, let alone success


def test_filter_multiple_labels_repeatable_and_csv(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt")
    res, sc = _run(ctx, tmp_path, "--filter", "Safari17.4-17.4",
                   "--filter", "XProtectPlistConfigData_10_15-2174")
    assert res.returncode == 0, res.stderr
    assert len(ctx["installs"]()) == 2
    sub = _subdir(tmp_path)
    ctx2 = su_env(sub, "incremental-updates.txt")
    res2, sc2 = _run(ctx2, sub, "--filter",
                     "Safari17.4-17.4,XProtectPlistConfigData_10_15-2174,Nope-1")
    assert res2.returncode == 0, res2.stderr
    assert len(ctx2["installs"]()) == 2


def test_label_with_spaces_is_one_whole_argument(tmp_path):
    ctx = su_env(tmp_path, "mixed-with-major.txt")
    res, sc = _run(ctx, tmp_path, "--filter", TAHOE)
    assert res.returncode == 75, res.stderr
    # '|' joins argv: the label is ONE argument, not split on its spaces.
    assert ctx["installs"]() == [f"-i|{TAHOE}|-R|--verbose|--include-config-data"]


def test_not_recommended_label_skipped_by_default_installed_with_all(tmp_path):
    listing = (
        "Software Update Tool\n\nFinding available software\n"
        "Software Update found the following new or updated software:\n"
        "* Label: OptionalThing-1.0\n"
        "\tTitle: Optional Thing, Version: 1.0, Size: 10K, Recommended: NO, \n"
    )
    ctx = su_env(tmp_path, listing_text=listing)
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 0
    assert ctx["installs"]() == [] and _status(sc) == {"OptionalThing-1.0": "skipped"}
    sub = _subdir(tmp_path)
    ctx2 = su_env(sub, listing_text=listing)
    res2, sc2 = _run(ctx2, sub, "--all")
    assert ctx2["installs"]() == ["-i|OptionalThing-1.0|-R|--verbose|--include-config-data"]
    assert _status(sc2) == {"OptionalThing-1.0": "success"}


# -- honest per-label status -------------------------------------------------------


def test_per_label_failure_and_success(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt", FAKE_SU_FAIL_LABELS="Safari17.4-17.4")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 20, res.stderr
    assert _status(sc) == {"Safari17.4-17.4": "failed",
                           "XProtectPlistConfigData_10_15-2174": "success"}
    assert any("Safari17.4-17.4: softwareupdate -i failed (rc=1)" in m.text for m in sc.messages)
    # the failed label did not stop the next one
    assert len(ctx["installs"]()) == 2


def test_remeasure_still_listed_is_failed_not_success(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt",
                 FAKE_SU_KEEP_LABELS="Safari17.4-17.4")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 20, res.stderr
    assert _status(sc)["Safari17.4-17.4"] == "failed"
    assert _status(sc)["XProtectPlistConfigData_10_15-2174"] == "success"
    assert any("still pending after install" in m.text for m in sc.messages)


def test_remeasure_probe_failure_is_unknown_never_success(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt", FAKE_SU_LIST_FAIL_AFTER_INSTALL="1")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 0, res.stderr
    assert set(_status(sc).values()) == {"skipped"}
    assert any("unknown" in m.text for m in sc.messages)
    assert len(ctx["installs"]()) == 2


def test_restart_batch_failure_is_failed_no_reboot(tmp_path):
    ctx = su_env(tmp_path, "two-restart.txt", FAKE_SU_FAIL_LABELS="Firmware Update-1.0")
    res, sc = _run(ctx, tmp_path)
    assert res.returncode == 20, res.stderr
    st = _status(sc)
    assert st[TAHOE] == "failed" and st["Firmware Update-1.0"] == "failed"
    assert st["Safari17.5-17.5"] == "success"
    assert sc.needs_reboot in (False, None)
