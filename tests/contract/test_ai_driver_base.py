"""Tests for shared subprocess streaming helpers in drivers/_base.py."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
import pytest

from ascendo.ai.drivers._base import (
    SubprocessFailure,
    SubprocessHang,
    discover_binary,
    probe_version,
    run_streaming,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ai_cli"


def test_discover_binary_finds_in_path(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))
    p = discover_binary("fake-claude")
    assert p is not None
    assert p.endswith("fake-claude")


def test_discover_binary_returns_none_when_missing(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    p = discover_binary("definitely-not-installed")
    assert p is None


def test_probe_version_reads_stdout(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FIXTURE_CASE", "version")
    version = probe_version("fake-claude", argv=["--version-mode"])
    assert "claude" in version.lower()


@pytest.mark.asyncio
async def test_run_streaming_yields_stdout_lines(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FIXTURE_CASE", "success")
    cancel = asyncio.Event()
    lines = []
    async for line in run_streaming(["fake-gemini", "-p", "hi"], cancel_event=cancel):
        lines.append(line)
    assert any("Hello" in l for l in lines)


@pytest.mark.asyncio
async def test_run_streaming_cancellation_kills_subprocess(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FIXTURE_CASE", "hang")
    cancel = asyncio.Event()

    async def cancel_soon():
        await asyncio.sleep(0.2)
        cancel.set()

    asyncio.create_task(cancel_soon())
    lines = []
    async for line in run_streaming(
        ["fake-claude", "-p", "hi"], cancel_event=cancel, grace_seconds=0.5,
    ):
        lines.append(line)
    # cancel_event set; iterator returns; subprocess killed.


@pytest.mark.asyncio
async def test_run_streaming_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FIXTURE_CASE", "crash")
    cancel = asyncio.Event()
    with pytest.raises(SubprocessFailure) as ei:
        async for _ in run_streaming(["fake-claude", "-p", "hi"], cancel_event=cancel):
            pass
    assert ei.value.exit_code != 0
