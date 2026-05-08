"""Tests for adapters/macos/lib/ascendo_npm.sh helpers.

Focus: ascendo_npm_scrub_npmrc — added 2026-05-09 after operators
hit nvm warnings on every shell start because Ascendo's apply.sh was
writing `prefix=` into ~/.npmrc via `npm config set prefix` (legacy
behavior). The scrub helper strips `prefix=` and `globalconfig=` lines
each apply run; combined with NPM_CONFIG_PREFIX env var (precedence
env > .npmrc) this leaves nvm happy without us giving up our toolchain
prefix.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
LIB = ADAPTER_ROOT / "lib" / "ascendo_npm.sh"


def _run_with_npmrc(npmrc_content: str | None, cmd: str, tmp_path: Path) -> tuple[int, str, Path]:
    """Run a bash snippet with $ASCENDO_NPMRC_PATH overridden to a tmp file.

    Returns (exit_code, stdout, npmrc_path). The npmrc file is left on
    disk so the caller can inspect contents post-scrub.
    """
    npmrc = tmp_path / "test_npmrc"
    if npmrc_content is not None:
        npmrc.write_text(npmrc_content, encoding="utf-8")
    script = f'''
        set -uo pipefail
        export ASCENDO_NPMRC_PATH="{npmrc}"
        . "{LIB}"
        {cmd}
    '''
    res = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, check=False,
    )
    return res.returncode, res.stdout, npmrc


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_scrub_removes_prefix_line(tmp_path: Path) -> None:
    """Plain `prefix=...` line is stripped, other lines preserved."""
    rc, _, npmrc = _run_with_npmrc(
        "registry=https://registry.npmjs.org/\nprefix=/old/path\nfund=false\n",
        "ascendo_npm_scrub_npmrc",
        tmp_path,
    )
    assert rc == 0
    contents = npmrc.read_text() if npmrc.exists() else ""
    assert "prefix=" not in contents
    assert "registry=" in contents
    assert "fund=" in contents


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_scrub_removes_globalconfig_line(tmp_path: Path) -> None:
    """`globalconfig=...` is also incompatible with nvm; scrubbed too."""
    rc, _, npmrc = _run_with_npmrc(
        "registry=https://registry.npmjs.org/\nglobalconfig=/etc/npmrc\n",
        "ascendo_npm_scrub_npmrc",
        tmp_path,
    )
    assert rc == 0
    contents = npmrc.read_text() if npmrc.exists() else ""
    assert "globalconfig=" not in contents
    assert "registry=" in contents


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_scrub_handles_whitespace_around_equals(tmp_path: Path) -> None:
    """Both `prefix = X` and `prefix=X` shapes are stripped (legacy
    code may have written either form)."""
    rc, _, npmrc = _run_with_npmrc(
        "prefix = /old/path\n  prefix=/another/path\nfund=false\n",
        "ascendo_npm_scrub_npmrc",
        tmp_path,
    )
    assert rc == 0
    contents = npmrc.read_text() if npmrc.exists() else ""
    assert "prefix" not in contents
    assert "fund=" in contents


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_scrub_deletes_empty_npmrc(tmp_path: Path) -> None:
    """When the only lines were prefix/globalconfig, scrub leaves no
    file behind — an empty .npmrc is functionally equivalent to none
    and tools that probe its existence (some nvm-init scripts do)
    work better without it."""
    rc, _, npmrc = _run_with_npmrc(
        "prefix=/old/path\nglobalconfig=/etc/npmrc\n",
        "ascendo_npm_scrub_npmrc",
        tmp_path,
    )
    assert rc == 0
    assert not npmrc.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_scrub_no_npmrc_is_noop(tmp_path: Path) -> None:
    """Missing ~/.npmrc returns 0 and creates nothing — idempotent."""
    rc, _, npmrc = _run_with_npmrc(
        None,  # do not create file
        "ascendo_npm_scrub_npmrc",
        tmp_path,
    )
    assert rc == 0
    assert not npmrc.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_scrub_clean_npmrc_is_idempotent(tmp_path: Path) -> None:
    """A .npmrc with no prefix/globalconfig is left byte-for-byte unchanged
    except for normalization of trailing newlines (grep behavior)."""
    original = "registry=https://registry.npmjs.org/\nfund=false\n"
    rc, _, npmrc = _run_with_npmrc(
        original,
        "ascendo_npm_scrub_npmrc && ascendo_npm_scrub_npmrc",  # twice
        tmp_path,
    )
    assert rc == 0
    contents = npmrc.read_text()
    assert "registry=" in contents
    assert "fund=" in contents
    assert "prefix" not in contents


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_npmrc_path_honors_override(tmp_path: Path) -> None:
    """ascendo_npm_npmrc_path returns ASCENDO_NPMRC_PATH when set,
    falls back to $HOME/.npmrc otherwise."""
    custom = tmp_path / "custom_npmrc"
    rc, out, _ = _run_with_npmrc(
        "",
        f'ASCENDO_NPMRC_PATH="{custom}" ascendo_npm_npmrc_path',
        tmp_path,
    )
    assert rc == 0
    assert str(custom) in out
