"""Unattended-sudo handling (Task 16, F1): the post-warm authorization gate,
ASCENDO_NO_SUDO, `sudo -n` installs, soft SUDO_UNAVAILABLE deferral.

Ports of macOS_updates ``test_no_sudo_policy_skips_even_cached_authorization``
and ``test_no_terminal_and_cold_sudo_defers_without_installing``. Everything
runs against fakes: the fake ``sudo`` (su_fakes / mas_fakes) logs install argv,
answers ``sudo -n true`` from FAKE_SUDO_COLD, and with FAKE_SUDO_STRICT=1
refuses (as a real sudo would PROMPT) any install without -A/-n. No real sudo,
softwareupdate or mas is ever reached (the suite tripwire also fakes ``sudo``).
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
from .su_fakes import su_env

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ADAPTER_ROOT / "lib" / "ascendo_json.sh"
SU_SCRIPT = ADAPTER_ROOT / "scripts" / "softwareupdate" / "apply.sh"
MAS_SCRIPT = ADAPTER_ROOT / "scripts" / "mas" / "apply.sh"

KEKA = "1153157709"


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


def _msgs(sc) -> str:
    out = [m.text or "" for m in sc.messages]
    out += [m.text or "" for i in sc.items for m in i.messages]
    return "\n".join(out)


# -- softwareupdate -------------------------------------------------------------

def _su_run(ctx, tmp_path, *, trigger="cli", extra=()):
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = subprocess.run(
        ["bash", str(SU_SCRIPT), "--run-id", rid, "--trigger", trigger,
         "--profile", "default", "--output-dir", str(out), *extra],
        capture_output=True, text=True, env=ctx["env"], check=False)
    side = out / rid / "apply__softwareupdate.json"
    return res, (_parse(side) if side.exists() else None)


def _su_sudo(ctx):
    p = ctx["sudo_log"]
    return p.read_text().strip().splitlines() if p.exists() else []


def test_su_cold_cache_defers_softly_without_installing(tmp_path):
    """Port of test_no_terminal_and_cold_sudo_defers_without_installing."""
    ctx = su_env(tmp_path, "incremental-updates.txt", FAKE_SUDO_COLD="1", FAKE_SUDO_STRICT="1")
    res, sc = _su_run(ctx, tmp_path)
    assert res.returncode == 0, res.stderr                    # soft, not a failure
    assert {i.status.value for i in sc.items} == {"skipped"}
    assert len(sc.items) == 2
    assert "SUDO_UNAVAILABLE" in _msgs(sc) and "authenticate interactively and re-run" in _msgs(sc)
    assert _su_sudo(ctx) == [] and ctx["installs"]() == []    # no install command invoked


def test_su_no_sudo_policy_skips_even_with_hot_cache(tmp_path):
    """Port of test_no_sudo_policy_skips_even_cached_authorization."""
    ctx = su_env(tmp_path, "incremental-updates.txt", ASCENDO_NO_SUDO="1")   # cache is HOT
    res, sc = _su_run(ctx, tmp_path)
    assert res.returncode == 0, res.stderr
    assert {i.status.value for i in sc.items} == {"skipped"}
    assert "ASCENDO_NO_SUDO" in _msgs(sc)
    assert _su_sudo(ctx) == [] and ctx["installs"]() == []


def test_su_restart_labels_are_deferred_too(tmp_path):
    ctx = su_env(tmp_path, "restart-required.txt", FAKE_SUDO_COLD="1")
    res, sc = _su_run(ctx, tmp_path)
    assert res.returncode == 0, res.stderr
    assert sc.items and {i.status.value for i in sc.items} == {"skipped"}
    assert not sc.needs_reboot
    assert ctx["installs"]() == []


def test_su_hot_cache_installs_with_sudo_n_never_prompting(tmp_path):
    """No askpass: installs use `sudo -n` (F1) -- a lapsed cache fails, never prompts."""
    ctx = su_env(tmp_path, "incremental-updates.txt", FAKE_SUDO_STRICT="1")
    res, sc = _su_run(ctx, tmp_path)
    assert res.returncode == 0, res.stderr
    lines = _su_sudo(ctx)
    assert lines and all(l.startswith("-n ") for l in lines), lines
    assert {i.status.value for i in sc.items} == {"success"}
    assert all(" -R " in f" {l} " for l in lines)             # -R rule untouched


def test_su_askpass_wired_cold_cache_still_installs_with_dash_a(tmp_path):
    ask = tmp_path / "ask.sh"
    ask.write_text("#!/bin/sh\necho pw\n")
    ask.chmod(0o755)
    ctx = su_env(tmp_path, "incremental-updates.txt", FAKE_SUDO_COLD="1",
                 FAKE_SUDO_STRICT="1", SUDO_ASKPASS=str(ask))
    res, sc = _su_run(ctx, tmp_path)
    assert res.returncode == 0, res.stderr
    assert all(l.startswith("-A ") for l in _su_sudo(ctx)) and _su_sudo(ctx)
    assert {i.status.value for i in sc.items} == {"success"}


def test_su_scheduler_trigger_cold_cache_is_unattended_deferral(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt", FAKE_SUDO_COLD="1", FAKE_SUDO_STRICT="1")
    res, sc = _su_run(ctx, tmp_path, trigger="scheduler")
    assert res.returncode == 0, res.stderr
    assert {i.status.value for i in sc.items} == {"skipped"}
    assert "SUDO_UNAVAILABLE_UNATTENDED" in _msgs(sc)
    assert "open Ascendo and run apply" in _msgs(sc)
    assert _su_sudo(ctx) == [] and ctx["installs"]() == []


def test_su_scheduler_trigger_hot_cache_proceeds(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt", FAKE_SUDO_STRICT="1")
    res, sc = _su_run(ctx, tmp_path, trigger="scheduler")
    assert res.returncode == 0, res.stderr
    assert {i.status.value for i in sc.items} == {"success"}


def test_su_dry_run_never_touches_the_gate(tmp_path):
    ctx = su_env(tmp_path, "incremental-updates.txt", FAKE_SUDO_COLD="1")
    res, sc = _su_run(ctx, tmp_path, extra=("--dry-run",))
    assert res.returncode == 0 and {i.status.value for i in sc.items} == {"planned"}


# -- mas ------------------------------------------------------------------------

def _mas_run(fakes, tmp_path, *, trigger="cli", env_extra=None):
    out, rid = tmp_path / "out", str(uuid.uuid4())
    env = dict(os.environ)
    env.update(fakes["env"])
    env.update(env_extra or {})
    res = subprocess.run(
        ["bash", str(MAS_SCRIPT), "--run-id", rid, "--trigger", trigger,
         "--profile", "default", "--output-dir", str(out)],
        capture_output=True, text=True, env=env, check=False)
    side = out / rid / "apply__mas.json"
    return res, (_parse(side) if side.exists() else None)


def _mas_sudo(fakes):
    p = fakes["state"] / "sudo.log"
    return p.read_text().strip().splitlines() if p.exists() else []


def _mas_upgrades(fakes):
    p = fakes["state"] / "calls.log"
    return [l for l in (p.read_text().splitlines() if p.exists() else []) if "ARGS=upgrade" in l]


def test_mas_cold_cache_defers_softly_without_sudo(tmp_path):
    fakes = mas_env(tmp_path)
    res, sc = _mas_run(fakes, tmp_path, env_extra={"FAKE_SUDO_COLD": "1", "FAKE_SUDO_STRICT": "1"})
    assert res.returncode == 0, res.stderr
    assert sc.items and {i.status.value for i in sc.items} == {"skipped"}
    assert "SUDO_UNAVAILABLE" in _msgs(sc)
    assert any(m.code == "SUDO_UNAVAILABLE" for m in sc.messages)
    assert _mas_sudo(fakes) == [] and _mas_upgrades(fakes) == []
    # I2: the deferral reaches REPORT.md as a listed soft deferral (slug matches the item id)
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.orchestrator.report import _soft_deferrals, generate_apply_report
    finally:
        sys.path.pop(0)
    assert {it.id for _c, it in _soft_deferrals([sc])} == {i.id for i in sc.items}
    md = generate_apply_report(tmp_path / "out" / str(sc.run.id))
    assert "deferred" in md.lower() and "authenticate interactively" in md.lower(), md


def test_mas_no_sudo_policy_skips_even_with_hot_cache(tmp_path):
    fakes = mas_env(tmp_path)
    res, sc = _mas_run(fakes, tmp_path, env_extra={"ASCENDO_NO_SUDO": "1"})
    assert res.returncode == 0, res.stderr
    assert {i.status.value for i in sc.items} == {"skipped"}
    assert _mas_sudo(fakes) == [] and _mas_upgrades(fakes) == []


def test_mas_hot_cache_upgrade_uses_sudo_n_inside_the_timeout_wrapper(tmp_path):
    """Task-6 carry-over: sudo can never prompt (SIGTTIN stall) inside
    ascendo_run_with_timeout -- the install is `sudo -n`, and STRICT proves it."""
    fakes = mas_env(tmp_path)
    res, sc = _mas_run(fakes, tmp_path, env_extra={
        "FAKE_SUDO_STRICT": "1", "ASCENDO_FORCE_TIMEOUT_FALLBACK": "1"})
    assert res.returncode == 0, res.stderr
    lines = _mas_sudo(fakes)
    assert len(lines) == 1 and lines[0].startswith("-n env MAS_NO_AUTO_INDEX=1 "), lines
    assert {i.status.value for i in sc.items} == {"success"}


def test_mas_scheduler_trigger_cold_cache_is_unattended_deferral(tmp_path):
    fakes = mas_env(tmp_path)
    res, sc = _mas_run(fakes, tmp_path, trigger="scheduler",
                       env_extra={"FAKE_SUDO_COLD": "1", "FAKE_SUDO_STRICT": "1"})
    assert res.returncode == 0, res.stderr
    assert {i.status.value for i in sc.items} == {"skipped"}
    assert "SUDO_UNAVAILABLE_UNATTENDED" in _msgs(sc)
    assert _mas_sudo(fakes) == [] and _mas_upgrades(fakes) == []


# -- _ascendo_sudo_warm / _ascendo_sudo_gate (library level) --------------------

def _lib(tmp_path: Path, snippet: str, *, env_extra=None, hot_after_v=False, cold=False):
    """Run ``snippet`` with the wrapper sourced, in a NO-controlling-TTY process,
    with a fake sudo that records every call. Returns (stdout, sudo_calls)."""
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    log = tmp_path / "sudo_calls.log"
    hot = tmp_path / "sudo_hot"
    (fake / "sudo").write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        # `sudo -v` (Touch ID) warms the cache; `-n ...` only honours it
        f'if [ "$1" = "-v" ]; then {"touch " + str(hot) if hot_after_v else "false"}; exit $?; fi\n'
        f'if [ "$1" = "-n" ]; then {"[ -e " + str(hot) + " ]" if hot_after_v else ("false" if cold else "true")}; exit $?; fi\n'
        "exit 0\n")
    os.chmod(fake / "sudo", 0o755)
    env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    env.update(env_extra or {})
    res = subprocess.run(["bash", "-c", f'. "{WRAPPER}"; {snippet}'], capture_output=True,
                         text=True, env=env, check=False, preexec_fn=os.setsid)  # noqa: PLW1509
    calls = log.read_text().strip().splitlines() if log.exists() else []
    return res.stdout, calls


def test_warm_unattended_cold_makes_only_sudo_n_v_and_sets_no_sudo(tmp_path):
    out, calls = _lib(tmp_path, '_ascendo_sudo_warm; echo "RC=$? NO=${ASCENDO_NO_SUDO:-}"',
                      env_extra={"ASCENDO_UNATTENDED": "1"}, cold=True)
    assert "RC=1 NO=1" in out
    assert calls == ["-n -v"]                      # never `sudo -v`
    assert not any(c.split()[0] == "-v" for c in calls)


def test_warm_unattended_hot_cache_returns_0_without_no_sudo(tmp_path):
    out, calls = _lib(tmp_path, '_ascendo_sudo_warm; echo "RC=$? NO=${ASCENDO_NO_SUDO:-}"',
                      env_extra={"TRIGGER": "scheduler"})
    assert "RC=0 NO=" in out and calls == ["-n -v"]


def test_touchid_tty_less_flow_still_works_warm_then_gate(tmp_path):
    """Sesja 81 invariant + F1 assumption: no tty, no askpass; `sudo -v </dev/null`
    (Touch ID) warms the cache and the gate's `sudo -n true` then honours it."""
    out, calls = _lib(
        tmp_path,
        '_ascendo_sudo_warm; if _ascendo_sudo_gate; then echo GATE_OK; else echo "GATE_NO $ASCENDO_SUDO_GATE_CODE"; fi',
        hot_after_v=True)
    assert "GATE_OK" in out, (out, calls)
    assert "-v" in calls and "-n true" in calls


def test_gate_codes(tmp_path):
    out, _ = _lib(tmp_path, '_ascendo_sudo_gate; echo "RC=$? $ASCENDO_SUDO_GATE_CODE"', cold=True)
    assert "RC=1 SUDO_UNAVAILABLE" in out
    out, _ = _lib(tmp_path, '_ascendo_sudo_gate; echo "RC=$? $ASCENDO_SUDO_GATE_CODE"',
                  env_extra={"ASCENDO_NO_SUDO": "1", "ASCENDO_UNATTENDED": "1"})
    assert "RC=1 SUDO_UNAVAILABLE_UNATTENDED" in out
    out, _ = _lib(tmp_path, '_ascendo_sudo_gate; echo "RC=$?"')
    assert "RC=0" in out


def test_sudo_prefix_is_dash_n_only_after_the_gate_opted_in(tmp_path):
    out, _ = _lib(tmp_path, 'echo "[$(_ascendo_sudo_prefix)]"; export ASCENDO_SUDO_NONINTERACTIVE=1; echo "[$(_ascendo_sudo_prefix)]"')
    assert out.splitlines()[:2] == ["[sudo]", "[sudo -n]"]


# -- web (I1): root paths honour ASCENDO_NO_SUDO ---------------------------------

def _web_unattended(tmp_path, *, trigger, env_extra=None):
    import json

    from .test_web_phase_vendor_truth import (  # noqa: PLC0415
        CLAUDE_BODY, SCRIPTS, Sandbox, prime_claude, registry, stub_open, stub_sleep,
    )
    sb = Sandbox(tmp_path)
    prime_claude(sb)
    stub_open(sb)
    stub_sleep(sb)
    reg = registry(sb, direct_first=True)
    feeds = sb.tmp / "feeds"
    feeds.mkdir()
    (feeds / "vt.feed").write_text(CLAUDE_BODY)
    # fake sudo from the Sandbox: logs to forbidden.log and exits 1 (a COLD cache)
    sb.apps.chmod(0o555)          # plain install fails -> the root fallback is reached
    out = sb.tmp / "out"
    env = sb.env(ASCENDO_WEB_REGISTRY_PATH=reg, ASCENDO_WEB_USER_REGISTRY_PATH="",
                 ASCENDO_SUDO_WARM_DISABLE="", ASCENDO_WEB_VENDOR_FEED_DIR=feeds,
                 ASCENDO_WEB_SLEEP_BIN=sb.bin / "sleep_stub",
                 ASCENDO_WEB_DOCKER_BIN=sb.tmp / "no-docker",
                 PYTHONPATH=f"{ADAPTER_ROOT.parent.parent}/core:{ADAPTER_ROOT}", **(env_extra or {}))
    rid = str(uuid.uuid4())
    try:
        res = subprocess.run(
            ["bash", str(SCRIPTS / "apply.sh"), "--run-id", rid, "--trigger", trigger,
             "--profile", "full", "--output-dir", str(out)],
            capture_output=True, text=True, env=env, check=False)
    finally:
        sb.apps.chmod(0o755)
    return res, json.loads((out / rid / "apply__web.json").read_text()), sb


def test_web_scheduler_trigger_cold_sudo_defers_root_install_without_sudo(tmp_path):
    res, sc, sb = _web_unattended(tmp_path, trigger="scheduler")
    assert res.returncode == 0, res.stderr
    (item,) = sc["items"]
    assert item["status"] == "skipped", sc
    assert any(m.get("code") == "SUDO_UNAVAILABLE_UNATTENDED" for m in sc["messages"]), sc["messages"]
    # the ONLY sudo call allowed is the unattended `sudo -n -v` cache probe
    sudo_calls = [l for l in sb.forbidden() if l.startswith("sudo ")]
    assert set(sudo_calls) <= {"sudo -n -v"}, sudo_calls


def test_web_no_sudo_policy_never_calls_sudo(tmp_path):
    res, sc, sb = _web_unattended(tmp_path, trigger="cli", env_extra={"ASCENDO_NO_SUDO": "1"})
    assert res.returncode == 0, res.stderr
    (item,) = sc["items"]
    assert item["status"] == "skipped", sc
    assert [l for l in sb.forbidden() if l.startswith("sudo ")] == []
