"""Shared builders and fixtures for apply-report contract tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import ItemSource, SourceType
from ascendo.models.result import Item, ItemStatus, Message, Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo, Trigger
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo
from ascendo.orchestrator.sidecar_io import write_sidecar


# ── Builders ────────────────────────────────────────────────────────────────


_RUN_ID = UUID("12345678-1234-1234-1234-123456789abc")


def _host() -> HostInfo:
    return HostInfo(
        hostname="testbox",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="tester",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def _run() -> RunInfo:
    return RunInfo(
        id=_RUN_ID,
        trigger=Trigger.CLI,
        profile="full",
        dry_run=False,
        started_at=datetime(2026, 5, 8, 22, 47, tzinfo=timezone.utc),
    )


def _build_item(
    *,
    item_id: str,
    name: str | None = None,
    category: SourceType,
    status: ItemStatus,
    current: str | None = None,
    target: str | None = None,
    resolved: str | None = None,
    messages: list[Message] | None = None,
    exit_code: int | None = None,
) -> Item:
    return Item(
        id=item_id,
        name=name or item_id,
        category=category,
        source=ItemSource(type=category),
        current_version=current,
        target_version=target,
        resolved_version=resolved,
        status=status,
        exit_code=exit_code,
        messages=messages or [],
    )


def _build_sidecar(
    *,
    phase: Phase,
    category: SourceType,
    items: list[Item],
    started: datetime,
    finished: datetime | None = None,
    summary: Summary | None = None,
    needs_reboot: bool = False,
    messages: list[Message] | None = None,
) -> Sidecar:
    finished = finished or (started + timedelta(seconds=30))
    if summary is None:
        # Compute a consistent summary from items[].
        success = sum(1 for it in items if it.status is ItemStatus.SUCCESS)
        u2d = sum(1 for it in items if it.status is ItemStatus.UP_TO_DATE)
        failed = sum(1 for it in items if it.status is ItemStatus.FAILED)
        skipped = sum(1 for it in items if it.status is ItemStatus.SKIPPED)
        triggered = sum(1 for it in items if it.status is ItemStatus.TRIGGERED)
        partial = sum(1 for it in items if it.status is ItemStatus.PARTIAL)
        planned = sum(1 for it in items if it.status is ItemStatus.PLANNED)
        summary = Summary(
            total=len(items),
            success=success,
            up_to_date=u2d,
            failed=failed,
            skipped=skipped,
            triggered=triggered,
            partial=partial,
            planned=planned,
            exit_code=0 if failed == 0 else 1,
        )
    overall = (
        PhaseStatus.FAILED if any(it.status is ItemStatus.FAILED for it in items) and any(
            it.status is ItemStatus.SUCCESS for it in items
        ) else PhaseStatus.PARTIAL if any(it.status is ItemStatus.FAILED for it in items)
        else PhaseStatus.SUCCESS
    )
    if any(it.status is ItemStatus.FAILED for it in items) and not any(
        it.status is ItemStatus.SUCCESS for it in items
    ):
        overall = PhaseStatus.FAILED
    payload = {
        "schema": SidecarSchema.V1_ASCENDO.value,
        "run": _run().model_dump(mode="json"),
        "host": _host().model_dump(mode="json"),
        "tool": ToolInfo(name=category.value, version="1.0").model_dump(mode="json"),
        "phase": phase.value,
        "category": category.value,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "status": overall.value,
        "items": [it.model_dump(mode="json") for it in items],
        "summary": summary.model_dump(),
        "needs_reboot": needs_reboot,
        "messages": [m.model_dump() for m in (messages or [])],
    }
    return Sidecar.model_validate(payload)


def _write(run_dir: Path, sc: Sidecar) -> None:
    """Write a sidecar to ``<base_dir>/<run_id>/`` using the canonical path."""
    write_sidecar(sc, base_dir=run_dir.parent)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    rd = tmp_path / str(_RUN_ID)
    rd.mkdir(parents=True, exist_ok=True)
    return rd


def _make_sidecar(
    run: RunInfo,
    host: HostInfo,
    *,
    phase: Phase = Phase.CHECK,
    summary: Summary | None = None,
    needs_reboot: bool = False,
    messages: list[Message] | None = None,
    items: list[dict] | None = None,
) -> Sidecar:
    now = datetime.now(timezone.utc)
    item_list = items or []
    # Keep summary.total consistent with len(items) when the caller doesn't
    # supply an explicit summary (the Sidecar model enforces total==len(items)).
    sm = summary if summary is not None else Summary(total=len(item_list), exit_code=0)
    return Sidecar.model_validate(
        {
            "schema": SidecarSchema.V1_ASCENDO.value,
            "run": run.model_dump(mode="json"),
            "host": host.model_dump(mode="json"),
            "tool": ToolInfo(name="winget", version="1.0").model_dump(mode="json"),
            "phase": phase.value,
            "category": "winget",
            "started_at": now.isoformat(),
            "finished_at": now.isoformat(),
            "status": PhaseStatus.SUCCESS.value,
            "items": item_list,
            "summary": sm.model_dump(),
            "needs_reboot": needs_reboot,
            "messages": [m.model_dump() for m in (messages or [])],
        }
    )
