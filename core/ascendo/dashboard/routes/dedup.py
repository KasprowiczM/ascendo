"""Cross-source deduplication consent surface.

The orchestrator's deduplicator runs *report-only* by default (fail-safe;
non-TTY callers never queue a destructive uninstall — see
``orchestrator/deduplicator.py``). These endpoints give the operator an
explicit consent path:

* ``GET  /dedup/pending`` — the recommended duplicate fixes for the latest
  (or a named) CHECK run, computed read-only.
* ``POST /dedup/apply``  — the operator approves a set; the server *recomputes*
  the fixes (it never trusts client-supplied uninstall ids), writes the
  validated ``DEDUPLICATION_TASKS.json`` into a fresh apply run dir, and
  triggers the apply. Consent is therefore always an explicit click.

The SPA renders ``/dedup/pending`` as an "Action required → resolve duplicate"
card (same pattern as the web action-required surface).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...models.package import SourceType
from ...models.run import Phase, RunInfo, Trigger
from ...orchestrator.deduplicator import compute_dedup_fixes
from ...orchestrator.sidecar_io import read_sidecar

router = APIRouter(prefix="/dedup", tags=["dedup"])


class DedupApplyRequest(BaseModel):
    """Optional run scoping + a subset of app ids to resolve. ``app_ids=None``
    means "every pending duplicate"."""

    run_id: UUID | None = None
    app_ids: list[str] | None = None


def _latest_run_with_check(runs_dir: Path) -> Path | None:
    if not runs_dir.is_dir():
        return None
    children = [c for c in runs_dir.iterdir() if c.is_dir()]
    children.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    for child in children:
        if any(child.glob("check__*.json")):
            return child
    return None


def _load_check_sidecars(run_dir: Path) -> list:
    out = []
    for p in sorted(run_dir.glob("check__*.json")):
        try:
            out.append(read_sidecar(p))
        except Exception:  # noqa: BLE001 — a corrupt sidecar must not 500 the consent view
            continue
    return out


def _resolve_source_run(runs_dir: Path, run_id: UUID | None) -> Path | None:
    if run_id is not None:
        run_dir = runs_dir / str(run_id)
        if not run_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return run_dir
    return _latest_run_with_check(runs_dir)


@router.get("/pending")
async def dedup_pending(request: Request, run_id: UUID | None = None) -> dict:
    """Read-only: the recommended cross-source duplicate fixes for a run.

    Defaults to the most recent CHECK run. Returns an empty list (never 404)
    when there are no runs or no duplicates, so the SPA can poll harmlessly.
    """
    runs_dir: Path = request.app.state.runs_dir
    run_dir = _resolve_source_run(runs_dir, run_id)
    if run_dir is None:
        return {"run_id": None, "count": 0, "fixes": []}
    fixes = compute_dedup_fixes(_load_check_sidecars(run_dir))
    return {"run_id": run_dir.name, "count": len(fixes), "fixes": fixes}


@router.post("/apply")
async def dedup_apply(req: DedupApplyRequest, request: Request) -> dict:
    """Explicit consent: queue the approved duplicate uninstalls and trigger
    the apply. The uninstall set is recomputed server-side from the CHECK
    sidecars — the client only chooses *which apps* (``app_ids``), never the
    raw package ids, so a crafted body cannot smuggle an arbitrary uninstall.
    """
    from ...orchestrator.run_async import RunRegistry, start_run_async

    adapter = getattr(request.app.state, "adapter", None)
    if adapter is None:
        raise HTTPException(status_code=503, detail="No adapter installed for this OS.")

    runs_dir: Path = request.app.state.runs_dir
    registry: RunRegistry = request.app.state.run_registry

    src_run_dir = _resolve_source_run(runs_dir, req.run_id)
    if src_run_dir is None:
        raise HTTPException(
            status_code=409,
            detail="no recent check run with duplicates to resolve",
        )

    fixes = compute_dedup_fixes(_load_check_sidecars(src_run_dir))
    wanted: set[str] | None = set(req.app_ids) if req.app_ids else None

    uninstall_tasks: dict[str, list[str]] = defaultdict(list)
    affected_categories: set[str] = set()
    for fix in fixes:
        if wanted is not None and fix["app_id"] not in wanted:
            continue
        for entry in fix["installed"]:
            if entry["recommended_uninstall"]:
                uninstall_tasks[entry["category"]].append(entry["id"])
                affected_categories.add(entry["category"])

    if not uninstall_tasks:
        raise HTTPException(
            status_code=400,
            detail="no pending duplicate uninstalls to apply for the requested apps",
        )

    # Explicit consent recorded → write the destructive artifact into the
    # apply run's OWN dir (the executor reads <run-dir>/DEDUPLICATION_TASKS.json).
    host = adapter.detect_host()
    run_info = RunInfo(
        id=uuid4(),
        trigger=Trigger.DASHBOARD,
        profile="full",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
    )
    new_run_dir = runs_dir / str(run_info.id)
    new_run_dir.mkdir(parents=True, exist_ok=True)
    (new_run_dir / "DEDUPLICATION_TASKS.json").write_text(
        json.dumps(dict(uninstall_tasks)), encoding="utf-8",
    )
    # Per-run approval marker. The Windows winget/npm/pip apply.ps1 executor
    # (Get-AscendoDedupUninstalls) performs an uninstall ONLY when this marker
    # — or the ASCENDO_DEDUP_AUTO_UNINSTALL=1 opt-in — is present, so a stray
    # tasks file alone can never trigger one. This is the explicit-consent
    # record (audit ASCENDO_ULTRA_REVIEW_2 §4, the Windows half of the P0).
    (new_run_dir / "DEDUPLICATION_APPROVED").write_text(
        datetime.now(timezone.utc).isoformat(), encoding="utf-8",
    )

    # Trigger an apply scoped to the affected categories + exactly the
    # duplicate items (item_filter), so the run never fans out to upgrade the
    # whole category. On platforms with a dedup executor (Windows winget/npm/pip
    # apply.ps1) this performs the uninstall; elsewhere the tasks file is the
    # recorded consent artifact and no uninstall occurs.
    categories: list[SourceType] = []
    for cat in sorted(affected_categories):
        try:
            categories.append(SourceType(cat))
        except ValueError:  # unknown category string — skip, executor will ignore
            continue
    item_filter = [pid for ids in uninstall_tasks.values() for pid in ids]

    state = await start_run_async(
        registry=registry,
        adapter=adapter,
        run=run_info,
        host=host,
        base_dir=runs_dir,
        phases=[Phase.APPLY],
        categories=categories or None,
        item_filter=item_filter,
        inventory_db=getattr(request.app.state, "inventory_db", None),
    )

    return {
        "run_id": str(run_info.id),
        "status": state.status.value,
        "uninstall_tasks": dict(uninstall_tasks),
        "source_run_id": src_run_dir.name,
        "stream_url": f"/runs/{run_info.id}/events",
        "status_url": f"/runs/{run_info.id}/status",
    }
