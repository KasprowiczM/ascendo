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
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.package_manager import IPackageManager, ManagerError
from ascendo_macos.managers.elevation import MacElevation
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo
from ascendo.orchestrator.stream_log import child_env_with_stream_log
from ascendo.models.sidecar import Sidecar
from ascendo_macos.managers._filter import (
    clean_filter,
    phase_of,
    softwareupdate_filter_tokens,
)
from ascendo_macos.managers._proc import handle_phase_timeout, run_streaming
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
        timeout_sec: Per-phase timeout. Default 14400 (4 h): a large macOS
                     install can run for well over an hour and must not be
                     SIGTERMed mid-way. Override with the
                     ``ASCENDO_SOFTWAREUPDATE_TIMEOUT`` env var (seconds);
                     an explicit ``timeout_sec`` argument wins over both.
                     Apply may reboot mid-run.
    """

    #: ``softwareupdate -R`` can restart the Mac: the orchestrator runs this
    #: manager's APPLY last, and not at all when brew/mas/npm/web apply failed.
    defer_until_end: ClassVar[bool] = True

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "softwareupdate/check.sh",
        Phase.PLAN: "softwareupdate/plan.sh",
        Phase.APPLY: "softwareupdate/apply.sh",
        Phase.VERIFY: "softwareupdate/verify.sh",
        Phase.CLEANUP: "softwareupdate/cleanup.sh",
    }
    # Only apply acts on --filter (a comma-separated list of update LABELS;
    # check/plan/verify/cleanup enumerate everything).
    FILTER_PHASES: ClassVar[frozenset[Phase]] = frozenset({Phase.APPLY})
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 14400
    TIMEOUT_ENV: ClassVar[str] = "ASCENDO_SOFTWAREUPDATE_TIMEOUT"

    @classmethod
    def _resolve_timeout(cls, explicit: int | None) -> int:
        """explicit arg > env override (positive integer seconds) > default."""
        if explicit is not None:
            return explicit
        raw = os.environ.get(cls.TIMEOUT_ENV, "").strip()
        if raw:
            try:
                value = int(raw)
            except ValueError:
                value = 0
            if value > 0:
                return value
            _log.warning("ignoring invalid %s=%r (need a positive integer of seconds)",
                         cls.TIMEOUT_ENV, raw)
        return cls.DEFAULT_TIMEOUT_SEC

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        elevation: MacElevation,
        bash_path: str | None = None,
        timeout_sec: int | None = None,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._elevation: MacElevation = elevation
        self._bash_override = bash_path
        self._timeout_sec = self._resolve_timeout(timeout_sec)
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
                phase=phase,
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
                timeout_msg = (
                    f"softwareupdate {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                )
                return handle_phase_timeout(
                    output_dir=output_dir, run_id=run.id, phase=phase,
                    category="softwareupdate", message=timeout_msg, exc=exc,
                )
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
        phase: Phase | None = None,
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
        if phase_of(phase, script_path) in self.FILTER_PHASES:
            cleaned = clean_filter(item_filter)
            if cleaned:
                # ALL label entries are forwarded (the script installs every
                # pending label that matches any of them). App names from
                # other categories are not labels: only label-shaped
                # tokens are sent; if none is label-shaped the raw tokens
                # are sent so the script selects nothing rather than
                # degrading to "install everything".
                argv.extend(["--filter", ",".join(softwareupdate_filter_tokens(cleaned))])
        return argv

    def _build_env(self, phase: Phase) -> dict[str, str]:
        """Build the env dict to pass explicitly to the child process.

        All phases receive a snapshot of ``os.environ`` passed via
        ``Popen(env=...)``.  For Phase.APPLY additionally, when a sudo
        password is cached, SUDO_ASKPASS is injected so the bash script's
        ``sudo -A softwareupdate ...`` can authenticate non-interactively
        from the dashboard.
        """
        # os.environ + the per-run stream-log path for THIS worker thread.
        env = child_env_with_stream_log()
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
        # Wall-clock deadline + process-group kill live in the shared helper.
        return run_streaming(argv, log_path, timeout, env=env)

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
