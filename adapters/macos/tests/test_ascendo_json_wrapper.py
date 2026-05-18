"""Smoke test that ascendo_json.sh round-trips through parse_sidecar()."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ADAPTER_ROOT / "lib" / "ascendo_json.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_wrapper_exists() -> None:
    assert WRAPPER.is_file(), f"missing {WRAPPER}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_wrapper_round_trip(tmp_path: Path) -> None:
    """Source the wrapper, init/add/save, parse the result through Pydantic."""
    out_dir = tmp_path / "runs"
    run_id = "00000000-0000-0000-0000-000000000042"
    script = f'''
        set -o pipefail
        export TMPDIR="{tmp_path}"
        . "{WRAPPER}"
        json_init "check" "brew" "{run_id}" "cli" "default" \
                  "brew" "4.4.0" \
                  "macbook.local" "macos" "14.5" "arm64" "mk" "false"
        json_add_item "node" "20.10.0" "21.0.0" "planned" "brew" "formula"
        json_add_message "info" "test message"
        json_save "{out_dir}"
    '''
    res = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, f"wrapper failed: {res.stderr}\n{res.stdout}"

    sidecar_path = out_dir / run_id / "check__brew.json"
    assert sidecar_path.is_file(), f"missing {sidecar_path}"

    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        sc = parse_sidecar(sidecar_path.read_text())
    finally:
        sys.path.pop(0)

    # schema_ is the Pydantic field name (alias "schema" in JSON)
    assert sc.schema_.value == "ascendo/v1"
    assert sc.phase.value == "check"
    assert sc.category.value == "brew"
    assert len(sc.items) == 1
    assert sc.items[0].id == "node"


# ---------------------------------------------------------------------------
# _ascendo_sudo_warm — Touch-ID-first regression guard
#
# Pins the fix for the "osascript keeps asking for a password" regression:
# when the dashboard spawns an apply script it has NO controlling TTY, yet
# `pam_tid.so` (Touch ID) must still be tried via `sudo -v` BEFORE the
# password-only osascript SecurityAgent fallback. The osascript fallback
# must be strictly opt-in (ASCENDO_SUDO_ALLOW_GUI=1) and never auto-fire.
# ---------------------------------------------------------------------------


def _run_warm(
    tmp_path: Path,
    *,
    sudo_v_rc: int,
    allow_gui: bool = False,
    askpass: bool = False,
) -> tuple[int, bool]:
    """Run _ascendo_sudo_warm in a NO-controlling-TTY process.

    `os.setsid` (preexec) detaches the controlling terminal, mirroring how
    `ascendo web start` / the Tauri sidecar spawn the dashboard. Returns
    (warm_return_code, osascript_was_invoked).
    """
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    (fake / "sudo").write_text(
        "#!/bin/bash\n"
        '[ "$1" = "-n" ] && [ "$2" = "-v" ] && exit 1\n'  # never pre-cached
        f'[ "$1" = "-v" ] && exit {sudo_v_rc}\n'           # Touch ID path
        "exit 0\n"
    )
    osa_marker = tmp_path / "osascript_called"
    (fake / "osascript").write_text(
        f"#!/bin/bash\necho called > {osa_marker}\nexit 0\n"
    )
    for name in ("sudo", "osascript"):
        os.chmod(fake / name, 0o755)

    env = {
        "PATH": f"{fake}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
    }
    if allow_gui:
        env["ASCENDO_SUDO_ALLOW_GUI"] = "1"
    if askpass:
        ask = tmp_path / "ask.sh"
        ask.write_text("#!/bin/bash\necho pw\n")
        os.chmod(ask, 0o755)
        env["SUDO_ASKPASS"] = str(ask)

    script = f'. "{WRAPPER}"; _ascendo_sudo_warm; echo "RC=$?"'
    res = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        preexec_fn=os.setsid,  # noqa: PLW1509 — intentional: drop controlling TTY
        env=env,
    )
    rc_line = [
        ln for ln in res.stdout.splitlines() if ln.startswith("RC=")
    ]
    warm_rc = int(rc_line[-1].split("=")[1]) if rc_line else -1
    return warm_rc, osa_marker.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.skipif(
    not hasattr(os, "setsid"), reason="POSIX setsid required"
)
def test_warm_touchid_path_used_without_tty_no_osascript(
    tmp_path: Path,
) -> None:
    """THE regression guard: Touch ID configured + no TTY (dashboard) +
    no GUI flag => warm succeeds via `sudo -v`, osascript NEVER fires."""
    rc, osa = _run_warm(tmp_path, sudo_v_rc=0)
    assert rc == 0
    assert osa is False, "osascript SecurityAgent popup must not fire"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.skipif(
    not hasattr(os, "setsid"), reason="POSIX setsid required"
)
def test_warm_no_touchid_no_gui_flag_stays_silent(tmp_path: Path) -> None:
    """No Touch ID + GUI flag unset: stay silent (no osascript). The
    subsequent sudo raises its own error / the password modal handles it."""
    rc, osa = _run_warm(tmp_path, sudo_v_rc=1)
    assert rc == 0  # best-effort contract: always returns 0
    assert osa is False, "osascript must not auto-fire when GUI flag unset"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.skipif(
    not hasattr(os, "setsid"), reason="POSIX setsid required"
)
def test_warm_osascript_is_strict_opt_in(tmp_path: Path) -> None:
    """Explicit ASCENDO_SUDO_ALLOW_GUI=1 still reaches the osascript
    escape hatch (no Touch ID available) — opt-in preserved, not removed."""
    rc, osa = _run_warm(tmp_path, sudo_v_rc=1, allow_gui=True)
    assert rc == 0
    assert osa is True, "explicit opt-in must still allow the GUI fallback"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.skipif(
    not hasattr(os, "setsid"), reason="POSIX setsid required"
)
def test_warm_askpass_short_circuits_before_biometric(
    tmp_path: Path,
) -> None:
    """Password-modal path (SUDO_ASKPASS set): warm returns at step 0b
    without attempting Touch ID and without osascript — the macOS-without-
    Touch-ID / password fallback that mirrors the Windows/Ubuntu flow."""
    rc, osa = _run_warm(tmp_path, sudo_v_rc=1, askpass=True)
    assert rc == 0
    assert osa is False
