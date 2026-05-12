"""Static analysis tests for the Windows watchdog heartbeat (mirror of
Ubuntu Sesja 55 `>>> still running (Ns)` keepalive).

The heartbeat helper lives in ``adapters/windows/lib/AscendoJson.psm1``
(``Start-AscendoHeartbeat`` / ``Stop-AscendoHeartbeat``). It must be:

  1. Exported from the module so phase scripts can `Import-Module` and
     call it without dot-sourcing internals.
  2. Wired into each of the 4 apply.ps1 scripts (winget / msstore / arp /
     windows_update) wrapping the long-running mutating section.
  3. Stopped inside a ``finally`` block so a thrown exception during the
     long-running section cannot leak the runspace.

These tests are pure static analysis on the .ps1 source — no pwsh
required. They run on any OS so the CI grid doesn't need a Windows
runner just to assert the heartbeat wrap.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_TESTS_DIR = Path(__file__).resolve().parent
_WINDOWS_ADAPTER = _TESTS_DIR.parent
_LIB = _WINDOWS_ADAPTER / "lib"
_SCRIPTS = _WINDOWS_ADAPTER / "scripts"

ASCENDO_JSON_PSM1 = _LIB / "AscendoJson.psm1"

APPLY_SCRIPTS = [
    _SCRIPTS / "winget" / "apply.ps1",
    _SCRIPTS / "msstore" / "apply.ps1",
    _SCRIPTS / "arp" / "apply.ps1",
    _SCRIPTS / "windows_update" / "apply.ps1",
]


def test_heartbeat_helpers_exported_from_ascendojson() -> None:
    """``Start-AscendoHeartbeat`` and ``Stop-AscendoHeartbeat`` must both
    appear inside an ``Export-ModuleMember -Function`` block in
    AscendoJson.psm1. Without the export, ``Import-Module`` won't surface
    them to dot-sourced phase scripts and every wire-up call would no-op
    silently with an "unrecognized cmdlet" error caught by the script's
    outer try/catch — a heartbeat that never fires is a regression we
    can't see from the outside.
    """
    assert ASCENDO_JSON_PSM1.exists(), f"missing fixture: {ASCENDO_JSON_PSM1}"
    text = ASCENDO_JSON_PSM1.read_text(encoding="utf-8")

    # Find the Export-ModuleMember -Function block and check both names
    # appear inside it. The block can span multiple lines as an array.
    export_block_match = re.search(
        r"Export-ModuleMember\s+-Function\s+@\(([^)]*)\)",
        text,
        flags=re.DOTALL,
    )
    assert export_block_match, (
        f"{ASCENDO_JSON_PSM1}: no `Export-ModuleMember -Function @( ... )` "
        f"block found — has the module been refactored?"
    )
    block = export_block_match.group(1)

    missing = []
    for name in ("Start-AscendoHeartbeat", "Stop-AscendoHeartbeat"):
        if name not in block:
            missing.append(name)

    if missing:
        pytest.fail(
            f"{ASCENDO_JSON_PSM1}: heartbeat helper(s) not exported: "
            f"{', '.join(missing)}.\n"
            f"--- Export-ModuleMember block ---\n{block}\n--- end block ---"
        )


@pytest.mark.parametrize(
    "script_path",
    APPLY_SCRIPTS,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_apply_scripts_wrap_long_operations_with_heartbeat(
    script_path: Path,
) -> None:
    """Every apply.ps1 must call both ``Start-AscendoHeartbeat`` AND
    ``Stop-AscendoHeartbeat``. A start without a stop would leak the
    runspace; a stop without a start would be dead code. The presence
    of both proves the wire-up landed.
    """
    assert script_path.exists(), f"missing fixture: {script_path}"
    text = script_path.read_text(encoding="utf-8")

    missing = []
    if "Start-AscendoHeartbeat" not in text:
        missing.append("Start-AscendoHeartbeat")
    if "Stop-AscendoHeartbeat" not in text:
        missing.append("Stop-AscendoHeartbeat")

    if missing:
        pytest.fail(
            f"{script_path}: missing heartbeat helper call(s): "
            f"{', '.join(missing)}.\n"
            f"Operators won't see live progress during long {script_path.parent.name} "
            f"apply runs — the SPA Run Center terminal will look frozen. "
            f"Wire the heartbeat around the longest-running invocation per "
            f"AscendoJson.psm1's Start-AscendoHeartbeat doc."
        )


@pytest.mark.parametrize(
    "script_path",
    APPLY_SCRIPTS,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_heartbeat_stop_is_in_finally_block(script_path: Path) -> None:
    """``Stop-AscendoHeartbeat`` must appear inside a ``finally { ... }``
    block. If it were on a normal sequential line after the long op,
    an exception thrown by the mutating call would skip the stop and
    leak the runspace until the parent pwsh.exe process exits (often
    minutes later, well after the SSE stream has already closed).

    Heuristic: ``\\bfinally\\s*\\{[^}]*Stop-AscendoHeartbeat`` in
    multiline DOTALL mode. The ``[^}]*`` cap means we tolerate any
    body content inside the finally as long as no ``}`` closes it
    before the Stop call — which catches the common case of
    ``finally { Stop-AscendoHeartbeat $hb }`` and the slightly fancier
    ``finally { ...comment... ; Stop-AscendoHeartbeat $hb }``.
    """
    assert script_path.exists(), f"missing fixture: {script_path}"
    text = script_path.read_text(encoding="utf-8")

    pattern = re.compile(
        r"\bfinally\s*\{[^}]*Stop-AscendoHeartbeat",
        flags=re.DOTALL,
    )

    if not pattern.search(text):
        pytest.fail(
            f"{script_path}: ``Stop-AscendoHeartbeat`` is not inside a "
            f"``finally {{ ... }}`` block. A thrown exception during the "
            f"long-running apply call would skip the stop and leak the "
            f"heartbeat runspace. Wrap the mutating section in "
            f"``try {{ ... }} finally {{ Stop-AscendoHeartbeat $hb }}``."
        )
