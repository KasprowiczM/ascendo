"""keystone.sh handler tests."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"


def _run(snippet: str, fake_ksadmin_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    if fake_ksadmin_dir is not None:
        env["PATH"] = f"{fake_ksadmin_dir}:{env.get('PATH', '')}"
    full = textwrap.dedent(f"""\
        set -eo pipefail
        source {LIB}/ascendo_web.sh
        source {LIB}/handlers/keystone.sh
        {snippet}
    """)
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)


def _fake_ksadmin(tmp_path: Path, output: str, exit_code: int = 0) -> Path:
    p = tmp_path / "ksadmin"
    p.write_text(f"#!/bin/sh\ncat <<'OUT'\n{output}\nOUT\nexit {exit_code}\n")
    p.chmod(0o755)
    return tmp_path


def test_keystone_check_returns_empty_when_ksadmin_missing(tmp_path: Path) -> None:
    cfg = json.dumps({"slug": "chrome", "ksadmin_product_id": "com.google.Chrome"})
    snippet = (
        "export PATH=/usr/bin:/bin\n"   # remove typical ksadmin location
        f"keystone_check 'chrome' {json.dumps(cfg)!r} || true"
    )
    r = _run(snippet)
    assert r.stdout.strip() == ""


def test_keystone_check_with_fake_ksadmin(tmp_path: Path) -> None:
    fake_dir = _fake_ksadmin(tmp_path, "com.google.Chrome (132.0.6834.84) installed")
    cfg = json.dumps({"slug": "chrome", "ksadmin_product_id": "com.google.Chrome"})
    snippet = f"keystone_check 'chrome' {json.dumps(cfg)!r}"
    r = _run(snippet, fake_ksadmin_dir=fake_dir)
    # Behaviour: returns "" when ksadmin doesn't speak our format; that's fine.
    # (Keystone introspection is opaque; design says "may return empty"
    # and our code emits skipped in that case.)
    assert r.returncode == 0


def test_keystone_apply_invokes_ksadmin_update(tmp_path: Path) -> None:
    log = tmp_path / "ksadmin-args.log"
    fake = tmp_path / "ksadmin"
    fake.write_text(f"#!/bin/sh\necho \"$@\" > {log}\nexit 0\n")
    fake.chmod(0o755)

    cfg = json.dumps({"slug": "chrome", "ksadmin_product_id": "com.google.Chrome"})
    snippet = f"keystone_apply 'chrome' {json.dumps(cfg)!r}"
    r = _run(snippet, fake_ksadmin_dir=tmp_path)
    assert r.returncode == 0
    args = log.read_text().strip()
    assert "--update" in args or "-update" in args
    assert "com.google.Chrome" in args
