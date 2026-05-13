"""Regression tests for the heartbeat -> ASCENDO_STREAM_LOG forwarding.

## Bug (run e5f0e0f1, 2026-05-12)

When the windows_update apply hung in Install-WindowsUpdate, the heartbeat
runspace (Start-AscendoHeartbeat) was firing every 10s, writing
``>>> still running Ns (Windows Update install)`` to ``[Console]::Error``.
But [Console]::Error of a child PowerShell subprocess gets captured by the
parent Python ``subprocess.run(capture_output=True)`` call into a memory
buffer — it never reaches a file the dashboard's SSE consumer is tailing.

Result: the SPA Run Center showed ZERO activity during a 5-minute hang.
Operator assumed Ascendo was wedged and killed the dashboard.

## Fix

In ``AscendoJson.psm1``, ``Start-AscendoHeartbeat`` now captures
``$env:ASCENDO_STREAM_LOG`` at start-time and passes it into the runspace.
The runspace's tick loop emits to BOTH ``[Console]::Error.WriteLine`` (for
backwards compat with terminal-tailing consumers) AND
``Add-Content $streamLog`` (so the dashboard's SSE log_line consumer sees
each heartbeat).

These tests are pure static analysis on the .psm1 source.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PSM1 = (
    _REPO_ROOT
    / "adapters"
    / "windows"
    / "lib"
    / "AscendoJson.psm1"
)


@pytest.fixture(scope="module")
def psm1_text() -> str:
    assert _PSM1.exists(), f"missing fixture: {_PSM1}"
    return _PSM1.read_text(encoding="utf-8")


def test_heartbeat_captures_stream_log_env_var(psm1_text: str) -> None:
    """``Start-AscendoHeartbeat`` reads ``$env:ASCENDO_STREAM_LOG`` BEFORE
    creating the runspace so the value is available to the script-block
    via AddArgument (runspaces get a fresh empty env block, so the
    in-script ``$env:ASCENDO_STREAM_LOG`` would return ``$null``).
    """
    # Find the Start-AscendoHeartbeat function block.
    fn_match = re.search(
        r"function\s+Start-AscendoHeartbeat\s*\{(.*?)(?=\n\s*function\b|\Z)",
        psm1_text,
        flags=re.DOTALL,
    )
    assert fn_match, "Start-AscendoHeartbeat function not found"
    body = fn_match.group(1)

    # The parent must read $env:ASCENDO_STREAM_LOG before launching the
    # runspace (otherwise the runspace's $env block is empty).
    assert re.search(r"\$streamLog\s*=\s*\$env:ASCENDO_STREAM_LOG", body), (
        "Start-AscendoHeartbeat must capture $env:ASCENDO_STREAM_LOG "
        "into a local variable BEFORE creating the runspace. Runspaces "
        "get a fresh empty env so the in-script lookup would no-op."
    )


def test_heartbeat_passes_stream_log_to_runspace(psm1_text: str) -> None:
    """The captured ``$streamLog`` value must be passed into the script
    block via ``.AddArgument($streamLog)`` and bound to a param. Without
    this the runspace receives the value as ``$null`` and never emits to
    the file.
    """
    fn_match = re.search(
        r"function\s+Start-AscendoHeartbeat\s*\{(.*?)(?=\n\s*function\b|\Z)",
        psm1_text,
        flags=re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)

    # The script block's param() must include $streamLog.
    param_match = re.search(
        r"param\(\$intervalSeconds,\s*\$label,\s*\$start,\s*\$stopFlag,\s*\$streamLog\)",
        body,
    )
    assert param_match, (
        "Heartbeat script block must accept $streamLog as a parameter "
        "(after $stopFlag). Without the param, AddArgument silently "
        "discards the value."
    )

    # AddArgument($streamLog) must appear in the runspace setup chain.
    assert re.search(r"AddArgument\(\$streamLog\)", body), (
        "Start-AscendoHeartbeat must AddArgument($streamLog) when "
        "building the runspace's PowerShell pipeline."
    )


def test_heartbeat_appends_to_stream_log(psm1_text: str) -> None:
    """Inside the runspace's tick loop, every emitted heartbeat message
    must be appended to ``$streamLog`` (when set) via ``Add-Content``.
    """
    fn_match = re.search(
        r"function\s+Start-AscendoHeartbeat\s*\{(.*?)(?=\n\s*function\b|\Z)",
        psm1_text,
        flags=re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)

    # The tick loop should append the message to $streamLog when set.
    # Pattern: `if ($streamLog) { ... Add-Content -LiteralPath $streamLog ... }`
    pattern = re.compile(
        r"if\s*\(\$streamLog\).*?Add-Content\s+-LiteralPath\s+\$streamLog",
        flags=re.DOTALL,
    )
    assert pattern.search(body), (
        "Heartbeat tick loop must Add-Content to $streamLog when set. "
        "Without this, [Console]::Error.WriteLine output is captured "
        "by the parent subprocess but never reaches the SPA's SSE "
        "consumer that tails the stream-log file."
    )


def test_heartbeat_add_content_tolerates_failures(psm1_text: str) -> None:
    """The Add-Content call must use ``-ErrorAction SilentlyContinue``
    so a transient I/O failure (locked file, full disk) doesn't kill the
    heartbeat runspace and re-introduce the silent-hang bug.
    """
    fn_match = re.search(
        r"function\s+Start-AscendoHeartbeat\s*\{(.*?)(?=\n\s*function\b|\Z)",
        psm1_text,
        flags=re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)

    # PS uses backtick-newline line continuation, so a single logical
    # `Add-Content` call can span multiple source lines. Strip those
    # continuations before matching so the regex sees one line.
    body_unwrapped = re.sub(r"`\r?\n\s*", " ", body)
    add_content_match = re.search(
        r"Add-Content[^\n]*\$streamLog[^\n]*-ErrorAction\s+SilentlyContinue",
        body_unwrapped,
    )
    assert add_content_match, (
        "Heartbeat's Add-Content to the stream log must use "
        "-ErrorAction SilentlyContinue so a transient file lock or "
        "disk-full condition doesn't abort the heartbeat runspace."
    )


def test_heartbeat_still_writes_to_console_error(psm1_text: str) -> None:
    """The original ``[Console]::Error.WriteLine`` emit MUST remain for
    backwards compatibility with terminal-tailing consumers (e.g. when
    the script is run directly from a PS prompt, not via the dashboard).
    """
    fn_match = re.search(
        r"function\s+Start-AscendoHeartbeat\s*\{(.*?)(?=\n\s*function\b|\Z)",
        psm1_text,
        flags=re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)

    assert "[Console]::Error.WriteLine" in body, (
        "Heartbeat must still emit to [Console]::Error.WriteLine — "
        "removing it would break terminal-tail consumers (and the "
        "Python subprocess stderr capture relied on by some tests)."
    )
