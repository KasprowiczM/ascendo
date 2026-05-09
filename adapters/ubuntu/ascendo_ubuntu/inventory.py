"""UbuntuInventory - IInventory implementation for Ubuntu / Linux.

Spawns ``adapters/ubuntu/scripts/inventory/list.sh``, which enumerates
installed packages across six sources (apt, snap, flatpak, brew, npm
globals, pip globals) and emits a single ``ascendo/v1`` sidecar at
``<output-dir>/<run-id>/check__inventory.json``. This module reads
that sidecar back via :func:`read_sidecar` and converts ``items[]``
into :class:`Package` instances.

Design mirrors :class:`MacOSInventory`:

* ``list_installed`` always invokes a fresh inventory (private tempdir
  + uuid4 run-id) so it never collides with phase-pipeline sidecars.
* On non-Linux hosts, returns ``[]`` (the IInventory contract treats a
  missing source as zero items, not an error).
* Crashes / missing sidecar / parse failures all surface as
  :class:`ManagerError`.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from ascendo.interfaces.inventory import IInventory
from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import ItemSource, Package, SourceType
from ascendo.models.result import Item, ItemStatus, MessageLevel, Message, Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo, Trigger
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)

_log = logging.getLogger(__name__)

# Linux family enums for the host-availability gate.
_LINUX_OS = {
    OperatingSystem.LINUX_UBUNTU,
    OperatingSystem.LINUX_DEBIAN,
    OperatingSystem.LINUX_FEDORA,
    OperatingSystem.LINUX_ARCH,
    OperatingSystem.LINUX_OTHER,
}


class UbuntuInventory(IInventory):
    """List installed packages on Ubuntu / Linux by shelling out to ``list.sh``.

    Args:
        scripts_dir: Path to the directory containing ``inventory/list.sh``.
            Conventionally ``adapters/ubuntu/scripts/`` — the adapter passes
            this in.
        lib_dir: Path to ``adapters/ubuntu/lib/`` (informational; the
            script does not rely on it).
        bash_path: Optional override for the bash executable. When ``None``,
            resolves at run time (``/bin/bash`` then ``bash`` on PATH).
        timeout_sec: Per-call timeout for the bash invocation. Default
            ``300`` (5 min) — apt is the slow source on machines with
            thousands of packages.
    """

    SCRIPT_REL: ClassVar[str] = "inventory/list.sh"
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 300

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        bash_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir: Path = Path(scripts_dir)
        self._lib_dir: Path = Path(lib_dir)
        self._bash_override: str | None = bash_path
        self._bash_resolved: str | None = None
        self._timeout_sec: int = timeout_sec

    @property
    def scripts_dir(self) -> Path:
        """Path to the scripts directory (for test introspection)."""
        return self._scripts_dir

    # ── IInventory ───────────────────────────────────────────────────

    def list_installed(
        self,
        host: HostInfo,
        *,
        categories: Iterable[str] | None = None,
    ) -> list[Package]:
        """Enumerate installed packages on the host.

        On non-Linux hosts returns an empty list (no error). On Linux,
        spawns ``list.sh``, reads back its sidecar, and converts items
        to :class:`Package` instances.

        Args:
            host: Host context. Non-Linux hosts return ``[]``.
            categories: Optional iterable of :class:`SourceType` values
                (or their string equivalents) used to filter the result.

        Returns:
            List of :class:`Package`. Order matches the sidecar's items[].

        Raises:
            ManagerError: bash unavailable, script crashed without emitting
                a sidecar, sidecar missing on clean exit, or sidecar
                parse failure.
        """
        if host.os not in _LINUX_OS:
            return []

        script_path = self._scripts_dir / self.SCRIPT_REL
        bash = self._resolve_bash()
        invocation_run_id = uuid4()

        with tempfile.TemporaryDirectory(prefix="ascendo-ubuntu-inventory-") as tmp_str:
            output_dir = Path(tmp_str)
            argv = self._build_argv(
                bash=bash,
                script_path=script_path,
                run_id=invocation_run_id,
                output_dir=output_dir,
            )
            _log.debug(
                "UbuntuInventory.list_installed: run_id=%s argv=%r",
                invocation_run_id,
                argv,
            )
            try:
                completed = subprocess.run(  # noqa: S603 (argv list, not shell)
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_sec,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                msg = (
                    f"inventory list script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                )
                raise ManagerError(msg) from exc
            except OSError as exc:
                msg = f"failed to spawn bash for inventory list: {exc}"
                raise ManagerError(msg) from exc

            sidecar_path = (
                output_dir / str(invocation_run_id) / "check__inventory.json"
            )

            if not sidecar_path.exists():
                msg = self._format_missing_sidecar_error(
                    script_path=script_path,
                    sidecar_path=sidecar_path,
                    completed=completed,
                )
                raise ManagerError(msg)

            try:
                sidecar = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                msg = (
                    f"inventory list script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                )
                raise ManagerError(msg) from exc

            if completed.returncode != 0:
                _log.warning(
                    "inventory list script exited %d but produced a valid "
                    "sidecar; trusting the sidecar (status=%s)",
                    completed.returncode,
                    sidecar.status.value,
                )

            return self._sidecar_to_packages(sidecar, categories=categories)

    def emit_sidecar(
        self,
        run: RunInfo,
        host: HostInfo,
        packages: list[Package],
    ) -> Sidecar:
        """Synthesise an inventory sidecar from a pre-built list of
        :class:`Package`. Used when the orchestrator wants to persist an
        inventory snapshot inside a bigger run.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        items: list[Item] = []
        for pkg in packages:
            source = pkg.source if pkg.source is not None else ItemSource(
                type=pkg.category, feed=None
            )
            items.append(
                Item(
                    id=pkg.id,
                    name=pkg.name,
                    category=pkg.category,
                    source=source,
                    current_version=pkg.installed_version,
                    target_version=pkg.installed_version,
                    status=ItemStatus.UP_TO_DATE,
                )
            )
        summary = Summary(
            total=len(items),
            success=0,
            up_to_date=len(items),
            failed=0,
            skipped=0,
            planned=0,
            partial=0,
            duration_ms=None,
            exit_code=0,
        )
        tool = ToolInfo(name="ubuntu-inventory", version="1.0", binary_path=None)
        return Sidecar.model_validate(
            {
                "schema": SidecarSchema.V1_ASCENDO,
                "run": run,
                "host": host,
                "tool": tool,
                "phase": Phase.CHECK,
                "category": SourceType.INVENTORY,
                "started_at": now,
                "finished_at": now,
                "status": PhaseStatus.SUCCESS,
                "items": items,
                "summary": summary,
                "messages": [
                    Message(
                        level=MessageLevel.INFO,
                        text=(
                            "Generated by UbuntuInventory.emit_sidecar "
                            "(in-memory, not a phase pipeline)."
                        ),
                    ),
                ],
                "needs_reboot": False,
            }
        )

    # ── Internals ────────────────────────────────────────────────────

    def _build_argv(
        self,
        *,
        bash: str,
        script_path: Path,
        run_id: object,
        output_dir: Path,
    ) -> list[str]:
        """Build bash argv for ``inventory/list.sh``."""
        return [
            bash,
            str(script_path),
            "--run-id",
            str(run_id),
            "--trigger",
            Trigger.PLUGIN.value,
            "--profile",
            "default",
            "--output-dir",
            str(output_dir),
        ]

    def _sidecar_to_packages(
        self,
        sidecar: Sidecar,
        *,
        categories: Iterable[str] | None,
    ) -> list[Package]:
        """Convert a sidecar's ``items[]`` into :class:`Package` instances.

        The categories filter (if given) is matched against
        ``item.source.type.value``. Unknown category strings are logged
        at WARNING and skipped (silent typos otherwise yield zero results).
        """
        cat_filter: set[str] | None = None
        if categories is not None:
            cat_filter = set()
            for cat in categories:
                if isinstance(cat, SourceType):
                    cat_filter.add(cat.value)
                else:
                    try:
                        cat_filter.add(SourceType(cat).value)
                    except ValueError:
                        _log.warning(
                            "UbuntuInventory: ignoring unknown category %r "
                            "(valid: %s)",
                            cat,
                            ", ".join(s.value for s in SourceType),
                        )

        packages: list[Package] = []
        for item in sidecar.items:
            if item.id.startswith("__") and item.id.endswith("__"):
                # Diagnostic synthetic items emitted by the script's catch
                # block (e.g. __phase_error__).
                continue
            if cat_filter is not None and item.source.type.value not in cat_filter:
                continue
            packages.append(
                Package(
                    id=item.id,
                    name=item.name if item.name else item.id,
                    category=item.source.type,
                    source=item.source,
                    installed_version=item.current_version,
                    available_version=item.target_version,
                )
            )
        return packages

    def _format_missing_sidecar_error(
        self,
        *,
        script_path: Path,
        sidecar_path: Path,
        completed: subprocess.CompletedProcess[str],
    ) -> str:
        def _tail(s: str | None, limit: int = 800) -> str:
            if not s:
                return "<empty>"
            if len(s) <= limit:
                return s
            return f"...<truncated {len(s) - limit} chars>...{s[-limit:]}"

        return (
            f"inventory list script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stderr (tail): {_tail(completed.stderr)}\n"
            f"  stdout (tail): {_tail(completed.stdout)}"
        )

    def _resolve_bash(self) -> str:
        """Resolve a bash binary; cache for instance lifetime."""
        if self._bash_resolved is not None:
            return self._bash_resolved
        if self._bash_override is not None:
            self._bash_resolved = self._bash_override
            return self._bash_resolved
        if Path("/bin/bash").is_file():
            self._bash_resolved = "/bin/bash"
            return self._bash_resolved
        found = shutil.which("bash")
        if found is not None:
            self._bash_resolved = found
            return found
        msg = (
            "no bash binary found: /bin/bash missing and 'bash' not on PATH. "
            "Pass bash_path= explicitly."
        )
        raise ManagerError(msg)
