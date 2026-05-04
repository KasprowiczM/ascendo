"""Tests for adapters/macos/scripts/scheduler/scheduler.sh.

Real-bash tests with a fake launchctl binary on PATH that records argv
to a log file. No real LaunchAgents written — the script's home dir is
overridden via env vars.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "scheduler" / "scheduler.sh"


def _make_fake_launchctl(tmp_path: Path) -> tuple[Path, Path]:
    """Fake launchctl binary recording each invocation to a log file."""
    log = tmp_path / "launchctl.log"
    binary = tmp_path / "launchctl"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        "exit 0\n"
    )
    os.chmod(binary, 0o755)
    return binary, log


def _run(action: str, *, payload: dict | None, tmp_path: Path,
         fake_home: Path | None = None,
         launchctl: Path | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the driver. Returns (CompletedProcess, output.json path)."""
    output = tmp_path / "result.json"
    argv = ["bash", str(SCRIPT), "--action", action, "--output-path", str(output)]
    if payload is not None:
        payload_path = tmp_path / "payload.json"
        payload_path.write_text(json.dumps(payload))
        argv += ["--payload-path", str(payload_path)]
    env = dict(os.environ)
    if fake_home is not None:
        env["ASCENDO_HOME_OVERRIDE"] = str(fake_home)
    if launchctl is not None:
        env["PATH"] = f"{launchctl.parent}{os.pathsep}{env.get('PATH', '')}"
    res = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    return res, output


def test_unknown_action_exits_2(tmp_path):
    res, _ = _run("bogus", payload=None, tmp_path=tmp_path)
    assert res.returncode == 2
    assert "unknown action" in (res.stderr + res.stdout).lower()


def test_missing_output_path_exits_2(tmp_path):
    res = subprocess.run(
        ["bash", str(SCRIPT), "--action", "list"],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 2
