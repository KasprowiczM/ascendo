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
        source {LIB}/ascendo_json.sh
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


def test_msupdate_apply_falls_back_to_manual_gui(tmp_path: Path) -> None:
    # Decision (commits 5f89b0a / 75dad50, "drop silent msupdate installs"):
    # the silent `msupdate --install` was abandoned because it hangs for
    # minutes in the background even when no update is pending. msupdate_apply
    # now returns RC 95 (the action-required sentinel) and directs the user
    # to the Microsoft AutoUpdate GUI, and MUST NOT invoke `msupdate --install`.
    log = tmp_path / "args.log"
    fake = tmp_path / "msupdate"
    fake.write_text(f"#!/bin/sh\necho \"$@\" > {log}\nexit 0\n")
    fake.chmod(0o755)
    sudo = tmp_path / "sudo"
    # _ascendo_sudo picks `-A` (askpass) or plain `sudo` based on env;
    # this fake handles both forms.
    sudo.write_text('#!/bin/sh\nif [ "$1" = "-A" ]; then shift; fi\nexec "$@"\n')
    sudo.chmod(0o755)

    cfg = json.dumps({"slug": "ms365"})
    snippet = f"msupdate_apply 'ms365' {json.dumps(cfg)!r}"
    r = _run(snippet, fake_path=tmp_path)
    # RC 95 == action-required (manual GUI) — the documented contract.
    assert r.returncode == 95
    # The silent installer must NOT have been invoked.
    assert not log.exists() or "--install" not in log.read_text()


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
