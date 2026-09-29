"""Tests for adapters/macos/lib/ascendo_proc.sh (ascendo_run_with_timeout).

macOS has no GNU ``timeout`` by default, so the process-group fallback is the
common path: it is forced with ASCENDO_FORCE_TIMEOUT_FALLBACK=1 and also
exercised with a PATH that genuinely lacks timeout/gtimeout.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
PROC = ADAPTER_ROOT / "lib" / "ascendo_proc.sh"
JSON_SH = ADAPTER_ROOT / "lib" / "ascendo_json.sh"
WEB_SH = ADAPTER_ROOT / "lib" / "ascendo_web.sh"
NPM_SH = ADAPTER_ROOT / "lib" / "ascendo_npm.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")

BASH = shutil.which("bash") or "bash"
HAS_GNU_TIMEOUT = bool(shutil.which("timeout") or shutil.which("gtimeout"))
MODES = [pytest.param("fallback", id="fallback")]
if HAS_GNU_TIMEOUT:
    MODES.append(pytest.param("native", id="native"))


def _bash(script: str, mode: str = "fallback", env_extra: dict | None = None,
          stdin: str | None = None, path: str | None = None,
          timeout: float = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "ASCENDO_KILL_AFTER": "1"}
    if mode == "fallback":
        env["ASCENDO_FORCE_TIMEOUT_FALLBACK"] = "1"
    if path is not None:
        env["PATH"] = path
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [BASH, "-c", f'. "{PROC}"\n{script}'],
        capture_output=True, text=True, env=env, input=stdin, timeout=timeout,
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


@pytest.mark.parametrize("mode", MODES)
def test_term_ignoring_child_is_killed_within_deadline_plus_grace(mode: str) -> None:
    started = time.monotonic()
    res = _bash("ascendo_run_with_timeout 1 sh -c 'trap \"\" TERM; sleep 30'", mode)
    elapsed = time.monotonic() - started
    assert res.returncode == 124, res.stderr
    # deadline 1 s + kill-after 1 s (+ generous slack); never the 30 s sleep
    assert elapsed < 10, elapsed


@pytest.mark.parametrize("mode", MODES)
def test_exit_codes_pass_through(mode: str) -> None:
    assert _bash("ascendo_run_with_timeout 5 sh -c 'exit 7'", mode).returncode == 7
    assert _bash("ascendo_run_with_timeout 5 true", mode).returncode == 0


@pytest.mark.parametrize("mode", MODES)
def test_unrelated_sigkill_before_deadline_stays_137(mode: str) -> None:
    res = _bash("ascendo_run_with_timeout 30 sh -c 'kill -9 $$'", mode)
    assert res.returncode == 137, res.stderr


@pytest.mark.skipif(not HAS_GNU_TIMEOUT, reason="GNU timeout/gtimeout not installed")
def test_native_137_after_deadline_maps_to_124() -> None:
    """GNU timeout --kill-after exits 137 when it had to SIGKILL; that is a timeout."""
    res = _bash("ascendo_run_with_timeout 1 sh -c 'trap \"\" TERM; sleep 30'", "native")
    assert res.returncode == 124, res.stderr


def test_map_rc_helper_only_maps_137_when_deadline_reached() -> None:
    script = (
        '_ascendo_proc_map_rc 137 0 5; echo "early=$?"\n'
        '_ascendo_proc_map_rc 137 5 5; echo "late=$?"\n'
        '_ascendo_proc_map_rc 124 0 5; echo "to=$?"\n'
        '_ascendo_proc_map_rc 3 9 5; echo "other=$?"\n'
    )
    res = _bash(script)
    assert res.stdout.split() == ["early=137", "late=124", "to=124", "other=3"], res.stdout


def test_grandchild_dies_with_the_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild.pid"
    res = _bash(
        f"ascendo_run_with_timeout 1 sh -c 'sleep 60 & echo $! > \"{pidfile}\"; wait'"
    )
    assert res.returncode == 124, res.stderr
    pid = int(pidfile.read_text().strip())
    assert _wait_gone(pid), f"grandchild {pid} survived the group kill"


def test_grandchild_that_outlives_exiting_leader_is_killed(tmp_path: Path) -> None:
    """Leader exits on TERM, grandchild ignores TERM -> the group KILL reaps it."""
    pidfile = tmp_path / "gc.pid"
    script = (
        "ascendo_run_with_timeout 1 sh -c "
        f"'(trap \"\" TERM; sleep 60) & echo $! > \"{pidfile}\"; sleep 60'"
    )
    res = _bash(script)
    assert res.returncode == 124, res.stderr
    assert _wait_gone(int(pidfile.read_text().strip()))


def test_fallback_works_with_path_lacking_timeout_and_gtimeout(tmp_path: Path) -> None:
    """The stock-macOS situation: no timeout/gtimeout anywhere on PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("sh", "sleep", "python3", "cat", "true"):
        found = shutil.which(tool)
        assert found, tool
        (bindir / tool).symlink_to(found)
    res = _bash(
        'if command -v timeout >/dev/null 2>&1 || command -v gtimeout >/dev/null 2>&1; '
        'then echo HAS_TIMEOUT; exit 9; fi\n'
        "ascendo_run_with_timeout 1 sh -c 'trap \"\" TERM; sleep 30'",
        mode="native",  # do NOT force: prove the natural fallback selection
        path=str(bindir),
    )
    assert "HAS_TIMEOUT" not in res.stdout
    assert res.returncode == 124, res.stderr


@pytest.mark.parametrize("mode", MODES)
def test_stdin_detached_by_default_and_opt_in_keeps_it(mode: str) -> None:
    detached = _bash("ascendo_run_with_timeout 5 cat", mode, stdin="hello\n")
    assert detached.returncode == 0 and detached.stdout == ""
    kept = _bash("ascendo_run_with_timeout 5 cat", mode,
                 env_extra={"ASCENDO_RUN_KEEP_STDIN": "1"}, stdin="hello\n")
    assert kept.returncode == 0 and kept.stdout == "hello\n"


def test_usage_errors() -> None:
    assert _bash("ascendo_run_with_timeout").returncode == 2
    assert _bash("ascendo_run_with_timeout abc true").returncode == 2


def test_survives_set_e_in_caller() -> None:
    res = _bash("set -e\nrc=0; ascendo_run_with_timeout 5 sh -c 'exit 3' || rc=$?\necho rc=$rc",
                "fallback")
    assert res.stdout.strip() == "rc=3", res.stderr


def test_sourced_by_json_npm_and_web_libs() -> None:
    for lib in (JSON_SH, NPM_SH, WEB_SH):
        res = subprocess.run(
            ["bash", "-c", f'. "{lib}"; type ascendo_run_with_timeout >/dev/null && echo yes'],
            capture_output=True, text=True,
        )
        assert res.stdout.strip() == "yes", (lib, res.stderr)


def test_npm_wrapper_kills_term_ignoring_child() -> None:
    started = time.monotonic()
    res = subprocess.run(
        ["bash", "-c", f'. "{NPM_SH}"; ascendo_npm_run_with_timeout 1 sh -c \'trap "" TERM; sleep 30\''],
        capture_output=True, text=True,
        env={**os.environ, "ASCENDO_FORCE_TIMEOUT_FALLBACK": "1", "ASCENDO_KILL_AFTER": "1"},
        timeout=60,
    )
    assert res.returncode == 124, res.stderr
    assert time.monotonic() - started < 10


def test_web_apply_cli_timeout_kills_term_ignoring_cli() -> None:
    started = time.monotonic()
    argv_json = '["sh", "-c", "trap \\"\\" TERM; sleep 30"]'
    res = subprocess.run(
        ["bash", "-c", f". \"{WEB_SH}\"; _web_run_apply_cli slug '{argv_json}'"],
        capture_output=True, text=True,
        env={**os.environ, "ASCENDO_FORCE_TIMEOUT_FALLBACK": "1", "ASCENDO_KILL_AFTER": "1",
             "ASCENDO_WEB_APPLY_CLI_TIMEOUT": "1"},
        timeout=60,
    )
    assert res.returncode == 124, res.stderr
    assert time.monotonic() - started < 10


def test_web_apply_cli_passes_exit_code_through() -> None:
    res = subprocess.run(
        ["bash", "-c", f". \"{WEB_SH}\"; _web_run_apply_cli slug '[\"sh\", \"-c\", \"exit 5\"]'"],
        capture_output=True, text=True,
    )
    assert res.returncode == 5, res.stderr
