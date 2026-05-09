"""Regression tests for the Windows apply.ps1 parity fixes.

These tests cover the parity work that brings the Windows adapter in
line with macOS Sesjas 30-44:

1. **Stderr capture on non-zero exit.** All four apply.ps1 scripts
   (winget / msstore / arp / windows_update) must capture stderr
   separately from stdout and surface the last 12 non-empty lines
   (capped at 1500 chars) on the failed item via ``Add-SidecarMessage``
   at level ``error`` (or as a per-item ``Messages`` entry). Mirrors
   ``adapters/macos/scripts/npm/apply.sh`` lines ~170-185.

2. **Pre-dispatch up_to_date guard.** ``winget/apply.ps1`` (and
   defensively ``msstore/apply.ps1``) must check ``installed ==
   target`` BEFORE invoking ``winget upgrade`` and skip with status
   ``up_to_date`` if equal — saving the subprocess spawn and matching
   the macOS web/apply.sh pre-dispatch pattern from Sesja 40.

These tests are deliberately STATIC: they read the .ps1 source and
assert the shape of the fix is present. End-to-end behavioural
verification requires real Windows (PowerShell + winget); operators
run that via ``bin/validate-windows.ps1``.

Skipped on macOS / Linux only when explicitly testing PowerShell-
dependent behaviour; the static-text checks here are pure-text and
run anywhere.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# Resolve the repo's adapter directory robustly. The tests live two
# levels above scripts/<category>/apply.ps1.
_THIS_FILE = Path(__file__).resolve()
_WINDOWS_ADAPTER_DIR = _THIS_FILE.parent.parent  # adapters/windows/
_SCRIPTS_DIR = _WINDOWS_ADAPTER_DIR / "scripts"
_LIB_DIR = _WINDOWS_ADAPTER_DIR / "lib"


def _read_ps1(relpath: str) -> str:
    """Read a PS1 file; tolerate CRLF line endings (gitattributes pins
    *.ps1 to crlf so the file IS CRLF on disk in real checkouts).
    """
    p = _SCRIPTS_DIR / relpath
    assert p.exists(), f"PowerShell script not found: {p}"
    raw = p.read_bytes().decode("utf-8")
    # Normalise so regex .* matches across both LF and CRLF.
    return raw.replace("\r\n", "\n")


def _read_lib(relpath: str) -> str:
    p = _LIB_DIR / relpath
    assert p.exists(), f"PowerShell module not found: {p}"
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


# ───────────────────────────────────────────────────────────────────────
# Stderr capture parity (Sesja 34 macOS pattern)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "script_relpath, must_contain_helper",
    [
        # winget: top-level helper Get-StderrTailExcerpt + Start-Process
        ("winget/apply.ps1", "Get-StderrTailExcerpt"),
        # msstore: local _Get-StderrTailExcerpt + _Invoke-MsStoreUpgrade
        ("msstore/apply.ps1", "_Get-StderrTailExcerpt"),
        # arp: local _Get-StderrTailExcerpt + Start-Process redirect
        ("arp/apply.ps1", "_Get-StderrTailExcerpt"),
    ],
)
def test_subprocess_apply_scripts_capture_stderr_separately(
    script_relpath: str, must_contain_helper: str
) -> None:
    """Each subprocess-based apply script must:

    1. Define a stderr-tail helper (truncates last 12 non-empty lines).
    2. Use ``Start-Process`` with both ``-RedirectStandardOutput`` and
       ``-RedirectStandardError`` so stderr is captured separately.
    3. Include "stderr (last 12 lines)" in the failure message
       formatting so the operator can find the excerpt.
    """
    src = _read_ps1(script_relpath)

    # 1. The stderr-tail helper must be defined.
    assert must_contain_helper in src, (
        f"{script_relpath} must define {must_contain_helper}; this is the "
        "shared 'last 12 non-empty stderr lines, capped at 1500 chars' "
        "helper. macOS reference: adapters/macos/scripts/npm/apply.sh "
        "lines ~170-185."
    )

    # 2. -RedirectStandardError must be wired into the Start-Process call.
    assert "-RedirectStandardError" in src, (
        f"{script_relpath} must capture stderr via "
        "Start-Process -RedirectStandardError <tempfile> so failures "
        "surface the actual error reason instead of bare exit codes."
    )
    assert "-RedirectStandardOutput" in src, (
        f"{script_relpath} must redirect stdout to a separate tempfile "
        "so the pipe to -RedirectStandardError is unambiguous."
    )

    # 3. The stderr excerpt must reach the sidecar message channel.
    # Permit either Add-SidecarMessage with stderr context OR a per-item
    # Messages entry that mentions "stderr".
    assert re.search(r"stderr\s*\(last\s*12\s*lines\)", src, re.IGNORECASE), (
        f"{script_relpath} must surface the stderr excerpt in a sidecar "
        "message labelled 'stderr (last 12 lines): ...' so operators can "
        "identify the upstream tail."
    )

    # 4. Sanity: the script must clean up its tempfile (Remove-Item -ErrorAction SilentlyContinue).
    assert "Remove-Item" in src and "SilentlyContinue" in src, (
        f"{script_relpath} must clean up its stderr tempfile via "
        "Remove-Item -ErrorAction SilentlyContinue (best-effort; tempfiles "
        "shouldn't leak even on the failure path)."
    )


def test_windows_update_apply_uses_module_stderr_helper() -> None:
    """windows_update/apply.ps1 is in-process (PSWindowsUpdate cmdlet),
    not subprocess-based. It captures stderr via the module's
    ``Install-WindowsUpdateBatch -ErrorVariable`` + ``Get-WUInstallStderr``
    accessor pattern.
    """
    apply_src = _read_ps1("windows_update/apply.ps1")
    module_src = _read_lib("AscendoPSWindowsUpdate.psm1")

    # The module must capture stderr via -ErrorVariable.
    assert "-ErrorVariable" in module_src, (
        "AscendoPSWindowsUpdate.psm1 must use -ErrorVariable to capture "
        "non-terminating errors from Install-WindowsUpdate. Mirrors the "
        "macOS Sesja 34 stderr-tail pattern."
    )

    # The accessor must exist and be exported.
    assert "function Get-WUInstallStderr" in module_src, (
        "AscendoPSWindowsUpdate.psm1 must export Get-WUInstallStderr so "
        "the apply script can surface the stderr tail in the sidecar."
    )
    assert "'Get-WUInstallStderr'" in module_src, (
        "Get-WUInstallStderr must be listed in Export-ModuleMember."
    )

    # The apply script must consume the accessor in BOTH the catch path
    # AND the per-item failed path.
    assert "Get-WUInstallStderr" in apply_src, (
        "windows_update/apply.ps1 must call Get-WUInstallStderr to surface "
        "the cmdlet's error stream tail in the sidecar."
    )
    # Should appear at least twice — once in catch / 0-results, once per item.
    assert apply_src.count("Get-WUInstallStderr") >= 2, (
        "windows_update/apply.ps1 should consume Get-WUInstallStderr in "
        "BOTH the catch / zero-results path AND the per-item failed path. "
        "Got count=%d." % apply_src.count("Get-WUInstallStderr")
    )

    # The user-visible message label must match the rest of the adapter.
    assert re.search(r"stderr\s*\(last\s*12\s*lines\)", apply_src, re.IGNORECASE), (
        "windows_update/apply.ps1 must label its stderr excerpt "
        "'stderr (last 12 lines): ...' for parity with the other 3 "
        "apply scripts."
    )


# ───────────────────────────────────────────────────────────────────────
# Pre-dispatch up_to_date guard (Sesja 40 macOS pattern)
# ───────────────────────────────────────────────────────────────────────


def test_winget_apply_pre_dispatch_up_to_date_guard() -> None:
    """winget/apply.ps1 must short-circuit packages where installed ==
    target before invoking ``winget upgrade``. Saves a subprocess spawn
    and matches macOS web/apply.sh's pre-dispatch behaviour (Sesja 40).
    """
    src = _read_ps1("winget/apply.ps1")

    # The guard's marker comment OR the literal status emit must be present.
    # Use a robust regex over the apply loop.
    has_guard = bool(
        re.search(
            r"\$current\s+-eq\s+\$target",
            src,
        )
    )
    assert has_guard, (
        "winget/apply.ps1 must compare installed vs target version BEFORE "
        "calling Invoke-WingetMutation. macOS reference: "
        "adapters/macos/scripts/web/apply.sh lines ~134-138."
    )

    # The guard must emit Status='up_to_date' (not 'success' or 'skipped')
    # so the SPA + REPORT.md classify it correctly.
    assert "'up_to_date'" in src, (
        "winget/apply.ps1's up_to_date guard must emit Status='up_to_date' "
        "so REPORT.md can categorise it under 'already up-to-date'."
    )

    # Sanity: the message text must indicate already-latest.
    assert "already at latest version" in src, (
        "winget/apply.ps1 should attach a clear 'already at latest version' "
        "message to up_to_date items so the operator sees why no upgrade ran."
    )


def test_msstore_apply_pre_dispatch_up_to_date_guard() -> None:
    """msstore/apply.ps1 honours the same up_to_date guard defensively.

    Get-WingetUpgradable rarely emits cur==tgt rows, but the macOS
    pattern keeps the apply path honest if the feed is ever
    inconsistent. (Pure parity — no behavioural regression on green
    paths.)
    """
    src = _read_ps1("msstore/apply.ps1")

    has_guard = bool(
        re.search(
            r"\$curVersion\s+-eq\s+\$tgtVersion",
            src,
        )
    )
    assert has_guard, (
        "msstore/apply.ps1 must guard against cur==tgt before invoking "
        "winget upgrade --source msstore."
    )
    assert "'up_to_date'" in src, "msstore/apply.ps1 must emit up_to_date status."
    assert "already at latest version" in src


def test_arp_apply_does_not_carry_up_to_date_guard() -> None:
    """ARP apply is an explicit-uninstall flow (-ItemFilter only), not
    an upgrade flow. 'already at latest' has no semantic meaning here,
    so the up_to_date guard MUST NOT be added (would silently drop
    explicit removals, bug class).

    Negative regression test: documents the intentional asymmetry vs
    winget/msstore.
    """
    src = _read_ps1("arp/apply.ps1")
    # No regex over '$current -eq $target' — this script's variables
    # don't carry that semantic anyway. Just sanity that we did NOT
    # accidentally copy the guard pattern.
    assert "already at latest version" not in src, (
        "arp/apply.ps1 must NOT carry the up_to_date message — it's an "
        "uninstall flow, not an upgrade flow."
    )
    # But it MUST have the stderr-capture (separate concern).
    assert "_Get-StderrTailExcerpt" in src


# ───────────────────────────────────────────────────────────────────────
# Generic structural sanity
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "script_relpath",
    [
        "winget/apply.ps1",
        "msstore/apply.ps1",
        "arp/apply.ps1",
        "windows_update/apply.ps1",
    ],
)
def test_apply_script_still_imports_ascendojson(script_relpath: str) -> None:
    """Every apply script must import AscendoJson.psm1 — it provides the
    sidecar emitter. The parity work shouldn't have removed the import.
    """
    src = _read_ps1(script_relpath)
    assert "AscendoJson.psm1" in src
    assert "Add-SidecarItem" in src or "Save-Sidecar" in src
