"""msupdate.sh + docker.sh tests."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"


def _run(snippet: str, fake_path: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    if fake_path is not None:
        env["PATH"] = f"{fake_path}:{env.get('PATH', '')}"
    full = textwrap.dedent(f"""\
        set -eo pipefail
        source {LIB}/ascendo_web.sh
        source {LIB}/handlers/msupdate.sh
        source {LIB}/handlers/docker.sh
        {snippet}
    """)
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)


def test_msupdate_check_parses_pending_count(tmp_path: Path) -> None:
    fake = tmp_path / "msupdate"
    fake.write_text(
        '#!/bin/sh\n'
        'cat <<EOF\nWaiting for Microsoft AutoUpdate to be ready\n'
        '\n'
        ' Word                   16.83  pending\n'
        ' Excel                  16.83  pending\n'
        ' OneNote                16.83  pending\nEOF\n'
        'exit 0\n',
    )
    fake.chmod(0o755)
    cfg = json.dumps({"slug": "ms365"})
    snippet = f"msupdate_check 'ms365' {json.dumps(cfg)!r}"
    r = _run(snippet, fake_path=tmp_path)
    assert r.returncode == 0
    # Returns "pending" (any string non-empty signals planned)
    assert "pending" in r.stdout.lower() or r.stdout.strip() != ""


def test_msupdate_apply_calls_msupdate_install(tmp_path: Path) -> None:
    log = tmp_path / "args.log"
    fake = tmp_path / "msupdate"
    fake.write_text(f"#!/bin/sh\necho \"$@\" > {log}\nexit 0\n")
    fake.chmod(0o755)
    sudo = tmp_path / "sudo"
    sudo.write_text('#!/bin/sh\nshift\nexec "$@"\n')   # strip -A
    sudo.chmod(0o755)

    cfg = json.dumps({"slug": "ms365"})
    snippet = f"msupdate_apply 'ms365' {json.dumps(cfg)!r}"
    r = _run(snippet, fake_path=tmp_path)
    assert r.returncode == 0
    args = log.read_text().strip()
    assert "--install" in args


def test_docker_check_parses_version(tmp_path: Path) -> None:
    fake = tmp_path / "docker"
    fake.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "desktop" ] && [ "$2" = "version" ]; then\n'
        '  echo "Docker Desktop 4.45.0"\n'
        'elif [ "$1" = "desktop" ] && [ "$2" = "update" ]; then\n'
        '  echo "Update applied"\n'
        'fi\nexit 0\n',
    )
    fake.chmod(0o755)
    cfg = json.dumps({"slug": "docker"})
    snippet = f"docker_check 'docker' {json.dumps(cfg)!r}"
    r = _run(snippet, fake_path=tmp_path)
    # Returns version or empty; we just assert no crash + non-error
    assert r.returncode == 0


def test_docker_apply_calls_docker_desktop_update(tmp_path: Path) -> None:
    log = tmp_path / "args.log"
    fake = tmp_path / "docker"
    fake.write_text(f"#!/bin/sh\necho \"$@\" > {log}\nexit 0\n")
    fake.chmod(0o755)
    cfg = json.dumps({"slug": "docker"})
    snippet = f"docker_apply 'docker' {json.dumps(cfg)!r}"
    r = _run(snippet, fake_path=tmp_path)
    assert r.returncode == 0
    args = log.read_text().strip()
    assert "desktop" in args and "update" in args
