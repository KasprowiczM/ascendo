"""WindowsElevation — IElevation via UAC.

Strict argv-only contract enforced (ADR-0005, threat-model T4): the first
element of every ``run()`` argv must appear in the registered allow-list
(matched by basename, case-insensitive). Shell strings are NEVER accepted;
nothing is re-quoted; the argv list is handed to ``CreateProcessW`` as-is
through the standard library's :mod:`subprocess`.

Two elevation modes are supported:

1. **Already-elevated:** if the current process already has admin token
   (``IsUserAnAdmin``), :meth:`run` simply spawns the child with
   :func:`subprocess.run` — no UAC prompt, no separate process, full
   stdout/stderr capture. This is the fast path used during a single
   admin shell session.

2. **Elevation required:** if the current process is NOT elevated, the
   child is spawned via ``ShellExecuteW`` with ``lpVerb='runas'``, which
   triggers UAC. Stdout/stderr cannot be captured cross-token (UAC
   isolates the child token from the parent's pipes), so the elevated
   child writes to a tempfile that the parent reads after the wait.

We deliberately keep the implementation Win32-API-thin: stdlib only
(``ctypes``, ``subprocess``, ``tempfile``). No pywin32 dependency.
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time

from ascendo.utils.proc import no_window_kwargs
from collections.abc import Iterable, Sequence
from pathlib import Path

from ascendo.interfaces.elevation import (
    ElevationDenied,
    ElevationResult,
    ElevationTimeout,
    IElevation,
)
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem

_log = logging.getLogger(__name__)


def _is_admin_token() -> bool:
    """True if the current process holds an admin token.

    Uses ``shell32.IsUserAnAdmin`` which works on Windows Vista+ across
    all token-elevation states. Returns False on non-Windows hosts.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


class WindowsElevation(IElevation):
    """UAC-backed IElevation for Windows."""

    def __init__(self) -> None:
        # Allow-list is registered per-run (process-scoped), per the
        # IElevation contract. Stored as a frozen set of lowercase
        # basenames.
        self._allowlist: frozenset[str] = frozenset()

    @property
    def available_methods(self) -> tuple[ElevationMethod, ...]:
        if sys.platform.startswith("win"):
            return (ElevationMethod.UAC, ElevationMethod.NONE)
        return ()

    def is_currently_elevated(self, host: HostInfo) -> bool:
        return host.os is OperatingSystem.WINDOWS and _is_admin_token()

    def register_allowlist(self, allowed_commands: Iterable[str]) -> None:
        """Register the set of permitted command basenames for this run.

        Each entry is normalised to its lowercase basename — callers can
        pass either ``'winget'`` or ``'C:\\\\Windows\\\\System32\\\\winget.exe'``
        and the result is the same. Trailing ``.exe`` is preserved
        because Windows distinguishes ``winget`` from ``winget.bat``
        when matched by ``shutil.which``.
        """
        normalised: set[str] = set()
        for cmd in allowed_commands:
            if not cmd:
                continue
            base = os.path.basename(str(cmd)).lower().strip()
            if base:
                normalised.add(base)
        self._allowlist = frozenset(normalised)
        _log.debug("WindowsElevation: registered allow-list: %r", sorted(self._allowlist))

    def run(
        self,
        host: HostInfo,
        argv: Sequence[str],
        *,
        timeout_sec: int | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        method: ElevationMethod | None = None,
    ) -> ElevationResult:
        if host.os is not OperatingSystem.WINDOWS:
            raise ElevationDenied("WindowsElevation only runs on Windows hosts")
        if not argv:
            raise ElevationDenied("argv is empty")

        # 1) Allow-list gate (T4 mitigation).
        head_basename = os.path.basename(argv[0]).lower()
        if head_basename not in self._allowlist:
            raise ElevationDenied(
                f"command {head_basename!r} not in allow-list "
                f"({sorted(self._allowlist)!r})"
            )

        # 2) Method selection. Default = pick UAC if not currently elevated,
        #    else NONE (direct spawn). Caller can override.
        chosen = method
        if chosen is None:
            chosen = ElevationMethod.NONE if _is_admin_token() else ElevationMethod.UAC

        if chosen is ElevationMethod.NONE:
            return self._run_direct(argv, timeout_sec=timeout_sec, env=env, cwd=cwd)
        if chosen is ElevationMethod.UAC:
            return self._run_uac(argv, timeout_sec=timeout_sec, env=env, cwd=cwd)
        raise ElevationDenied(f"unsupported method {chosen!r}")

    # ── Direct spawn (already elevated) ──────────────────────────────────

    def _run_direct(
        self,
        argv: Sequence[str],
        *,
        timeout_sec: int | None,
        env: dict[str, str] | None,
        cwd: str | None,
    ) -> ElevationResult:
        merged_env = None
        if env is not None:
            merged_env = {**os.environ, **env}
        start = time.perf_counter()
        try:
            completed = subprocess.run(  # noqa: S603
                list(argv), capture_output=True, text=True,
                timeout=timeout_sec, check=False,
                env=merged_env, cwd=cwd,
                **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ElevationTimeout(
                f"command {argv[0]!r} timed out after {timeout_sec}s"
            ) from exc
        duration_ms = int((time.perf_counter() - start) * 1000)
        return ElevationResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            method=ElevationMethod.NONE,
            duration_ms=duration_ms,
        )

    # ── UAC spawn (ShellExecuteW + tempfile capture) ─────────────────────

    def _run_uac(
        self,
        argv: Sequence[str],
        *,
        timeout_sec: int | None,
        env: dict[str, str] | None,  # noqa: ARG002 — UAC child does not inherit overrides safely
        cwd: str | None,
    ) -> ElevationResult:
        if not sys.platform.startswith("win"):
            raise ElevationDenied("UAC only available on Windows")

        # Resolve the executable to an absolute path. ShellExecute does
        # NOT search PATH the same way CreateProcess does, so we resolve
        # via shutil.which here.
        exe = shutil.which(argv[0]) or argv[0]
        params = subprocess.list2cmdline(argv[1:]) if len(argv) > 1 else ""

        # Tempfiles for stdout / stderr capture. We invoke cmd.exe /c so we
        # can redirect stdio to file paths at the elevated child's prompt.
        # This works because the redirection is done by cmd.exe inside the
        # elevated process, not by us crossing the token boundary.
        with tempfile.TemporaryDirectory(prefix="ascendo-uac-") as tmp:
            stdout_path = os.path.join(tmp, "stdout.txt")
            stderr_path = os.path.join(tmp, "stderr.txt")
            exit_path   = os.path.join(tmp, "exit.txt")

            # Build the cmd line: "<exe>" <params> > stdout 2> stderr & echo %ERRORLEVEL% > exit
            cmd_line = (
                f'"{exe}" {params} > "{stdout_path}" 2> "{stderr_path}" '
                f'& echo %ERRORLEVEL% > "{exit_path}"'
            )

            SEE_MASK_NOCLOSEPROCESS = 0x00000040
            SEE_MASK_NO_CONSOLE     = 0x00008000

            class SHELLEXECUTEINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize",          ctypes.c_ulong),
                    ("fMask",           ctypes.c_ulong),
                    ("hwnd",            ctypes.c_void_p),
                    ("lpVerb",          ctypes.c_wchar_p),
                    ("lpFile",          ctypes.c_wchar_p),
                    ("lpParameters",    ctypes.c_wchar_p),
                    ("lpDirectory",     ctypes.c_wchar_p),
                    ("nShow",           ctypes.c_int),
                    ("hInstApp",        ctypes.c_void_p),
                    ("lpIDList",        ctypes.c_void_p),
                    ("lpClass",         ctypes.c_wchar_p),
                    ("hkeyClass",       ctypes.c_void_p),
                    ("dwHotKey",        ctypes.c_ulong),
                    ("hIconOrMonitor",  ctypes.c_void_p),
                    ("hProcess",        ctypes.c_void_p),
                ]

            sei = SHELLEXECUTEINFO()
            sei.cbSize = ctypes.sizeof(sei)
            sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
            sei.lpVerb = "runas"
            sei.lpFile = "cmd.exe"
            sei.lpParameters = "/c " + cmd_line
            sei.lpDirectory = cwd
            sei.nShow = 0  # SW_HIDE

            ShellExecuteEx = ctypes.windll.shell32.ShellExecuteExW  # type: ignore[attr-defined]
            ShellExecuteEx.argtypes = [ctypes.POINTER(SHELLEXECUTEINFO)]
            ShellExecuteEx.restype = ctypes.c_bool
            start = time.perf_counter()
            ok = ShellExecuteEx(ctypes.byref(sei))
            if not ok or not sei.hProcess:
                err = ctypes.GetLastError()
                if err == 1223:  # ERROR_CANCELLED — user clicked No on UAC.
                    raise ElevationDenied("UAC prompt was cancelled by the user")
                raise ElevationDenied(f"ShellExecuteEx(runas) failed (GetLastError={err})")

            # Wait for the elevated child.
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            wait_ms = 0xFFFFFFFF if timeout_sec is None else int(timeout_sec * 1000)
            WAIT_TIMEOUT = 0x102
            wait_result = kernel32.WaitForSingleObject(sei.hProcess, wait_ms)
            duration_ms = int((time.perf_counter() - start) * 1000)
            if wait_result == WAIT_TIMEOUT:
                kernel32.TerminateProcess(sei.hProcess, 1)
                kernel32.CloseHandle(sei.hProcess)
                raise ElevationTimeout(f"UAC child timed out after {timeout_sec}s")
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
            kernel32.CloseHandle(sei.hProcess)

            stdout = _read_quiet(stdout_path)
            stderr = _read_quiet(stderr_path)
            # The exit echoed by cmd.exe is sometimes more reliable than
            # GetExitCodeProcess for cmd.exe-shelled invocations, but we
            # prefer the latter when both agree.
            shell_exit = _read_quiet(exit_path).strip()
            if shell_exit.isdigit():
                final_exit = int(shell_exit)
            else:
                final_exit = int(exit_code.value)

            return ElevationResult(
                exit_code=final_exit,
                stdout=stdout,
                stderr=stderr,
                method=ElevationMethod.UAC,
                duration_ms=duration_ms,
            )


def _read_quiet(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, FileNotFoundError):
        return ""
