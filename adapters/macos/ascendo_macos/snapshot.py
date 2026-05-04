"""TimeMachineSnapshot — ISnapshot implementation for macOS.

Spawns ``adapters/macos/scripts/snapshot/list.sh``, which calls
``tmutil listlocalsnapshots /`` and emits an ``ascendo/v1`` sidecar with one
``success`` item per APFS local snapshot. Python re-reads via
:func:`read_sidecar` and converts ``items[]`` into :class:`SnapshotInfo`
instances.

**Create is intentionally read-only.** APFS local snapshots on macOS are
managed automatically by the OS (Time Machine daemon, APFS COW engine) and
are not user-creatable on demand via a public stable API. ``create()``
raises :class:`SnapshotError` immediately with an explanatory message.

The Python <-> Bash IPC contract mirrors :class:`MacOSInventory`:
the script writes its sidecar to
``<OutputDir>/<RunId>/check__snapshot.json`` and exits.
Python re-reads via :func:`read_sidecar`. Crashes that produce no sidecar
surface as :class:`SnapshotError`.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from ascendo.interfaces.snapshot import ISnapshot, SnapshotError, SnapshotInfo
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.run import Trigger
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)

_log = logging.getLogger(__name__)

# Regex to extract timestamp from APFS local snapshot names.
# Example: com.apple.TimeMachine.2026-05-03-140425.local
_SNAP_RE = re.compile(
    r"com\.apple\.TimeMachine\."
    r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})"
    r"\.local$"
)


class TimeMachineSnapshot(ISnapshot):
    """Lists APFS local Time Machine snapshots by shelling out to
    ``snapshot/list.sh``.

    Args:
        scripts_dir: Path to ``adapters/macos/scripts/`` (the script lives at
            ``<scripts_dir>/snapshot/list.sh``).
        lib_dir: Path to ``adapters/macos/lib/`` (informational; the script
            imports helper files relative to its own directory).
        bash_path: Optional override for the bash executable. When ``None``,
            resolved at run time (``/bin/bash`` then ``bash``).
        timeout_sec: Per-call timeout in seconds. Default ``60`` (listing is
            fast — a simple ``tmutil listlocalsnapshots /`` call).
    """

    SCRIPT_REL: ClassVar[str] = "snapshot/list.sh"
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 60

    def __init__(
        self,
        scripts_dir: Path,
        lib_dir: Path,
        *,
        bash_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir: Path = Path(scripts_dir)
        self._lib_dir: Path = Path(lib_dir)
        self._bash_override: str | None = bash_path
        self._bash_resolved: str | None = None
        self._timeout_sec: int = timeout_sec

    # ── ISnapshot ────────────────────────────────────────────────────

    @property
    def backend(self) -> str:
        """Backend slug for Time Machine / APFS local snapshots."""
        return "time_machine"

    def is_available(self, host: HostInfo) -> bool:
        """True if on macOS and ``tmutil`` is on the PATH.

        ``tmutil`` ships with every macOS installation; missing = unusual
        (sandboxed / managed environment). Returns False immediately on
        non-macOS hosts without checking for the binary.
        """
        if host.os is not OperatingSystem.MACOS:
            return False
        return shutil.which("tmutil") is not None

    def create(
        self,
        host: HostInfo,
        *,
        label: str,
        notes: str | None = None,
    ) -> SnapshotInfo:
        """Not supported — macOS local snapshots are auto-managed by APFS.

        APFS creates and prunes local snapshots automatically. There is no
        stable public API for on-demand creation. To configure Time Machine
        destinations open **System Settings > General > Time Machine**.

        Raises:
            SnapshotError: Always raised with an explanatory message.
        """
        raise SnapshotError(
            "macOS local snapshots are auto-managed by APFS. "
            "To configure backups: System Settings > General > Time Machine. "
            "Ascendo cannot create local snapshots on demand."
        )

    def list(self, host: HostInfo) -> list[SnapshotInfo]:
        """List APFS local snapshots on this host via ``tmutil listlocalsnapshots /``.

        Returns an empty list on non-macOS hosts (no error).

        Args:
            host: Host context. Non-macOS hosts return ``[]``.

        Returns:
            List of :class:`SnapshotInfo` sorted newest-first (by
            ``created_at``). Returns ``[]`` on non-macOS hosts.

        Raises:
            SnapshotError: bash unavailable, script crashed without emitting
                a sidecar, sidecar missing on clean exit, or parse failure.
        """
        if host.os is not OperatingSystem.MACOS:
            return []

        script_path = self._scripts_dir / self.SCRIPT_REL
        bash = self._resolve_bash()
        invocation_run_id = uuid4()

        with tempfile.TemporaryDirectory(prefix="ascendo-snapshot-") as tmp_str:
            output_dir = Path(tmp_str)

            argv = self._build_argv(
                bash=bash,
                script_path=script_path,
                run_id=invocation_run_id,
                output_dir=output_dir,
            )

            _log.debug(
                "TimeMachineSnapshot.list: run_id=%s argv=%r",
                invocation_run_id,
                argv,
            )

            try:
                completed = subprocess.run(  # noqa: S603
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_sec,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                msg = (
                    f"snapshot list script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                )
                raise SnapshotError(msg) from exc
            except OSError as exc:
                msg = f"failed to spawn bash for snapshot list: {exc}"
                raise SnapshotError(msg) from exc

            sidecar_path = (
                output_dir
                / str(invocation_run_id)
                / "check__snapshot.json"
            )

            if not sidecar_path.exists():
                msg = self._format_missing_sidecar_error(
                    script_path=script_path,
                    sidecar_path=sidecar_path,
                    completed=completed,
                )
                raise SnapshotError(msg)

            try:
                sidecar = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                msg = (
                    f"snapshot list script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                )
                raise SnapshotError(msg) from exc

            if completed.returncode != 0:
                _log.warning(
                    "snapshot list script exited %d but produced a valid "
                    "sidecar; trusting the sidecar (status=%s)",
                    completed.returncode,
                    sidecar.status.value,
                )

            return self._sidecar_to_snapshots(sidecar)

    def get(self, host: HostInfo, snapshot_id: str) -> SnapshotInfo | None:
        """Return metadata for a specific snapshot, or ``None`` if not found.

        Args:
            host: Host context (passed through to ``list()``).
            snapshot_id: The APFS snapshot name to look up (e.g.
                ``com.apple.TimeMachine.2026-05-03-140425.local``).

        Returns:
            :class:`SnapshotInfo` if found, ``None`` otherwise.
        """
        snapshots = self.list(host)
        for info in snapshots:
            if info.id == snapshot_id:
                return info
        return None

    # ── Internals ────────────────────────────────────────────────────

    def _build_argv(
        self,
        *,
        bash: str,
        script_path: Path,
        run_id: object,
        output_dir: Path,
    ) -> list[str]:
        """Build the bash argv for ``snapshot/list.sh``.

        Mirrors :meth:`MacOSInventory._build_argv`. Snapshot listing is
        read-only, so ``--dry-run`` is not emitted (accepted by the script
        for build_argv parity but has no effect).
        """
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

    def _sidecar_to_snapshots(self, sidecar: object) -> list[SnapshotInfo]:
        """Convert sidecar ``items[]`` into :class:`SnapshotInfo` instances.

        Items whose id does not match the APFS snapshot name pattern are
        silently skipped (synthetic error sentinels, header artefacts, etc.).
        The result is sorted newest-first by ``created_at``.
        """
        results: list[SnapshotInfo] = []
        for item in sidecar.items:
            m = _SNAP_RE.match(item.id)
            if m is None:
                _log.debug(
                    "TimeMachineSnapshot: skipping unrecognised item id %r",
                    item.id,
                )
                continue
            year, month, day, hour, minute, second = (int(g) for g in m.groups())
            created_at = datetime(
                year, month, day, hour, minute, second, tzinfo=timezone.utc
            )
            results.append(
                SnapshotInfo(
                    id=item.id,
                    created_at=created_at,
                    backend=self.backend,
                    label=None,
                    size_bytes=None,
                    notes=None,
                )
            )
        # Newest first
        results.sort(key=lambda s: s.created_at, reverse=True)
        return results

    def _format_missing_sidecar_error(
        self,
        *,
        script_path: Path,
        sidecar_path: Path,
        completed: subprocess.CompletedProcess[str],
    ) -> str:
        """Compose a debugging-friendly error for the missing-sidecar case."""

        def _tail(s: str | None, limit: int = 800) -> str:
            if not s:
                return "<empty>"
            if len(s) <= limit:
                return s
            return f"...<truncated {len(s) - limit} chars>...{s[-limit:]}"

        return (
            f"snapshot list script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stderr (tail): {_tail(completed.stderr)}\n"
            f"  stdout (tail): {_tail(completed.stdout)}"
        )

    def _resolve_bash(self) -> str:
        """Find ``/bin/bash`` (preferred, macOS default) or ``bash`` on PATH.

        Caches result for the lifetime of this instance. Mirrors
        :meth:`MacOSInventory._resolve_bash`.
        """
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
        raise SnapshotError(msg)
