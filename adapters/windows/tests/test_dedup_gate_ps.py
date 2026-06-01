"""PowerShell EXECUTION test for the deduplicator uninstall gate.

The first review (ASCENDO_ULTRA_REVIEW_2 §4) flagged that the Windows
winget/npm/pip ``apply.ps1`` scripts *execute* ``DEDUPLICATION_TASKS.json``
with **zero PowerShell execution tests** gating the merge. This shells out to
``pwsh`` (or Windows PowerShell) and runs ``Dedup.Gate.Tests.ps1``, which
exercises ``Get-AscendoDedupUninstalls`` for real and asserts the critical
safety property: *a stray tasks file with no opt-in returns NO uninstalls.*

Skips cleanly where no PowerShell binary is on PATH (e.g. the Linux unit
leg). The ``windows-latest`` CI leg has pwsh, so it runs there.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_PS_TEST = Path(__file__).resolve().parent / "ps" / "Dedup.Gate.Tests.ps1"


def _powershell() -> str | None:
    for candidate in ("pwsh", "pwsh.exe", "powershell.exe", "powershell"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


@pytest.mark.skipif(_powershell() is None, reason="no PowerShell binary on PATH")
def test_dedup_gate_blocks_stray_tasks_file() -> None:
    ps = _powershell()
    assert _PS_TEST.is_file(), f"missing test script: {_PS_TEST}"
    res = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-File", str(_PS_TEST)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    # The .ps1 prints per-scenario [ OK ]/[FAIL] lines and exits 0 only when
    # every scenario passes — including "stray file + no opt-in => NO uninstall".
    assert res.returncode == 0, (
        f"Dedup gate PS test failed (exit {res.returncode}).\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    assert "DEDUP GATE TESTS PASSED" in res.stdout, res.stdout
