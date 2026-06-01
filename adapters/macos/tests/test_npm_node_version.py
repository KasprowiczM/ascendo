"""Regression tests for Node version selection (Sesja 89).

A full-update run (409da30e) verify-failed on `node`: check/plan
computed target 26.3.0 (latest Current) but the apply phase ran a
hardcoded `n lts` which installed 24.16.0 (latest LTS), so the toolchain
node was effectively downgraded and verify (recomputing target 26.3.0)
reported `failed`. Two combining defects:

  1. `ascendo_npm.sh::ascendo_npm_node_latest_version` used
     `installed_major -ge lts_major` — when the user is *on* the LTS
     line (installed major == LTS major, e.g. 24 == 24) it wrongly
     concluded "Current track" and targeted pre-LTS 26.x. A user on the
     active LTS line should track LTS. Must be `-gt`.

  2. `scripts/npm/apply.sh::apply_native_node` ran a hardcoded `n lts`
     instead of installing the version the picker computed (`$_latest`),
     so apply installed a *different* version than check/plan/verify
     expected. Apply must install the picker's target (falling back to
     `lts` only when the picker returns nothing — offline / fresh box).

These tests fake an isolated `n`/node toolchain under
MAC_UPDATE_TOOLCHAIN_HOME so no real Node is installed or probed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
LIB = ADAPTER_ROOT / "lib" / "ascendo_npm.sh"
APPLY = ADAPTER_ROOT / "scripts" / "npm" / "apply.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")


def _mk_toolchain(tmp_path: Path, *, node_ver: str, lts: str, latest: str,
                  n_log: Path | None = None) -> Path:
    """Create a fake toolchain: <tc>/node/bin/{node,n}.

    The fake `node --version` prints v<node_ver>. The fake `n` answers
    `--lts`/`--latest` with the given versions and, for any other arg
    (an install invocation), appends that arg to n_log and exits 0.
    Returns the toolchain home to pass as MAC_UPDATE_TOOLCHAIN_HOME.
    """
    tc = tmp_path / "tc"
    nbin = tc / "node" / "bin"
    nbin.mkdir(parents=True)

    node = nbin / "node"
    node.write_text(f'#!/usr/bin/env bash\n[ "$1" = "--version" ] && echo "v{node_ver}"\n')
    os.chmod(node, 0o755)

    log = n_log or Path("/dev/null")
    n = nbin / "n"
    n.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        f'  --lts) echo "{lts}" ;;\n'
        f'  --latest) echo "{latest}" ;;\n'
        f'  *) echo "$*" >> "{log}" ;;\n'
        "esac\n"
    )
    os.chmod(n, 0o755)
    return tc


def _picker(tmp_path: Path, *, node_ver: str, lts: str, latest: str) -> str:
    tc = _mk_toolchain(tmp_path, node_ver=node_ver, lts=lts, latest=latest)
    script = f'''
        set -uo pipefail
        export MAC_UPDATE_TOOLCHAIN_HOME="{tc}"
        . "{LIB}"
        ascendo_npm_node_latest_version
    '''
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


# -- defect 2: track picker ---------------------------------------------------

def test_picker_lts_line_user_stays_on_lts(tmp_path: Path) -> None:
    """Installed major == LTS major (user on the active LTS line) must
    target the latest LTS, NOT jump to the pre-LTS Current line."""
    assert _picker(tmp_path, node_ver="24.16.0", lts="24.16.0", latest="26.3.0") == "24.16.0"


def test_picker_current_line_user_stays_on_current(tmp_path: Path) -> None:
    """Installed major > LTS major (genuinely ahead, on Current) keeps
    targeting Current — Sesja 72 behavior preserved."""
    assert _picker(tmp_path, node_ver="26.2.0", lts="24.16.0", latest="26.3.0") == "26.3.0"


def test_picker_old_lts_user_offered_current_lts(tmp_path: Path) -> None:
    """A user on an older LTS line (22.x) is offered the current LTS
    (24.x), not the pre-LTS Current line."""
    assert _picker(tmp_path, node_ver="22.10.0", lts="24.16.0", latest="26.3.0") == "24.16.0"


# -- defect 1: apply installs the picker's target -----------------------------

def _extract_func(text: str, name: str) -> str:
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(f"{name}() {{"))
    for j in range(start + 1, len(lines)):
        if lines[j] == "}":
            return "\n".join(lines[start:j + 1])
    raise AssertionError(f"could not extract {name}() from apply.sh")


def test_apply_installs_picker_target_not_hardcoded_lts(tmp_path: Path) -> None:
    """apply_native_node must invoke `n <picker-target>`, not a hardcoded
    `n lts`. With a Current-line node (26.2.0) the picker resolves 26.3.0,
    so the `n` install invocation must carry 26.3.0 — never bare 'lts'."""
    n_log = tmp_path / "n_install.log"
    n_log.touch()
    tc = _mk_toolchain(tmp_path, node_ver="26.2.0", lts="24.16.0",
                       latest="26.3.0", n_log=n_log)
    func = _extract_func(APPLY.read_text(), "apply_native_node")

    script = f'''
        set -uo pipefail
        export MAC_UPDATE_TOOLCHAIN_HOME="{tc}"
        . "{LIB}"
        DRY_RUN=false
        NPM_BIN="{tc}/node/bin/n"            # any executable; bootstrap guard only
        NPM_GLOBAL_PREFIX="{tc}/node"        # so _N=<prefix>/bin/n resolves to fake n
        classify() {{ [ "$1" = "$2" ] && echo up_to_date || echo planned; }}
        _stream_emit() {{ :; }}
        _stream_tee() {{ cat >/dev/null; }}
        _run_npm() {{ :; }}                  # pretend `npm install -g n` succeeded
        json_add_item() {{ :; }}
        json_add_message() {{ :; }}
        {func}
        apply_native_node "node"
    '''
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    installed_arg = n_log.read_text().strip()
    assert installed_arg == "26.3.0", (
        f"apply invoked `n {installed_arg!r}`; expected the picker target "
        f"'26.3.0' (hardcoded 'lts' is the bug)"
    )
