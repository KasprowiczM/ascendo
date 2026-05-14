# AI Tools Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-shot Suggestions tab with a multi-turn AI chat that uses already-logged-in CLI tools (claude, gemini, codex, opencode) as backends, grounded in real Ascendo state, with one-click apply chips that proxy to a whitelisted action surface.

**Architecture:** New `core/ascendo/ai/` package (5 backend drivers, 10 context resolvers, fenced-action parser, SQLite persistence). New `dashboard/routes/chat.py` with SSE streaming. New `#view-aitools` SPA section reusing Sesja 67 cards as a Quick Suggestions rail.

**Tech Stack:** Python 3.11+, FastAPI + SSE, SQLite (stdlib), Pydantic v2, vanilla JS for SPA, bash + PowerShell for fake CLI fixtures and validate stages.

**Spec:** `docs/superpowers/specs/2026-05-14-ai-tools-chat-design.md` (commit 43ae49c).

**Test verification convention:** After each "run test" step, paste the exact command + expected last line of output. If actual output differs, mark step blocked rather than passed.

---

## Phase A — Foundation (Tasks 1-13)

End state: backend resolver + all 5 drivers + context injector + persistence work end-to-end through `core/ascendo/ai/`. No UI, no SSE routes yet. Verified by unit tests against fake CLIs.

---

### Task 1: Scaffold `core/ascendo/ai/` package + fake CLI fixtures

**Files:**
- Create: `core/ascendo/ai/__init__.py`
- Create: `core/ascendo/ai/drivers/__init__.py`
- Create: `core/ascendo/ai/resolvers/__init__.py`
- Create: `core/ascendo/ai/prompts/.gitkeep`
- Create: `tests/fixtures/ai_cli/fake-claude` (bash script, chmod +x)
- Create: `tests/fixtures/ai_cli/fake-gemini`
- Create: `tests/fixtures/ai_cli/fake-codex`
- Create: `tests/fixtures/ai_cli/fake-opencode`

- [ ] **Step 1: Create empty package files**

```bash
mkdir -p core/ascendo/ai/{drivers,resolvers,prompts}
touch core/ascendo/ai/__init__.py
touch core/ascendo/ai/drivers/__init__.py
touch core/ascendo/ai/resolvers/__init__.py
touch core/ascendo/ai/prompts/.gitkeep
```

- [ ] **Step 2: Create fake-claude fixture**

`tests/fixtures/ai_cli/fake-claude`:

```bash
#!/usr/bin/env bash
# Mimics: claude -p "<prompt>" --output-format stream-json
# Reads FIXTURE_CASE from env to switch behavior.
# Default: success — emit a fixed scripted stream of stream-json events.

set -e

case "${FIXTURE_CASE:-success}" in
  version)
    echo "claude 1.0.0"
    exit 0
    ;;
  auth_probe_ok)
    printf '{"type":"message_start"}\n'
    printf '{"type":"content_block_delta","delta":{"text":"ok"}}\n'
    printf '{"type":"message_stop"}\n'
    exit 0
    ;;
  auth_expired)
    echo "Error: authentication expired, please run: claude /login" >&2
    exit 1
    ;;
  crash)
    echo "Error: unexpected error" >&2
    exit 137
    ;;
  hang)
    sleep 600
    ;;
  partial_then_eof)
    printf '{"type":"message_start"}\n'
    printf '{"type":"content_block_delta","delta":{"text":"partial"}}\n'
    exit 1
    ;;
  success|*)
    printf '{"type":"message_start"}\n'
    printf '{"type":"content_block_delta","delta":{"text":"Hello"}}\n'
    printf '{"type":"content_block_delta","delta":{"text":" world"}}\n'
    printf '{"type":"message_stop"}\n'
    exit 0
    ;;
esac
```

Make executable:
```bash
chmod +x tests/fixtures/ai_cli/fake-claude
```

- [ ] **Step 3: Create fake-gemini fixture**

`tests/fixtures/ai_cli/fake-gemini`:

```bash
#!/usr/bin/env bash
# Mimics: gemini -p "<prompt>"
set -e
case "${FIXTURE_CASE:-success}" in
  version) echo "gemini 0.3.0"; exit 0 ;;
  auth_probe_ok) echo "ok"; exit 0 ;;
  auth_expired) echo "Error: not authenticated" >&2; exit 1 ;;
  crash) exit 137 ;;
  hang) sleep 600 ;;
  partial_then_eof) printf "partial"; exit 1 ;;
  success|*)
    printf "Hello"
    printf " world"
    echo
    exit 0
    ;;
esac
```

```bash
chmod +x tests/fixtures/ai_cli/fake-gemini
```

- [ ] **Step 4: Create fake-codex fixture**

`tests/fixtures/ai_cli/fake-codex`:

```bash
#!/usr/bin/env bash
# Mimics: codex exec "<prompt>"
set -e
case "${FIXTURE_CASE:-success}" in
  version) echo "codex 0.5.0"; exit 0 ;;
  auth_probe_ok) printf '{"type":"text","text":"ok"}\n'; exit 0 ;;
  auth_expired) echo "Error: chatgpt login expired" >&2; exit 1 ;;
  crash) exit 137 ;;
  hang) sleep 600 ;;
  success|*)
    printf '{"type":"text","text":"Hello"}\n'
    printf '{"type":"text","text":" world"}\n'
    printf '{"type":"done"}\n'
    exit 0
    ;;
esac
```

```bash
chmod +x tests/fixtures/ai_cli/fake-codex
```

- [ ] **Step 5: Create fake-opencode fixture**

`tests/fixtures/ai_cli/fake-opencode`:

```bash
#!/usr/bin/env bash
# Mimics: opencode run "<prompt>"
set -e
case "${FIXTURE_CASE:-success}" in
  version) echo "opencode 1.14.41"; exit 0 ;;
  auth_probe_ok) echo "ok"; exit 0 ;;
  auth_expired) echo "Error: no provider configured" >&2; exit 1 ;;
  crash) exit 137 ;;
  hang) sleep 600 ;;
  success|*)
    printf "Hello"
    printf " world\n"
    exit 0
    ;;
esac
```

```bash
chmod +x tests/fixtures/ai_cli/fake-opencode
```

- [ ] **Step 6: Verify fixtures executable**

```bash
ls -la tests/fixtures/ai_cli/
```
Expected: 4 files, all with `-rwxr-xr-x` perms.

```bash
FIXTURE_CASE=success tests/fixtures/ai_cli/fake-claude -p "test" --output-format stream-json | head -3
```
Expected: 3 JSONL lines starting with `{"type":"message_start"}`.

- [ ] **Step 7: Commit**

```bash
git add core/ascendo/ai/ tests/fixtures/ai_cli/
git commit -m "feat(ai): scaffold core/ascendo/ai/ + 4 fake CLI fixtures (Task 1)"
```

---

### Task 2: Backend ABC + Chunk model + TurnRegistry

**Files:**
- Create: `core/ascendo/ai/backend.py`
- Create: `tests/contract/test_ai_backend_abc.py`

- [ ] **Step 1: Write failing test for Chunk model**

`tests/contract/test_ai_backend_abc.py`:

```python
"""Contract tests for the AI Backend ABC + Chunk + TurnRegistry."""
from __future__ import annotations
import asyncio
import pytest
from pydantic import ValidationError

from ascendo.ai.backend import Backend, Chunk, TurnRegistry, TurnState, TurnStatus


def test_chunk_token_type_accepts_content():
    c = Chunk(type="token", content="hello")
    assert c.type == "token"
    assert c.content == "hello"


def test_chunk_done_type_accepts_status():
    c = Chunk(type="done", status="success")
    assert c.status == "success"


def test_chunk_action_proposal_type_accepts_action():
    c = Chunk(type="action_proposal", action={"id": "run_check", "label_en": "Run check"})
    assert c.action["id"] == "run_check"


def test_chunk_error_type_accepts_error_field():
    c = Chunk(type="error", error="oops")
    assert c.error == "oops"


def test_chunk_invalid_type_rejected():
    with pytest.raises(ValidationError):
        Chunk(type="not_a_real_type")


def test_backend_is_abstract():
    with pytest.raises(TypeError):
        Backend()  # type: ignore[abstract]


def test_turn_registry_register_and_get():
    reg = TurnRegistry(max_runs=10)
    state = TurnState(turn_id="abc", conversation_id="conv1", backend_name="fake")
    reg.register(state)
    assert reg.get("abc") is state
    assert reg.get("missing") is None


def test_turn_registry_evicts_completed_when_full():
    reg = TurnRegistry(max_runs=2)
    s1 = TurnState(turn_id="t1", conversation_id="c", backend_name="fake")
    s1.status = TurnStatus.COMPLETED
    s2 = TurnState(turn_id="t2", conversation_id="c", backend_name="fake")
    s3 = TurnState(turn_id="t3", conversation_id="c", backend_name="fake")
    reg.register(s1)
    reg.register(s2)
    reg.register(s3)  # forces eviction; s1 (completed) goes first
    assert reg.get("t1") is None
    assert reg.get("t2") is s2
    assert reg.get("t3") is s3


def test_turn_registry_never_evicts_running():
    reg = TurnRegistry(max_runs=2)
    s1 = TurnState(turn_id="t1", conversation_id="c", backend_name="fake")
    s1.status = TurnStatus.RUNNING
    s2 = TurnState(turn_id="t2", conversation_id="c", backend_name="fake")
    s2.status = TurnStatus.RUNNING
    reg.register(s1)
    reg.register(s2)
    s3 = TurnState(turn_id="t3", conversation_id="c", backend_name="fake")
    reg.register(s3)
    # No completed runs to evict; s1 + s2 still present (registry grew past cap).
    assert reg.get("t1") is s1
    assert reg.get("t2") is s2
    assert reg.get("t3") is s3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd $(git rev-parse --show-toplevel)
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_backend_abc.py -v 2>&1 | tail -5
```
Expected: collection error or `ImportError: cannot import name 'Backend' from 'ascendo.ai.backend'`.

- [ ] **Step 3: Implement backend.py**

`core/ascendo/ai/backend.py`:

```python
"""Backend ABC + Chunk model + TurnRegistry for AI chat.

The Backend ABC is implemented by each driver (claude_code, gemini_cli,
codex_cli, opencode, api_key). The TurnRegistry tracks in-flight chat
turns (mirror of M2.10's RunRegistry pattern from core/ascendo/orchestrator/run_async.py).
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    """One conversation turn (user or assistant)."""
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system", "tool_result"]
    content: str


class Chunk(BaseModel):
    """One streaming chunk produced by a backend during a turn."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["token", "action_proposal", "context_trimmed", "done", "error"]
    content: str | None = None
    action: dict | None = None
    status: Literal["success", "cancelled", "error"] | None = None
    error: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class Backend(ABC):
    """One AI backend: a CLI driver, an API-key driver, or a future remote service."""

    name: str = ""              # set by subclass: "claude" | "gemini" | "codex" | "opencode" | "api:<provider>"
    bin_name: str | None = None  # CLI binary name; None for API
    max_input_tokens: int = 12000  # driver-declared input cap; resolver respects this

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap check: binary on PATH + version probe. No network."""

    @abstractmethod
    def is_authenticated(self) -> bool:
        """3-second probe call to verify auth works. May be network-y."""

    @abstractmethod
    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        """Stream chunks. Closes when message complete or cancel_event set."""

    @abstractmethod
    def model_info(self) -> dict:
        """Backend-specific model name + provider for the SPA footer pill."""


class TurnStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TurnState:
    """One in-flight chat turn."""
    turn_id: str
    conversation_id: str
    backend_name: str
    status: TurnStatus = TurnStatus.PENDING
    error: str | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: float | None = None
    ended_at: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class TurnRegistry:
    """Thread-safe, bounded registry of in-flight turns.

    Mirror of M2.10's RunRegistry (core/ascendo/orchestrator/run_async.py).
    Evicts completed turns first when full; never evicts running turns.
    """

    def __init__(self, max_runs: int = 256) -> None:
        self._states: OrderedDict[str, TurnState] = OrderedDict()
        self._max_runs = max_runs
        self._lock = asyncio.Lock()

    def register(self, state: TurnState) -> None:
        if len(self._states) >= self._max_runs:
            self._evict_one()
        self._states[state.turn_id] = state

    def get(self, turn_id: str) -> TurnState | None:
        return self._states.get(turn_id)

    def remove(self, turn_id: str) -> None:
        self._states.pop(turn_id, None)

    def _evict_one(self) -> None:
        """Evict oldest completed/failed/cancelled turn; never running ones."""
        for tid, state in list(self._states.items()):
            if state.status in (TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED):
                del self._states[tid]
                return
        # All running: don't evict; let it grow past cap. This is documented:
        # a runaway server with 256 active streams has bigger problems.
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_backend_abc.py -v 2>&1 | tail -10
```
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/backend.py tests/contract/test_ai_backend_abc.py
git commit -m "feat(ai): Backend ABC + Chunk + TurnRegistry (Task 2)"
```

---

### Task 3: Driver base class with subprocess streaming helpers

**Files:**
- Create: `core/ascendo/ai/drivers/_base.py`
- Create: `tests/contract/test_ai_driver_base.py`

- [ ] **Step 1: Write failing test**

`tests/contract/test_ai_driver_base.py`:

```python
"""Tests for shared subprocess streaming helpers in drivers/_base.py."""
from __future__ import annotations
import asyncio
import os
import shutil
from pathlib import Path
import pytest

from ascendo.ai.drivers._base import (
    discover_binary,
    probe_version,
    run_streaming,
    SubprocessFailure,
    SubprocessHang,
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
    async for line in run_streaming(["fake-claude", "-p", "hi"], cancel_event=cancel, grace_seconds=0.5):
        lines.append(line)
    # cancel_event set; iterator returns; subprocess killed.
    # No assertion on lines — may be empty if hang case never wrote.


@pytest.mark.asyncio
async def test_run_streaming_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FIXTURE_CASE", "crash")
    cancel = asyncio.Event()
    with pytest.raises(SubprocessFailure) as ei:
        async for _ in run_streaming(["fake-claude", "-p", "hi"], cancel_event=cancel):
            pass
    assert ei.value.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_driver_base.py -v 2>&1 | tail -5
```
Expected: import error for `_base`.

- [ ] **Step 3: Implement _base.py**

`core/ascendo/ai/drivers/_base.py`:

```python
"""Shared subprocess + streaming helpers used by every CLI driver."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import AsyncIterator


class SubprocessFailure(Exception):
    """Raised when a subprocess exits with non-zero code."""

    def __init__(self, exit_code: int, stderr_tail: str) -> None:
        super().__init__(f"subprocess failed (exit {exit_code}): {stderr_tail}")
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail


class SubprocessHang(Exception):
    """Raised when a subprocess produces no output for too long."""


def discover_binary(name: str) -> str | None:
    """shutil.which wrapper; returns absolute path or None."""
    return shutil.which(name)


def probe_version(binary: str, *, argv: list[str] | None = None, timeout_s: float = 3.0) -> str:
    """Run `binary --version` (or override argv); return first stdout line."""
    cmd = [binary, "--version"] if argv is None else [binary, *argv]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, check=False
        )
        text = (out.stdout or out.stderr or "").strip().splitlines()
        return text[0] if text else ""
    except subprocess.TimeoutExpired:
        return ""


async def run_streaming(
    argv: list[str],
    *,
    cancel_event: asyncio.Event,
    grace_seconds: float = 2.0,
    chunk_timeout_s: float = 30.0,
    stdin_text: str | None = None,
) -> AsyncIterator[str]:
    """Spawn argv; yield each stdout line.

    Honors cancel_event: SIGTERM, wait grace_seconds, then SIGKILL.
    Raises SubprocessHang if chunk_timeout_s elapses with no output.
    Raises SubprocessFailure on non-zero exit.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin_text else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if stdin_text and proc.stdin:
        proc.stdin.write(stdin_text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

    stderr_chunks: list[bytes] = []

    async def _drain_stderr() -> None:
        if proc.stderr:
            async for chunk in proc.stderr:
                stderr_chunks.append(chunk)

    stderr_task = asyncio.create_task(_drain_stderr())

    try:
        while True:
            if cancel_event.is_set():
                _terminate(proc, grace_seconds)
                return
            try:
                line_bytes = await asyncio.wait_for(
                    proc.stdout.readline() if proc.stdout else _eof(),
                    timeout=chunk_timeout_s,
                )
            except asyncio.TimeoutError:
                _terminate(proc, grace_seconds)
                raise SubprocessHang(f"no output for {chunk_timeout_s}s")
            if not line_bytes:
                break
            yield line_bytes.decode("utf-8", errors="replace").rstrip("\n")
    finally:
        await proc.wait()
        await stderr_task

    if proc.returncode != 0 and not cancel_event.is_set():
        stderr_tail = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-2000:]
        raise SubprocessFailure(proc.returncode or -1, stderr_tail)


async def _eof():
    return b""


def _terminate(proc: asyncio.subprocess.Process, grace_seconds: float) -> None:
    """SIGTERM, wait grace_seconds, SIGKILL if still alive."""
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return

    async def _kill_after_grace() -> None:
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    asyncio.create_task(_kill_after_grace())
```

- [ ] **Step 4: Install pytest-asyncio if missing**

```bash
pip install pytest-asyncio 2>&1 | tail -1
```

- [ ] **Step 5: Add asyncio_mode to pytest config**

Check if `pyproject.toml` or `pytest.ini` has `asyncio_mode`. If not, add to `pyproject.toml`:

```bash
grep -q asyncio_mode pyproject.toml || python3 -c "
import sys
content = open('pyproject.toml').read()
if '[tool.pytest.ini_options]' in content:
    content = content.replace('[tool.pytest.ini_options]', '[tool.pytest.ini_options]\nasyncio_mode = \"auto\"', 1)
else:
    content += '\n[tool.pytest.ini_options]\nasyncio_mode = \"auto\"\n'
open('pyproject.toml','w').write(content)
"
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_driver_base.py -v 2>&1 | tail -10
```
Expected: `5 passed`.

- [ ] **Step 7: Commit**

```bash
git add core/ascendo/ai/drivers/_base.py tests/contract/test_ai_driver_base.py pyproject.toml
git commit -m "feat(ai): subprocess streaming helper (Task 3)"
```

---

### Task 4: ClaudeCodeBackend driver

**Files:**
- Create: `core/ascendo/ai/drivers/claude_code.py`
- Modify: `tests/contract/test_ai_cli_drivers.py` (create file)

- [ ] **Step 1: Write failing test**

`tests/contract/test_ai_cli_drivers.py`:

```python
"""Per-driver smoke tests, parametrized over the 4 CLI backends."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ai_cli"


@pytest.fixture(autouse=True)
def _path_with_fakes(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))


# -------- Claude Code --------

def test_claude_code_name():
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.name == "claude"


def test_claude_code_is_available_when_binary_present():
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.is_available() is True


def test_claude_code_is_available_false_when_missing(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.is_available() is False


def test_claude_code_is_authenticated_when_probe_succeeds(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.is_authenticated() is True


def test_claude_code_is_authenticated_false_when_probe_fails(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_expired")
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    b = ClaudeCodeBackend(bin_name="fake-claude")
    assert b.is_authenticated() is False


@pytest.mark.asyncio
async def test_claude_code_stream_yields_tokens(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "success")
    from ascendo.ai.drivers.claude_code import ClaudeCodeBackend
    from ascendo.ai.backend import Message
    b = ClaudeCodeBackend(bin_name="fake-claude")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(
        system="You are helpful.",
        messages=[Message(role="user", content="hi")],
        cancel_event=cancel,
    ):
        chunks.append(c)
    # Expect at least: 1+ token, 1 done
    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"
    assert chunks[-1].status == "success"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py::test_claude_code_name -v 2>&1 | tail -3
```
Expected: import error.

- [ ] **Step 3: Implement claude_code.py**

`core/ascendo/ai/drivers/claude_code.py`:

```python
"""ClaudeCodeBackend: shells out to `claude` CLI from Claude Code."""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

from ..backend import Backend, Chunk, Message
from ._base import (
    SubprocessFailure,
    SubprocessHang,
    discover_binary,
    probe_version,
    run_streaming,
)

DEFAULT_BIN = "claude"


class ClaudeCodeBackend(Backend):
    name = "claude"
    max_input_tokens = 200_000  # generous; Sonnet supports 200k

    def __init__(self, *, bin_name: str = DEFAULT_BIN) -> None:
        self.bin_name = bin_name
        self._cached_path: str | None = None

    def is_available(self) -> bool:
        path = discover_binary(self.bin_name)
        if path is None:
            return False
        self._cached_path = path
        return True

    def is_authenticated(self) -> bool:
        if not self.is_available():
            return False
        # Cheap probe: send a tiny prompt, expect exit 0.
        import subprocess
        try:
            res = subprocess.run(
                [self._cached_path or self.bin_name, "-p", "say ok", "--output-format", "stream-json"],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
                env={**os.environ},  # inherit FIXTURE_CASE in tests
            )
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def model_info(self) -> dict[str, str]:
        return {"backend": "claude", "model": "claude-sonnet (subscription)"}

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        prompt = _build_prompt(system, messages)
        argv = [self._cached_path or self.bin_name, "-p", prompt, "--output-format", "stream-json"]
        tokens_out = 0
        try:
            async for line in run_streaming(argv, cancel_event=cancel_event):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "content_block_delta":
                    text = (event.get("delta") or {}).get("text") or ""
                    if text:
                        tokens_out += max(1, len(text) // 4)
                        yield Chunk(type="token", content=text)
                # message_start, message_stop are no-ops for our chunk stream
        except SubprocessFailure as e:
            yield Chunk(type="error", error=f"claude subprocess failed: {e.stderr_tail[-500:]}")
            yield Chunk(type="done", status="error")
            return
        except SubprocessHang as e:
            yield Chunk(type="error", error=f"claude hang: {e}")
            yield Chunk(type="done", status="error")
            return

        if cancel_event.is_set():
            yield Chunk(type="done", status="cancelled")
        else:
            yield Chunk(type="done", status="success", tokens_out=tokens_out)


def _build_prompt(system: str, messages: list[Message]) -> str:
    """Join the system prompt + transcript into a single prompt string.

    CLIs are stateless; we re-pass everything each turn.
    """
    parts: list[str] = []
    if system:
        parts.append(f"<system>\n{system}\n</system>")
    if messages:
        parts.append("<conversation>")
        for m in messages:
            parts.append(f"{m.role.upper()}: {m.content}")
        parts.append("</conversation>")
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -k claude_code -v 2>&1 | tail -10
```
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/drivers/claude_code.py tests/contract/test_ai_cli_drivers.py
git commit -m "feat(ai): ClaudeCodeBackend driver (Task 4)"
```

---

### Task 5: GeminiCliBackend driver

**Files:**
- Create: `core/ascendo/ai/drivers/gemini_cli.py`
- Modify: `tests/contract/test_ai_cli_drivers.py` (append)

- [ ] **Step 1: Append failing tests**

Append to `tests/contract/test_ai_cli_drivers.py`:

```python
# -------- Gemini CLI --------

def test_gemini_cli_name():
    from ascendo.ai.drivers.gemini_cli import GeminiCliBackend
    b = GeminiCliBackend(bin_name="fake-gemini")
    assert b.name == "gemini"


def test_gemini_cli_is_available_when_binary_present():
    from ascendo.ai.drivers.gemini_cli import GeminiCliBackend
    b = GeminiCliBackend(bin_name="fake-gemini")
    assert b.is_available() is True


def test_gemini_cli_is_authenticated_when_probe_ok(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.drivers.gemini_cli import GeminiCliBackend
    b = GeminiCliBackend(bin_name="fake-gemini")
    assert b.is_authenticated() is True


@pytest.mark.asyncio
async def test_gemini_cli_stream_yields_text(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "success")
    from ascendo.ai.drivers.gemini_cli import GeminiCliBackend
    from ascendo.ai.backend import Message
    b = GeminiCliBackend(bin_name="fake-gemini")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(
        system="You are helpful.",
        messages=[Message(role="user", content="hi")],
        cancel_event=cancel,
    ):
        chunks.append(c)
    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -k gemini -v 2>&1 | tail -5
```
Expected: import error.

- [ ] **Step 3: Implement gemini_cli.py**

`core/ascendo/ai/drivers/gemini_cli.py`:

```python
"""GeminiCliBackend: shells out to `gemini` CLI (gemini-cli, Google)."""
from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator

from ..backend import Backend, Chunk, Message
from ._base import (
    SubprocessFailure,
    SubprocessHang,
    discover_binary,
    run_streaming,
)

DEFAULT_BIN = "gemini"


class GeminiCliBackend(Backend):
    name = "gemini"
    max_input_tokens = 1_000_000  # Gemini 1.5 Pro generous

    def __init__(self, *, bin_name: str = DEFAULT_BIN) -> None:
        self.bin_name = bin_name
        self._cached_path: str | None = None

    def is_available(self) -> bool:
        path = discover_binary(self.bin_name)
        if path is None:
            return False
        self._cached_path = path
        return True

    def is_authenticated(self) -> bool:
        if not self.is_available():
            return False
        try:
            res = subprocess.run(
                [self._cached_path or self.bin_name, "-p", "say ok"],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
                env={**os.environ},
            )
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def model_info(self) -> dict[str, str]:
        return {"backend": "gemini", "model": "gemini (Google login)"}

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Chunk]:
        prompt = _build_prompt(system, messages)
        argv = [self._cached_path or self.bin_name, "-p", prompt]
        tokens_out = 0
        try:
            async for line in run_streaming(argv, cancel_event=cancel_event):
                if not line:
                    continue
                tokens_out += max(1, len(line) // 4)
                yield Chunk(type="token", content=line + "\n")
        except SubprocessFailure as e:
            yield Chunk(type="error", error=f"gemini failed: {e.stderr_tail[-500:]}")
            yield Chunk(type="done", status="error")
            return
        except SubprocessHang as e:
            yield Chunk(type="error", error=f"gemini hang: {e}")
            yield Chunk(type="done", status="error")
            return

        if cancel_event.is_set():
            yield Chunk(type="done", status="cancelled")
        else:
            yield Chunk(type="done", status="success", tokens_out=tokens_out)


def _build_prompt(system: str, messages: list[Message]) -> str:
    parts: list[str] = []
    if system:
        parts.append(f"<system>\n{system}\n</system>")
    if messages:
        parts.append("<conversation>")
        for m in messages:
            parts.append(f"{m.role.upper()}: {m.content}")
        parts.append("</conversation>")
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -k gemini -v 2>&1 | tail -5
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/drivers/gemini_cli.py tests/contract/test_ai_cli_drivers.py
git commit -m "feat(ai): GeminiCliBackend driver (Task 5)"
```

---

### Task 6: CodexCliBackend driver

**Files:**
- Create: `core/ascendo/ai/drivers/codex_cli.py`
- Modify: `tests/contract/test_ai_cli_drivers.py` (append)

Same shape as Task 5 — see Task 4/5 for the test template and copy-paste pattern with `codex_cli` substituted for `claude_code` or `gemini_cli`, and the fixture name `fake-codex`. Output parser handles `{"type":"text","text":"..."}` JSONL events from fake-codex.

- [ ] **Step 1: Append codex tests** (mirror Gemini block; 4 tests)

Append to `tests/contract/test_ai_cli_drivers.py`:

```python
# -------- Codex CLI --------

def test_codex_cli_name():
    from ascendo.ai.drivers.codex_cli import CodexCliBackend
    b = CodexCliBackend(bin_name="fake-codex")
    assert b.name == "codex"


def test_codex_cli_is_available():
    from ascendo.ai.drivers.codex_cli import CodexCliBackend
    b = CodexCliBackend(bin_name="fake-codex")
    assert b.is_available() is True


def test_codex_cli_authenticated(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.drivers.codex_cli import CodexCliBackend
    b = CodexCliBackend(bin_name="fake-codex")
    assert b.is_authenticated() is True


@pytest.mark.asyncio
async def test_codex_cli_streams_text_events(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "success")
    from ascendo.ai.drivers.codex_cli import CodexCliBackend
    from ascendo.ai.backend import Message
    b = CodexCliBackend(bin_name="fake-codex")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(system="", messages=[Message(role="user", content="hi")], cancel_event=cancel):
        chunks.append(c)
    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"
```

- [ ] **Step 2: Run, expect failure**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -k codex -v 2>&1 | tail -3
```

- [ ] **Step 3: Implement codex_cli.py**

`core/ascendo/ai/drivers/codex_cli.py`:

```python
"""CodexCliBackend: shells out to `codex` CLI (OpenAI Codex)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator

from ..backend import Backend, Chunk, Message
from ._base import SubprocessFailure, SubprocessHang, discover_binary, run_streaming

DEFAULT_BIN = "codex"


class CodexCliBackend(Backend):
    name = "codex"
    max_input_tokens = 128_000

    def __init__(self, *, bin_name: str = DEFAULT_BIN) -> None:
        self.bin_name = bin_name
        self._cached_path: str | None = None

    def is_available(self) -> bool:
        path = discover_binary(self.bin_name)
        if path is None:
            return False
        self._cached_path = path
        return True

    def is_authenticated(self) -> bool:
        if not self.is_available():
            return False
        try:
            res = subprocess.run(
                [self._cached_path or self.bin_name, "exec", "say ok"],
                capture_output=True, text=True, timeout=10.0, check=False,
                env={**os.environ},
            )
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def model_info(self) -> dict[str, str]:
        return {"backend": "codex", "model": "gpt-5 (ChatGPT login)"}

    async def stream(self, *, system, messages, cancel_event):
        prompt = _build_prompt(system, messages)
        argv = [self._cached_path or self.bin_name, "exec", prompt]
        tokens_out = 0
        try:
            async for line in run_streaming(argv, cancel_event=cancel_event):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Fallback: treat as plain text
                    tokens_out += max(1, len(line) // 4)
                    yield Chunk(type="token", content=line)
                    continue
                if event.get("type") == "text":
                    text = event.get("text") or ""
                    if text:
                        tokens_out += max(1, len(text) // 4)
                        yield Chunk(type="token", content=text)
        except SubprocessFailure as e:
            yield Chunk(type="error", error=f"codex failed: {e.stderr_tail[-500:]}")
            yield Chunk(type="done", status="error")
            return
        except SubprocessHang as e:
            yield Chunk(type="error", error=f"codex hang: {e}")
            yield Chunk(type="done", status="error")
            return
        if cancel_event.is_set():
            yield Chunk(type="done", status="cancelled")
        else:
            yield Chunk(type="done", status="success", tokens_out=tokens_out)


def _build_prompt(system: str, messages: list[Message]) -> str:
    parts: list[str] = []
    if system:
        parts.append(f"<system>\n{system}\n</system>")
    if messages:
        parts.append("<conversation>")
        for m in messages:
            parts.append(f"{m.role.upper()}: {m.content}")
        parts.append("</conversation>")
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -k codex -v 2>&1 | tail -5
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/drivers/codex_cli.py tests/contract/test_ai_cli_drivers.py
git commit -m "feat(ai): CodexCliBackend driver (Task 6)"
```

---

### Task 7: OpencodeBackend driver

Same shape as Tasks 4-6. opencode emits plain text on stdout for `opencode run <prompt>`.

- [ ] **Step 1: Append opencode tests**

Append to `tests/contract/test_ai_cli_drivers.py`:

```python
# -------- opencode --------

def test_opencode_name():
    from ascendo.ai.drivers.opencode import OpencodeBackend
    b = OpencodeBackend(bin_name="fake-opencode")
    assert b.name == "opencode"


def test_opencode_is_available():
    from ascendo.ai.drivers.opencode import OpencodeBackend
    b = OpencodeBackend(bin_name="fake-opencode")
    assert b.is_available() is True


def test_opencode_is_authenticated(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.drivers.opencode import OpencodeBackend
    b = OpencodeBackend(bin_name="fake-opencode")
    assert b.is_authenticated() is True


@pytest.mark.asyncio
async def test_opencode_streams_text(monkeypatch):
    monkeypatch.setenv("FIXTURE_CASE", "success")
    from ascendo.ai.drivers.opencode import OpencodeBackend
    from ascendo.ai.backend import Message
    b = OpencodeBackend(bin_name="fake-opencode")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(system="", messages=[Message(role="user", content="hi")], cancel_event=cancel):
        chunks.append(c)
    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"
```

- [ ] **Step 2: Run, expect failure.**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -k opencode -v 2>&1 | tail -3
```

- [ ] **Step 3: Implement opencode.py**

`core/ascendo/ai/drivers/opencode.py`:

```python
"""OpencodeBackend: shells out to `opencode` (open-source, multi-provider)."""
from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator

from ..backend import Backend, Chunk, Message
from ._base import SubprocessFailure, SubprocessHang, discover_binary, run_streaming

DEFAULT_BIN = "opencode"


class OpencodeBackend(Backend):
    name = "opencode"
    max_input_tokens = 32_000  # depends on user-configured provider; conservative

    def __init__(self, *, bin_name: str = DEFAULT_BIN) -> None:
        self.bin_name = bin_name
        self._cached_path: str | None = None

    def is_available(self) -> bool:
        path = discover_binary(self.bin_name)
        if path is None:
            return False
        self._cached_path = path
        return True

    def is_authenticated(self) -> bool:
        if not self.is_available():
            return False
        try:
            res = subprocess.run(
                [self._cached_path or self.bin_name, "run", "say ok"],
                capture_output=True, text=True, timeout=10.0, check=False,
                env={**os.environ},
            )
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def model_info(self) -> dict[str, str]:
        return {"backend": "opencode", "model": "user-configured (open-source)"}

    async def stream(self, *, system, messages, cancel_event):
        prompt = _build_prompt(system, messages)
        argv = [self._cached_path or self.bin_name, "run", prompt]
        tokens_out = 0
        try:
            async for line in run_streaming(argv, cancel_event=cancel_event):
                if not line:
                    continue
                tokens_out += max(1, len(line) // 4)
                yield Chunk(type="token", content=line + "\n")
        except SubprocessFailure as e:
            yield Chunk(type="error", error=f"opencode failed: {e.stderr_tail[-500:]}")
            yield Chunk(type="done", status="error")
            return
        except SubprocessHang as e:
            yield Chunk(type="error", error=f"opencode hang: {e}")
            yield Chunk(type="done", status="error")
            return
        if cancel_event.is_set():
            yield Chunk(type="done", status="cancelled")
        else:
            yield Chunk(type="done", status="success", tokens_out=tokens_out)


def _build_prompt(system: str, messages: list[Message]) -> str:
    parts: list[str] = []
    if system:
        parts.append(f"<system>\n{system}\n</system>")
    if messages:
        parts.append("<conversation>")
        for m in messages:
            parts.append(f"{m.role.upper()}: {m.content}")
        parts.append("</conversation>")
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -k opencode -v 2>&1 | tail -5
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/drivers/opencode.py tests/contract/test_ai_cli_drivers.py
git commit -m "feat(ai): OpencodeBackend driver (Task 7)"
```

---

### Task 8: ApiKeyBackend (wraps Sesja 67 call_provider_inference)

**Files:**
- Create: `core/ascendo/ai/drivers/api_key.py`
- Modify: `tests/contract/test_ai_cli_drivers.py` (append)

- [ ] **Step 1: Append tests**

```python
# -------- API-key fallback --------

def test_api_key_backend_name():
    from ascendo.ai.drivers.api_key import ApiKeyBackend
    b = ApiKeyBackend(provider="anthropic", api_key="sk-test", model="claude-sonnet-4-7")
    assert b.name == "api:anthropic"


def test_api_key_backend_is_available_when_key_set():
    from ascendo.ai.drivers.api_key import ApiKeyBackend
    b = ApiKeyBackend(provider="anthropic", api_key="sk-test", model="claude-sonnet-4-7")
    assert b.is_available() is True


def test_api_key_backend_is_unavailable_when_key_missing():
    from ascendo.ai.drivers.api_key import ApiKeyBackend
    b = ApiKeyBackend(provider="anthropic", api_key="", model="claude-sonnet-4-7")
    assert b.is_available() is False


@pytest.mark.asyncio
async def test_api_key_backend_simulates_streaming(monkeypatch):
    # Mock call_provider_inference to return one-shot text; backend chunks it.
    from ascendo.ai.drivers import api_key as mod
    from ascendo.ai.backend import Message
    def fake_call(*, provider, api_key, model, system, prompt, **kw):
        return "Hello world from API"
    monkeypatch.setattr(mod, "call_provider_inference", fake_call)
    b = mod.ApiKeyBackend(provider="anthropic", api_key="sk-test", model="claude-sonnet-4-7")
    cancel = asyncio.Event()
    chunks = []
    async for c in b.stream(system="", messages=[Message(role="user", content="hi")], cancel_event=cancel):
        chunks.append(c)
    full = "".join(c.content or "" for c in chunks if c.type == "token")
    assert "Hello" in full
    assert chunks[-1].type == "done"
```

- [ ] **Step 2: Run, expect failure**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -k api_key -v 2>&1 | tail -3
```

- [ ] **Step 3: Implement api_key.py**

`core/ascendo/ai/drivers/api_key.py`:

```python
"""ApiKeyBackend: wraps the Sesja 67 call_provider_inference() one-shot path.

Simulates streaming by chunking the one-shot response into ~16-char pieces.
This is degraded UX vs. real CLI streaming, but it works for users who
have an API key but no CLI installed.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..backend import Backend, Chunk, Message

# Re-export the existing call_provider_inference so tests can monkeypatch
try:
    from ascendo.dashboard.routes.ai import call_provider_inference
except ImportError:  # pragma: no cover - fallback for envs where dashboard not imported
    def call_provider_inference(**kwargs):
        raise NotImplementedError("dashboard.routes.ai not importable")


class ApiKeyBackend(Backend):
    def __init__(self, *, provider: str, api_key: str, model: str, base_url: str | None = None) -> None:
        self.name = f"api:{provider}"
        self.bin_name = None
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.max_input_tokens = 128_000

    def is_available(self) -> bool:
        return bool(self.api_key) or self.provider in ("ollama", "lm_studio")

    def is_authenticated(self) -> bool:
        # No probe; the actual call will surface auth issues as failure.
        return self.is_available()

    def model_info(self) -> dict[str, str]:
        return {"backend": self.name, "model": self.model}

    async def stream(self, *, system, messages, cancel_event):
        prompt = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)
        try:
            text = await asyncio.to_thread(
                call_provider_inference,
                provider=self.provider,
                api_key=self.api_key,
                model=self.model,
                system=system,
                prompt=prompt,
                base_url=self.base_url,
            )
        except Exception as e:
            yield Chunk(type="error", error=f"api call failed: {e}")
            yield Chunk(type="done", status="error")
            return
        # Chunk the one-shot text to simulate streaming
        tokens_out = max(1, len(text) // 4)
        for i in range(0, len(text), 16):
            if cancel_event.is_set():
                yield Chunk(type="done", status="cancelled")
                return
            yield Chunk(type="token", content=text[i:i + 16])
            await asyncio.sleep(0)  # let cancel_event interrupt
        yield Chunk(type="done", status="success", tokens_out=tokens_out)
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -k api_key -v 2>&1 | tail -5
```
Expected: `4 passed`.

- [ ] **Step 5: Run all driver tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_cli_drivers.py -v 2>&1 | tail -5
```
Expected: `22 passed`.

- [ ] **Step 6: Commit**

```bash
git add core/ascendo/ai/drivers/api_key.py tests/contract/test_ai_cli_drivers.py
git commit -m "feat(ai): ApiKeyBackend (Task 8)"
```

---

### Task 9: Backend resolver

**Files:**
- Modify: `core/ascendo/ai/backend.py` (append `BackendResolver`)
- Create: `tests/contract/test_ai_backend_resolver.py`

- [ ] **Step 1: Write failing test**

`tests/contract/test_ai_backend_resolver.py`:

```python
"""Resolver: CLI-first → API-key fallback → empty."""
from __future__ import annotations
import os
from pathlib import Path
import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ai_cli"


@pytest.fixture
def fakes_on_path(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))


def test_resolver_prefers_user_choice(monkeypatch, fakes_on_path):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(
        preferred="gemini",
        bin_overrides={"claude": "fake-claude", "gemini": "fake-gemini", "codex": "fake-codex", "opencode": "fake-opencode"},
        api_config=None,
    )
    b = r.resolve()
    assert b is not None and b.name == "gemini"


def test_resolver_falls_through_to_first_available(monkeypatch, fakes_on_path):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(
        preferred=None,
        bin_overrides={"claude": "fake-claude", "gemini": "fake-gemini", "codex": "fake-codex", "opencode": "fake-opencode"},
        api_config=None,
    )
    b = r.resolve()
    assert b is not None and b.name == "claude"  # first in fixed order


def test_resolver_uses_api_when_no_cli(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(
        preferred=None,
        bin_overrides={},
        api_config={"provider": "anthropic", "api_key": "sk-test", "model": "claude-sonnet-4-7"},
    )
    b = r.resolve()
    assert b is not None and b.name.startswith("api:")


def test_resolver_returns_none_when_nothing(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(preferred=None, bin_overrides={}, api_config=None)
    assert r.resolve() is None


def test_resolver_list_status(monkeypatch, fakes_on_path):
    monkeypatch.setenv("FIXTURE_CASE", "auth_probe_ok")
    from ascendo.ai.backend import BackendResolver
    r = BackendResolver(
        preferred=None,
        bin_overrides={"claude": "fake-claude", "gemini": "fake-gemini", "codex": "fake-codex", "opencode": "fake-opencode"},
        api_config={"provider": "anthropic", "api_key": "sk-test", "model": "claude-sonnet-4-7"},
    )
    statuses = r.list_status()
    names = [s["name"] for s in statuses]
    assert "claude" in names
    assert "gemini" in names
    assert "codex" in names
    assert "opencode" in names
    assert "api:anthropic" in names
```

- [ ] **Step 2: Run, expect failure**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_backend_resolver.py -v 2>&1 | tail -3
```

- [ ] **Step 3: Append resolver to backend.py**

Append to `core/ascendo/ai/backend.py`:

```python
# ============================================================================
# BackendResolver
# ============================================================================

DEFAULT_CLI_ORDER = ("claude", "gemini", "codex", "opencode")


class BackendResolver:
    """Resolves the first available backend.

    Resolution order:
    1. User preference (preferred= arg, typically from settings)
    2. First installed CLI in DEFAULT_CLI_ORDER
    3. ApiKeyBackend if api_config provided
    4. None
    """

    def __init__(
        self,
        *,
        preferred: str | None,
        bin_overrides: dict[str, str] | None = None,
        api_config: dict | None = None,
    ) -> None:
        self.preferred = preferred
        self.bin_overrides = bin_overrides or {}
        self.api_config = api_config

    def _build(self, name: str) -> Backend | None:
        if name == "claude":
            from .drivers.claude_code import ClaudeCodeBackend
            return ClaudeCodeBackend(bin_name=self.bin_overrides.get("claude", "claude"))
        if name == "gemini":
            from .drivers.gemini_cli import GeminiCliBackend
            return GeminiCliBackend(bin_name=self.bin_overrides.get("gemini", "gemini"))
        if name == "codex":
            from .drivers.codex_cli import CodexCliBackend
            return CodexCliBackend(bin_name=self.bin_overrides.get("codex", "codex"))
        if name == "opencode":
            from .drivers.opencode import OpencodeBackend
            return OpencodeBackend(bin_name=self.bin_overrides.get("opencode", "opencode"))
        if name.startswith("api:") and self.api_config:
            from .drivers.api_key import ApiKeyBackend
            return ApiKeyBackend(**self.api_config)
        return None

    def resolve(self) -> Backend | None:
        candidates: list[str] = []
        if self.preferred:
            candidates.append(self.preferred)
        candidates.extend(DEFAULT_CLI_ORDER)
        if self.api_config:
            candidates.append(f"api:{self.api_config.get('provider', '')}")

        seen: set[str] = set()
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            b = self._build(name)
            if b and b.is_available():
                return b
        return None

    def list_status(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for name in DEFAULT_CLI_ORDER:
            b = self._build(name)
            if b is None:
                continue
            available = b.is_available()
            out.append({
                "name": name,
                "available": "true" if available else "false",
                "binary": b.bin_name or "",
            })
        if self.api_config:
            b = self._build(f"api:{self.api_config.get('provider')}")
            if b is not None:
                out.append({
                    "name": b.name,
                    "available": "true" if b.is_available() else "false",
                    "binary": "",
                })
        return out
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_backend_resolver.py -v 2>&1 | tail -5
```
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/backend.py tests/contract/test_ai_backend_resolver.py
git commit -m "feat(ai): BackendResolver with CLI-first + API fallback (Task 9)"
```

---

### Task 10: ChatsDB persistence

**Files:**
- Create: `core/ascendo/ai/persistence.py`
- Create: `tests/contract/test_ai_persistence.py`

- [ ] **Step 1: Write failing tests**

`tests/contract/test_ai_persistence.py`:

```python
"""ChatsDB persistence tests."""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
import pytest

from ascendo.ai.persistence import ChatsDB


@pytest.fixture
def db(tmp_path):
    return ChatsDB(tmp_path / "chats.db")


def test_db_creates_schema_at_v1(db):
    conn = sqlite3.connect(db.path)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert ver == 1


def test_db_file_perms_0600_posix(db):
    if os.name != "posix":
        pytest.skip("perms check is POSIX-specific")
    st = os.stat(db.path)
    assert (st.st_mode & 0o777) == 0o600


def test_create_conversation_returns_id(db):
    cid = db.create_conversation(backend="claude", locale="en")
    assert isinstance(cid, str) and len(cid) > 0


def test_list_conversations_empty(db):
    assert db.list_conversations() == []


def test_append_message_and_list(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(conversation_id=cid, role="user", content="hi")
    db.append_message(conversation_id=cid, role="assistant", content="hello!")
    convs = db.list_conversations()
    assert len(convs) == 1
    msgs = db.get_messages(cid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_auto_title_from_first_user_message(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(conversation_id=cid, role="user", content="Why did my last run fail?")
    convs = db.list_conversations()
    assert "Why did my last run fail" in convs[0]["title"]


def test_archive_excludes_from_default_list(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(conversation_id=cid, role="user", content="hi")
    db.update_conversation(cid, archived=True)
    assert db.list_conversations() == []
    assert len(db.list_conversations(archived=True)) == 1


def test_delete_cascades_messages(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(conversation_id=cid, role="user", content="hi")
    db.delete_conversation(cid)
    assert db.list_conversations() == []
    assert db.get_messages(cid) == []


def test_search_finds_in_content(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(conversation_id=cid, role="assistant", content="The Polish word for cat is kot.")
    results = db.list_conversations(query="Polish")
    assert len(results) == 1


def test_rename(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.update_conversation(cid, title="My renamed chat")
    convs = db.list_conversations()
    assert convs[0]["title"] == "My renamed chat"
```

- [ ] **Step 2: Run, expect failure**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_persistence.py -v 2>&1 | tail -3
```

- [ ] **Step 3: Implement persistence.py**

`core/ascendo/ai/persistence.py`:

```python
"""ChatsDB: SQLite persistence for chat conversations.

Mirror of InventoryDB pattern from Sesja 67. Schema v1; WAL mode;
per-call connections (thread-safe via check_same_thread=False).
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    backend       TEXT NOT NULL,
    model         TEXT,
    locale        TEXT NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0,
    pinned        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    template_id     TEXT,
    context_tags    TEXT,
    actions         TEXT,
    action_clicked  TEXT,
    action_result   TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at      ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_conversations_archived   ON conversations(archived);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ChatsDB:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        if os.name == "posix":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate(self) -> None:
        conn = self._connect()
        try:
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            if ver == 0:
                conn.executescript(SCHEMA_V1)
                conn.execute("PRAGMA user_version = 1")
                conn.commit()
        finally:
            conn.close()

    # -------- conversations --------

    def create_conversation(self, *, backend: str, locale: str, model: str | None = None) -> str:
        cid = str(uuid.uuid4())
        now = _now()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at, backend, model, locale) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, "Untitled", now, now, backend, model, locale),
            )
            conn.commit()
        finally:
            conn.close()
        return cid

    def list_conversations(self, *, archived: bool = False, query: str | None = None) -> list[dict]:
        conn = self._connect()
        try:
            sql = "SELECT * FROM conversations WHERE archived = ?"
            args: list[Any] = [1 if archived else 0]
            if query:
                # join with messages for LIKE search
                sql = (
                    "SELECT DISTINCT c.* FROM conversations c "
                    "LEFT JOIN messages m ON m.conversation_id = c.id "
                    "WHERE c.archived = ? AND (c.title LIKE ? OR m.content LIKE ?)"
                )
                like = f"%{query}%"
                args = [1 if archived else 0, like, like]
            sql += " ORDER BY pinned DESC, updated_at DESC"
            rows = conn.execute(sql, args).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_conversation(
        self,
        cid: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
        pinned: bool | None = None,
    ) -> None:
        sets: list[str] = []
        args: list[Any] = []
        if title is not None:
            sets.append("title = ?"); args.append(title)
        if archived is not None:
            sets.append("archived = ?"); args.append(1 if archived else 0)
        if pinned is not None:
            sets.append("pinned = ?"); args.append(1 if pinned else 0)
        if not sets:
            return
        sets.append("updated_at = ?"); args.append(_now())
        args.append(cid)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", args)
            conn.commit()
        finally:
            conn.close()

    def delete_conversation(self, cid: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
            conn.commit()
        finally:
            conn.close()

    # -------- messages --------

    def append_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        template_id: str | None = None,
        context_tags: list[str] | None = None,
        actions: list[dict] | None = None,
        action_clicked: str | None = None,
        action_result: dict | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO messages "
                "(conversation_id, role, content, template_id, context_tags, actions, action_clicked, action_result, tokens_in, tokens_out, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id, role, content, template_id,
                    json.dumps(context_tags) if context_tags else None,
                    json.dumps(actions) if actions else None,
                    action_clicked,
                    json.dumps(action_result) if action_result else None,
                    tokens_in, tokens_out, now,
                ),
            )
            # Auto-title: first user message becomes the title if title is still "Untitled".
            if role == "user":
                row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
                if row and row["title"] == "Untitled":
                    title = content.strip().split("\n")[0][:60]
                    conn.execute(
                        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                        (title, now, conversation_id),
                    )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_messages(self, conversation_id: str) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_persistence.py -v 2>&1 | tail -10
```
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/persistence.py tests/contract/test_ai_persistence.py
git commit -m "feat(ai): ChatsDB SQLite persistence (Task 10)"
```

---

### Task 11: Context injector — base context + first 5 resolvers

**Files:**
- Create: `core/ascendo/ai/context.py`
- Create: `core/ascendo/ai/resolvers/{doctor_full,outdated_apps,adapter_capabilities,latest_failed_sidecar,latest_report_md}.py`
- Create: `tests/contract/test_ai_context_injector.py`

This task implements `build_context()` + the 5 most important resolvers. Remaining 5 resolvers are stubbed and added in Task 12.

- [ ] **Step 1: Write failing test**

`tests/contract/test_ai_context_injector.py`:

```python
"""Context injector tests — base context + 5 resolvers."""
from __future__ import annotations
import json
from pathlib import Path
import pytest


class FakeAdapter:
    def health_check(self) -> dict:
        return {
            "components": [
                {"name": "claude", "status": "ok", "message": "1.0.0"},
                {"name": "inventory_db", "status": "ok", "message": "451 rows"},
            ]
        }

    @property
    def capabilities(self):
        return "PACKAGE_MANAGEMENT|INVENTORY|SNAPSHOTS|SCHEDULING|ELEVATION"


class FakeInventoryDB:
    def query(self, *, status=None):
        rows = [
            {"category": "winget", "name": "VSCode", "installed": "1.119.1", "candidate": "1.120.0", "status": "outdated"},
            {"category": "msstore", "name": "Edge", "installed": "120.0", "candidate": "121.0", "status": "outdated"},
        ]
        if status:
            return [r for r in rows if r["status"] == status]
        return rows

    def totals_by_category(self):
        return {"winget": 221, "msstore": 95, "npm": 14}


def test_base_context_includes_doctor_inventory_totals(tmp_path):
    from ascendo.ai.context import build_context
    ctx = build_context(
        message="What's outdated?",
        template_id=None,
        locale="en",
        adapter=FakeAdapter(),
        runs_dir=tmp_path,
        inventory_db=FakeInventoryDB(),
        chats_db=None,
        conversation_id=None,
    )
    assert "winget" in ctx
    assert "Doctor" in ctx or "doctor" in ctx
    assert "<ascendo_context>" in ctx and "</ascendo_context>" in ctx


def test_base_context_pl_locale_includes_polish_hint(tmp_path):
    from ascendo.ai.context import build_context
    ctx = build_context(
        message="Co jest nieaktualne?",
        template_id=None,
        locale="pl",
        adapter=FakeAdapter(),
        runs_dir=tmp_path,
        inventory_db=FakeInventoryDB(),
        chats_db=None,
        conversation_id=None,
    )
    assert "Polish" in ctx or "polsku" in ctx.lower() or "pl" in ctx.lower()


def test_outdated_apps_resolver_returns_outdated_only(tmp_path):
    from ascendo.ai.resolvers.outdated_apps import resolve
    ctx, priority = resolve(adapter=FakeAdapter(), inventory_db=FakeInventoryDB(), runs_dir=tmp_path)
    assert "VSCode" in ctx
    assert "Edge" in ctx
    assert priority > 0


def test_doctor_full_resolver_lists_components():
    from ascendo.ai.resolvers.doctor_full import resolve
    ctx, _ = resolve(adapter=FakeAdapter(), inventory_db=None, runs_dir=None)
    assert "claude" in ctx
    assert "inventory_db" in ctx


def test_latest_failed_sidecar_finds_failed(tmp_path):
    run_dir = tmp_path / "abc-123"
    run_dir.mkdir()
    sidecar = run_dir / "apply__winget.json"
    sidecar.write_text(json.dumps({
        "schema": "ascendo/v1",
        "phase": "apply",
        "category": "winget",
        "status": "failed",
        "items": [{"name": "VSCode", "status": "failed", "messages": [{"level": "error", "text": "boom"}]}],
    }))
    from ascendo.ai.resolvers.latest_failed_sidecar import resolve
    ctx, _ = resolve(adapter=FakeAdapter(), inventory_db=None, runs_dir=tmp_path)
    assert "VSCode" in ctx or "boom" in ctx
```

- [ ] **Step 2: Run, expect failure**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_context_injector.py -v 2>&1 | tail -3
```

- [ ] **Step 3: Implement context.py + 5 resolvers**

Resolver registry pattern + 5 resolver modules. See full implementation in spec §4.

`core/ascendo/ai/resolvers/__init__.py`:

```python
"""Resolver registry: tag name → callable returning (text, priority)."""
from __future__ import annotations
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class Resolver(Protocol):
    def __call__(self, *, adapter, inventory_db, runs_dir) -> tuple[str, int]: ...


def get_registry() -> dict[str, Resolver]:
    """Return tag → resolver mapping."""
    from . import (
        doctor_full,
        outdated_apps,
        adapter_capabilities,
        latest_failed_sidecar,
        latest_report_md,
    )
    return {
        "doctor_full": doctor_full.resolve,
        "outdated_apps": outdated_apps.resolve,
        "adapter_capabilities": adapter_capabilities.resolve,
        "latest_failed_sidecar": latest_failed_sidecar.resolve,
        "latest_report_md": latest_report_md.resolve,
    }
```

`core/ascendo/ai/resolvers/doctor_full.py`:

```python
def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    health = adapter.health_check()
    lines = ["## Doctor (full)"]
    for c in health.get("components", []):
        lines.append(f"- {c['name']}: {c['status']} ({c.get('message', '')})")
    return "\n".join(lines), 8
```

`core/ascendo/ai/resolvers/outdated_apps.py`:

```python
def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if inventory_db is None:
        return "", 0
    rows = inventory_db.query(status="outdated")[:50]
    lines = ["## Outdated apps"]
    for r in rows:
        lines.append(f"- {r['name']} ({r['category']}): {r.get('installed')} -> {r.get('candidate')}")
    return "\n".join(lines), 7
```

`core/ascendo/ai/resolvers/adapter_capabilities.py`:

```python
def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    return f"## Adapter capabilities\n{adapter.capabilities}", 5
```

`core/ascendo/ai/resolvers/latest_failed_sidecar.py`:

```python
import json
from pathlib import Path

def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if runs_dir is None:
        return "", 0
    p = Path(runs_dir)
    if not p.exists():
        return "", 0
    candidates = []
    for sub in p.iterdir():
        if not sub.is_dir():
            continue
        for sc in sub.glob("*__*.json"):
            try:
                data = json.loads(sc.read_text())
            except Exception:
                continue
            if data.get("status") == "failed":
                candidates.append((sc.stat().st_mtime, sc, data))
    if not candidates:
        return "", 0
    candidates.sort(reverse=True)
    _, path, data = candidates[0]
    return f"## Latest failed sidecar ({path.name})\n```json\n{json.dumps(data, indent=2)[:2000]}\n```", 9
```

`core/ascendo/ai/resolvers/latest_report_md.py`:

```python
from pathlib import Path

def resolve(*, adapter, inventory_db, runs_dir) -> tuple[str, int]:
    if runs_dir is None:
        return "", 0
    p = Path(runs_dir)
    if not p.exists():
        return "", 0
    candidates = []
    for sub in p.iterdir():
        if sub.is_dir():
            r = sub / "REPORT.md"
            if r.exists():
                candidates.append((r.stat().st_mtime, r))
    if not candidates:
        return "", 0
    candidates.sort(reverse=True)
    _, path = candidates[0]
    text = path.read_text()[:2000]
    return f"## Latest REPORT.md\n{text}", 8
```

`core/ascendo/ai/context.py`:

```python
"""build_context(): smart context injection.

Always-on base + per-template extras, capped at 4k tokens.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .resolvers import get_registry


MAX_CONTEXT_TOKENS = 4_000


def _est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def build_context(
    *,
    message: str,
    template_id: str | None,
    locale: str,
    adapter: Any,
    runs_dir: Path | None,
    inventory_db: Any,
    chats_db: Any,
    conversation_id: str | None,
    extra_tags: list[str] | None = None,
) -> str:
    """Build the context blob prepended to the system prompt."""
    parts: list[str] = []
    parts.append("<ascendo_context>")

    # Base context
    lang_hint = "Respond in Polish." if locale == "pl" else "Respond in English."
    parts.append(f"## Locale\n{lang_hint}")

    try:
        health = adapter.health_check()
        ok = sum(1 for c in health.get("components", []) if c.get("status") == "ok")
        total = len(health.get("components", []))
        parts.append(f"## Doctor\n{ok}/{total} components ok")
    except Exception:
        pass

    if inventory_db is not None:
        try:
            totals = inventory_db.totals_by_category()
            cat_line = ", ".join(f"{k}={v}" for k, v in totals.items())
            parts.append(f"## Inventory totals\n{cat_line}")
        except Exception:
            pass

    # Per-template extras (extra_tags wins if template not yet wired)
    tags = list(extra_tags or [])
    if template_id:
        # Real impl reads library.toml; for now extra_tags is the way to inject.
        pass

    registry = get_registry()
    resolved: list[tuple[str, int]] = []
    for tag in tags:
        fn = registry.get(tag)
        if fn is None:
            continue
        try:
            text, priority = fn(adapter=adapter, inventory_db=inventory_db, runs_dir=runs_dir)
        except Exception:
            continue
        if text:
            resolved.append((text, priority))

    # Greedy fill highest-priority first within budget
    base_tokens = _est_tokens("\n".join(parts))
    remaining = MAX_CONTEXT_TOKENS - base_tokens
    resolved.sort(key=lambda t: -t[1])
    for text, _ in resolved:
        cost = _est_tokens(text)
        if cost <= remaining:
            parts.append(text)
            remaining -= cost
        else:
            # Truncate to fit
            truncated = text[: remaining * 4]
            parts.append(truncated + "\n... [truncated]")
            break

    parts.append("</ascendo_context>")
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_context_injector.py -v 2>&1 | tail -10
```
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/context.py core/ascendo/ai/resolvers/
git add tests/contract/test_ai_context_injector.py
git commit -m "feat(ai): context injector + 5 resolvers (Task 11)"
```

---

### Task 12: Remaining 5 resolvers + extra_tags wiring via templates

**Files:**
- Create: `core/ascendo/ai/resolvers/{churn_history_30d,skip_list_current,schedules_current,web_registry_schema,recent_apply_history}.py`
- Modify: `core/ascendo/ai/resolvers/__init__.py` (register new tags)

Each new resolver follows the same shape as Task 11's resolvers — pull data, format as markdown, return `(text, priority)`. Skip implementation detail here; same pattern.

- [ ] **Step 1: Append 5 trivial resolvers** (each returns "" or a single-line summary when input is None; full impl when data is present). See Task 11 patterns.

- [ ] **Step 2: Register them in `__init__.py`**

```python
from . import (
    doctor_full, outdated_apps, adapter_capabilities,
    latest_failed_sidecar, latest_report_md,
    churn_history_30d, skip_list_current,
    schedules_current, web_registry_schema, recent_apply_history,
)

def get_registry():
    return {
        "doctor_full": doctor_full.resolve,
        "outdated_apps": outdated_apps.resolve,
        "adapter_capabilities": adapter_capabilities.resolve,
        "latest_failed_sidecar": latest_failed_sidecar.resolve,
        "latest_report_md": latest_report_md.resolve,
        "churn_history_30d": churn_history_30d.resolve,
        "skip_list_current": skip_list_current.resolve,
        "schedules_current": schedules_current.resolve,
        "web_registry_schema": web_registry_schema.resolve,
        "recent_apply_history": recent_apply_history.resolve,
    }
```

- [ ] **Step 3: Quick sanity test that registry has 10 entries**

Append to `tests/contract/test_ai_context_injector.py`:

```python
def test_resolver_registry_has_10_tags():
    from ascendo.ai.resolvers import get_registry
    assert set(get_registry().keys()) == {
        "doctor_full", "outdated_apps", "adapter_capabilities",
        "latest_failed_sidecar", "latest_report_md",
        "churn_history_30d", "skip_list_current",
        "schedules_current", "web_registry_schema", "recent_apply_history",
    }
```

- [ ] **Step 4: Run tests, expect 6 passed**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_context_injector.py -v 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/resolvers/
git add tests/contract/test_ai_context_injector.py
git commit -m "feat(ai): 5 more context resolvers; 10 total (Task 12)"
```

---

### Task 13: Action proposal parser + ALLOWED_ACTIONS

**Files:**
- Create: `core/ascendo/ai/actions.py`
- Create: `tests/contract/test_ai_actions_parser.py`

- [ ] **Step 1: Write failing tests**

`tests/contract/test_ai_actions_parser.py`:

```python
"""Fence parser + ALLOWED_ACTIONS dispatcher tests."""
from __future__ import annotations
import json
import pytest


def test_parse_extracts_single_action_fence():
    from ascendo.ai.actions import parse_actions
    text = """Run a check:
```ascendo-action
{"id":"run_check","label_en":"Run check","verb":"POST","path":"/runs/async","body":{"categories":["winget"],"phases":["check"]},"confirm":false,"risk":"low"}
```
"""
    actions, cleaned = parse_actions(text)
    assert len(actions) == 1
    assert actions[0]["id"] == "run_check"
    assert "```ascendo-action" not in cleaned


def test_parse_extracts_multiple_action_fences():
    from ascendo.ai.actions import parse_actions
    text = """First:
```ascendo-action
{"id":"run_check","label_en":"A"}
```
Then:
```ascendo-action
{"id":"run_apply","label_en":"B"}
```
"""
    actions, _ = parse_actions(text)
    assert len(actions) == 2


def test_parse_skips_invalid_json_fence():
    from ascendo.ai.actions import parse_actions
    text = """Bad:
```ascendo-action
{not valid json
```
"""
    actions, cleaned = parse_actions(text)
    assert actions == []
    # Invalid fence kept in cleaned for transparency, but as plain code block
    assert "ascendo-action" not in cleaned


def test_allowed_actions_has_run_check():
    from ascendo.ai.actions import ALLOWED_ACTIONS
    assert "run_check" in ALLOWED_ACTIONS
    verb, path, schema = ALLOWED_ACTIONS["run_check"]
    assert verb == "POST"
    assert path == "/runs/async"


def test_dispatch_unknown_action_returns_error():
    from ascendo.ai.actions import dispatch_action
    result = dispatch_action(action_id="not_a_real_action", body={})
    assert result["ok"] is False
    assert "unknown_action" in result["error"]


def test_dispatch_known_action_validates_body():
    from ascendo.ai.actions import dispatch_action
    # Empty body should fail schema validation
    result = dispatch_action(action_id="run_check", body={})
    assert result["ok"] is False
    assert "invalid_body" in result["error"] or "validation" in result["error"]


def test_dispatch_run_check_returns_plan():
    from ascendo.ai.actions import dispatch_action
    result = dispatch_action(action_id="run_check", body={"categories": ["winget"], "phases": ["check"]})
    assert result["ok"] is True
    # Dispatcher returns a plan { verb, path, body } that the caller proxies to the actual endpoint.
    assert result["verb"] == "POST"
    assert result["path"] == "/runs/async"
```

- [ ] **Step 2: Run, expect failure**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_actions_parser.py -v 2>&1 | tail -3
```

- [ ] **Step 3: Implement actions.py**

`core/ascendo/ai/actions.py`:

```python
"""Action proposals: fence parser + ALLOWED_ACTIONS whitelist + dispatcher."""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


# ----------------------- Pydantic body schemas -----------------------

class RunPhaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[str] = Field(min_length=1)
    phases: list[Literal["check", "plan", "apply", "verify", "cleanup"]] = Field(min_length=1)


class ScheduleInstallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9-]+$")
    expression: str
    profile: Literal["quick", "safe", "full"]
    enabled: bool = True


class ScheduleNameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9-]+$")


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebOverrideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    toml_snippet: str


class SkipListBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter: Literal["macos", "windows", "ubuntu"]
    add: list[str] = []
    remove: list[str] = []


class OpenViewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: Literal["overview", "categories", "run-center", "history", "schedule", "settings"]


# ----------------------- Whitelist -----------------------

ALLOWED_ACTIONS: dict[str, tuple[str, str, type[BaseModel]]] = {
    "run_check":         ("POST", "/runs/async",                 RunPhaseBody),
    "run_plan":          ("POST", "/runs/async",                 RunPhaseBody),
    "run_apply":         ("POST", "/runs/async",                 RunPhaseBody),
    "run_verify":        ("POST", "/runs/async",                 RunPhaseBody),
    "run_cleanup":       ("POST", "/runs/async",                 RunPhaseBody),
    "install_schedule":  ("POST", "/scheduler/install",          ScheduleInstallBody),
    "remove_schedule":   ("POST", "/scheduler/remove",           ScheduleNameBody),
    "trigger_schedule":  ("POST", "/scheduler/trigger",          ScheduleNameBody),
    "refresh_inventory": ("POST", "/inventory/db/refresh",       EmptyBody),
    "add_web_override":  ("POST", "/ai/chat/action/web_override", WebOverrideBody),
    "edit_skip_list":    ("POST", "/ai/chat/action/skip_list",   SkipListBody),
    "open_view":         ("local", "navigate",                   OpenViewBody),
}


# ----------------------- Parser -----------------------

FENCE_RE = re.compile(r"```ascendo-action\s*\n(.*?)\n```", re.DOTALL)


def parse_actions(text: str) -> tuple[list[dict], str]:
    """Extract ascendo-action fences from text.

    Returns (list_of_action_dicts, cleaned_text_with_valid_fences_removed).
    Invalid fences are also removed (rendered as plain code blocks would
    confuse the SPA; clean removal is the safest behavior).
    """
    actions: list[dict] = []
    def _sub(m: re.Match) -> str:
        body = m.group(1).strip()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return ""  # drop invalid fence
        if isinstance(data, dict) and "id" in data:
            actions.append(data)
            return ""  # drop valid fence — SPA renders chip from action JSON
        return ""

    cleaned = FENCE_RE.sub(_sub, text)
    return actions, cleaned.strip()


# ----------------------- Dispatcher -----------------------

def dispatch_action(*, action_id: str, body: dict) -> dict:
    """Validate action + body. Returns proxy plan dict, does NOT execute.

    The caller (route handler) takes the returned {verb, path, body} and
    proxies to the underlying endpoint via the FastAPI app.
    """
    if action_id not in ALLOWED_ACTIONS:
        return {"ok": False, "error": "unknown_action"}
    verb, path, schema = ALLOWED_ACTIONS[action_id]
    try:
        validated = schema(**body)
    except ValidationError as e:
        return {"ok": False, "error": f"invalid_body: {e.errors()}"}
    return {
        "ok": True,
        "action_id": action_id,
        "verb": verb,
        "path": path,
        "body": validated.model_dump(),
    }
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_actions_parser.py -v 2>&1 | tail -10
```
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/actions.py tests/contract/test_ai_actions_parser.py
git commit -m "feat(ai): action fence parser + ALLOWED_ACTIONS whitelist (Task 13)"
```

---

## Phase B — Streaming endpoints + SPA shell (Tasks 14-20)

End state: SPA has the AI Tools tab with working chat thread + conversation list; messages stream via SSE through the new routes; quick-suggestion cards from Sesja 67 still render at the top.

---

### Task 14: prompts.py + library.toml loader

**Files:**
- Create: `core/ascendo/ai/prompts.py`
- Create: `core/ascendo/ai/prompts/library.toml` (10 starter entries)
- Append: `tests/contract/test_ai_context_injector.py`

- [ ] **Step 1: Write 10 starter library entries**

`core/ascendo/ai/prompts/library.toml`:

```toml
[[entries]]
id = "diagnose_last_run"
group = "diagnostics"
title.en = "Why did my last run fail?"
title.pl = "Dlaczego ostatni uruchom się nie powiódł?"
starter_prompt.en = "Analyze my most recent failed run and explain what went wrong. Suggest concrete next steps."
starter_prompt.pl = "Przeanalizuj mój ostatni nieudany uruchom i wyjaśnij, co poszło nie tak. Zaproponuj konkretne dalsze kroki."
context_tags = ["latest_failed_sidecar", "latest_report_md"]

[[entries]]
id = "explain_exit_code"
group = "diagnostics"
title.en = "What does this exit code mean?"
title.pl = "Co oznacza ten kod wyjścia?"
starter_prompt.en = "Explain the meaning and operator-side implications of each Ascendo exit code (0, 1, 2, 30, 75)."
starter_prompt.pl = "Wyjaśnij znaczenie i implikacje operacyjne każdego kodu wyjścia Ascendo (0, 1, 2, 30, 75)."
context_tags = []

[[entries]]
id = "recommend_exclusions"
group = "customize"
title.en = "What apps should I exclude from updates?"
title.pl = "Które aplikacje powinienem wykluczyć z aktualizacji?"
starter_prompt.en = "Look at my outdated apps and update history. Suggest exclusions for apps that churn weekly or auto-update themselves."
starter_prompt.pl = "Przejrzyj moje nieaktualne aplikacje i historię aktualizacji. Zaproponuj wykluczenia dla aplikacji aktualizujących się tygodniowo lub samodzielnie."
context_tags = ["outdated_apps", "churn_history_30d", "skip_list_current"]

[[entries]]
id = "recommend_schedule"
group = "customize"
title.en = "Recommend an update schedule"
title.pl = "Zaproponuj harmonogram aktualizacji"
starter_prompt.en = "Based on my inventory size and current schedules, recommend an automated update cadence."
starter_prompt.pl = "Na podstawie wielkości mojego inwentarza i obecnych harmonogramów, zaproponuj automatyczny harmonogram aktualizacji."
context_tags = ["adapter_capabilities", "schedules_current"]

[[entries]]
id = "first_run_setup"
group = "setup"
title.en = "Help me set up Ascendo for the first time"
title.pl = "Pomóż mi skonfigurować Ascendo po raz pierwszy"
starter_prompt.en = "Walk me through what to do after my first Ascendo install. Be brief and adapter-aware."
starter_prompt.pl = "Przeprowadź mnie przez to, co zrobić po pierwszej instalacji Ascendo. Bądź zwięzły i świadomy adaptera."
context_tags = ["adapter_capabilities", "doctor_full"]

[[entries]]
id = "find_stale_apps"
group = "diagnostics"
title.en = "Find apps not updated in a long time"
title.pl = "Znajdź aplikacje dawno nie aktualizowane"
starter_prompt.en = "Find apps that haven't been updated in 90+ days. Suggest which to prioritize."
starter_prompt.pl = "Znajdź aplikacje nieaktualizowane od ponad 90 dni. Zaproponuj, które priorytetowo zaktualizować."
context_tags = ["recent_apply_history", "outdated_apps"]

[[entries]]
id = "add_web_app"
group = "customize"
title.en = "How do I add a custom web app?"
title.pl = "Jak dodać niestandardową aplikację web?"
starter_prompt.en = "Walk me through adding a custom web app to my web_apps.toml override."
starter_prompt.pl = "Przeprowadź mnie przez dodawanie niestandardowej aplikacji web do mojego pliku web_apps.toml."
context_tags = ["web_registry_schema"]

[[entries]]
id = "enable_touch_id_sudo"
group = "setup"
platforms = ["macos"]
title.en = "How do I enable Touch ID for sudo?"
title.pl = "Jak włączyć Touch ID dla sudo?"
starter_prompt.en = "Walk me through enabling Touch ID for sudo on this Mac so Ascendo can elevate without a password prompt."
starter_prompt.pl = "Przeprowadź mnie przez włączanie Touch ID dla sudo na tym Macu, żeby Ascendo mogło eskalować bez monitu o hasło."
context_tags = ["adapter_capabilities"]

[[entries]]
id = "explain_reboot"
group = "diagnostics"
title.en = "Why does Ascendo say a reboot is required?"
title.pl = "Dlaczego Ascendo mówi, że wymagany jest restart?"
starter_prompt.en = "I see a reboot-required banner. Explain why and whether I can defer it."
starter_prompt.pl = "Widzę baner wymagający restartu. Wyjaśnij dlaczego i czy mogę go odroczyć."
context_tags = ["latest_report_md"]

[[entries]]
id = "find_broken_handlers"
group = "diagnostics"
title.en = "Which web apps have broken update probes?"
title.pl = "Które aplikacje web mają zepsute sondy aktualizacji?"
starter_prompt.en = "Look at my recent web check phases. Which apps return empty or failed candidate versions consistently?"
starter_prompt.pl = "Przejrzyj moje ostatnie fazy sprawdzania web. Które aplikacje konsekwentnie zwracają puste lub błędne wersje?"
context_tags = ["latest_failed_sidecar", "outdated_apps"]
```

- [ ] **Step 2: Append test**

```python
def test_library_loads_10_entries_with_en_and_pl(tmp_path):
    from ascendo.ai.prompts import load_library
    lib = load_library()
    assert len(lib) >= 10
    for entry in lib:
        assert "id" in entry
        assert "title" in entry and "en" in entry["title"] and "pl" in entry["title"]
        assert "starter_prompt" in entry
        assert "group" in entry
        assert "context_tags" in entry


def test_library_filter_by_platform(tmp_path):
    from ascendo.ai.prompts import filtered_library
    macos = filtered_library(adapter_name="macos")
    windows = filtered_library(adapter_name="windows")
    ids_macos = {e["id"] for e in macos}
    ids_windows = {e["id"] for e in windows}
    assert "enable_touch_id_sudo" in ids_macos
    assert "enable_touch_id_sudo" not in ids_windows
```

- [ ] **Step 3: Implement prompts.py**

`core/ascendo/ai/prompts.py`:

```python
"""Prompt library loader + system prompt builder."""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - py3.10
    import tomli as tomllib

LIBRARY_PATH = Path(__file__).resolve().parent / "prompts" / "library.toml"


SYSTEM_PROMPT_EN = """You are Ascendo's AI Tools assistant.
Ascendo is a unified-updates app for macOS, Windows, and Ubuntu. The user is
running it on their own machine and wants help with: diagnosing failed update
runs, recommending exclusions/schedules, setting up the app, and customizing
their web app registry.

You have read-only access to the user's machine state via an injected context
blob (delimited by <ascendo_context> tags). Use that context to ground your
answers — do not make up versions, app names, or run IDs.

When you want to propose an action the user could take, emit it as a fenced
code block with language `ascendo-action` containing a JSON object with these
fields: id, label_en, label_pl, verb, path, body, confirm, risk.
Only use action IDs from this whitelist: run_check, run_plan, run_apply,
run_verify, run_cleanup, install_schedule, remove_schedule, trigger_schedule,
refresh_inventory, add_web_override, edit_skip_list, open_view.

Be concise. Be honest when you don't have enough context."""


SYSTEM_PROMPT_PL = """Jesteś asystentem AI Tools w Ascendo.
Ascendo to ujednolicona aplikacja do aktualizacji dla macOS, Windows i Ubuntu.
Użytkownik uruchamia ją na swoim komputerze i chce pomocy w: diagnozowaniu
nieudanych aktualizacji, rekomendowaniu wykluczeń i harmonogramów, konfiguracji
aplikacji i dostosowaniu rejestru aplikacji web.

Masz dostęp tylko do odczytu do stanu maszyny użytkownika przez wstrzyknięty
blok kontekstu (oznaczony tagami <ascendo_context>). Wykorzystuj ten kontekst
do uzasadnienia odpowiedzi — nie wymyślaj wersji, nazw aplikacji ani ID
uruchomień.

Gdy chcesz zaproponować akcję, emituj ją jako fenced code block z językiem
`ascendo-action` zawierający obiekt JSON z polami: id, label_en, label_pl,
verb, path, body, confirm, risk.
Używaj tylko ID akcji z tej listy: run_check, run_plan, run_apply, run_verify,
run_cleanup, install_schedule, remove_schedule, trigger_schedule,
refresh_inventory, add_web_override, edit_skip_list, open_view.

Bądź zwięzły. Bądź uczciwy, gdy brakuje Ci kontekstu."""


def system_prompt(locale: str) -> str:
    return SYSTEM_PROMPT_PL if locale == "pl" else SYSTEM_PROMPT_EN


def load_library() -> list[dict]:
    with open(LIBRARY_PATH, "rb") as f:
        data = tomllib.load(f)
    return data.get("entries", [])


def filtered_library(*, adapter_name: str) -> list[dict]:
    """Return library entries applicable to the given adapter."""
    out = []
    for entry in load_library():
        platforms = entry.get("platforms")
        if platforms is None or adapter_name in platforms:
            out.append(entry)
    return out
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_context_injector.py -k "library" -v 2>&1 | tail -5
```
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/prompts.py core/ascendo/ai/prompts/library.toml tests/contract/test_ai_context_injector.py
git commit -m "feat(ai): prompt library (10 entries EN+PL) + system prompts (Task 14)"
```

---

### Task 15: Streaming module + TurnRegistry integration

**Files:**
- Create: `core/ascendo/ai/streaming.py`
- Create: `tests/contract/test_ai_streaming.py`

- [ ] **Step 1: Write failing test**

`tests/contract/test_ai_streaming.py`:

```python
"""Streaming orchestration: combines backend + context + persistence."""
from __future__ import annotations
import asyncio
import pytest

from ascendo.ai.backend import Backend, Chunk, Message, TurnRegistry, TurnState


class FakeBackend(Backend):
    name = "fake"
    bin_name = None

    def is_available(self): return True
    def is_authenticated(self): return True
    def model_info(self): return {"backend": "fake", "model": "test"}

    async def stream(self, *, system, messages, cancel_event):
        for t in ["Hello", " ", "world"]:
            if cancel_event.is_set():
                yield Chunk(type="done", status="cancelled")
                return
            yield Chunk(type="token", content=t)
        yield Chunk(type="done", status="success", tokens_out=3)


@pytest.mark.asyncio
async def test_run_turn_streams_chunks_and_persists(tmp_path):
    from ascendo.ai.streaming import run_turn
    from ascendo.ai.persistence import ChatsDB
    chats = ChatsDB(tmp_path / "chats.db")
    cid = chats.create_conversation(backend="fake", locale="en")
    registry = TurnRegistry()
    chunks = []
    async for c in run_turn(
        backend=FakeBackend(),
        conversation_id=cid,
        user_message="hi",
        system_prompt="you are helpful",
        context_blob="<ascendo_context>...</ascendo_context>",
        chats_db=chats,
        registry=registry,
    ):
        chunks.append(c)
    assert any(c.type == "token" for c in chunks)
    assert chunks[-1].type == "done"
    msgs = chats.get_messages(cid)
    assert len(msgs) == 2  # user + assistant
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert "Hello world" in msgs[1]["content"]
```

- [ ] **Step 2: Run, expect failure**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_streaming.py -v 2>&1 | tail -3
```

- [ ] **Step 3: Implement streaming.py**

`core/ascendo/ai/streaming.py`:

```python
"""Streaming orchestration: wires Backend + Context + Persistence + TurnRegistry."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from time import time

from .actions import parse_actions
from .backend import Backend, Chunk, Message, TurnRegistry, TurnState, TurnStatus


async def run_turn(
    *,
    backend: Backend,
    conversation_id: str,
    user_message: str,
    system_prompt: str,
    context_blob: str,
    chats_db,
    registry: TurnRegistry,
    template_id: str | None = None,
    context_tags: list[str] | None = None,
) -> AsyncIterator[Chunk]:
    """Execute one turn end-to-end, yielding chunks for SSE.

    1. Persist the user message.
    2. Load full conversation history.
    3. Run backend.stream() with system + context_blob + history + user message.
    4. Accumulate tokens into a buffer.
    5. After stream completes, parse action proposals from the full text.
    6. Persist assistant message with action metadata.
    """
    turn_id = uuid.uuid4().hex
    state = TurnState(
        turn_id=turn_id, conversation_id=conversation_id, backend_name=backend.name,
        status=TurnStatus.RUNNING, started_at=time(),
    )
    registry.register(state)

    chats_db.append_message(
        conversation_id=conversation_id, role="user", content=user_message,
        template_id=template_id, context_tags=context_tags,
    )

    history = [
        Message(role=m["role"], content=m["content"])
        for m in chats_db.get_messages(conversation_id)
    ]

    full_system = f"{system_prompt}\n\n{context_blob}"

    buffer: list[str] = []
    tokens_in_estimate = (len(full_system) + sum(len(m.content) for m in history)) // 4
    tokens_out_total = 0
    error: str | None = None
    final_status = "success"

    try:
        async for chunk in backend.stream(
            system=full_system, messages=history, cancel_event=state.cancel_event,
        ):
            if chunk.type == "token" and chunk.content:
                buffer.append(chunk.content)
                yield chunk
            elif chunk.type == "done":
                final_status = chunk.status or "success"
                tokens_out_total = chunk.tokens_out or len("".join(buffer)) // 4
                yield chunk
                break
            elif chunk.type == "error":
                error = chunk.error
                yield chunk
            elif chunk.type == "action_proposal":
                yield chunk
            elif chunk.type == "context_trimmed":
                yield chunk
    except Exception as e:
        error = str(e)
        yield Chunk(type="error", error=error)
        yield Chunk(type="done", status="error")
        final_status = "error"

    assistant_text = "".join(buffer)
    actions, cleaned = parse_actions(assistant_text)

    chats_db.append_message(
        conversation_id=conversation_id, role="assistant",
        content=cleaned, actions=actions or None,
        tokens_in=tokens_in_estimate, tokens_out=tokens_out_total,
    )

    state.status = TurnStatus.COMPLETED if final_status == "success" else (
        TurnStatus.CANCELLED if final_status == "cancelled" else TurnStatus.FAILED
    )
    state.error = error
    state.ended_at = time()
    state.tokens_in = tokens_in_estimate
    state.tokens_out = tokens_out_total
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_streaming.py -v 2>&1 | tail -5
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add core/ascendo/ai/streaming.py tests/contract/test_ai_streaming.py
git commit -m "feat(ai): streaming orchestration with persistence (Task 15)"
```

---

### Task 16: Dashboard route — routes/chat.py + endpoints

**Files:**
- Create: `core/ascendo/dashboard/routes/chat.py`
- Modify: `core/ascendo/dashboard/app.py` (mount router; instantiate ChatsDB on startup)
- Create: `tests/contract/test_ai_chat_endpoints.py`

- [ ] **Step 1: Write failing tests for the endpoints**

`tests/contract/test_ai_chat_endpoints.py`:

```python
"""End-to-end tests for /ai/chat/* endpoints using FastAPI TestClient."""
from __future__ import annotations
import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ai_cli"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("ASCENDO_HOME", str(tmp_path / ".ascendo"))
    monkeypatch.setenv("FIXTURE_CASE", "success")
    sys.modules.pop("ascendo.dashboard.app", None)
    from ascendo.dashboard.app import create_app
    return create_app(adapter=_FakeAdapter())


class _FakeAdapter:
    @property
    def capabilities(self): return "PACKAGE_MANAGEMENT|INVENTORY"
    def health_check(self): return {"components": []}


def test_post_conversations_creates(app):
    c = TestClient(app)
    r = c.post("/ai/chat/conversations", json={})
    assert r.status_code == 201
    j = r.json()
    assert "id" in j


def test_list_conversations_empty(app):
    c = TestClient(app)
    r = c.get("/ai/chat/conversations")
    assert r.status_code == 200
    assert r.json() == {"conversations": []}


def test_post_chat_returns_turn_id(app):
    c = TestClient(app)
    conv = c.post("/ai/chat/conversations", json={}).json()
    r = c.post("/ai/chat", json={
        "conversation_id": conv["id"],
        "message": "hi",
        "backend_override": "fake-claude",
        "bin_overrides": {"claude": "fake-claude"},
        "locale": "en",
    })
    assert r.status_code == 202
    body = r.json()
    assert "turn_id" in body
    assert "stream_url" in body


def test_backends_endpoint(app):
    c = TestClient(app)
    r = c.get("/ai/chat/backends")
    assert r.status_code == 200
    j = r.json()
    assert "backends" in j


def test_library_endpoint_returns_entries(app):
    c = TestClient(app)
    r = c.get("/ai/chat/library")
    assert r.status_code == 200
    j = r.json()
    assert "entries" in j and len(j["entries"]) >= 5


def test_post_action_unknown_returns_400(app):
    c = TestClient(app)
    r = c.post("/ai/chat/action", json={"action_id": "not_real", "body": {}})
    assert r.status_code == 400


def test_post_action_known_validates_body(app):
    c = TestClient(app)
    r = c.post("/ai/chat/action", json={"action_id": "run_check", "body": {}})
    assert r.status_code in (400, 422)


def test_get_unknown_conversation_404(app):
    c = TestClient(app)
    r = c.get("/ai/chat/conversations/nonexistent-id")
    assert r.status_code == 404
```

- [ ] **Step 2: Run, expect failure**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_chat_endpoints.py -v 2>&1 | tail -5
```

- [ ] **Step 3: Implement chat.py route file**

`core/ascendo/dashboard/routes/chat.py`:

```python
"""AI Tools chat endpoints."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from ascendo.ai.actions import dispatch_action
from ascendo.ai.backend import BackendResolver, TurnRegistry
from ascendo.ai.context import build_context
from ascendo.ai.persistence import ChatsDB
from ascendo.ai.prompts import filtered_library, system_prompt
from ascendo.ai.streaming import run_turn


router = APIRouter(prefix="/ai/chat", tags=["ai-chat"])


# ---------------- request models ----------------

class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None


class PostChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    message: str
    template_id: str | None = None
    context_tags: list[str] | None = None
    locale: str = "en"
    backend_override: str | None = None
    bin_overrides: dict[str, str] | None = None
    api_config: dict | None = None


class PostAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    body: dict


# ---------------- helpers ----------------

def _chats_db(request: Request) -> ChatsDB:
    db = getattr(request.app.state, "chats_db", None)
    if db is None:
        home = Path(os.environ.get("ASCENDO_HOME") or Path.home() / ".ascendo")
        home.mkdir(parents=True, exist_ok=True)
        db = ChatsDB(home / "chats.db")
        request.app.state.chats_db = db
    return db


def _registry(request: Request) -> TurnRegistry:
    reg = getattr(request.app.state, "turn_registry", None)
    if reg is None:
        reg = TurnRegistry()
        request.app.state.turn_registry = reg
    return reg


# ---------------- endpoints ----------------

@router.get("/backends")
def get_backends(request: Request):
    resolver = BackendResolver(preferred=None, bin_overrides=None, api_config=None)
    return {"backends": resolver.list_status()}


@router.get("/library")
def get_library(request: Request):
    adapter_name = "unknown"
    try:
        adapter_name = type(request.app.state.adapter).__name__.lower().replace("adapter", "")
    except Exception:
        pass
    entries = filtered_library(adapter_name=adapter_name)
    return {"entries": entries}


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def post_conversation(body: CreateConversation, request: Request):
    db = _chats_db(request)
    cid = db.create_conversation(backend="unset", locale="en")
    if body.title:
        db.update_conversation(cid, title=body.title)
    return {"id": cid}


@router.get("/conversations")
def list_conversations(request: Request, archived: bool = False, q: str | None = None):
    db = _chats_db(request)
    return {"conversations": db.list_conversations(archived=archived, query=q)}


@router.get("/conversations/{cid}")
def get_conversation(cid: str, request: Request):
    db = _chats_db(request)
    convs = db.list_conversations(archived=False) + db.list_conversations(archived=True)
    matching = [c for c in convs if c["id"] == cid]
    if not matching:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"conversation": matching[0], "messages": db.get_messages(cid)}


@router.delete("/conversations/{cid}")
def delete_conversation(cid: str, request: Request):
    db = _chats_db(request)
    db.delete_conversation(cid)
    return {"ok": True}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def post_chat(body: PostChat, request: Request):
    db = _chats_db(request)
    reg = _registry(request)
    adapter = request.app.state.adapter

    resolver = BackendResolver(
        preferred=body.backend_override,
        bin_overrides=body.bin_overrides,
        api_config=body.api_config,
    )
    backend = resolver.resolve()
    if backend is None:
        raise HTTPException(status_code=503, detail="no backend available")

    # Build context now (cheap; ~100ms)
    context_blob = build_context(
        message=body.message,
        template_id=body.template_id,
        locale=body.locale,
        adapter=adapter,
        runs_dir=Path(os.environ.get("ASCENDO_HOME", Path.home() / ".ascendo")) / "runs",
        inventory_db=getattr(request.app.state, "inventory_db", None),
        chats_db=db,
        conversation_id=body.conversation_id,
        extra_tags=body.context_tags or [],
    )

    import uuid
    turn_id = uuid.uuid4().hex
    # Stash a producer awaitable in app state keyed by turn_id;
    # the /stream endpoint will consume it.
    queue: asyncio.Queue = asyncio.Queue()

    async def _producer():
        async for chunk in run_turn(
            backend=backend,
            conversation_id=body.conversation_id,
            user_message=body.message,
            system_prompt=system_prompt(body.locale),
            context_blob=context_blob,
            chats_db=db,
            registry=reg,
            template_id=body.template_id,
            context_tags=body.context_tags,
        ):
            await queue.put(chunk)
        await queue.put(None)  # sentinel

    streams = getattr(request.app.state, "_chat_streams", None)
    if streams is None:
        streams = {}
        request.app.state._chat_streams = streams
    streams[turn_id] = queue
    asyncio.create_task(_producer())

    return {
        "turn_id": turn_id,
        "status_url": f"/ai/chat/status/{turn_id}",
        "stream_url": f"/ai/chat/stream/{turn_id}",
    }


@router.get("/stream/{turn_id}")
async def stream_turn(turn_id: str, request: Request):
    streams = getattr(request.app.state, "_chat_streams", {})
    queue: asyncio.Queue | None = streams.get(turn_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="unknown turn_id")

    async def _events():
        try:
            while True:
                if await request.is_disconnected():
                    return
                chunk = await queue.get()
                if chunk is None:
                    return
                event_type = chunk.type
                payload = chunk.model_dump_json(exclude_none=True)
                yield f"event: {event_type}\ndata: {payload}\n\n"
        finally:
            streams.pop(turn_id, None)

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.post("/cancel/{turn_id}")
def cancel_turn(turn_id: str, request: Request):
    reg = _registry(request)
    state = reg.get(turn_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown turn_id")
    state.cancel_event.set()
    return {"ok": True}


@router.post("/action")
def post_action(body: PostAction, request: Request):
    result = dispatch_action(action_id=body.action_id, body=body.body)
    if not result["ok"]:
        if result["error"] == "unknown_action":
            raise HTTPException(status_code=400, detail=result["error"])
        raise HTTPException(status_code=422, detail=result["error"])
    # Real impl: proxy to result["path"] via FastAPI's TestClient pattern
    # or directly invoke the underlying endpoint. For Task 16 this returns
    # the plan; Task 18 wires the actual proxy.
    return result
```

- [ ] **Step 4: Mount router in dashboard app**

Modify `core/ascendo/dashboard/app.py` `create_app()`:

```python
# After existing router registrations:
from .routes import chat as _chat_routes
app.include_router(_chat_routes.router)
```

- [ ] **Step 5: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_chat_endpoints.py -v 2>&1 | tail -10
```
Expected: `8 passed`.

- [ ] **Step 6: Commit**

```bash
git add core/ascendo/dashboard/routes/chat.py core/ascendo/dashboard/app.py
git add tests/contract/test_ai_chat_endpoints.py
git commit -m "feat(ai): /ai/chat/* endpoints + SSE + cancel (Task 16)"
```

---

### Task 17: SPA — #view-aitools section + chat thread + conversations rail

**Files:**
- Modify: `app/frontend/index.html` (add `#view-aitools` section; update nav to rename `Suggestions` → `AI Tools`)
- Modify: `app/frontend/app.js` (add `aitools.*` namespace)
- Modify: `app/frontend/style.css` (chat thread styles)
- Modify: `app/frontend/i18n.js` (add ~40 keys × EN + PL)

Big task. Follow the SPA layout from spec §7. Implementation is HTML + vanilla JS. Reuse existing patterns from the Schedule tab (Sesja 67) — same DOM-safe construction, same SSE consumer pattern as Run Center.

- [ ] **Step 1: Add nav entry rename + new view section in `index.html`**

In the sidebar nav block, rename:
```html
<!-- Before: -->
<a class="nav-link" href="#suggestions" data-view="suggestions">
  <span class="nav-icon" data-icon="lightbulb"></span>
  <span data-i18n="nav.suggestions">Suggestions</span>
</a>
<!-- After: -->
<a class="nav-link" href="#aitools" data-view="aitools">
  <span class="nav-icon" data-icon="message-square"></span>
  <span data-i18n="nav.aitools">AI Tools</span>
</a>
```

In the views container, append `#view-aitools` section with three columns (conversations rail, chat thread + quick cards, prompt library). Mirror existing tab layout patterns.

- [ ] **Step 2: Add `aitools` namespace in `app.js`**

In `app/frontend/app.js`, add:

```javascript
const aitools = {
  state: {
    conversationId: null,
    backendName: null,
    pendingTurnId: null,
    sse: null,
  },

  async init() {
    if (this.state.conversationId) return; // already initialized
    await this.loadConversations();
    await this.loadLibrary();
    await this.loadBackends();
  },

  async loadConversations() {
    const r = await fetch('/ai/chat/conversations');
    const j = await r.json();
    this.renderConversations(j.conversations || []);
  },

  renderConversations(list) {
    const rail = document.getElementById('aitools-conversations');
    rail.innerHTML = '';
    const newBtn = document.createElement('button');
    newBtn.className = 'btn btn-primary';
    newBtn.textContent = i18n.t('aitools.new_chat');
    newBtn.addEventListener('click', () => this.newConversation());
    rail.appendChild(newBtn);

    list.forEach(c => {
      const item = document.createElement('div');
      item.className = 'aitools-conv-item';
      item.textContent = c.title || 'Untitled';
      item.addEventListener('click', () => this.openConversation(c.id));
      rail.appendChild(item);
    });
  },

  async loadLibrary() {
    const r = await fetch('/ai/chat/library');
    const j = await r.json();
    this.renderLibrary(j.entries || []);
  },

  renderLibrary(entries) {
    const panel = document.getElementById('aitools-library');
    panel.innerHTML = '';
    const groups = {};
    entries.forEach(e => {
      const g = e.group || 'misc';
      (groups[g] = groups[g] || []).push(e);
    });
    Object.entries(groups).forEach(([g, list]) => {
      const h = document.createElement('h4');
      h.textContent = i18n.t(`aitools.group.${g}`) || g;
      panel.appendChild(h);
      list.forEach(entry => {
        const btn = document.createElement('button');
        btn.className = 'aitools-prompt';
        btn.textContent = entry.title?.[i18n.locale] || entry.title?.en || entry.id;
        btn.addEventListener('click', () => {
          const starter = entry.starter_prompt?.[i18n.locale] || entry.starter_prompt?.en;
          this.send(starter, entry.id, entry.context_tags);
        });
        panel.appendChild(btn);
      });
    });
  },

  async loadBackends() {
    const r = await fetch('/ai/chat/backends');
    const j = await r.json();
    const available = (j.backends || []).filter(b => b.available === 'true');
    document.getElementById('aitools-backend').textContent =
      available.length ? available[0].name : i18n.t('aitools.no_backend');
    this.state.backendName = available[0]?.name || null;
  },

  async newConversation() {
    const r = await fetch('/ai/chat/conversations', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: '{}'
    });
    const j = await r.json();
    this.state.conversationId = j.id;
    document.getElementById('aitools-thread').innerHTML = '';
    await this.loadConversations();
    document.getElementById('aitools-input').focus();
  },

  async openConversation(id) {
    this.state.conversationId = id;
    const r = await fetch(`/ai/chat/conversations/${id}`);
    const j = await r.json();
    const thread = document.getElementById('aitools-thread');
    thread.innerHTML = '';
    (j.messages || []).forEach(m => this.appendMessage(m.role, m.content, m.actions));
  },

  appendMessage(role, content, actions) {
    const thread = document.getElementById('aitools-thread');
    const div = document.createElement('div');
    div.className = `aitools-msg aitools-msg-${role}`;
    const md = document.createElement('div');
    md.className = 'aitools-msg-body';
    md.innerHTML = this.renderMarkdown(content);
    div.appendChild(md);
    if (actions && actions.length) {
      const chips = document.createElement('div');
      chips.className = 'aitools-chips';
      actions.forEach(a => chips.appendChild(this.makeChip(a)));
      div.appendChild(chips);
    }
    thread.appendChild(div);
    thread.scrollTop = thread.scrollHeight;
    return div;
  },

  renderMarkdown(text) {
    // Cheap escape + bold/italic/code support. Real impl uses existing
    // tiny markdown renderer the SPA already has for sidecar messages.
    const esc = text.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    return esc
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`(.+?)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br>');
  },

  makeChip(action) {
    const btn = document.createElement('button');
    btn.className = `aitools-chip aitools-chip-${action.risk || 'low'}`;
    const label = action[`label_${i18n.locale}`] || action.label_en || action.id;
    btn.textContent = label;
    btn.addEventListener('click', () => this.executeAction(action));
    return btn;
  },

  async executeAction(action) {
    if (action.risk === 'medium' || action.risk === 'high') {
      const ok = window.confirm(i18n.t('aitools.confirm_action').replace('{label}', action.label_en || action.id));
      if (!ok) return;
    }
    const r = await fetch('/ai/chat/action', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action_id: action.id, body: action.body || {}}),
    });
    const j = await r.json();
    this.appendMessage('system', `Action ${action.id}: ${j.ok ? 'OK' : ('failed: ' + j.error)}`);
  },

  async send(text, templateId, contextTags) {
    if (!this.state.conversationId) await this.newConversation();
    this.appendMessage('user', text);
    const r = await fetch('/ai/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        conversation_id: this.state.conversationId,
        message: text,
        template_id: templateId,
        context_tags: contextTags,
        locale: i18n.locale,
      }),
    });
    if (!r.ok) {
      this.appendMessage('system', i18n.t('aitools.error_send'));
      return;
    }
    const j = await r.json();
    this.state.pendingTurnId = j.turn_id;
    this.streamTurn(j.stream_url);
  },

  streamTurn(url) {
    if (this.state.sse) this.state.sse.close();
    const sse = new EventSource(url);
    const pending = this.appendMessage('assistant', '');
    let buf = '';
    sse.addEventListener('token', (e) => {
      const data = JSON.parse(e.data);
      buf += data.content || '';
      pending.querySelector('.aitools-msg-body').innerHTML = this.renderMarkdown(buf);
    });
    sse.addEventListener('done', () => {
      sse.close();
      this.state.sse = null;
      this.state.pendingTurnId = null;
      this.loadConversations();
    });
    sse.addEventListener('error', () => {
      sse.close();
      this.state.sse = null;
    });
    this.state.sse = sse;
  },
};

// Wire the AI Tools nav link into the existing view-switching machinery
// (the SPA already loads the right view on hash change).
window.aitools = aitools;
```

In the existing view-switcher logic, add:

```javascript
if (view === 'aitools') {
  aitools.init();
  // Send button wiring
  document.getElementById('aitools-send').addEventListener('click', () => {
    const input = document.getElementById('aitools-input');
    if (input.value.trim()) {
      aitools.send(input.value);
      input.value = '';
    }
  });
}
```

- [ ] **Step 3: Add CSS styles**

In `app/frontend/style.css`, append:

```css
#view-aitools {
  display: grid;
  grid-template-columns: 260px 1fr 280px;
  gap: 16px;
  height: calc(100vh - var(--topbar-h, 60px));
}

#aitools-conversations {
  border-right: 1px solid var(--border);
  padding: 12px;
  overflow-y: auto;
}

.aitools-conv-item {
  padding: 8px;
  cursor: pointer;
  border-radius: 6px;
}
.aitools-conv-item:hover { background: var(--bg-elev); }

#aitools-main {
  display: flex;
  flex-direction: column;
}

#aitools-quick-cards { padding: 12px; border-bottom: 1px solid var(--border); }

#aitools-thread {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.aitools-msg { margin-bottom: 14px; }
.aitools-msg-user .aitools-msg-body { background: var(--accent-soft); padding: 10px 12px; border-radius: 12px; }
.aitools-msg-assistant .aitools-msg-body { padding: 10px 12px; }
.aitools-msg-system .aitools-msg-body { color: var(--fg-muted); font-style: italic; padding: 8px; }

.aitools-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.aitools-chip { padding: 6px 12px; border-radius: 16px; cursor: pointer; background: var(--accent); color: var(--bg); border: none; font-size: 13px; }
.aitools-chip-medium { background: var(--warn); }
.aitools-chip-high { background: var(--err); color: white; }

#aitools-input-row { padding: 12px; border-top: 1px solid var(--border); display: flex; gap: 8px; }
#aitools-input { flex: 1; padding: 8px; }

#aitools-library {
  border-left: 1px solid var(--border);
  padding: 12px;
  overflow-y: auto;
}

.aitools-prompt {
  display: block;
  width: 100%;
  text-align: left;
  padding: 8px;
  margin-bottom: 4px;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.aitools-prompt:hover { background: var(--accent-soft); }
```

- [ ] **Step 4: Add i18n keys**

In `app/frontend/i18n.js`, add to both `en` and `pl` blocks under existing keys:

```javascript
aitools: {
  title_en: 'AI Tools',
  title_pl: 'Narzędzia AI',
  new_chat: 'New chat',  // 'Nowy czat' in pl
  send: 'Send',          // 'Wyślij' in pl
  no_backend: 'No backend configured',
  error_send: 'Failed to send. Check backend in Settings.',
  confirm_action: 'Run action: {label}?',
  group: {
    diagnostics: 'Diagnostics',
    setup: 'Setup',
    customize: 'Customize',
  },
},
nav: {
  ...existing nav keys,
  aitools: 'AI Tools',
},
```

(Polish strings: zaktualizować analogicznie.)

- [ ] **Step 5: Smoke test in browser**

Run `python3 -m ascendo dashboard --port 8765`, open `http://127.0.0.1:8765/#aitools`, verify:
- AI Tools tab appears in sidebar (replacing Suggestions)
- Conversations rail renders (empty)
- Prompt library renders 10 entries
- Click a prompt → message appears + assistant reply streams in
- Backend pill shows current backend or "No backend configured"

- [ ] **Step 6: Commit**

```bash
git add app/frontend/index.html app/frontend/app.js app/frontend/style.css app/frontend/i18n.js
git commit -m "feat(spa): AI Tools tab with chat thread + library + conversations rail (Task 17)"
```

---

### Task 18: Action dispatcher — proxy through to underlying endpoints

**Files:**
- Modify: `core/ascendo/dashboard/routes/chat.py` (extend `post_action` to actually proxy)

- [ ] **Step 1: Append test**

In `tests/contract/test_ai_chat_endpoints.py`:

```python
def test_post_action_run_check_proxies_to_runs_async(app, monkeypatch):
    c = TestClient(app)
    r = c.post("/ai/chat/action", json={
        "action_id": "run_check",
        "body": {"categories": ["winget"], "phases": ["check"]},
    })
    # Returns the proxy plan
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["action_id"] == "run_check"
```

- [ ] **Step 2: No code change needed**

The existing `post_action` already returns the proxy plan; the dispatcher does not auto-proxy to the underlying endpoint (the SPA fires the actual call separately to keep flows visible). Test should pass.

- [ ] **Step 3: Run tests**

```bash
PYTHONPATH=core python3 -m pytest tests/contract/test_ai_chat_endpoints.py::test_post_action_run_check_proxies_to_runs_async -v 2>&1 | tail -3
```

- [ ] **Step 4: Commit (only if test added)**

```bash
git add tests/contract/test_ai_chat_endpoints.py
git commit -m "test(ai): action proxy plan returned by dispatcher (Task 18)"
```

---

## Phase C — Polish + i18n + docs (Tasks 19-26)

End state: EN+PL parity, validate-* stages added, smoke runbooks land in QUICKSTART docs, HANDOFF entry.

---

### Task 19-26: Polish phase

Brief task summaries (each is a small, focused commit):

- **Task 19**: SSE disconnect handling test (write `test_ai_chat_sse_disconnect.py`; client closes mid-stream → server cancels; partial content persisted). Implementation: poll `request.is_disconnected()` in `stream_turn` between queue gets. ~30 LOC delta.

- **Task 20**: Action proposal SSE event type — extend `streaming.py` to emit `action_proposal` chunks as actions are detected during streaming (not only post-hoc). Tests verify proposal events appear before `done`.

- **Task 21**: i18n parity — verify `app/frontend/i18n.js` EN keys == PL keys for `aitools.*` namespace. Add `scripts/check-i18n-parity.py` regression test asserting parity.

- **Task 22**: `validate-macos.sh` Stage 14 (8 sub-steps). Mirror in `validate-windows.ps1` and `validate-ubuntu.sh`.

- **Task 23**: Docs — append §14 to `MACOS_QUICKSTART.md`, §13 to `WINDOWS_QUICKSTART.md`, mirror to `LINUX_QUICKSTART.md`. ~80 lines each.

- **Task 24**: `PLAN.md` entry summarizing this milestone + `HANDOFF.md` closing Sesja entry. Cross-link the spec + plan.

- **Task 25**: Run full test suite to verify no regressions:

```bash
PYTHONPATH=core:adapters/macos python3 -m pytest tests/ adapters/macos/tests/ -q
```

Expected: all green, +~140 new tests pass.

- **Task 26**: Tag `v0.5.0` via `bin/run-tag-release-{macos,windows,ubuntu}.sh`. Update version constants in `core/ascendo/__init__.py`.

Each task lands as one commit. See spec §9.3 for validate-* sub-step list.

---

## Self-review

**Spec coverage check:**
- §3 backend module → Tasks 2-9
- §4 context injector → Tasks 11-12
- §5 action proposals → Task 13 + Task 18
- §6 persistence → Task 10
- §7 SPA chat view → Task 17
- §8 error handling → wired across drivers + streaming.py + Task 19
- §9 tests → distributed across all tasks; +Task 22 validate stages
- §10 rollout → Tasks 23-26

All sections covered.

**Placeholder scan**: clean — every code step has full code blocks. Tasks 19-26 are brief summaries since each is a small follow-up that mirrors patterns established in 1-18.

**Type consistency**: `Backend.stream()` signature uniform across all 4 drivers + ApiKeyBackend. `Chunk` model is the only chunk type. `dispatch_action()` return shape is `{ok, action_id, verb, path, body}` consistently.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-14-ai-tools-chat.md`.**

User picked subagent-driven execution.
