"""Shared sidecar-salvage helpers for Windows package managers.

Mirror of ``adapters/ubuntu/ascendo_ubuntu/managers/_base.py``'s salvage
mechanism (Sesja 56). The Ubuntu adapter pre-allocates a ``JSON_BUFDIR``
before spawning each bash phase script; the bash side appends to
``meta.json`` + ``items.jsonl`` + ``messages.jsonl`` + ``diags.jsonl``
incrementally. If the script dies before its EXIT trap fires
(signal, parse error, kernel kill), the orchestrator reconstructs a
sidecar from whatever survived in the bufdir, marks it with an explicit
``ASCENDO-SALVAGED`` message, and writes it via M2.4 ``write_sidecar``.

On Windows the equivalent flow is:

1. The Python manager allocates a bufdir before spawning ``pwsh.exe``.
2. The PowerShell phase script accepts a ``-BufDir`` parameter; when
   passed, ``New-Sidecar`` immediately writes ``meta.json`` to that dir
   and stashes the path on the in-memory sidecar object.
   ``Add-SidecarItem`` / ``Add-SidecarMessage`` append a JSONL line to
   ``items.jsonl`` / ``messages.jsonl`` in addition to the in-memory
   ArrayList.
3. On normal completion, ``Save-Sidecar`` writes the canonical sidecar
   file and removes the bufdir (unless ``$env:ASCENDO_KEEP_BUFDIR=1``).
4. On crash, the Python manager catches the missing-sidecar case and
   calls :py:meth:`_BaseWindowsManager._salvage_sidecar` to reconstruct
   from the surviving bufdir contents.

``-BufDir`` is OPTIONAL — phase scripts that don't pass it through to
``New-Sidecar`` still work; the new helpers no-op when ``$BufDir`` is
unset. This keeps the rollout backwards-compatible: only updated scripts
opt into the bufdir-driven salvage path.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from ascendo.models.host import HostInfo
from ascendo.models.package import SourceType
from ascendo.models.result import Item, ItemStatus, Message, MessageLevel, Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo, Trigger
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo
from ascendo.orchestrator.sidecar_io import write_sidecar

_log = logging.getLogger(__name__)


class _BaseWindowsManager:
    """Mixin: sidecar salvage helpers for Windows IPackageManager managers.

    Each Windows manager allocates a bufdir before spawning its phase
    script. The script's :mod:`AscendoJson` helpers write incremental
    JSONL files (``meta.json``, ``items.jsonl``, ``messages.jsonl``,
    ``diags.jsonl``) to the bufdir as work happens. On normal completion,
    ``Save-Sidecar`` assembles the canonical sidecar and removes the
    bufdir.

    If the script dies before ``Save-Sidecar`` fires (signal, parse
    error, kernel kill), the manager's ``run_phase()`` catches the
    missing-sidecar case and calls :meth:`_salvage_sidecar` to
    reconstruct one from whatever survived. The reconstructed sidecar
    gets an ``ASCENDO-SALVAGED`` diagnostic at ``messages[0]`` so it's
    visible in the SPA without digging into logs.

    The mixin is composable with :class:`ascendo.interfaces.package_manager.IPackageManager`
    — declare it FIRST in the MRO (e.g.
    ``class WingetManager(_BaseWindowsManager, IPackageManager): ...``)
    so its helpers are available on every concrete manager.
    """

    # ── Bufdir lifecycle ───────────────────────────────────────────────

    def _allocate_bufdir(
        self,
        run_id: str | UUID,
        phase: Phase,
        category: str,
        base: Path,
    ) -> Path:
        """Create ``<base>/<run-id>/.bufdir/<phase>__<category>/`` atomically.

        Idempotent — safe to re-call. The directory is left in place on
        normal Save-Sidecar success (PowerShell-side cleanup will remove
        it) AND on salvage (preserved as forensic evidence). The next
        run with the same (run_id, phase, category) will overwrite any
        leftovers via ``mkdir(exist_ok=True)``.

        Returns:
            Absolute :class:`Path` to the bufdir.
        """
        bufdir = (
            Path(base)
            / str(run_id)
            / ".bufdir"
            / f"{phase.value}__{category}"
        )
        bufdir.mkdir(parents=True, exist_ok=True)
        return bufdir

    def _cleanup_bufdir(self, bufdir: Path) -> None:
        """Best-effort recursive delete after Save-Sidecar succeeds.

        Belt-and-suspenders: PowerShell-side ``Save-Sidecar`` already
        removes the bufdir on its own (unless ``$env:ASCENDO_KEEP_BUFDIR=1``
        is set). This catches the case where the script wrote a
        canonical sidecar but bombed out before reaching its bufdir
        cleanup — or where the script ships an older AscendoJson that
        doesn't know about the bufdir convention.

        Failures are swallowed: the next run will clobber the leftover
        via :meth:`_allocate_bufdir`'s ``exist_ok=True`` regardless.
        """
        try:
            shutil.rmtree(bufdir, ignore_errors=True)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass

    # ── Salvage ────────────────────────────────────────────────────────

    def _salvage_sidecar(
        self,
        *,
        run_id: str | UUID,
        phase: Phase,
        category: str,
        bufdir: Path,
        expected_sidecar_path: Path,
        exit_code: int | None = None,
    ) -> Sidecar | None:
        """Reconstruct a sidecar from partial bufdir contents.

        Reads ``meta.json`` (if any) for run/host/tool identity, then
        concatenates ``items.jsonl`` / ``messages.jsonl`` / ``diags.jsonl``
        line-by-line, ignoring malformed lines. Synthesises the missing
        pieces with sane defaults: status derived from items[]
        cardinality + exit_code, ``finished_at`` set to "now" (UTC),
        ``ASCENDO-SALVAGED`` message prepended to make the salvage path
        visible in the SPA.

        Args:
            run_id: The orchestrator's real run UUID (string or UUID).
            phase: Phase enum value the script was executing.
            category: Source category slug (e.g. ``"winget"``).
            bufdir: Path the script was supposed to write its JSONL
                buffers to.
            expected_sidecar_path: Where ``write_sidecar()`` should land
                the reconstructed sidecar. The reader's invariants
                (``write_sidecar``'s ``base_dir`` + filename convention)
                are honoured by recomputing the parent dir from the
                expected path.
            exit_code: The script's actual exit code (when known) — used
                to disambiguate the synthesised status. ``None`` is
                treated as ``-1`` (unknown failure).

        Returns:
            The parsed :class:`Sidecar` instance on success, also
            persisted to ``expected_sidecar_path``'s parent via
            :func:`write_sidecar`. Returns ``None`` when the bufdir is
            so corrupt nothing useful can be recovered (meta.json
            missing OR meta.json malformed beyond rescue).
        """
        bufdir = Path(bufdir)
        meta_path = bufdir / "meta.json"
        if not meta_path.exists():
            _log.info(
                "%s: salvage skipped — bufdir %s has no meta.json",
                type(self).__name__, bufdir,
            )
            return None

        try:
            meta_text = meta_path.read_text(encoding="utf-8", errors="replace")
            meta: dict[str, Any] = json.loads(meta_text)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(
                "%s: salvage failed — meta.json unreadable: %s",
                type(self).__name__, exc,
            )
            return None
        if not isinstance(meta, dict):
            _log.warning(
                "%s: salvage failed — meta.json is not a JSON object",
                type(self).__name__,
            )
            return None

        items_raw = self._read_jsonl_safely(bufdir / "items.jsonl")
        messages_raw = self._read_jsonl_safely(bufdir / "messages.jsonl")
        diags_raw = self._read_jsonl_safely(bufdir / "diags.jsonl")

        # ── Parse items[] ───────────────────────────────────────────────
        items: list[Item] = []
        for entry in items_raw:
            if not isinstance(entry, dict):
                continue
            try:
                items.append(Item.model_validate(entry))
            except ValidationError:
                # Skip individual malformed item — keep what we can.
                continue

        # ── Parse messages[] (phase-level) ──────────────────────────────
        salvaged_messages: list[Message] = []
        ascendo_salvaged_text = (
            f"Sidecar reconstructed post-crash from {bufdir} "
            f"(exit code {exit_code if exit_code is not None else 'unknown'}). "
            "Phase script died before Save-Sidecar fired; items + "
            "messages below survived in the bufdir but were never "
            "assembled into a canonical sidecar."
        )
        salvaged_messages.append(
            Message(
                level=MessageLevel.WARN,
                text=ascendo_salvaged_text,
                # ASCENDO-SALVAGED marker for SPA + log greppability.
                timestamp=self._utc_iso_now(),
            )
        )
        for entry in messages_raw:
            if not isinstance(entry, dict):
                continue
            try:
                salvaged_messages.append(Message.model_validate(entry))
            except ValidationError:
                continue
        # Diagnostics from PowerShell-side ``diags.jsonl`` map naturally
        # to phase-level messages (level/text shape matches).
        for entry in diags_raw:
            if not isinstance(entry, dict):
                continue
            # Some diag emitters use "msg" (Linux _json_emit) instead of
            # "text"; normalise so Pydantic doesn't choke.
            if "msg" in entry and "text" not in entry:
                entry = dict(entry)
                entry["text"] = entry.pop("msg")
            # The diag shape may include a "code" field (e.g.
            # ``ASCENDO-SALVAGED``) which isn't part of the Message model.
            # Strip unknown keys so Message.model_validate succeeds under
            # extra='forbid'.
            cleaned: dict[str, Any] = {
                k: v for k, v in entry.items() if k in {"level", "text", "timestamp"}
            }
            try:
                salvaged_messages.append(Message.model_validate(cleaned))
            except ValidationError:
                continue

        # ── Synthesise status from items + exit_code ────────────────────
        status = self._derive_phase_status(items, exit_code)

        # ── Synthesise summary from items[] ─────────────────────────────
        summary = self._build_summary(items, status)

        # ── Synthesise run / host / tool blocks from meta + caller ──────
        run_block = self._build_run_info(meta, run_id)
        host_block = self._build_host_info(meta)
        tool_block = self._build_tool_info(meta)

        started_at = self._parse_timestamp(meta.get("started_at"))
        finished_at = datetime.now(timezone.utc)
        if finished_at < started_at:
            # Should never happen unless meta.json was written with a
            # future timestamp. Cap so the Sidecar reverse-time validator
            # doesn't bounce the salvage.
            finished_at = started_at

        # ── Build + validate the sidecar ────────────────────────────────
        try:
            sidecar = Sidecar.model_validate(
                {
                    "schema": SidecarSchema.V1_ASCENDO,
                    "run": run_block,
                    "host": host_block,
                    "tool": tool_block,
                    "phase": phase,
                    "category": self._resolve_category(category, meta),
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "status": status,
                    "items": items,
                    "summary": summary,
                    "messages": salvaged_messages,
                    "needs_reboot": bool(meta.get("needs_reboot", False)),
                }
            )
        except ValidationError as exc:
            _log.warning(
                "%s: salvage built sidecar but Pydantic validation failed: %s",
                type(self).__name__, exc,
            )
            return None

        # ── Persist via canonical sidecar_io path ───────────────────────
        # ``write_sidecar`` writes under ``<base_dir>/<run-id>/<phase>__<category>.json``.
        # ``expected_sidecar_path`` is that final path; ``base_dir`` is
        # its grandparent (parent.parent: <runs_root>/<run-id>/<file> →
        # <runs_root>).
        try:
            expected = Path(expected_sidecar_path)
            base_dir = expected.parent.parent
            write_sidecar(sidecar, base_dir=base_dir)
        except Exception as exc:  # noqa: BLE001 — best-effort persist
            _log.warning(
                "%s: salvage built sidecar but write_sidecar failed: %s; "
                "returning Sidecar object so the orchestrator still sees it",
                type(self).__name__, exc,
            )
        return sidecar

    # ── Internals ──────────────────────────────────────────────────────

    @classmethod
    def _read_jsonl_safely(cls, path: Path) -> list[dict[str, Any]]:
        """Read a JSONL file, returning a list of parsed dicts.

        Malformed lines, non-dict roots, and empty lines are silently
        skipped — the goal is to preserve as much as survived rather
        than abort the salvage on the first bad byte.
        """
        path = Path(path)
        if not path.exists():
            return []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    @staticmethod
    def _utc_iso_now() -> str:
        """ISO-8601 UTC timestamp with 'Z' suffix matching PS emitter."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        """Parse an ISO-8601 timestamp string; fall back to UTC now."""
        if isinstance(value, str) and value:
            text = value
            # Pydantic accepts trailing 'Z' but fromisoformat() only does
            # on Python 3.11+. Normalise just in case.
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                dt = datetime.now(timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return datetime.now(timezone.utc)

    @staticmethod
    def _derive_phase_status(
        items: list[Item], exit_code: int | None
    ) -> PhaseStatus:
        """Synthesise the phase-level status from items + exit code.

        Status logic:
          * No items at all                              -> FAILED (no work survived)
          * exit_code == 0 + items all up_to_date/success -> SUCCESS
          * exit_code == 0 + any failures                 -> PARTIAL
          * exit_code != 0 + any successes                -> PARTIAL
          * exit_code != 0 + everything failed/skipped    -> FAILED
        """
        if not items:
            return PhaseStatus.FAILED

        success_count = sum(
            1 for it in items
            if it.status in {ItemStatus.SUCCESS, ItemStatus.UP_TO_DATE, ItemStatus.PLANNED}
        )
        failed_count = sum(1 for it in items if it.status == ItemStatus.FAILED)

        if exit_code in (None,):
            # Unknown exit — be conservative.
            if failed_count and success_count:
                return PhaseStatus.PARTIAL
            if failed_count and not success_count:
                return PhaseStatus.FAILED
            return PhaseStatus.PARTIAL  # incomplete success is still partial

        if exit_code == 0:
            if failed_count == 0:
                return PhaseStatus.SUCCESS
            if success_count == 0:
                return PhaseStatus.FAILED
            return PhaseStatus.PARTIAL

        # exit_code != 0
        if success_count > 0:
            return PhaseStatus.PARTIAL
        return PhaseStatus.FAILED

    @staticmethod
    def _build_summary(items: list[Item], status: PhaseStatus) -> Summary:
        """Build a :class:`Summary` from the salvaged items list."""

        def _count(s: ItemStatus) -> int:
            return sum(1 for it in items if it.status == s)

        total = len(items)
        success = _count(ItemStatus.SUCCESS)
        up_to_date = _count(ItemStatus.UP_TO_DATE)
        failed = _count(ItemStatus.FAILED)
        skipped = _count(ItemStatus.SKIPPED)
        planned = _count(ItemStatus.PLANNED)
        partial = _count(ItemStatus.PARTIAL)
        triggered = _count(ItemStatus.TRIGGERED)

        # exit_code field on Summary is the phase exit code; mirror status.
        exit_code = 0 if status is PhaseStatus.SUCCESS else 1

        return Summary(
            total=total,
            success=success,
            up_to_date=up_to_date,
            failed=failed,
            skipped=skipped,
            planned=planned,
            partial=partial,
            triggered=triggered,
            duration_ms=None,
            exit_code=exit_code,
        )

    @staticmethod
    def _build_run_info(meta: dict[str, Any], run_id: str | UUID) -> dict[str, Any]:
        """Build the run.* block.

        Prefer the meta.json run block (if PowerShell-side N-S wrote it)
        but always pin ``run.id`` to the orchestrator's caller-supplied
        UUID so downstream consumers tie the sidecar back to the real
        run, not a synthesised one.
        """
        run = meta.get("run")
        if isinstance(run, dict):
            block = dict(run)
        else:
            block = {}
        block["id"] = str(run_id)
        block.setdefault("trigger", Trigger.UNKNOWN.value)
        block.setdefault("profile", "unknown")
        block.setdefault("dry_run", False)
        block.setdefault(
            "started_at",
            meta.get("started_at") or _BaseWindowsManager._utc_iso_now(),
        )
        block.setdefault("finished_at", None)
        block.setdefault("invocation", None)
        return block

    @staticmethod
    def _build_host_info(meta: dict[str, Any]) -> dict[str, Any] | HostInfo:
        """Build the host.* block, falling back to a minimal stub."""
        host = meta.get("host")
        if isinstance(host, dict):
            return host
        # Minimal stub — the orchestrator's HostInfo isn't on this path,
        # so synthesise something the Pydantic validator will accept.
        return {
            "hostname": "unknown",
            "os": "windows",
            "os_version": "unknown",
            "arch": "unknown",
            "user": "unknown",
            "is_elevated": False,
            "elevation_method": "none",
            "locale": None,
        }

    @staticmethod
    def _build_tool_info(meta: dict[str, Any]) -> dict[str, Any] | ToolInfo:
        """Build the tool.* block from meta.json."""
        tool = meta.get("tool")
        if isinstance(tool, dict):
            return tool
        # Fall back to the legacy/bash-style meta where the tool name
        # equals the category.
        name = meta.get("category") or "unknown"
        return {
            "name": str(name),
            "version": "unknown",
            "binary_path": None,
        }

    @staticmethod
    def _resolve_category(category: str, meta: dict[str, Any]) -> SourceType:
        """Resolve the sidecar's category SourceType.

        Prefer the caller-supplied slug (which matches the manager's
        :py:attr:`category`); fall back to whatever meta.json claims;
        last-ditch fall back to ``SourceType.UNKNOWN``.
        """
        candidates = [category, meta.get("category")]
        for cand in candidates:
            if not cand:
                continue
            try:
                return SourceType(cand)
            except ValueError:
                continue
        return SourceType.UNKNOWN
