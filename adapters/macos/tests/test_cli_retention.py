"""Vendor CLI version retention (Task 17, Spec P7): lib/cli_retention.py + npm/cleanup.sh.

Everything happens under a temp HOME; nothing real is touched. The cleanup.sh
tests run with a fake npm on a hermetic PATH.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER_ROOT / "lib"))
import cli_retention as cr  # noqa: E402

CLEANUP = ADAPTER_ROOT / "scripts" / "npm" / "cleanup.sh"


def _mkver(root: Path, name: str, age: int) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "payload").write_text("x" * 100)
    t = time.time() - age
    os.utime(d, (t, t))
    return d


def _codex(home: Path):
    root = home / ".codex/packages/standalone/releases"
    return root, home / ".codex/packages/standalone/current"


def test_codex_keeps_current_plus_one_previous(tmp_path):
    root, cur = _codex(tmp_path)
    names = ["1.0.0-aarch64-apple-darwin", "1.1.0-aarch64-apple-darwin",
             "1.2.0-aarch64-apple-darwin", "1.3.0-aarch64-apple-darwin"]
    for i, n in enumerate(names):
        _mkver(root, n, age=(len(names) - i) * 1000)   # 1.3.0 newest
    cur.symlink_to(root / "1.0.0-aarch64-apple-darwin")   # current is the OLDEST
    count, _ = cr.prune_codex(str(tmp_path))
    left = sorted(p.name for p in root.iterdir())
    assert left == ["1.0.0-aarch64-apple-darwin", "1.3.0-aarch64-apple-darwin"]
    assert count == 2


def test_codex_ignores_unexpected_names_and_files(tmp_path):
    root, cur = _codex(tmp_path)
    _mkver(root, "1.0.0-aarch64-apple-darwin", 3000)
    _mkver(root, "1.1.0-aarch64-apple-darwin", 2000)
    _mkver(root, "1.2.0-aarch64-apple-darwin", 1000)
    _mkver(root, "notes", 9000)
    (root / "stray.txt").write_text("x")
    cur.symlink_to(root / "1.2.0-aarch64-apple-darwin")
    cr.prune_codex(str(tmp_path))
    assert (root / "notes").exists() and (root / "stray.txt").exists()
    assert not (root / "1.0.0-aarch64-apple-darwin").exists()


def test_codex_link_outside_root_aborts_pruning(tmp_path):
    root, cur = _codex(tmp_path)
    for i, n in enumerate(["1.0.0-aarch64-apple-darwin", "1.1.0-aarch64-apple-darwin",
                           "1.2.0-aarch64-apple-darwin"]):
        _mkver(root, n, 3000 - i * 1000)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    cur.symlink_to(outside)
    assert cr.prune_codex(str(tmp_path)) == (0, 0)
    assert len(list(root.iterdir())) == 3


def test_codex_never_deletes_symlink_target_dir_link(tmp_path):
    root, cur = _codex(tmp_path)
    names = ["1.0.0-aarch64-apple-darwin", "1.1.0-aarch64-apple-darwin", "1.2.0-aarch64-apple-darwin"]
    for i, n in enumerate(names):
        _mkver(root, n, 3000 - i * 1000)
    cur.symlink_to(root / names[0])
    cr.prune_codex(str(tmp_path))
    assert (root / names[0]).exists()


def _cursor(home: Path):
    root = home / ".local/share/cursor-agent/versions"
    binp = home / ".local/bin"
    binp.mkdir(parents=True, exist_ok=True)
    return root, binp


def test_cursor_agent_keeps_linked_plus_one_with_file_symlinks(tmp_path):
    root, binp = _cursor(tmp_path)
    vs = ["2026.01.01-aaaa1", "2026.02.01-bbbb2", "2026.03.01-cccc3", "2026.04.01-dddd4"]
    for i, n in enumerate(vs):
        d = _mkver(root, n, 5000 - i * 1000)
        (d / "cursor-agent").write_text("bin")
    # active = oldest, via FILE symlinks inside the version dir
    (binp / "cursor-agent").symlink_to(root / vs[0] / "cursor-agent")
    (binp / "agent").symlink_to(root / vs[0] / "cursor-agent")
    cr.prune_cursor_agent(str(tmp_path))
    assert sorted(p.name for p in root.iterdir()) == [vs[0], vs[3]]


def test_cursor_agent_link_outside_root_prunes_nothing(tmp_path):
    root, binp = _cursor(tmp_path)
    for i, n in enumerate(["2026.01.01-aaaa1", "2026.02.01-bbbb2", "2026.03.01-cccc3"]):
        _mkver(root, n, 5000 - i * 1000)
    ext = tmp_path / "ext"
    ext.mkdir()
    (binp / "cursor-agent").symlink_to(ext)
    assert cr.prune_cursor_agent(str(tmp_path)) == (0, 0)


def test_agy_old_pruned_only_when_agy_works(tmp_path):
    binp = tmp_path / ".local/bin"
    binp.mkdir(parents=True)
    for n in ("agy.1.old", "agy.22.old"):
        (binp / n).write_text("x")
    (binp / "agy.keep").write_text("x")
    assert cr.prune_agy(str(tmp_path), False) == (0, 0)
    assert (binp / "agy.1.old").exists()
    count, _ = cr.prune_agy(str(tmp_path), True)
    assert count == 2 and (binp / "agy.keep").exists()


# -- cleanup.sh ------------------------------------------------------------------


def _cleanup(tmp_path, *extra, env_extra=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    fb = tmp_path / "fbin"
    fb.mkdir(exist_ok=True)
    npm = fb / "npm"
    npm.write_text("#!/bin/sh\n[ \"$1\" = --version ] && echo 11.0.0\nexit 0\n")
    npm.chmod(0o755)
    py = os.popen("command -v python3").read().strip()
    (fb / "python3").symlink_to(py) if not (fb / "python3").exists() and not (fb / "python3").is_symlink() else None
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ASCENDO_", "MAC_UPDATE_", "BUN_INSTALL"))}
    env.update({"HOME": str(home), "PATH": f"{fb}:/usr/bin:/bin:/usr/sbin:/sbin",
                "ASCENDO_NPM_BREW_DIRS": "", "MAC_UPDATE_TOOLCHAIN_HOME": str(tmp_path / "tc")})
    env.update(env_extra or {})
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    r = subprocess.run(["bash", str(CLEANUP), "--run-id", rid, "--trigger", "cli", "--profile", "safe",
                        "--output-dir", str(out), *extra], capture_output=True, text=True, env=env)
    side = out / rid / "cleanup__npm.json"
    return home, r, (json.loads(side.read_text()) if side.exists() else None)


def _seed_codex(home: Path):
    root, cur = _codex(home)
    names = ["1.0.0-aarch64-apple-darwin", "1.1.0-aarch64-apple-darwin", "1.2.0-aarch64-apple-darwin"]
    for i, n in enumerate(names):
        _mkver(root, n, 3000 - i * 1000)
    cur.symlink_to(root / names[2])
    return root


def test_cleanup_prunes_old_versions(tmp_path):
    home = tmp_path / "home"
    root = _seed_codex(home)
    _, r, sc = _cleanup(tmp_path)
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in root.iterdir()) == ["1.1.0-aarch64-apple-darwin", "1.2.0-aarch64-apple-darwin"]
    assert any(i["id"] == "prune:Codex" and i["status"] == "success" for i in sc["items"])
    assert any("Removed 1 old Codex versions" in m["text"] for m in sc["messages"])


def test_cleanup_dry_run_deletes_nothing(tmp_path):
    root = _seed_codex(tmp_path / "home")
    _, r, sc = _cleanup(tmp_path, "--dry-run")
    assert len(list(root.iterdir())) == 3
    assert any(i["id"] == "prune:cli-versions" and i["status"] == "planned" for i in sc["items"])


def test_cleanup_keep_env_opt_out(tmp_path):
    root = _seed_codex(tmp_path / "home")
    _, r, sc = _cleanup(tmp_path, env_extra={"ASCENDO_KEEP_CLI_VERSIONS": "1"})
    assert len(list(root.iterdir())) == 3
    assert not any(i["id"].startswith("prune:") for i in sc["items"])


def test_cleanup_keep_env_alias(tmp_path):
    root = _seed_codex(tmp_path / "home")
    _cleanup(tmp_path, env_extra={"MAC_UPDATE_KEEP_CLI_VERSIONS": "1"})
    assert len(list(root.iterdir())) == 3


def test_cleanup_surfaces_helper_failure_as_warning(tmp_path):
    _seed_codex(tmp_path / "home")
    # python3 wrapper that fails only for the retention helper script.
    fb = tmp_path / "fbin"
    fb.mkdir(exist_ok=True)
    real = os.popen("command -v python3").read().strip()
    w = fb / "python3"
    w.write_text(f"#!/bin/bash\nif [ \"$1\" = - ]; then s=$(cat); case \"$s\" in *cli_retention*) echo boom-import >&2; exit 1;; esac; printf '%s' \"$s\" | exec {real} \"$@\"; fi\nexec {real} \"$@\"\n")
    w.chmod(0o755)
    _, r, sc = _cleanup(tmp_path)
    assert r.returncode == 0, r.stderr
    assert any(m["level"] == "warn" and "CLI version pruning failed" in m["text"]
               and "boom-import" in m["text"] for m in sc["messages"])
