"""Shared bash-script bridge for every Ubuntu IPackageManager.

The legacy bash phase scripts at top-level ``scripts/<cat>/<phase>.sh``
read their inputs from environment variables (set by
``lib/orchestrator.sh``):

    JSON_OUT      — absolute path the script writes its sidecar to
    LOG_FILE      — absolute path for the plain-text log
    ORCH_RUN_ID   — the run UUID
    ORCH_RUN_DIR  — the per-run base dir (parent of all category dirs)
    ORCH_DRY_RUN  — "1" if dry run, else "0" (or unset)
    ORCH_PROFILE  — profile slug (quick/safe/full)

The scripts do NOT take CLI args; everything flows through env. The
helper here wraps that contract: it builds the env, spawns bash, and
then re-reads the sidecar via :func:`read_sidecar` (which auto-translates
the ``ubuntu-aktualizacje/v1`` schema to ``ascendo/v1``).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.package_manager import IPackageManager, ManagerError
from ascendo.models.host import HostInfo
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo
from ascendo.models.sidecar import Sidecar
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)

_log = logging.getLogger(__name__)


class BashPhaseManager(IPackageManager):
    """Mixin / base class implementing run_phase() against legacy bash scripts.

    Subclasses MUST set:
        CATEGORY      — :class:`SourceType` for this manager (e.g. APT)
        SCRIPT_DIR    — relative subdir under ``scripts/`` (e.g. ``"apt"``)
        DISPLAY_NAME  — human-readable label

    Subclasses MAY override:
        is_available(host)            — default checks the underlying CLI exists
        AVAILABILITY_BINARIES         — tuple of binary names; first that
                                        resolves on PATH wins
        DEFAULT_TIMEOUT_SEC           — per-phase timeout in seconds
    """

    CATEGORY: ClassVar[SourceType]
    SCRIPT_DIR: ClassVar[str]
    DISPLAY_NAME: ClassVar[str]
    AVAILABILITY_BINARIES: ClassVar[tuple[str, ...]] = ()
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 1800

    SUPPORTED_PHASES: ClassVar[frozenset[Phase]] = frozenset(
        {Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP}
    )

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        repo_root: Path | None = None,
        bash_path: str | None = None,
        timeout_sec: int | None = None,
        elevation: object | None = None,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._repo_root = (
            Path(repo_root) if repo_root is not None
            else self._scripts_dir.parent
        )
        self._bash_override = bash_path
        self._timeout_sec = timeout_sec or self.DEFAULT_TIMEOUT_SEC
        # Optional LinuxElevation — when non-None and a sudo password is
        # registered, _build_env(phase=APPLY) extends the child env with
        # SUDO_ASKPASS + _ASCENDO_SUDO_PW so the legacy bash apply scripts
        # (which already honour SUDO_ASKPASS via lib/orchestrator.sh) can
        # authenticate non-interactively from the dashboard.
        self._elevation = elevation
        # Test seam: populated each run_phase call so tests can inspect
        # the env we would have passed to the child.
        self._last_env_for_test: dict[str, str] = {}

    # ── Identity ────────────────────────────────────────────────────────

    @property
    def category(self) -> SourceType:
        return self.CATEGORY

    @property
    def display_name(self) -> str:
        return self.DISPLAY_NAME

    # ── Availability (subclasses may override) ──────────────────────────

    def is_available(self, host: HostInfo) -> bool:
        """Default: True iff host is Linux AND any AVAILABILITY_BINARIES is on PATH.

        Subclasses with more nuanced checks should override.
        """
        # Linux family check (any linux_*).
        if not host.os.value.startswith("linux_"):
            return False
        if not self.AVAILABILITY_BINARIES:
            return True
        for binary in self.AVAILABILITY_BINARIES:
            if shutil.which(binary) is not None:
                return True
        return False

    # ── Phase execution ─────────────────────────────────────────────────

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        if phase not in self.SUPPORTED_PHASES:
            raise ManagerError(
                f"{type(self).__name__} does not support phase {phase.value!r}; "
                f"supported: {sorted(p.value for p in self.SUPPORTED_PHASES)}"
            )
        script_path = self._scripts_dir / self.SCRIPT_DIR / f"{phase.value}.sh"
        bash = self._resolve_bash()

        with tempfile.TemporaryDirectory(
            prefix=f"ascendo-ubuntu-{self.SCRIPT_DIR}-",
        ) as tmp:
            tmp_dir = Path(tmp)
            run_dir = tmp_dir / str(run.id)
            cat_dir = run_dir / self.SCRIPT_DIR
            cat_dir.mkdir(parents=True, exist_ok=True)

            sidecar_path = cat_dir / f"{phase.value}.json"
            log_path = cat_dir / f"{phase.value}.log"

            argv = [bash, str(script_path)]
            env = self._build_env(
                phase=phase,
                run=run,
                run_dir=run_dir,
                sidecar_path=sidecar_path,
                log_path=log_path,
            )
            self._last_env_for_test = dict(env)

            _log.debug(
                "%s.run_phase phase=%s run_id=%s argv=%r sidecar=%s",
                type(self).__name__, phase.value, run.id, argv, sidecar_path,
            )

            # Heartbeat: write a marker into ASCENDO_STREAM_LOG so the SPA
            # SSE stream shows "now running <cat> <phase>" even before the
            # bash script produces its own output. Without this, multi-
            # category apply runs look frozen between phases.
            stream_log = env.get("ASCENDO_STREAM_LOG")
            if stream_log:
                try:
                    with open(stream_log, "a", encoding="utf-8") as f:
                        f.write(f">>> {self.SCRIPT_DIR} {phase.value} (starting)\n")
                except OSError:
                    pass  # best-effort

            # Watchdog thread: if the bash subprocess produces no
            # _stream.log output for 10s, append a "still running …"
            # heartbeat. This keeps the SPA's SSE consumer rendering
            # live activity during silent stretches (e.g. brew network
            # download, npm install postinstall) so the user never
            # mistakes slow work for a hang.
            stop_heartbeat = threading.Event()

            def _heartbeat_loop() -> None:
                if not stream_log:
                    return
                start = time.time()
                last_size = 0
                try:
                    last_size = os.path.getsize(stream_log)
                except OSError:
                    pass
                while not stop_heartbeat.wait(10.0):
                    try:
                        cur_size = os.path.getsize(stream_log)
                    except OSError:
                        continue
                    if cur_size == last_size:
                        elapsed = int(time.time() - start)
                        try:
                            with open(stream_log, "a", encoding="utf-8") as f:
                                f.write(
                                    f">>> {self.SCRIPT_DIR} {phase.value} "
                                    f"still running ({elapsed}s elapsed)\n"
                                )
                            last_size = os.path.getsize(stream_log)
                        except OSError:
                            pass
                    else:
                        last_size = cur_size

            heartbeat = threading.Thread(target=_heartbeat_loop, daemon=True)
            heartbeat.start()

            try:
                completed = subprocess.run(  # noqa: S603 (argv list, not shell)
                    argv,
                    env=env,
                    # CRITICAL: stdin=DEVNULL so any read attempt gets
                    # immediate EOF instead of blocking forever. Without
                    # this, sudo / apt / dpkg / brew prompts hang the
                    # whole dashboard when launched from a non-TTY parent.
                    stdin=subprocess.DEVNULL,
                    # CRITICAL: start_new_session=True puts the bash
                    # child in its own process group. Without this, a
                    # Ctrl+C in the terminal running `ascendo dashboard`
                    # sends SIGINT to the entire foreground process
                    # group — including any in-flight apply scripts —
                    # killing them mid-execution before they can write
                    # their sidecar via the EXIT trap. With this set,
                    # the dashboard process catches SIGINT alone; child
                    # bash processes continue and finish cleanly. If
                    # you really want to kill an in-flight apply, send
                    # SIGTERM to the bash PID directly.
                    start_new_session=True,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_sec,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                stop_heartbeat.set()
                raise ManagerError(
                    f"{self.SCRIPT_DIR} {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                ) from exc
            except OSError as exc:
                stop_heartbeat.set()
                raise ManagerError(
                    f"failed to spawn bash for {self.SCRIPT_DIR} "
                    f"{phase.value}: {exc}"
                ) from exc
            finally:
                stop_heartbeat.set()

            if not sidecar_path.exists():
                raise ManagerError(self._missing_sidecar_error(
                    phase=phase, script_path=script_path,
                    sidecar_path=sidecar_path, completed=completed,
                ))
            try:
                sc = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                raise ManagerError(
                    f"{self.SCRIPT_DIR} {phase.value} script wrote unparseable "
                    f"sidecar at {sidecar_path}: {exc}"
                ) from exc

            # Legacy bash sidecars carry no run.id; the legacy_compat
            # translator synthesizes a uuid5 from (host, started_at).
            # Overwrite it with the orchestrator's real run.id so:
            #   (a) write_sidecar lands at base_dir/<orchestrator-run-id>/
            #       (where the runner's REPORT.md hook expects it).
            #   (b) update_history rows tie back to this run, not a synthetic id.
            if sc.run.id != run.id:
                sc = sc.model_copy(update={"run": sc.run.model_copy(update={"id": run.id})})

            if completed.returncode != 0:
                _log.warning(
                    "%s %s script exited %d but produced a valid sidecar; "
                    "trusting sidecar (status=%s)",
                    self.SCRIPT_DIR, phase.value, completed.returncode,
                    sc.status.value,
                )
            return sc

    # ── Internals ───────────────────────────────────────────────────────

    def _build_env(
        self,
        *,
        phase: Phase,
        run: RunInfo,
        run_dir: Path,
        sidecar_path: Path,
        log_path: Path,
    ) -> Mapping[str, str]:
        """Build the subprocess env for a phase script.

        Mirrors the variables exported by ``lib/orchestrator.sh`` plus
        passes through the parent process's PATH so the script can find
        ``apt-get``, ``snap``, etc.

        For ``Phase.APPLY`` only, when a ``LinuxElevation`` instance was
        injected at construction time AND a password has been registered
        (typically via the dashboard's ``/elevation/auth`` endpoint), the
        env is further extended with ``SUDO_ASKPASS`` and
        ``_ASCENDO_SUDO_PW`` so the legacy apply scripts can authenticate
        non-interactively. The static askpass helper at
        ``adapters/ubuntu/lib/askpass_helper.sh`` reads the password from
        ``_ASCENDO_SUDO_PW`` and prints it on stdout when sudo asks for it.
        """
        # Base env: full environment + orchestrator-contract variables.
        if self._elevation is not None and phase is Phase.APPLY:
            build_env = getattr(self._elevation, "build_subprocess_env", None)
            if callable(build_env):
                env: dict[str, str] = dict(build_env())
            else:
                env = dict(os.environ)
        else:
            env = dict(os.environ)
        env["JSON_OUT"] = str(sidecar_path)
        env["LOG_FILE"] = str(log_path)
        env["ORCH_RUN_ID"] = str(run.id)
        env["ORCH_RUN_DIR"] = str(run_dir)
        env["ORCH_DRY_RUN"] = "1" if run.dry_run else "0"
        env["ORCH_PROFILE"] = run.profile
        # Quiet mode hint: this isn't the legacy orchestrator driving the
        # script; we capture stdout/stderr directly.
        env.setdefault("ORCH_QUIET", "1")
        # Force every downstream tool (apt/dpkg/snap/brew/npm) into
        # non-interactive mode. Without these, an apt-listchanges prompt
        # or a needrestart "services to restart?" question would deadlock
        # the bridge subprocess (we close stdin so the child gets EOF
        # immediately, but some tools still wait for tty input). These
        # env vars are the standard escape hatches each tool documents.
        env.setdefault("DEBIAN_FRONTEND", "noninteractive")
        env.setdefault("APT_LISTCHANGES_FRONTEND", "none")
        env.setdefault("NEEDRESTART_MODE", "a")          # auto-restart services
        env.setdefault("NEEDRESTART_SUSPEND", "1")       # never prompt
        env.setdefault("HOMEBREW_NO_AUTO_UPDATE", "1")
        env.setdefault("HOMEBREW_NO_ENV_HINTS", "1")
        env.setdefault("HOMEBREW_NO_INSTALL_CLEANUP", "1")
        env.setdefault("ASCENDO_NONINTERACTIVE", "1")
        return env

    def _resolve_bash(self) -> str:
        if self._bash_override is not None:
            return self._bash_override
        for cand in ("bash", "/bin/bash"):
            if cand.startswith("/"):
                if Path(cand).is_file():
                    return cand
            else:
                found = shutil.which(cand)
                if found:
                    return found
        raise ManagerError("no bash on PATH and /bin/bash missing")

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
            f"{self.SCRIPT_DIR} {phase.value} script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stdout (tail): {_tail(completed.stdout)}\n"
            f"  stderr (tail): {_tail(completed.stderr)}"
        )
