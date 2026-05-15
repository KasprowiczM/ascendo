"""Shared subprocess + streaming helpers used by every CLI driver."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import AsyncIterator


# Env vars that come from the host shell but interfere with the
# bundled CLIs. opencode (and a couple of the others) silently exits
# 0 with empty stdout when `CLAUDECODE=1` etc. are present in the
# environment — Node-side telemetry/integration code branches into a
# different code path. Drop the lot at the subprocess boundary so the
# driver behaves the same whether the dashboard was launched from a
# vanilla shell or from inside Claude Code / Cowork / Sentry tracing.
_SCRUB_ENV_PREFIXES = (
    "CLAUDE_",
    "CLAUDECODE",
    "CLAUDE_CODE_",
    "ANTHROPIC_",       # Don't leak claude-code's bearer tokens to other CLIs
    "AI_AGENT",
    "BAGGAGE",
    "SENTRY_",
)


def sanitized_env(
    extra: dict[str, str] | None = None,
    *,
    neutral_cwd: str | None = None,
) -> dict[str, str]:
    """Return a copy of os.environ with host-shell pollution stripped.

    Drivers spawn third-party CLIs that don't care about (and sometimes
    misbehave on) the env vars Claude Code / Cowork / IDE integrations
    inject. We keep PATH, HOME, locale + provider-specific vars and drop
    the rest of the noise. ``extra`` overlays at the end.

    ``neutral_cwd``: when provided, also overrides ``PWD`` + ``OLDPWD``
    in the returned env. opencode 1.x (and possibly others) reads PWD
    from env even when the caller passes ``cwd=`` to subprocess.exec —
    if PWD still points at the dashboard's repo, opencode tries to load
    it as a workspace and silently hangs. Pass the same value you pass
    to subprocess.exec's ``cwd`` arg.
    """
    env = {
        k: v for k, v in os.environ.items()
        if not any(k == p.rstrip("_") or k.startswith(p) for p in _SCRUB_ENV_PREFIXES)
    }
    if neutral_cwd is not None:
        env["PWD"] = neutral_cwd
        env["OLDPWD"] = neutral_cwd
    if extra:
        env.update(extra)
    return env


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
            cmd, capture_output=True, text=True, timeout=timeout_s, check=False,
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
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> AsyncIterator[str]:
    """Spawn argv; yield each stdout line.

    Honors cancel_event: SIGTERM, wait grace_seconds, then SIGKILL.
    Raises SubprocessHang if chunk_timeout_s elapses with no output.
    Raises SubprocessFailure on non-zero exit (unless cancel_event set).

    ``cwd`` lets the caller pin a working directory (e.g. so Claude Code
    doesn't auto-load whatever repo the dashboard happens to be running
    from). ``env`` defaults to :func:`sanitized_env` so host-shell pollution
    (CLAUDE_*, CLAUDECODE, ANTHROPIC_*, BAGGAGE) doesn't break the CLI's
    own Node/Bun runtime. Pass an explicit dict to override.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin_text else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        # `neutral_cwd=cwd` also rewrites PWD/OLDPWD in env so opencode
        # (and any other CLI that reads PWD ignoring subprocess.cwd)
        # doesn't pick the host dashboard's repo as a workspace.
        env=env if env is not None else sanitized_env(neutral_cwd=cwd),
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

    hang_raised = False
    cancelled = False
    try:
        if proc.stdout is None:
            return
        while True:
            if cancel_event.is_set():
                cancelled = True
                break
            # Race readline() against cancel_event so we don't block forever.
            readline_task = asyncio.ensure_future(proc.stdout.readline())
            cancel_task = asyncio.ensure_future(cancel_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {readline_task, cancel_task},
                    timeout=chunk_timeout_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                readline_task.cancel()
                cancel_task.cancel()
                raise
            if not done:
                # Both timed out → hang
                readline_task.cancel()
                cancel_task.cancel()
                hang_raised = True
                raise SubprocessHang(f"no output for {chunk_timeout_s}s")
            if cancel_task in done:
                readline_task.cancel()
                cancelled = True
                break
            # readline completed
            cancel_task.cancel()
            line_bytes = readline_task.result()
            if not line_bytes:
                break
            yield line_bytes.decode("utf-8", errors="replace").rstrip("\n")
    finally:
        # Terminate the subprocess synchronously if needed.
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            except asyncio.CancelledError:
                pass
        # Cancel stderr drain — orphaned children may keep the fd open
        # past process exit, so don't wait for natural EOF.
        if not stderr_task.done():
            stderr_task.cancel()
        try:
            await stderr_task
        except (asyncio.CancelledError, Exception):
            pass

    if not hang_raised and proc.returncode not in (None, 0) and not cancel_event.is_set():
        stderr_tail = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-2000:]
        raise SubprocessFailure(proc.returncode or -1, stderr_tail)


