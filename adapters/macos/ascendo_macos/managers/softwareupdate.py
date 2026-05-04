"""SoftwareUpdateManager - IPackageManager for macOS OS updates via `softwareupdate`.

Mirrors MasManager exactly with these differences:

1. Wraps Apple's built-in /usr/sbin/softwareupdate CLI (not `mas`).
2. No version-floor check — softwareupdate ships with macOS and is
   version-bound to the OS release.
3. No sign-in concept (softwareupdate queries Apple's update server
   anonymously; no Apple ID required).

Same MacElevation injection pattern as MasManager: Phase.APPLY runs
get SUDO_ASKPASS in env (mirrors the M5.2 mas pattern; uniform sudo
treatment regardless of any documented CVE).

The bash script (scripts/softwareupdate/apply.sh) handles the actual
`sudo -A softwareupdate -i <flags> -R --verbose` invocation. The -R
flag is mandatory (sets boot metadata for restart-applied updates).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.package_manager import IPackageManager, ManagerError
from ascendo_macos.managers.elevation import MacElevation
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo
from ascendo.models.sidecar import Sidecar
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)

_log = logging.getLogger(__name__)


class SoftwareUpdateManager(IPackageManager):
    """macOS OS-update per-source manager via the `softwareupdate` CLI.

    Args:
        scripts_dir: Path to ``adapters/macos/scripts/``.
        lib_dir:     Path to ``adapters/macos/lib/`` (informational only).
        elevation:   MacElevation instance — used to detect whether a sudo
                     password is cached and to retrieve the SUDO_ASKPASS
                     helper path for the apply phase.
        bash_path:   Optional override for bash binary.
        timeout_sec: Per-phase timeout. Default 1800 (30 min) — matches mas.
                     Real-world `softwareupdate -i ... -R` can run 5-20 min
                     for major OS updates. Apply may reboot mid-run.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "softwareupdate/check.sh",
        Phase.PLAN: "softwareupdate/plan.sh",
        Phase.APPLY: "softwareupdate/apply.sh",
        Phase.VERIFY: "softwareupdate/verify.sh",
        Phase.CLEANUP: "softwareupdate/cleanup.sh",
    }
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 1800

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        elevation: MacElevation,
        bash_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._elevation: MacElevation = elevation
        self._bash_override = bash_path
        self._timeout_sec = timeout_sec
        # Test seam: populated before each _run_streaming call so tests can
        # inspect the env that would be passed to the child process.
        self._last_env_for_test: dict[str, str] = {}

    # -- Identity --------------------------------------------------------

    @property
    def category(self) -> SourceType:
        return SourceType.SOFTWAREUPDATE

    @property
    def display_name(self) -> str:
        return "macOS softwareupdate (OS patches)"

    # -- Availability ----------------------------------------------------

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.MACOS:
            return False
        # softwareupdate ships with macOS at /usr/sbin/softwareupdate.
        # Probe via `which` for installation flexibility (e.g. PATH override
        # in tests via SU_BIN env, but we still require the binary somewhere).
        if shutil.which("softwareupdate") is None and not Path("/usr/sbin/softwareupdate").is_file():
            return False
        if shutil.which("jq") is None:
            return False
        # No version floor: softwareupdate is OS-bound, not user-installed.
        return True

    # -- Phase execution -------------------------------------------------

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        script_rel = self.SCRIPT_BY_PHASE.get(phase)
        if script_rel is None:
            raise ManagerError(
                f"SoftwareUpdateManager does not support phase {phase.value!r}; "
                f"supported: {sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
        script_path = self._scripts_dir / script_rel
        bash = self._resolve_bash()

        with tempfile.TemporaryDirectory(prefix="ascendo-softwareupdate-") as tmp:
            output_dir = Path(tmp)
            argv = self._build_argv(
                bash=bash,
                script_path=script_path,
                run=run,
                output_dir=output_dir,
                item_filter=item_filter,
            )
            log_path = (
                output_dir / str(run.id) / f"{phase.value}__softwareupdate.log"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)

            # Build env before spawning — test seam reads _last_env_for_test.
            env = self._build_env(phase)
            self._last_env_for_test = env

            _log.debug("SoftwareUpdateManager.run_phase phase=%s run_id=%s argv=%r",
                       phase.value, run.id, argv)

            try:
                completed = self._run_streaming(argv, log_path, self._timeout_sec, env=env)
            except subprocess.TimeoutExpired as exc:
                raise ManagerError(
                    f"softwareupdate {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                ) from exc
            except OSError as exc:
                raise ManagerError(
                    f"failed to spawn bash for softwareupdate {phase.value}: {exc}"
                ) from exc

            sidecar_path = output_dir / str(run.id) / f"{phase.value}__softwareupdate.json"
            if not sidecar_path.exists():
                raise ManagerError(self._missing_sidecar_error(
                    phase=phase, script_path=script_path,
                    sidecar_path=sidecar_path, completed=completed,
                ))
            try:
                sc = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                raise ManagerError(
                    f"softwareupdate {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                ) from exc

            if completed.returncode != 0:
                _log.warning(
                    "softwareupdate %s script exited %d but produced a valid sidecar; "
                    "trusting sidecar (status=%s)",
                    phase.value, completed.returncode, sc.status.value,
                )
            return sc

    # -- Internals -------------------------------------------------------

    def _build_argv(
        self,
        *,
        bash: str,
        script_path: Path,
        run: RunInfo,
        output_dir: Path,
        item_filter: Iterable[str] | None,
    ) -> list[str]:
        argv: list[str] = [
            bash,
            str(script_path),
            "--run-id", str(run.id),
            "--trigger", run.trigger.value,
            "--profile", run.profile,
            "--output-dir", str(output_dir),
        ]
        if run.dry_run:
            argv.append("--dry-run")
        if item_filter is not None:
            cleaned = [s.strip() for s in item_filter if s and isinstance(s, str) and s.strip()]
            if cleaned:
                # softwareupdate apply.sh takes ONE label per --filter (single-label
                # MVP per spec §4). If multiple are passed, take the first; log a
                # warning. Future enhancement: emit multiple --filter args or
                # iterate per-id.
                if len(cleaned) > 1:
                    _log.warning(
                        "SoftwareUpdateManager: --filter MVP supports one label only; "
                        "using %r and ignoring %d additional", cleaned[0], len(cleaned) - 1,
                    )
                argv.extend(["--filter", cleaned[0]])
        return argv

    def _build_env(self, phase: Phase) -> dict[str, str]:
        """Build the env dict to pass explicitly to the child process.

        All phases receive a snapshot of ``os.environ`` passed via
        ``Popen(env=...)``.  For Phase.APPLY additionally, when a sudo
        password is cached, SUDO_ASKPASS is injected so the bash script's
        ``sudo -A softwareupdate ...`` can authenticate non-interactively
        from the dashboard.
        """
        env = dict(os.environ)
        if phase is Phase.APPLY and self._elevation.has_password_registered():
            helper = self._elevation.askpass_path()
            if helper is not None:
                env["SUDO_ASKPASS"] = str(helper)
        return env

    def _run_streaming(
        self,
        argv: list[str],
        log_path: Path,
        timeout: float,
        *,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.Popen(  # noqa: S603 (argv list)
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        captured: list[str] = []
        started = time.monotonic()
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                for line in iter(proc.stdout.readline, ""):
                    if time.monotonic() - started > timeout:
                        proc.kill()
                        raise subprocess.TimeoutExpired(argv, timeout)
                    if not line:
                        break
                    captured.append(line)
                    try:
                        fh.write(line)
                        fh.flush()
                    except OSError:
                        pass
        finally:
            proc.stdout.close()
        try:
            rc = proc.wait(timeout=max(1.0, timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
        return subprocess.CompletedProcess(
            args=argv, returncode=rc, stdout="".join(captured), stderr="",
        )

    def _missing_sidecar_error(
        self,
        *,
        phase: Phase,
        script_path: Path,
        sidecar_path: Path,
        completed: subprocess.CompletedProcess[str],
    ) -> str:
        def _tail(s: str | None, limit: int = 800) -> str:
            if not s:
                return "<empty>"
            return s if len(s) <= limit else f"...<truncated {len(s) - limit}>...{s[-limit:]}"
        return (
            f"softwareupdate {phase.value} script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stdout (tail): {_tail(completed.stdout)}"
        )

    def _resolve_bash(self) -> str:
        if self._bash_override is not None:
            return self._bash_override
        for cand in ("bash", "/bin/bash"):
            found = shutil.which(cand) if not cand.startswith("/") else (cand if Path(cand).is_file() else None)
            if found:
                return found
        raise ManagerError("no bash on PATH and /bin/bash missing")
