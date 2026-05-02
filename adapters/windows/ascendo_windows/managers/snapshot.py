"""WindowsSnapshot — ISnapshot via Volume Shadow Copy Service (VSS).

VSS is the Windows-native snapshot facility. Behind the scenes Windows uses
the same shadow-copy infrastructure that powers System Restore, "Previous
Versions" on file shares, and most third-party backup tools. We drive it
through ``vssadmin`` (admin-only) plus a small WMI / CIM read for listing
and metadata.

The interface is deliberately thin (create / list / get; no restore — that
is a deliberate user-only operation, exposed in the dashboard with an
explicit confirmation). When we ship a desktop UI in M4 the restore path
will spawn ``vssadmin`` from a UAC-elevated child via :class:`WindowsElevation`.

Implementation notes:

* ``vssadmin`` is admin-only; ``WindowsSnapshot.is_available`` does a
  privilege check (``IsUserAnAdmin``) and reports false on standard tokens.
  Snapshot create/delete in the apply pipeline therefore must be sequenced
  AFTER UAC elevation has succeeded.
* Listing uses ``Get-CimInstance Win32_ShadowCopy`` which is not
  privilege-gated, so ``list()`` can run from any token.
* The "label" passed at create time is stored as a reusable hint inside
  the sidecar's notes channel (no place for it on a real shadow copy
  object) and round-tripped via a JSON registry under ``%PROGRAMDATA%``.
* Subprocess spawn matches the pattern used by :class:`WingetManager`
  exactly: ``pwsh.exe -File <script.ps1> -RunId ... -OutputDir ...``.
  The script writes a small JSON payload (NOT an ascendo/v1 sidecar) and
  this manager parses it directly.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.snapshot import ISnapshot, SnapshotError, SnapshotInfo
from ascendo.models.host import HostInfo, OperatingSystem

_log = logging.getLogger(__name__)


class WindowsSnapshot(ISnapshot):
    """VSS-backed snapshot manager for Windows."""

    BACKEND: ClassVar[str] = "vss"
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 600

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        pwsh_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._pwsh_override = pwsh_path
        self._pwsh_resolved: str | None = None
        self._timeout_sec = timeout_sec

    @property
    def backend(self) -> str:
        return self.BACKEND

    def is_available(self, host: HostInfo) -> bool:
        """True on Windows hosts with vssadmin on PATH.

        We deliberately do NOT require elevation here — listing snapshots
        works without admin, and create() handles the access-denied case
        with a typed :class:`SnapshotError`.
        """
        if host.os is not OperatingSystem.WINDOWS:
            return False
        return shutil.which("vssadmin") is not None or shutil.which("vssadmin.exe") is not None

    def create(
        self,
        host: HostInfo,
        *,
        label: str,
        notes: str | None = None,
    ) -> SnapshotInfo:
        return self._invoke(
            "create",
            label=label,
            notes=notes,
        )

    def list(self, host: HostInfo) -> list[SnapshotInfo]:  # noqa: A003
        result = self._invoke("list")
        if isinstance(result, list):
            return result
        return []

    def get(self, host: HostInfo, snapshot_id: str) -> SnapshotInfo | None:
        for snap in self.list(host):
            if snap.id == snapshot_id:
                return snap
        return None

    # ── Internals ────────────────────────────────────────────────────────

    def _invoke(self, action: str, **kwargs):
        """Spawn the snapshot.ps1 script with the requested action and parse
        the JSON it emits. Returns either a :class:`SnapshotInfo` (create)
        or a ``list[SnapshotInfo]`` (list)."""
        script = self._scripts_dir / "snapshot" / "snapshot.ps1"
        pwsh = self._resolve_pwsh()
        with tempfile.TemporaryDirectory(prefix="ascendo-vss-") as tmp:
            output = Path(tmp) / "result.json"
            argv: list[str] = [
                pwsh, "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", str(script),
                "-Action", action,
                "-OutputPath", str(output),
            ]
            if action == "create":
                argv += ["-Label", str(kwargs.get("label", ""))]
                notes = kwargs.get("notes")
                if notes:
                    argv += ["-Notes", str(notes)]
            try:
                completed = subprocess.run(  # noqa: S603
                    argv, capture_output=True, text=True,
                    timeout=self._timeout_sec, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise SnapshotError(f"VSS {action} timed out after {self._timeout_sec}s") from exc
            except OSError as exc:
                raise SnapshotError(f"failed to spawn pwsh for VSS {action}: {exc}") from exc
            if not output.exists():
                msg = (
                    f"VSS {action} script produced no output (exit={completed.returncode}); "
                    f"stderr={completed.stderr[:300]!r}"
                )
                raise SnapshotError(msg)
            try:
                payload = json.loads(output.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SnapshotError(f"VSS {action} emitted invalid JSON: {exc}") from exc

        if action == "create":
            return self._parse_one(payload)
        elif action == "list":
            items = payload if isinstance(payload, list) else payload.get("snapshots", [])
            return [self._parse_one(it) for it in items]
        else:
            raise SnapshotError(f"unknown action: {action}")

    def _parse_one(self, item: dict) -> SnapshotInfo:
        """Translate a script-emitted dict into a :class:`SnapshotInfo`."""
        created_at_raw = item.get("created_at") or item.get("CreationTime")
        if isinstance(created_at_raw, str):
            try:
                created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            except ValueError:
                created_at = datetime.now(timezone.utc)
        else:
            created_at = datetime.now(timezone.utc)
        return SnapshotInfo(
            id=str(item.get("id") or item.get("ID") or ""),
            created_at=created_at,
            label=item.get("label"),
            backend=self.BACKEND,
            size_bytes=item.get("size_bytes") or item.get("AllocatedSpace"),
            notes=item.get("notes"),
        )

    def _resolve_pwsh(self) -> str:
        if self._pwsh_resolved is not None:
            return self._pwsh_resolved
        if self._pwsh_override is not None:
            self._pwsh_resolved = self._pwsh_override
            return self._pwsh_resolved
        for candidate in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            found = shutil.which(candidate)
            if found is not None:
                self._pwsh_resolved = found
                return found
        raise SnapshotError("no PowerShell binary on PATH")
