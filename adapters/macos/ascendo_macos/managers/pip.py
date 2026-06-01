"""PipManager - IPackageManager for pip / Python global CLIs.

Mirrors :class:`NpmManager` shape — bash phase scripts under
``adapters/macos/scripts/pip/``, JSON-IPC over a temp dir.

Scope on macOS:
  * Power-user Python tooling installed globally (uv, ruff, black, mypy,
    pytest, poetry, virtualenv, pipx, httpx, isort, ...).
  * Package list is in ``adapters/macos/config/pip_global_clis.txt``.

Why this manager and not Homebrew? Several Python CLIs ship more
frequent releases on PyPI than on brew (e.g. ``ruff``, ``uv``); a
direct ``pip install -U`` honours those upstream cadences without
waiting on a brew formula bump. Users who prefer brew can simply leave
the manifest empty.

pip on macOS NEVER invokes sudo. The bash apply targets the user site
or whichever prefix the active pip itself owns (brew Python's pip
honours its own prefix without --user; system Python's pip writes to
~/Library/Python/X.Y/bin via --user). Apple-supplied /usr/bin/python3
is intentionally excluded from the resolution chain in
``ascendo_pip.sh``.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.package_manager import IPackageManager, ManagerError
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo
from ascendo.orchestrator.stream_log import child_env_with_stream_log
from ascendo.models.sidecar import Sidecar
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)

_log = logging.getLogger(__name__)


class PipManager(IPackageManager):
    """pip / Python global-CLI per-source manager.

    Args:
        scripts_dir: Path to ``adapters/macos/scripts/``.
        lib_dir:     Path to ``adapters/macos/lib/`` (informational only).
        bash_path:   Optional override for bash binary.
        timeout_sec: Per-phase timeout. Default 1800 (30 min) — pip
                     install can be slow when wheels need to compile.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "pip/check.sh",
        Phase.PLAN: "pip/plan.sh",
        Phase.APPLY: "pip/apply.sh",
        Phase.VERIFY: "pip/verify.sh",
        Phase.CLEANUP: "pip/cleanup.sh",
    }
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 1800

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        bash_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._bash_override = bash_path
        self._timeout_sec = timeout_sec

    # -- Identity --------------------------------------------------------

    @property
    def category(self) -> SourceType:
        return SourceType.PIP

    @property
    def display_name(self) -> str:
        return "Python global packages (pip + pipx)"

    # -- Availability ----------------------------------------------------

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.MACOS:
            return False
        # The phase scripts gracefully degrade when pip is missing
        # (every manifest item reported as ``missing``); a bare bash + jq
        # are the only hard requirements for the read-only phases. For
        # apply, the script reports a per-package error if pip is gone.
        if shutil.which("bash") is None and not Path("/bin/bash").is_file():
            return False
        # Probe for a usable pip via the bash helper. Any non-empty
        # resolution is sufficient — actual install happens during apply
        # (where we re-resolve to pick up freshly installed pip).
        bash = self._resolve_bash_or_none()
        if bash is None:
            return False
        try:
            res = subprocess.run(  # noqa: S603 (argv list)
                [
                    bash,
                    "-c",
                    f". {self._lib_dir / 'ascendo_pip.sh'} && ascendo_pip_pip_bin",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env={"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return bool((res.stdout or "").strip())

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
                f"PipManager does not support phase {phase.value!r}; "
                f"supported: {sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
        script_path = self._scripts_dir / script_rel
        bash = self._resolve_bash()

        with tempfile.TemporaryDirectory(prefix="ascendo-pip-") as tmp:
            output_dir = Path(tmp)
            argv = self._build_argv(
                bash=bash,
                script_path=script_path,
                run=run,
                output_dir=output_dir,
                item_filter=item_filter,
            )
            log_path = output_dir / str(run.id) / f"{phase.value}__pip.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            _log.debug("PipManager.run_phase phase=%s run_id=%s argv=%r",
                       phase.value, run.id, argv)

            try:
                completed = self._run_streaming(argv, log_path, self._timeout_sec)
            except subprocess.TimeoutExpired as exc:
                raise ManagerError(
                    f"pip {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                ) from exc
            except OSError as exc:
                raise ManagerError(
                    f"failed to spawn bash for pip {phase.value}: {exc}"
                ) from exc

            sidecar_path = output_dir / str(run.id) / f"{phase.value}__pip.json"
            if not sidecar_path.exists():
                raise ManagerError(
                    f"pip {phase.value} script produced no sidecar at "
                    f"{sidecar_path}; exit={completed.returncode}; "
                    f"tail={completed.stdout[-400:] if completed.stdout else '<empty>'}"
                )
            try:
                sc = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                raise ManagerError(
                    f"pip {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                ) from exc

            if completed.returncode != 0:
                _log.warning(
                    "pip %s script exited %d but produced a valid sidecar; "
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
                argv.extend(["--filter", ",".join(cleaned)])
        return argv

    def _run_streaming(
        self,
        argv: list[str],
        log_path: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.Popen(  # noqa: S603 (argv list)
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            # Per-run stream-log path from this thread's run (race-free).
            env=child_env_with_stream_log(),
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

    def _resolve_bash(self) -> str:
        if self._bash_override is not None:
            return self._bash_override
        if Path("/bin/bash").is_file():
            return "/bin/bash"
        found = shutil.which("bash")
        if found is not None:
            return found
        raise ManagerError("no bash on PATH and /bin/bash missing")

    def _resolve_bash_or_none(self) -> str | None:
        try:
            return self._resolve_bash()
        except ManagerError:
            return None
