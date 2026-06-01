"""W2: PowerShell EXECUTION test for the release_feed version_regex contract.

Audit ASCENDO_ULTRA_REVIEW_2 sec.4: a configured ``version_regex`` that does
not match must fail loud (return ``$null`` => the row is classified
``skipped``/probe_unavailable by scripts/web/check.ps1) instead of silently
reporting the raw body as the candidate version. Mirrors the macOS
release_feed rc=28 contract.

Shells out to ``pwsh``/PowerShell to run ``ReleaseFeed.Regex.Tests.ps1``,
which dot-sources the real handler and exercises ``_RF-ApplyRegexTransform``.
Skips where no PowerShell binary is on PATH (the Linux unit leg); runs on the
``windows-latest`` CI leg.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_PS_TEST = Path(__file__).resolve().parent / "ps" / "ReleaseFeed.Regex.Tests.ps1"


def _powershell() -> str | None:
    for candidate in ("pwsh", "pwsh.exe", "powershell.exe", "powershell"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


@pytest.mark.skipif(_powershell() is None, reason="no PowerShell binary on PATH")
def test_release_feed_regex_fails_loud_on_no_match() -> None:
    ps = _powershell()
    assert _PS_TEST.is_file(), f"missing test script: {_PS_TEST}"
    res = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-File", str(_PS_TEST)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == 0, (
        f"release_feed regex PS test failed (exit {res.returncode}).\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    assert "RELEASE_FEED REGEX TESTS PASSED" in res.stdout, res.stdout
