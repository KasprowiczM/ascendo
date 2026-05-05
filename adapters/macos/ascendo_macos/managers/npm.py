"""NpmManager - IPackageManager for npm globals + Node + Bun.

Mirrors :class:`BrewManager` shape — bash phase scripts under
``adapters/macos/scripts/npm/``, JSON-IPC over a temp dir.

Scope on macOS:
  * ``node`` itself (managed via ``n`` under ``$TOOLCHAIN_HOME/node``).
  * ``npm`` and ``pnpm`` (npm-installed-globally).
  * ``bun`` (native installer under ``$BUN_INSTALL``).
  * Anthropic / OpenAI / Google CLI tools shipped via npm
    (claude-code, codex-cli, gemini-cli, qwen-code, opencode-cli) —
    package list is in ``adapters/macos/config/npm_global_clis.txt``.

Why this manager and not Homebrew? The legacy macOS app
``Aktualizacje_MAC/update_npm_cli.sh`` migrated these CLIs OFF Homebrew
to native (``n`` for node, native installer for bun, ``npm install -g``
for the rest) so they update independently of brew's slower release
cadence and don't drag in libraries the user doesn't want.
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
from ascendo.models.sidecar import Sidecar
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)

_log = logging.getLogger(__name__)


class NpmManager(IPackageManager):
    """npm/Node/Bun per-source manager.

    Args:
        scripts_dir: Path to ``adapters/macos/scripts/``.
        lib_dir:     Path to ``adapters/macos/lib/`` (informational only).
        bash_path:   Optional override for bash binary.
        timeout_sec: Per-phase timeout. Default 1800 (30 min) — npm install
                     of large CLIs (Claude Code, Codex) can be slow.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "npm/check.sh",
        Phase.PLAN: "npm/plan.sh",
        Phase.APPLY: "npm/apply.sh",
        Phase.VERIFY: "npm/verify.sh",
        Phase.CLEANUP: "npm/cleanup.sh",
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
        return SourceType.NPM

    @property
    def display_name(self) -> str:
        return "Node toolchain (npm + bun + global CLIs)"

    # -- Availability ----------------------------------------------------

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.MACOS:
            return False
        # The phase scripts gracefully degrade when individual tools are
        # missing (e.g. bun not installed yet); a bare bash + jq are the
        # only hard requirements. Node/npm/bun are bootstrapped during
        # apply.
        if shutil.which("bash") is None and not Path("/bin/bash").is_file():
            return False
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
                f"NpmManager does not support phase {phase.value!r}; "
                f"supported: {sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
        script_path = self._scripts_dir / script_rel
        bash = self._resolve_bash()

        with tempfile.TemporaryDirectory(prefix="ascendo-npm-") as tmp:
            output_dir = Path(tmp)
            argv = self._build_argv(
                bash=bash,
                script_path=script_path,
                run=run,
                output_dir=output_dir,
                item_filter=item_filter,
            )
            log_path = output_dir / str(run.id) / f"{phase.value}__npm.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            _log.debug("NpmManager.run_phase phase=%s run_id=%s argv=%r",
                       phase.value, run.id, argv)

            try:
                completed = self._run_streaming(argv, log_path, self._timeout_sec)
            except subprocess.TimeoutExpired as exc:
                raise ManagerError(
                    f"npm {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                ) from exc
            except OSError as exc:
                raise ManagerError(
                    f"failed to spawn bash for npm {phase.value}: {exc}"
                ) from exc

            sidecar_path = output_dir / str(run.id) / f"{phase.value}__npm.json"
            if not sidecar_path.exists():
                raise ManagerError(
                    f"npm {phase.value} script produced no sidecar at "
                    f"{sidecar_path}; exit={completed.returncode}; "
                    f"tail={completed.stdout[-400:] if completed.stdout else '<empty>'}"
                )
            try:
                sc = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                raise ManagerError(
                    f"npm {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                ) from exc

            if completed.returncode != 0:
                _log.warning(
                    "npm %s script exited %d but produced a valid sidecar; "
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
