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
from ...models.run import Phase, RunInfo, Trigger
from ...orchestrator import DEFAULT_PHASE_ORDER, RunReport, run_phases


# Profile -> default phase set. Profiles are the user-facing knob; phases
# are the orchestrator-facing primitive. The dashboard only exposes
# profile, so we need an explicit map. Aligned with docs/agents/contract.md
# §Profiles:
#   quick — read-only sweep (CHECK only). No mutations, no sudo.
#   safe  — full 5-phase, drivers excluded (driver exclusion happens at
#           the categories layer; here we run the whole pipeline).
#   full  — full 5-phase, every category.
_PROFILE_PHASES: dict[str, tuple[Phase, ...]] = {
    "quick": (Phase.CHECK,),
    "safe":  DEFAULT_PHASE_ORDER,
    "full":  DEFAULT_PHASE_ORDER,
}


def _phases_for_request(
    explicit: list[Phase] | None,
    profile: str | None,
) -> tuple[Phase, ...]:
    """Resolve which phases a run should execute.

    Precedence: explicit > profile > DEFAULT_PHASE_ORDER.

    A frontend sending `{profile: "quick"}` (no phases) expects a read-only
    sweep — without this helper, the orchestrator falls back to
    DEFAULT_PHASE_ORDER (all 5 phases including apply), which is exactly
    what the user reported: clicking Quick check on Overview kicked off
    `apply:mas` and failed.
    """
    if explicit:
        return tuple(explicit)
    if profile and profile in _PROFILE_PHASES:
        return _PROFILE_PHASES[profile]
    return DEFAULT_PHASE_ORDER


def _resolve_item_filter(
    explicit_filter: list[str] | None,
    categories: list[str] | None,
    phases: tuple[Phase, ...],
    runs_dir: Path,
) -> list[str] | None:
    """Auto-derive an inclusion list when the caller didn't pass one.

    If the SPA fires apply with no explicit ``item_filter`` and the user
    has excluded packages via ``/apps/exclude``, we materialise a
    server-side filter = ``installed minus excluded``. This way clicking
    "apply" on the Categories tab respects the per-package opt-out
    without changes to the bash apply scripts.

    Returns ``None`` when:
      - explicit_filter was provided (caller wins)
      - no apply/cleanup phase is in scope (filter only matters for
        mutating phases; check/plan/verify list everything regardless)
      - no exclusions exist (filter would be a no-op against all items)
      - we can't read a recent check sidecar (no inclusion data yet)
    """
    if explicit_filter:
        return list(explicit_filter)
    has_mutating = any(
        p in (Phase.APPLY, Phase.CLEANUP) for p in phases
    )
    if not has_mutating:
        return None
    # Local import — apps.excluded_keys() reads ~/.ascendo/apps_excluded.json
    # which is shared module state. Importing at module-init would create a
    # circular dep with the SPA real router (apps imports schemas which
    # imports here in some paths).
    from . import apps as apps_mod
    excluded = apps_mod.excluded_keys()
    if not excluded:
        return None
    # Read latest check sidecar per category to get the universe of items.
    inclusion: list[str] = []
    cats = categories or _enumerate_known_categories(runs_dir)
    for cat in cats:
        sidecar_items = _latest_check_items(runs_dir, cat)
        for item in sidecar_items:
            name = item.get("name") or item.get("id")
            if not name:
                continue
            key = f"{cat}:{name}"
            if key in excluded:
                continue
            inclusion.append(str(name))
    return inclusion or None


def _enumerate_known_categories(runs_dir: Path) -> list[str]:
    """Best-effort: list categories that have a check sidecar in the
    most recent run dir. Used when caller didn't pass categories."""
    if not runs_dir.is_dir():
        return []
    try:
        runs = sorted(
            (p for p in runs_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []
    out: set[str] = set()
    for run_dir in runs[:5]:
        for sidecar in run_dir.glob("check__*.json"):
            stem = sidecar.stem  # "check__brew"
            if "__" in stem:
                out.add(stem.split("__", 1)[1])
    return sorted(out)


def _latest_check_items(runs_dir: Path, category: str) -> list[dict]:
    """Return the items[] list from the freshest ``check__<category>.json``."""
    if not runs_dir.is_dir():
        return []
    candidates: list[tuple[float, Path]] = []
    try:
        runs = sorted(
            (p for p in runs_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:50]
    except OSError:
        return []
    for run_dir in runs:
        sidecar = run_dir / f"check__{category}.json"
        if sidecar.is_file():
            try:
                candidates.append((sidecar.stat().st_mtime, sidecar))
            except OSError:
                continue
    if not candidates:
        return []
    candidates.sort(reverse=True)
    import json as _json
    try:
        data = _json.loads(candidates[0][1].read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return []
    items = data.get("items") or []
    return [it for it in items if isinstance(it, dict)]
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

    resolved_phases = _phases_for_request(req.phases, req.profile)
    resolved_filter = _resolve_item_filter(
        req.item_filter, req.categories, resolved_phases, runs_dir,
    )
    try:
        report: RunReport = run_phases(
            adapter,
            run_info,
            host,
            phases=resolved_phases,
            categories=req.categories,
            base_dir=runs_dir,
            stop_on_failure=req.stop_on_failure,
            item_filter=resolved_filter,
        )
    except ValueError as exc:
        # Bad input (e.g. empty phases list).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ManagerError as exc:
        # Should not happen — ManagerError is swallowed by _safe_run_phase.
        raise HTTPException(status_code=500, detail=f"manager error: {exc}") from exc

    return RunResponse.from_orchestrator(report)


_STATUS_PRIORITY = {
    "failed": 4,
    "partial": 3,
    "skipped": 2,
    "success": 1,
    "up_to_date": 0,
}


def _aggregate_status(statuses: list[str]) -> str | None:
    """Return the worst per-sidecar status across a run.

    The legacy SPA renders a single status pill per row (success/partial/
    failed); collapse the set with a fixed precedence so e.g. one failed
    sidecar wins over four successes.
    """
    if not statuses:
        return None
    return max(statuses, key=lambda s: _STATUS_PRIORITY.get(s, -1))


def _read_run_metadata(run_dir: Path) -> dict:
    """Best-effort enrichment for one run dir's sidecars.

    Returns a dict with the legacy SPA fields the History tab consumes
    (started_at / ended_at / status / profile / dry_run / needs_reboot /
    summary). Each field is ``None`` if no sidecar could supply it; we
    never raise from this helper because the History list MUST keep
    rendering even when a single run dir has a corrupted sidecar.
    """
    paths = list_run_sidecars(run_dir)
    if not paths:
        return {}

    started: list[datetime] = []
    ended: list[datetime] = []
    statuses: list[str] = []
    profile: str | None = None
    dry_run: bool | None = None
    needs_reboot: bool = False
    phase_rows: list[dict] = []

    for p in paths:
        try:
            sc = read_sidecar(p)
        except SidecarReadError:
            continue
        if sc.started_at is not None:
            started.append(sc.started_at)
        if sc.finished_at is not None:
            ended.append(sc.finished_at)
        statuses.append(sc.status.value)
        if profile is None:
            profile = sc.run.profile
        if dry_run is None:
            dry_run = sc.run.dry_run
        # Sidecar.needs_reboot may not exist on every schema version.
        if getattr(sc, "needs_reboot", False):
            needs_reboot = True
        summary = sc.summary
        phase_rows.append(
            {
                "category": sc.category.value,
                "phase": sc.phase.value,
                "exit_code": getattr(summary, "exit_code", None),
                "summary": {
                    "ok": getattr(summary, "success", 0)
                    + getattr(summary, "up_to_date", 0),
                    "warn": getattr(summary, "partial", 0)
                    + getattr(summary, "skipped", 0),
                    "err": getattr(summary, "failed", 0),
                    "total": getattr(summary, "total", 0),
                },
            },
        )

    return {
        "started_at": min(started) if started else None,
        "ended_at": max(ended) if ended else None,
        "status": _aggregate_status(statuses),
        "profile": profile,
        "dry_run": dry_run,
        "needs_reboot": needs_reboot if statuses else None,
        "summary": {"phases": phase_rows} if phase_rows else None,
    }


@router.get("", response_model=RunListResponse)
async def list_runs(request: Request) -> RunListResponse:
    """List run-ids that have at least one sidecar on disk under runs_dir.

    Each entry is enriched (best-effort) with the legacy SPA fields the
    History tab consumes — ``id`` (alias for ``run_id``), ``started_at``,
    ``ended_at``, ``status``, ``profile``, ``dry_run``, ``needs_reboot``,
    ``summary``. Reading sidecars per row is the price; for typical
    inventories (few hundred runs × a handful of sidecars each) it stays
    well under 100 ms wall-clock and keeps the schema additive — clients
    that only consume ``run_id``/``sidecar_count``/``phases`` keep working.
    """
    runs_dir: Path = request.app.state.runs_dir
    if not runs_dir.is_dir():
        return RunListResponse(runs=[], total=0)

    entries: list[RunListEntry] = []
    # Sort by mtime descending so the History tab shows newest runs first
    # without the SPA needing to re-sort. Falls back to name order on
    # filesystems whose mtime resolution coalesces fast back-to-back runs.
    children = [c for c in runs_dir.iterdir() if c.is_dir()]
    children.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)

    for child in children:
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
        meta = _read_run_metadata(child)
        entries.append(
            RunListEntry(
                run_id=run_id,
                id=run_id,
                sidecar_count=len(sidecar_paths),
                phases=sorted(phases),  # type: ignore[arg-type]  # Pydantic coerces str→Phase
                started_at=meta.get("started_at"),
                ended_at=meta.get("ended_at"),
                status=meta.get("status"),
                profile=meta.get("profile"),
                dry_run=meta.get("dry_run"),
                needs_reboot=meta.get("needs_reboot"),
                summary=meta.get("summary"),
            ),
        )

    return RunListResponse(runs=entries, total=len(entries))


@router.get("/{run_id}/report", response_class=None)
async def get_run_report(run_id: UUID, request: Request):
    """Return the human-readable ``REPORT.md`` for ``run_id``.

    Returns ``text/markdown`` content when the file exists. If no report
    is on disk and the run has an apply phase, the report is generated
    on-demand. Returns 404 when the run never had an apply phase (and
    therefore has nothing to report on).
    """
    from fastapi.responses import PlainTextResponse

    from ...orchestrator.report import REPORT_FILENAME, generate_apply_report

    runs_dir: Path = request.app.state.runs_dir
    run_dir = runs_dir / str(run_id)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    report_path = run_dir / REPORT_FILENAME
    if report_path.is_file():
        try:
            markdown = report_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"could not read report: {exc}") from exc
    else:
        markdown = generate_apply_report(run_dir)
        if markdown is None:
            raise HTTPException(
                status_code=404,
                detail=f"no apply report available for run {run_id}",
            )

    return PlainTextResponse(content=markdown, media_type="text/markdown")


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

    resolved_phases = _phases_for_request(req.phases, req.profile)
    resolved_filter = _resolve_item_filter(
        req.item_filter, req.categories, resolved_phases, runs_dir,
    )
    state = await start_run_async(
        registry=registry,
        adapter=adapter,
        run=run_info,
        host=host,
        base_dir=runs_dir,
        phases=resolved_phases,
        categories=req.categories,
        stop_on_failure=req.stop_on_failure,
        item_filter=resolved_filter,
        inventory_db=getattr(request.app.state, "inventory_db", None),
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

        from ...orchestrator.run_async import STREAM_LOG_FILENAME

        seen: set[str] = set()
        # Per-log-file byte offset so we only stream NEW lines on each
        # poll cycle (not the whole file every 500 ms). Key = log path.
        # Per-request bookkeeping — every connected client gets its own
        # offsets so two browsers can stream the same run without
        # cross-talk or skipped lines.
        log_offsets: dict[str, int] = {}
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

                # Tail two kinds of log files:
                #   1. ``_stream.log`` — the per-run aggregate written
                #      by every cooperating apply.sh through ``_stream_tee``.
                #      Carries every stdout/stderr line from brew, sudo,
                #      mas, npm, softwareupdate, plus PROGRESS sentinels.
                #   2. ``<phase>__<source>.log`` — per-phase logs the
                #      managers may write directly (older convention,
                #      kept for backward compat with winget's apply path).
                try:
                    # Sesja 50 fix — `_stream.log` matches `*.log`, so the
                    # glob below pulls it back in after the explicit
                    # append, producing a duplicate Path in `log_files`.
                    # The per-file offset check usually dedupes the second
                    # iteration, BUT under concurrent writes (apply.sh
                    # tee'ing live) the file size CAN grow between the
                    # explicit and glob iterations, so the second
                    # iteration ends up emitting whatever bytes arrived
                    # in that tiny window — causing the operator-visible
                    # 2x output. Fix: dedupe by Path identity.
                    log_files: list[Path] = []
                    seen_paths: set[str] = set()
                    stream_log = run_dir / STREAM_LOG_FILENAME
                    if stream_log.is_file():
                        log_files.append(stream_log)
                        seen_paths.add(str(stream_log))
                    for p in sorted(run_dir.glob("*.log")):
                        key = str(p)
                        if key in seen_paths:
                            continue
                        seen_paths.add(key)
                        log_files.append(p)
                except OSError:
                    log_files = []
                for log_path in log_files:
                    try:
                        size = log_path.stat().st_size
                    except OSError:
                        continue
                    last = log_offsets.get(str(log_path), 0)
                    if size <= last:
                        continue
                    try:
                        with log_path.open("rb") as fh:
                            fh.seek(last)
                            chunk = fh.read(size - last)
                    except OSError:
                        continue
                    log_offsets[str(log_path)] = size
                    text = chunk.decode("utf-8", errors="replace")
                    is_stream = log_path.name == STREAM_LOG_FILENAME
                    # Stream line by line so the SPA can append per-line
                    # without re-parsing a multi-line blob.
                    for raw_line in text.splitlines():
                        line = raw_line.rstrip("\r")
                        if not line:
                            continue
                        # Bash apply scripts emit progress sentinels of
                        # the form ``>>> PROGRESS <pct> <label>`` (or
                        # ``>>> ITEM <label>``) into the stream log. Lift
                        # those to first-class events so the frontend can
                        # update the progress bar without parsing log
                        # lines twice.
                        if is_stream and line.startswith(">>> PROGRESS "):
                            payload = _parse_progress(line)
                            if payload is not None:
                                yield _sse("progress", payload)
                                continue
                        if is_stream and line.startswith(">>> ITEM "):
                            label = line[len(">>> ITEM "):].strip()
                            if label:
                                yield _sse("progress", {
                                    "label": label,
                                    "pct": None,
                                })
                                continue
                        # log_line for the new aggregate stream;
                        # legacy ``log`` event for the per-phase logs so
                        # the existing SPA handler keeps working.
                        if is_stream:
                            yield _sse("log_line", {
                                "line": line,
                                "stream": "stream",
                            })
                        else:
                            yield _sse("log", {
                                "source": log_path.stem,  # e.g. "apply__winget"
                                "line": line,
                            })
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


def _parse_progress(line: str) -> dict | None:
    """Parse a ``>>> PROGRESS <pct> <label>`` sentinel line.

    Returns ``{"pct": int, "label": str}`` or ``None`` if the line
    doesn't follow the convention.

    The bash side emits these from cooperating apply.sh scripts whenever
    they start a new per-package operation. The percentage is computed
    on the bash side as ``current_index / total_count * 100``; the label
    is a free-form short description (e.g. ``upgrading uv 0.11.8 -> 0.11.9``).
    """
    body = line[len(">>> PROGRESS "):].strip()
    if not body:
        return None
    parts = body.split(None, 1)
    pct_raw = parts[0]
    try:
        pct = int(pct_raw)
    except (TypeError, ValueError):
        return None
    label = parts[1] if len(parts) > 1 else ""
    pct = max(0, min(100, pct))
    return {"pct": pct, "label": label}
