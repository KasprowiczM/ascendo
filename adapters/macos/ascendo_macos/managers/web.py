"""WebManager - sixth IPackageManager on macOS, covers every web-orphan app.

Mirrors :class:`NpmManager` shape: bash phase scripts under
``adapters/macos/scripts/web/``, JSON-IPC via temp dir, JSON-v1 sidecar
contract unchanged.

Scope on macOS (M5.7 v0.4.0+):
  * Every installed ``/Applications/*.app`` not owned by brew / mas /
    softwareupdate. Discovery layer (``adapters/macos/lib/web_discovery.sh``)
    walks Info.plist fingerprints + ownership exclusions; ~50 apps on
    a typical Mac.
  * 8 update mechanisms via two tiers:

    Tier-A (real candidate-version probe):
      sparkle (appcast XML + DMG), github_dmg (GH Releases API +
      arm64 asset), release_feed (NEW — generic JSON-over-HTTPS probe),
      msupdate (Microsoft AutoUpdate), docker (Docker Desktop's CLI).

    Tier-B (trigger-only with honest async semantics):
      keystone (Google Software Update agent), squirrel (Squirrel.Mac
      auto-on-relaunch), builtin (open + emit manual-update instruction).
      Apply emits ItemStatus.TRIGGERED; verify reports
      triggered_pending / triggered_confirmed via informational messages.

  * registry shipped at ``adapters/macos/config/web_apps.toml`` (schema
    v2, bundle_id-keyed); user override at ``~/.config/ascendo/web_apps.toml``
    (merge by bundle_id, user replaces shipped wholesale).
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

from ascendo_macos.managers.elevation import MacElevation

_log = logging.getLogger(__name__)


class WebManager(IPackageManager):
    """Web app updater for macOS.

    Args:
        scripts_dir: Path to ``adapters/macos/scripts/``.
        lib_dir:     Path to ``adapters/macos/lib/``.
        bash_path:   Optional bash override.
        timeout_sec: Per-phase timeout. Apply DMG downloads can be slow.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "web/check.sh",
        Phase.PLAN: "web/plan.sh",
        Phase.APPLY: "web/apply.sh",
        Phase.VERIFY: "web/verify.sh",
        Phase.CLEANUP: "web/cleanup.sh",
    }
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 1800

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        elevation: MacElevation | None = None,
        bash_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._elevation = elevation
        self._bash_override = bash_path
        self._timeout_sec = timeout_sec

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def category(self) -> SourceType:
        return SourceType.WEB

    @property
    def display_name(self) -> str:
        return "Web apps (Sparkle / GitHub / Keystone / Squirrel / msupdate / Docker)"

    # ── Availability ──────────────────────────────────────────────────────

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.MACOS:
            return False
        if shutil.which("bash") is None and not Path("/bin/bash").is_file():
            return False
        # Registry must parse + handler scripts must exist.
        try:
            from ascendo_macos.web_registry import WebRegistry
            shipped = self._scripts_dir.parent / "config" / "web_apps.toml"
            if not shipped.is_file():
                return False
            user = Path("~/.config/ascendo/web_apps.toml").expanduser()
            user_arg = user if user.exists() else None
            WebRegistry.load(shipped, user_arg)
        except Exception:   # noqa: BLE001 — health_check surfaces details
            return False
        for h in ("sparkle", "github_dmg", "keystone", "squirrel",
                  "builtin", "msupdate", "docker"):
            if not (self._lib_dir / "handlers" / f"{h}.sh").is_file():
                return False
        return True

    # ── Phase execution ───────────────────────────────────────────────────

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
                f"WebManager does not support phase {phase.value!r}; "
                f"supported: {sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
        script_path = self._scripts_dir / script_rel
        bash = self._resolve_bash()

        with tempfile.TemporaryDirectory(prefix="ascendo-web-") as tmp:
            output_dir = Path(tmp)
            argv = self._build_argv(
                bash=bash, script_path=script_path, run=run,
                output_dir=output_dir, item_filter=item_filter,
            )
            log_path = output_dir / str(run.id) / f"{phase.value}__web.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            _log.debug("WebManager.run_phase phase=%s run_id=%s argv=%r",
                       phase.value, run.id, argv)

            env = self._build_env(phase)
            try:
                completed = self._run_streaming(argv, log_path, self._timeout_sec, env=env)
            except subprocess.TimeoutExpired as exc:
                raise ManagerError(
                    f"web {phase.value} script timed out after "
                    f"{self._timeout_sec}s"
                ) from exc
            except OSError as exc:
                raise ManagerError(
                    f"failed to spawn bash for web {phase.value}: {exc}"
                ) from exc

            sidecar_path = output_dir / str(run.id) / f"{phase.value}__web.json"
            if not sidecar_path.exists():
                raise ManagerError(
                    f"web {phase.value} script produced no sidecar at "
                    f"{sidecar_path}; exit={completed.returncode}; "
                    f"tail={completed.stdout[-400:] if completed.stdout else '<empty>'}"
                )
            try:
                sc = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                raise ManagerError(
                    f"web {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                ) from exc

            if completed.returncode != 0:
                _log.warning(
                    "web %s script exited %d but produced a valid sidecar; "
                    "trusting sidecar (status=%s)",
                    phase.value, completed.returncode, sc.status.value,
                )
            return sc

    # ── Internals ─────────────────────────────────────────────────────────

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
            bash, str(script_path),
            "--run-id", str(run.id),
            "--trigger", run.trigger.value,
            "--profile", run.profile,
            "--output-dir", str(output_dir),
        ]
        if run.dry_run:
            argv.append("--dry-run")
        if item_filter is not None:
            cleaned = [s.strip() for s in item_filter
                       if s and isinstance(s, str) and s.strip()]
            if cleaned:
                argv.extend(["--filter", ",".join(cleaned)])
        return argv

    def _build_env(self, phase: Phase) -> dict[str, str]:
        """Build the env passed to the bash phase script.

        For Phase.APPLY when an elevation cache is present, inject
        SUDO_ASKPASS so apply.sh's `sudo -A …` (msupdate, /Applications
        cp fallback) can authenticate non-interactively from the cached
        password the user typed once into the SPA modal. Without this
        the per-phase `_ascendo_sudo_warm` falls through to the TTY-PAM
        path and fires a Touch ID prompt for every web run — exactly
        the duplicate-prompt UX the operator reported on Mac.r12.home.
        """
        # os.environ + the per-run stream-log path for THIS worker thread.
        env = child_env_with_stream_log()
        if (
            phase is Phase.APPLY
            and self._elevation is not None
            and self._elevation.has_password_registered()
        ):
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
        env: dict[str, str] | None = None,
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

    def _resolve_bash(self) -> str:
        if self._bash_override is not None:
            return self._bash_override
        if Path("/bin/bash").is_file():
            return "/bin/bash"
        found = shutil.which("bash")
        if found is not None:
            return found
        raise ManagerError("no bash on PATH and /bin/bash missing")
