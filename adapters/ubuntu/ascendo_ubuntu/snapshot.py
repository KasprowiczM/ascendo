"""TimeshiftSnapshot — ISnapshot implementation for Ubuntu / Debian.

Wraps the ``timeshift`` CLI to expose system snapshot create + list + get
through the shared :class:`ISnapshot` contract. ``restore`` is intentionally
NOT exposed — it's a destructive operation that should only be performed
from a recovery shell via ``timeshift --restore`` directly.

Sudo handling:
    Both ``--create`` and ``--list`` typically require root. The
    implementation invokes ``sudo -A`` so a configured ``SUDO_ASKPASS``
    helper (the dashboard sets one) can supply credentials non-
    interactively. If sudo isn't configured / cached, calls fail with
    :class:`SnapshotError` carrying a clear remediation message.

Timeshift output format (parsed by :meth:`TimeshiftSnapshot.list`)::

    Num     Name                 Tags  Description
    ------------------------------------------------
    0    >  2026-05-09_15-30-22  O     Ascendo pre-apply
    1    >  2026-05-08_22-11-14  D     Daily snapshot

Snapshot IDs are the ``Name`` column (a timestamp like
``2026-05-09_15-30-22``) — used as a stable opaque identifier.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone

from ascendo.interfaces.snapshot import ISnapshot, SnapshotError, SnapshotInfo
from ascendo.models.host import HostInfo, OperatingSystem

_log = logging.getLogger(__name__)

# Match a timeshift snapshot Name column. Format: YYYY-MM-DD_HH-MM-SS
_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")

# Match a timeshift list row. Lines look like:
#   "0    >  2026-05-09_15-30-22  O     Ascendo pre-apply"
# The leading number, a ">" marker, the timestamp Name, a Tags column
# (1+ chars, often O/D/W/M/B), then optional Description.
_ROW_RE = re.compile(
    r"^\s*(?P<num>\d+)\s+>?\s*"
    r"(?P<name>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\s+"
    r"(?P<tags>\S+)"
    r"(?:\s+(?P<desc>.*))?$"
)

_DEFAULT_TIMEOUT_SEC = 60
_CREATE_TIMEOUT_SEC = 600  # snapshots can take minutes on rsync mode


class TimeshiftSnapshot(ISnapshot):
    """ISnapshot backed by the ``timeshift`` CLI on Ubuntu / Debian.

    Args:
        timeshift_path: Optional override for the ``timeshift`` binary.
            When ``None``, resolved at run time via ``shutil.which``.
        sudo_path: Optional override for ``sudo``. Defaults to ``"sudo"``
            (resolved on PATH at exec time).
        list_timeout_sec: Per-call timeout for read operations.
        create_timeout_sec: Per-call timeout for ``--create`` (longer
            because rsync-mode snapshots can take minutes).
    """

    def __init__(
        self,
        *,
        timeshift_path: str | None = None,
        sudo_path: str = "sudo",
        list_timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
        create_timeout_sec: int = _CREATE_TIMEOUT_SEC,
    ) -> None:
        self._timeshift_override = timeshift_path
        self._sudo = sudo_path
        self._list_timeout = list_timeout_sec
        self._create_timeout = create_timeout_sec

    # ── ISnapshot ────────────────────────────────────────────────────

    @property
    def backend(self) -> str:
        return "timeshift"

    def is_available(self, host: HostInfo) -> bool:
        """True iff host is Linux AND ``timeshift`` is on PATH."""
        if host.os not in {
            OperatingSystem.LINUX_UBUNTU,
            OperatingSystem.LINUX_DEBIAN,
            OperatingSystem.LINUX_OTHER,
        }:
            return False
        return self._resolve_timeshift() is not None

    def create(
        self,
        host: HostInfo,
        *,
        label: str,
        notes: str | None = None,
    ) -> SnapshotInfo:
        """Create a new timeshift snapshot.

        Builds ``sudo -A timeshift --create --comments <label> --scripted``
        and runs it. The new snapshot's ID is parsed from a follow-up
        ``timeshift --list`` call (timeshift's create output isn't a
        stable machine-readable contract).

        Raises:
            SnapshotError: timeshift unavailable, sudo denied, or the
                CLI exited non-zero.
        """
        ts = self._resolve_timeshift()
        if ts is None:
            raise SnapshotError(
                "timeshift not installed (try: sudo apt install timeshift)"
            )

        # Prefer 'label' for the comment; fall back to notes.
        comment = label or notes or "Ascendo pre-apply"

        argv = [
            self._sudo,
            "-A",
            ts,
            "--create",
            "--comments",
            comment,
            "--scripted",
        ]
        _log.debug("TimeshiftSnapshot.create: argv=%r", argv)

        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                timeout=self._create_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SnapshotError(
                f"timeshift --create timed out after {self._create_timeout}s"
            ) from exc
        except OSError as exc:
            raise SnapshotError(f"failed to spawn timeshift: {exc}") from exc

        if completed.returncode != 0:
            raise SnapshotError(
                "timeshift --create failed "
                f"(exit {completed.returncode}). "
                "Configure sudo (or run as root) and ensure timeshift is "
                "set up via 'sudo timeshift --setup' first.\n"
                f"stderr: {(completed.stderr or '').strip()[:500]}"
            )

        # The newest snapshot in the list is the one we just made.
        snapshots = self.list(host)
        if not snapshots:
            raise SnapshotError(
                "timeshift --create returned 0 but the new snapshot is not "
                "visible in 'timeshift --list'. The system may be in an "
                "inconsistent state."
            )
        # SnapshotInfo is frozen — return the newest with our metadata.
        newest = snapshots[0]
        return SnapshotInfo(
            id=newest.id,
            created_at=newest.created_at,
            backend=self.backend,
            label=label,
            notes=notes,
            size_bytes=newest.size_bytes,
        )

    def list(self, host: HostInfo) -> list[SnapshotInfo]:
        """List all timeshift snapshots, newest first.

        Returns ``[]`` on non-Linux hosts (no error). Calls
        ``sudo -A timeshift --list`` because timeshift typically refuses
        to read its DB without root.
        """
        if host.os not in {
            OperatingSystem.LINUX_UBUNTU,
            OperatingSystem.LINUX_DEBIAN,
            OperatingSystem.LINUX_OTHER,
        }:
            return []

        ts = self._resolve_timeshift()
        if ts is None:
            raise SnapshotError(
                "timeshift not installed (try: sudo apt install timeshift)"
            )

        argv = [self._sudo, "-A", ts, "--list"]
        _log.debug("TimeshiftSnapshot.list: argv=%r", argv)

        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                timeout=self._list_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SnapshotError(
                f"timeshift --list timed out after {self._list_timeout}s"
            ) from exc
        except OSError as exc:
            raise SnapshotError(f"failed to spawn timeshift: {exc}") from exc

        if completed.returncode != 0:
            raise SnapshotError(
                f"timeshift --list failed (exit {completed.returncode}). "
                "Configure sudo (or run as root).\n"
                f"stderr: {(completed.stderr or '').strip()[:500]}"
            )

        return self._parse_list(completed.stdout or "")

    def get(
        self, host: HostInfo, snapshot_id: str
    ) -> SnapshotInfo | None:
        """Return the matching :class:`SnapshotInfo` or None."""
        for info in self.list(host):
            if info.id == snapshot_id:
                return info
        return None

    # ── Internals ────────────────────────────────────────────────────

    def _resolve_timeshift(self) -> str | None:
        if self._timeshift_override is not None:
            return self._timeshift_override
        return shutil.which("timeshift")

    def _parse_list(self, raw: str) -> list[SnapshotInfo]:
        """Parse ``timeshift --list`` table output into SnapshotInfo rows.

        Skips header / separator / banner lines silently. Returns the
        parsed rows sorted newest-first by ``created_at``.
        """
        results: list[SnapshotInfo] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Skip header + separator
            if stripped.startswith("Num") or set(stripped) <= {"-"}:
                continue
            m = _ROW_RE.match(line)
            if m is None:
                _log.debug("TimeshiftSnapshot: skipping unrecognised line %r", line)
                continue
            name = m.group("name")
            if not _NAME_RE.match(name):
                continue
            try:
                created_at = datetime.strptime(name, "%Y-%m-%d_%H-%M-%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                _log.debug("TimeshiftSnapshot: invalid name timestamp %r", name)
                continue
            desc = (m.group("desc") or "").strip() or None
            results.append(
                SnapshotInfo(
                    id=name,
                    created_at=created_at,
                    backend=self.backend,
                    label=desc,
                    size_bytes=None,
                    notes=None,
                )
            )
        results.sort(key=lambda s: s.created_at, reverse=True)
        return results
