"""``/runs`` REST endpoints — synchronous run + history listing.

MVP behavior — synchronous: ``POST /runs`` blocks until all phases finish.
That's fine for short runs (``check`` only) but will need an async/SSE
counterpart for ``apply`` in production. Tracked as a follow-up.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request

from ...interfaces.package_manager import ManagerError
from ...models.run import RunInfo, Trigger
from ...orchestrator import DEFAULT_PHASE_ORDER, RunReport, run_phases
from ...orchestrator.sidecar_io import (
    SidecarReadError,
    list_run_sidecars,
    read_sidecar,
)
from ..schemas import (
    RunListEntry,
    RunListResponse,
    RunRequest,
    RunResponse,
)

router = APIRouter(tags=["runs"])

_log = logging.getLogger(__name__)


@router.post("", response_model=RunResponse, status_code=200)
async def create_run(req: RunRequest, request: Request) -> RunResponse:
    """Execute a synchronous run and return the aggregated report.

    Raises:
        503: no adapter installed.
        500: run aborted before producing any sidecars (manager-level
             crash that wasn't caught by the orchestrator's
             ``_safe_run_phase``). Should be rare.
    """
    adapter = getattr(request.app.state, "adapter", None)
    runs_dir: Path = request.app.state.runs_dir

    if adapter is None:
        raise HTTPException(
            status_code=503,
            detail="No adapter installed for this OS.",
        )

    try:
        host = adapter.detect_host()
    except Exception as exc:  # noqa: BLE001
        _log.exception("adapter.detect_host() failed")
        raise HTTPException(
            status_code=500,
            detail=f"detect_host failed: {exc!r}",
        ) from exc

    run_info = RunInfo(
        id=uuid4(),
        trigger=Trigger.DASHBOARD,
        profile=req.profile,
        dry_run=req.dry_run,
        started_at=datetime.now(timezone.utc),
    )

    try:
        report: RunReport = run_phases(
            adapter,
            run_info,
            host,
            phases=req.phases if req.phases else DEFAULT_PHASE_ORDER,
            categories=req.categories,
            base_dir=runs_dir,
            stop_on_failure=req.stop_on_failure,
            item_filter=req.item_filter,
        )
    except ValueError as exc:
        # Bad input (e.g. empty phases list).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ManagerError as exc:
        # Should not happen — ManagerError is swallowed by _safe_run_phase.
        raise HTTPException(status_code=500, detail=f"manager error: {exc}") from exc

    return RunResponse.from_orchestrator(report)


@router.get("", response_model=RunListResponse)
async def list_runs(request: Request) -> RunListResponse:
    """List run-ids that have at least one sidecar on disk under runs_dir."""
    runs_dir: Path = request.app.state.runs_dir
    if not runs_dir.is_dir():
        return RunListResponse(runs=[], total=0)

    entries: list[RunListEntry] = []
    for child in sorted(runs_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            run_id = UUID(child.name)
        except ValueError:
            # Not a UUID-named directory — skip (could be tempdirs etc).
            continue
        sidecar_paths = list_run_sidecars(child)
        if not sidecar_paths:
            continue
        # Phase set without parsing the full sidecar — derive from filenames
        # (<phase>__<category>.json).
        phases: set[str] = set()
        for p in sidecar_paths:
            stem = p.stem  # e.g. "check__winget"
            if "__" in stem:
                phases.add(stem.split("__", 1)[0])
        entries.append(
            RunListEntry(
                run_id=run_id,
                sidecar_count=len(sidecar_paths),
                phases=sorted(phases),  # type: ignore[arg-type]  # Pydantic coerces str→Phase
            ),
        )

    return RunListResponse(runs=entries, total=len(entries))


@router.get("/{run_id}", response_model=list)
async def get_run(run_id: UUID, request: Request) -> list[dict]:
    """Return all sidecars for ``run_id`` as raw JSON dicts.

    Returns ``[]`` if the run dir exists but has no sidecars; 404 if
    it doesn't exist at all.
    """
    runs_dir: Path = request.app.state.runs_dir
    run_dir = runs_dir / str(run_id)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    sidecars: list[dict] = []
    for path in list_run_sidecars(run_dir):
        try:
            sc = read_sidecar(path)
            sidecars.append(sc.model_dump(mode="json", by_alias=True))
        except SidecarReadError as exc:
            # Surface the corrupted sidecar as a stub entry — caller can
            # decide whether to render or skip.
            sidecars.append(
                {
                    "_recovery_stub": True,
                    "path": str(path),
                    "error": str(exc),
                },
            )

    return sidecars


# ── Async run + SSE (M2.10) ────────────────────────────────────────────────


@router.post("/async", status_code=202)
async def create_run_async(req: RunRequest, request: Request) -> dict:
    """Kick off a run asynchronously. Returns the run_id immediately (202).

    The caller polls :func:`get_run_status` or subscribes to
    :func:`stream_run_events` to track progress.
    """
    from ...orchestrator.run_async import RunRegistry, start_run_async

    adapter = getattr(request.app.state, "adapter", None)
    runs_dir: Path = request.app.state.runs_dir
    registry: RunRegistry = request.app.state.run_registry

    if adapter is None:
        raise HTTPException(status_code=503, detail="No adapter installed for this OS.")

    host = adapter.detect_host()
    run_info = RunInfo(
        id=uuid4(),
        trigger=Trigger.DASHBOARD,
        profile=req.profile,
        dry_run=req.dry_run,
        started_at=datetime.now(timezone.utc),
    )

    state = await start_run_async(
        registry=registry,
        adapter=adapter,
        run=run_info,
        host=host,
        base_dir=runs_dir,
        phases=req.phases if req.phases else DEFAULT_PHASE_ORDER,
        categories=req.categories,
        stop_on_failure=req.stop_on_failure,
        item_filter=req.item_filter,
    )

    return {
        "run_id": str(run_info.id),
        "status": state.status.value,
        "stream_url": f"/runs/{run_info.id}/events",
        "status_url": f"/runs/{run_info.id}/status",
    }


@router.get("/{run_id}/status")
async def get_run_status(run_id: UUID, request: Request) -> dict:
    """Poll the lifecycle of an async run. 404 if unknown."""
    from ...orchestrator.run_async import RunRegistry

    registry: RunRegistry = request.app.state.run_registry
    state = registry.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id {run_id}")

    return {
        "run_id": str(run_id),
        "status": state.status.value,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "finished_at": state.finished_at.isoformat() if state.finished_at else None,
        "error": state.error,
        "sidecar_count": (
            len(list_run_sidecars(state.base_dir / str(run_id)))
            if state.base_dir is not None and (state.base_dir / str(run_id)).is_dir()
            else 0
        ),
    }


@router.get("/{run_id}/events")
async def stream_run_events(run_id: UUID, request: Request):
    """Server-Sent Events stream of newly-emitted sidecars for ``run_id``.

    Events:
      - ``status``     — sent on connect + on every state transition.
      - ``sidecar``    — sent for each new sidecar JSON file in the run dir.
      - ``done``       — sent when the worker completes; closes the stream.

    The stream polls the run directory every 500 ms. Polling is cheap
    (one ``listdir`` per cycle) and immune to filesystem-watch quirks
    that vary across Linux/macOS/Windows.
    """
    from fastapi.responses import StreamingResponse

    from ...orchestrator.run_async import RunRegistry, RunStatus

    registry: RunRegistry = request.app.state.run_registry
    state = registry.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id {run_id}")

    async def event_gen():
        import asyncio
        import json as _json

        seen: set[str] = set()
        # Initial status event.
        yield _sse("status", {"status": state.status.value, "run_id": str(run_id)})

        run_dir = state.base_dir / str(run_id) if state.base_dir else None

        while True:
            # New sidecars on disk?
            if run_dir is not None and run_dir.is_dir():
                for path in list_run_sidecars(run_dir):
                    if path.name in seen:
                        continue
                    seen.add(path.name)
                    try:
                        sc = read_sidecar(path)
                        yield _sse("sidecar", sc.model_dump(mode="json", by_alias=True))
                    except SidecarReadError as exc:
                        yield _sse("sidecar_error", {"path": str(path), "error": str(exc)})
            if state.status in (RunStatus.COMPLETED, RunStatus.FAILED):
                yield _sse("done", {
                    "status": state.status.value,
                    "error": state.error,
                    "duration_ms": (
                        int((state.finished_at - state.started_at).total_seconds() * 1000)
                        if state.started_at and state.finished_at else None
                    ),
                })
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


def _sse(event: str, data: object) -> bytes:
    """Build one SSE event frame as bytes."""
    import json as _json
    payload = _json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
