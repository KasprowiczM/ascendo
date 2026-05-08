"""Tests for adapters/macos/scripts/npm/apply.sh.

Focus: the apply phase MUST NOT write to ~/.npmrc. Until 2026-05-09 it
called `npm config set prefix "$NPM_GLOBAL_PREFIX"` which writes that
line into ~/.npmrc — every nvm-init shell would then warn:

    Your user's .npmrc file (${HOME}/.npmrc) has a `globalconfig` and/or
    a `prefix` setting, which are incompatible with nvm.

The fix:
  1. Use NPM_CONFIG_PREFIX env var (precedence: env > .npmrc).
  2. Scrub any pre-existing `prefix=` / `globalconfig=` lines once per
     apply run so a stale value left by another tool can't trigger
     the warning either.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "npm" / "apply.sh"
LIB = ADAPTER_ROOT / "lib" / "ascendo_npm.sh"


@pytest.fixture(autouse=True)
def _require_bash() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash required")


def _make_fake_npm(tmp_path: Path, *, install_log: Path | None = None) -> Path:
    """Fake npm: logs argv to install_log; behaves correctly for the
    subset of commands apply.sh issues (config get, --version, ls -g,
    install -g, view, outdated)."""
    p = tmp_path / "fake_npm"
    log = install_log or Path("/dev/null")
    body = f"""#!/usr/bin/env bash
echo "$@" >> "{log}"
case "$1" in
    --version)
        echo "10.0.0"
        ;;
    ls)
        echo '{{}}'
        ;;
    outdated)
        echo '{{}}'
        ;;
    install)
        # mimic success
        exit 0
        ;;
    view)
        echo "1.0.0"
        ;;
    config)
        # config set prefix should NEVER be invoked any more; assert in
        # tests that this fake never sees it.
        if [ "$2" = "set" ] && [ "$3" = "prefix" ]; then
            echo "FAIL: apply.sh called npm config set prefix" >&2
            exit 99
        fi
        ;;
    *) ;;
esac
"""
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _make_minimal_manifest(tmp_path: Path) -> Path:
    """Override config/npm_global_clis.txt with a minimal one (one
    fake CLI). We point ascendo_npm_manifest_path's parent dir
    elsewhere via TOOLCHAIN_HOME indirection? No — the manifest path
    is hard-derived from BASH_SOURCE. Easier: point ascendo's home
    dir into tmp via env so we don't accidentally mutate the real
    ~/.local/share/mac-update."""
    return tmp_path / "fake_manifest.txt"  # not used directly here


def _run_apply_with_npmrc_setup(
    tmp_path: Path,
    *,
    npmrc_initial: str | None,
    extra_args: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run apply.sh with $HOME pointed at tmp_path so any ~/.npmrc
    writes land in the test sandbox. Returns (result, npmrc_path,
    install_log_path)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    npmrc = fake_home / ".npmrc"
    if npmrc_initial is not None:
        npmrc.write_text(npmrc_initial)

    install_log = tmp_path / "install.log"
    install_log.touch()
    fake_npm = _make_fake_npm(tmp_path, install_log=install_log)

    # Prepend fake bin dir so `npm` resolves to the fake.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "npm").symlink_to(fake_npm)

    out_dir = tmp_path / "out"
    rid = str(uuid.uuid4())

    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    # Point Ascendo's toolchain elsewhere so we don't touch real
    # ~/.local/share/mac-update during the test.
    env["MAC_UPDATE_TOOLCHAIN_HOME"] = str(tmp_path / "toolchain")

    args = [
        "bash", str(SCRIPT),
        "--run-id", rid, "--trigger", "cli",
        "--profile", "default",
        "--output-dir", str(out_dir),
        "--dry-run",  # important: skip real installs but still run bootstrap path
    ]
    if extra_args:
        args.extend(extra_args)
    res = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
    return res, npmrc, install_log


def test_apply_does_not_write_prefix_to_empty_npmrc(tmp_path: Path) -> None:
    """A pristine $HOME (no ~/.npmrc) must not gain one after apply."""
    res, npmrc, install_log = _run_apply_with_npmrc_setup(
        tmp_path, npmrc_initial=None,
    )
    assert res.returncode == 0, res.stderr
    # No .npmrc must have been created.
    assert not npmrc.exists(), \
        f"apply.sh created unexpected .npmrc:\n{npmrc.read_text()}"


def test_apply_strips_prefix_from_existing_npmrc(tmp_path: Path) -> None:
    """An existing ~/.npmrc with a prefix line must be scrubbed."""
    res, npmrc, _ = _run_apply_with_npmrc_setup(
        tmp_path,
        npmrc_initial="registry=https://registry.npmjs.org/\nprefix=/old/path\nfund=false\n",
    )
    assert res.returncode == 0, res.stderr
    contents = npmrc.read_text() if npmrc.exists() else ""
    assert "prefix=" not in contents, \
        f"apply.sh did not scrub prefix= line; .npmrc contains:\n{contents}"
    # Other lines preserved.
    assert "registry=" in contents
    assert "fund=" in contents


def test_apply_strips_globalconfig_from_existing_npmrc(tmp_path: Path) -> None:
    """globalconfig= triggers the same nvm warning; scrub it too."""
    res, npmrc, _ = _run_apply_with_npmrc_setup(
        tmp_path,
        npmrc_initial="globalconfig=/etc/npmrc\nregistry=https://registry.npmjs.org/\n",
    )
    assert res.returncode == 0, res.stderr
    contents = npmrc.read_text() if npmrc.exists() else ""
    assert "globalconfig" not in contents
    assert "registry=" in contents


def test_apply_never_calls_npm_config_set_prefix(tmp_path: Path) -> None:
    """Defense-in-depth: the fake npm exits 99 if it sees `npm config
    set prefix`. apply.sh should never invoke that subcommand any more."""
    res, _, install_log = _run_apply_with_npmrc_setup(
        tmp_path,
        npmrc_initial="registry=https://registry.npmjs.org/\n",
    )
    log_text = install_log.read_text() if install_log.exists() else ""
    # Apply may dispatch any number of subcommands; the only one we
    # forbid is `config set prefix`.
    for line in log_text.splitlines():
        assert not line.strip().startswith("config set prefix"), \
            f"apply.sh invoked forbidden 'npm config set prefix' (line: {line!r})"
    assert res.returncode == 0
