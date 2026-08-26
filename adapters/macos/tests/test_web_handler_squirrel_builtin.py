"""squirrel.sh + builtin.sh tests."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"


def _run(snippet: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    full = textwrap.dedent(f"""\
        set -eo pipefail
        source {LIB}/ascendo_web.sh
        source {LIB}/handlers/squirrel.sh
        source {LIB}/handlers/builtin.sh
        {snippet}
    """)
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)


def test_squirrel_check_always_returns_empty() -> None:
    cfg = json.dumps({"slug": "slack", "bundle_id": "com.tinyspeck.slackmacgap"})
    snippet = f"squirrel_check 'slack' {json.dumps(cfg)!r}"
    r = _run(snippet)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_squirrel_apply_invokes_open_a(tmp_path: Path) -> None:
    log = tmp_path / "open-args.log"
    fake_open = tmp_path / "open"
    fake_open.write_text(f"#!/bin/sh\necho \"$@\" > {log}\nexit 0\n")
    fake_open.chmod(0o755)

    cfg = json.dumps({"slug": "slack", "app_path": "/Applications/Slack.app"})
    snippet = (
        f"export PATH={tmp_path}:$PATH\n"
        f"squirrel_apply 'slack' {json.dumps(cfg)!r}"
    )
    r = _run(snippet)
    assert r.returncode == 0
    args = log.read_text().strip()
    assert "-gjF" in args
    assert "-a" in args
    assert "/Applications/Slack.app" in args


def test_builtin_check_always_returns_empty() -> None:
    cfg = json.dumps({"slug": "zoom", "bundle_id": "us.zoom.xos"})
    snippet = f"builtin_check 'zoom' {json.dumps(cfg)!r}"
    r = _run(snippet)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_builtin_apply_invokes_open_a_and_returns_0(tmp_path: Path) -> None:
    log = tmp_path / "open-args.log"
    fake_open = tmp_path / "open"
    fake_open.write_text(f"#!/bin/sh\necho \"$@\" > {log}\nexit 0\n")
    fake_open.chmod(0o755)

    cfg = json.dumps({"slug": "zoom", "app_path": "/Applications/Zoom.app"})
    snippet = (
        f"export PATH={tmp_path}:$PATH\n"
        f"builtin_apply 'zoom' {json.dumps(cfg)!r}"
    )
    r = _run(snippet)
    assert r.returncode == 0
    args = log.read_text().strip()
    assert "/Applications/Zoom.app" in args


# Squirrel matches macOS_updates silent_launch: hidden `open -gjF` even
# when ASCENDO_SAFE_MODE=true. builtin still returns 95 (manual URL).


def test_squirrel_apply_safe_mode_still_hidden_launch(
    tmp_path: Path,
) -> None:
    log = tmp_path / "open-args.log"
    fake_open = tmp_path / "open"
    fake_open.write_text(f"#!/bin/sh\necho \"$@\" > {log}\nexit 0\n")
    fake_open.chmod(0o755)

    cfg = json.dumps({"slug": "slack", "app_path": "/Applications/Slack.app"})
    snippet = (
        f"export PATH={tmp_path}:$PATH\n"
        f"squirrel_apply 'slack' {json.dumps(cfg)!r}"
    )
    r = _run(snippet, env_extra={"ASCENDO_SAFE_MODE": "true"})
    assert r.returncode == 0, (
        f"safe-mode squirrel_apply must hidden-launch like macOS_updates, "
        f"got {r.returncode}: stderr={r.stderr!r}"
    )
    assert log.exists(), "safe-mode squirrel_apply must call open -gjF"
    args = log.read_text().strip()
    assert "-gjF" in args
    assert "/Applications/Slack.app" in args


def test_builtin_apply_safe_mode_skips_open_and_returns_95(
    tmp_path: Path,
) -> None:
    log = tmp_path / "open-args.log"
    fake_open = tmp_path / "open"
    fake_open.write_text(f"#!/bin/sh\necho \"$@\" > {log}\nexit 0\n")
    fake_open.chmod(0o755)

    cfg = json.dumps(
        {
            "slug": "zoom",
            "app_path": "/Applications/Zoom.app",
            "update_url": "https://zoom.us/download",
        }
    )
    snippet = (
        f"export PATH={tmp_path}:$PATH\n"
        f"builtin_apply 'zoom' {json.dumps(cfg)!r}"
    )
    r = _run(snippet, env_extra={"ASCENDO_SAFE_MODE": "true"})
    assert r.returncode == 95
    assert not log.exists(), "safe-mode builtin_apply must NOT call `open -a`"
    assert "https://zoom.us/download" in r.stderr, (
        "stderr should still surface the update URL hint: "
        f"{r.stderr!r}"
    )
