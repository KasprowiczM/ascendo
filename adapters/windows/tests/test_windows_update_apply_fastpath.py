"""Regression tests for the windows_update/apply.ps1 fast-path pre-check.

## Bug (run e5f0e0f1, 2026-05-12)

Operator ran a safe-profile full update via the dashboard. The orchestrator
got through winget/msstore/npm/pip/web/registry_arp apply, then started
``windows_update/apply.ps1``. The check phase had already reported 4 KBs all
``up_to_date`` (0 pending). But ``apply.ps1`` unconditionally called
PSWindowsUpdate's ``Install-WindowsUpdate`` cmdlet through
``Install-WindowsUpdateBatch``, which wedged for many minutes inside the
Windows Update Agent COM search even though there was nothing to install.
The operator killed the dashboard, the sidecar never landed, and the
orchestrator never got to subsequent categories (plugin).

## Fix

In the real-run branch of ``apply.ps1``, before calling
``Install-WindowsUpdateBatch``, scan via ``Get-PendingWindowsUpdates``
(the same read-only call ``check.ps1`` uses). If the pending set is
empty (or empty after applying ``-ItemFilter``), emit a success sidecar
with ``items=[]`` plus an info message and exit 0. ``Install-WindowsUpdate``
is never called in the no-op case.

These tests are pure static analysis on the .ps1 source — they read the
file and assert the fast-path code lands in the right place. No pwsh
required; safe on any CI runner.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_APPLY_PS1 = (
    _REPO_ROOT
    / "adapters"
    / "windows"
    / "scripts"
    / "windows_update"
    / "apply.ps1"
)


@pytest.fixture(scope="module")
def apply_text() -> str:
    assert _APPLY_PS1.exists(), f"missing fixture: {_APPLY_PS1}"
    return _APPLY_PS1.read_text(encoding="utf-8")


def test_apply_real_run_calls_get_pending_before_install(apply_text: str) -> None:
    """``Get-PendingWindowsUpdates`` MUST be called before
    ``Install-WindowsUpdateBatch`` on the real-run path. Otherwise the
    pre-check fast-path is gone and we'll hang again on 0-pending hosts.
    """
    pending_idx = apply_text.find("Get-PendingWindowsUpdates")
    install_idx = apply_text.find("Install-WindowsUpdateBatch -")
    assert pending_idx != -1, (
        "Get-PendingWindowsUpdates not called in apply.ps1; the fast-path "
        "pre-check is missing and we will hang on 0-pending hosts."
    )
    assert install_idx != -1, (
        "Install-WindowsUpdateBatch invocation not found in apply.ps1; "
        "did the function get renamed?"
    )
    # Find the FIRST Get-PendingWindowsUpdates call in the real-run path
    # (skipping the DryRun branch). The DryRun branch is before the
    # "Real-run path" comment; everything after the marker is the real
    # path. Asserting "any Get-Pending before Install" is too loose
    # because the DryRun branch always has one.
    real_run_marker = "Real-run path"
    real_run_idx = apply_text.find(real_run_marker)
    assert real_run_idx != -1, (
        f"Real-run path marker '{real_run_marker}' not found in apply.ps1"
    )
    pending_in_real = apply_text.find("Get-PendingWindowsUpdates", real_run_idx)
    assert pending_in_real != -1, (
        "Get-PendingWindowsUpdates must appear in the real-run path of "
        "apply.ps1 (not just the DryRun branch)."
    )
    assert pending_in_real < install_idx, (
        "Get-PendingWindowsUpdates must be called BEFORE "
        "Install-WindowsUpdateBatch in the real-run path. Otherwise the "
        "fast-path early-exit fires only after the slow COM scan."
    )


def test_apply_short_circuits_when_pending_count_zero(apply_text: str) -> None:
    """The pre-check branch must end in an explicit ``exit 0`` (and
    ``Save-Sidecar``) when ``$pending.Count -eq 0`` so we never fall
    through to the Install path on an empty pending set.
    """
    # Pattern: the early-exit code path. We don't pin the exact wording
    # but require an `$pending.Count -eq 0` test plus `Save-Sidecar` plus
    # `exit 0` to appear in the same surrounding block.
    assert "$pending.Count -eq 0" in apply_text, (
        "Fast-path: apply.ps1 must check `$pending.Count -eq 0` to detect "
        "the no-work case."
    )
    # Verify the zero-count branch saves the sidecar AND exits 0 in the
    # same logical block. We search for the pattern within ~600 chars
    # after the count check.
    zero_match = apply_text.find("$pending.Count -eq 0")
    block = apply_text[zero_match : zero_match + 1500]
    assert "Save-Sidecar -Sidecar $sidecar" in block, (
        "Fast-path branch must call Save-Sidecar so the orchestrator "
        "sees a real apply__windows_update.json file when nothing pending."
    )
    assert re.search(r"\bexit\s+0\b", block), (
        "Fast-path branch must `exit 0` after Save-Sidecar so the script "
        "doesn't fall through to Install-WindowsUpdateBatch."
    )


def test_apply_filter_excludes_pending_correctly(apply_text: str) -> None:
    """The fast-path applies ``-ItemFilter`` to the pending set so a
    filter that excludes all pending KBs is also treated as "nothing to
    apply" rather than running Install on the full set.
    """
    # The filter intersection lives between the Get-Pending call and the
    # zero-count check; search for the canonical "Where-Object" filter
    # pattern that references the filter array.
    real_run_marker = "Real-run path"
    real_run_idx = apply_text.find(real_run_marker)
    block = apply_text[real_run_idx:]
    # The filter intersection must reference $filterArray (the cleaned
    # ItemFilter from the parameter parsing earlier in the script).
    assert "$filterArray" in block, (
        "Real-run path doesn't reference $filterArray — the ItemFilter "
        "intersection is missing from the fast-path."
    )


def test_apply_pre_scan_stream_marker_emits(apply_text: str) -> None:
    """When the pre-check scan starts, apply.ps1 emits a clear
    ``>>> Scanning pending Windows updates`` marker so operators see
    progress in the SPA's Run Center even before the install begins.
    """
    assert "Scanning pending Windows updates" in apply_text, (
        "apply.ps1 should emit a Write-AscendoStreamLine marker when "
        "the pre-check scan starts; operators see no progress otherwise."
    )


def test_apply_install_stream_marker_includes_count(apply_text: str) -> None:
    """The Install-WindowsUpdateBatch marker now includes the pending
    count so operators know exactly how many KBs we're about to install
    rather than the vague "(this may take several minutes)".
    """
    # The new marker uses {0} format placeholder for the count.
    pattern = re.compile(
        r"Install-WindowsUpdateBatch starting for \{0\} pending update\(s\)"
    )
    assert pattern.search(apply_text), (
        "apply.ps1's install-starting marker should announce the count "
        "of pending updates (use {0} format placeholder filled from "
        "$pending.Count)."
    )


def test_apply_pre_scan_heartbeat_lifecycle(apply_text: str) -> None:
    """The pre-check scan should be wrapped in its own heartbeat so the
    SPA shows liveness even during the read-only Get-PendingWindowsUpdates
    call (which can take several seconds on slow WUA hosts).
    """
    # We expect both Start- and Stop- AscendoHeartbeat in the pre-check
    # area (between "Real-run path" and the zero-count check).
    real_run_marker = "Real-run path"
    real_run_idx = apply_text.find(real_run_marker)
    zero_idx = apply_text.find("$pending.Count -eq 0", real_run_idx)
    block = apply_text[real_run_idx:zero_idx]
    assert "Start-AscendoHeartbeat" in block, (
        "Pre-check section must start a heartbeat so SPA shows liveness "
        "during the read-only scan."
    )
    assert "Stop-AscendoHeartbeat" in block, (
        "Pre-check section must stop its heartbeat before falling into "
        "the install path (otherwise we leak the runspace)."
    )


def test_apply_no_non_ascii_in_string_literals() -> None:
    """Sesja 58 lesson: PS5.1 reads .ps1 via CP1252, so em-dashes (U+2014)
    inside STRING LITERALS break the parser by mis-decoding to a quote
    character. Comments are tokenized to end-of-line so non-ASCII is
    harmless there. This test scans only single/double-quoted string
    literals (the parser-relevant slice) and rejects non-ASCII bytes
    inside them.

    The apply.ps1 file has pre-existing U+2500 / U+2014 in comment-only
    section separators (lines 171, 219, etc.); those are intentional and
    don't break parsing. We only fail if the fast-path edit introduced
    non-ASCII inside an actual string literal.
    """
    raw = _APPLY_PS1.read_bytes()
    decoded = raw.decode("utf-8")
    # Crude tokenization: strip line comments first (the '#' to EOL), then
    # extract string literal contents (single + double quoted), then check
    # for non-ASCII bytes. This catches the real PS5.1 bug class without
    # false-positiving on legitimate ASCII-art separators in comments.
    bad_lines: list[int] = []
    for line_num, line in enumerate(decoded.splitlines(), start=1):
        # Drop everything from the first un-quoted '#' to EOL. Approximate
        # tokenizer: walk char-by-char tracking quote state.
        in_single = False
        in_double = False
        slice_end = len(line)
        for i, ch in enumerate(line):
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "#" and not in_single and not in_double:
                slice_end = i
                break
        code = line[:slice_end]
        # Within `code`, find non-ASCII chars inside any "..." or '...'.
        i = 0
        n = len(code)
        while i < n:
            ch = code[i]
            if ch in ("'", '"'):
                quote = ch
                i += 1
                while i < n and code[i] != quote:
                    if ord(code[i]) > 127:
                        bad_lines.append(line_num)
                        break
                    i += 1
                if i < n:
                    i += 1
            else:
                i += 1
    assert not bad_lines, (
        "apply.ps1 contains non-ASCII characters inside string literals "
        f"at lines: {bad_lines[:10]}. PS5.1 parses .ps1 with CP1252 "
        "which mis-decodes UTF-8 punctuation as quote chars, terminating "
        "string literals prematurely. Use ASCII equivalents (-- for "
        "em-dash, ... for ellipsis, etc.) inside string literals."
    )
