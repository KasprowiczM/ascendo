"""verify.sh + cleanup.sh tests."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "adapters" / "macos" / "scripts" / "web"


def _run(script: str, output_dir: Path, *, run_id: str | None = None,
         env_extra: dict | None = None) -> subprocess.CompletedProcess:
    rid = run_id or str(uuid.uuid4())
    env = {**os.environ,
           "PYTHONPATH": f"{REPO_ROOT}/core:{REPO_ROOT}/adapters/macos"}
    if env_extra:
        env.update(env_extra)
    cmd = ["bash", str(SCRIPTS / script),
           "--run-id", rid, "--trigger", "cli", "--profile", "full",
           "--output-dir", str(output_dir)]
    return subprocess.run(cmd, capture_output=True, text=True, env=env), rid


def test_verify_no_apply_sidecar_emits_empty_no_op(tmp_path: Path) -> None:
    out = tmp_path / "out"
    r, run_id = _run("verify.sh", out)
    assert r.returncode == 0, r.stderr
    sc = json.loads((out / run_id / "verify__web.json").read_text())
    assert sc["items"] == []


def test_cleanup_prunes_old_dmgs(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    old = cache / "old.dmg"
    old.write_text("x")
    os.utime(old, (0, 0))   # 1970 — definitely older than 7 days
    new = cache / "new.dmg"
    new.write_text("y")

    out = tmp_path / "out"
    r, run_id = _run(
        "cleanup.sh", out,
        env_extra={"ASCENDO_WEB_CACHE_DIR": str(cache)},
    )
    assert r.returncode == 0, r.stderr
    assert not old.exists()
    assert new.exists()


def test_cleanup_idempotent_with_empty_cache(tmp_path: Path) -> None:
    out = tmp_path / "out"
    r, run_id = _run(
        "cleanup.sh", out,
        env_extra={"ASCENDO_WEB_CACHE_DIR": str(tmp_path / "nonexistent")},
    )
    assert r.returncode == 0
