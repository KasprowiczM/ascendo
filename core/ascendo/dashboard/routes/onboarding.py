"""Persistent first-run onboarding state.

The legacy SPA stubs in :mod:`spa_stubs` claimed ``onboarded: true`` so the
wizard never opened. This module replaces those stubs with a real
JSON-file-backed store so:

- ``GET /onboarding/state`` returns ``{onboarded: false, ...}`` on a fresh
  install (no file yet) and the recorded payload otherwise. The wizard
  shows on first dashboard visit and stays hidden afterwards.
- ``POST /onboarding/complete`` writes the wizard's choices payload, marks
  ``onboarded: true``, and stamps ``completed_at``. Idempotent — calling
  it twice just rewrites the file.

State file location:

- ``%APPDATA%\\Ascendo\\onboarding.json`` on Windows.
- ``$ASCENDO_ONBOARDING_FILE`` env var if set (tests inject this).
- ``~/.ascendo/onboarding.json`` everywhere else.

The schema is intentionally tolerant via ``extra='allow'`` on the choices
payload so the frontend can evolve without bumping a wire version, but
the wrapper fields are strict (``extra='forbid'``).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger(__name__)

router = APIRouter(tags=["onboarding"])


# ── State file resolver ───────────────────────────────────────────────────


def _onboarding_file() -> Path:
    """Resolve the on-disk onboarding state location.

    Order of precedence:

    1. ``ASCENDO_ONBOARDING_FILE`` env var (tests inject this).
    2. ``%APPDATA%\\Ascendo\\onboarding.json`` on Windows.
    3. ``~/.ascendo/onboarding.json`` elsewhere.
    """
    override = os.environ.get("ASCENDO_ONBOARDING_FILE")
    if override:
        return Path(override)

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Ascendo" / "onboarding.json"

    return Path.home() / ".ascendo" / "onboarding.json"


def _read_state() -> dict[str, Any]:
    """Read and parse the on-disk state, or return defaults if missing/corrupt."""
    path = _onboarding_file()
    if not path.is_file():
        return {"onboarded": False}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            _log.warning("onboarding state at %s is not a dict; ignoring", path)
            return {"onboarded": False}
        # Coerce ``onboarded`` to bool — a missing flag means a partial write.
        data["onboarded"] = bool(data.get("onboarded", False))
        return data
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("failed to read onboarding state at %s: %s", path, exc)
        return {"onboarded": False}


def _write_state(payload: dict[str, Any]) -> Path:
    """Atomically persist the onboarding state. Returns the file path."""
    path = _onboarding_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish write: serialise to a temp sibling then replace.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        _log.exception("failed to persist onboarding state to %s", path)
        raise HTTPException(
            status_code=500,
            detail=f"could not write onboarding state: {exc}",
        ) from exc
    return path


# ── Wire schemas ──────────────────────────────────────────────────────────


class OnboardingStateResponse(BaseModel):
    """Shape of ``GET /onboarding/state``."""

    model_config = ConfigDict(extra="allow")  # tolerant of forward-compatible fields

    onboarded: bool = Field(
        default=False,
        description="True once the wizard has been finished or explicitly skipped.",
    )
    completed_at: str | None = Field(
        default=None,
        description="ISO-8601 timestamp of the most-recent /onboarding/complete write.",
    )


class OnboardingCompleteRequest(BaseModel):
    """Body of ``POST /onboarding/complete``.

    All fields are optional so the wizard can ship partial state when the
    user skips early. ``extra='forbid'`` keeps the payload tight against
    typos — frontend changes that need new fields must be added here too.
    """

    model_config = ConfigDict(extra="forbid")

    language: str | None = Field(default=None, description="UI language: 'en' or 'pl'.")
    theme: str | None = Field(default=None, description="UI theme: 'dark', 'light', or 'auto'.")
    admin_authorised: bool | None = Field(
        default=None,
        description="True if the user authenticated via the UAC modal during step 3.",
    )
    inventory_seen: bool | None = Field(
        default=None,
        description="True once the wizard's inventory scan finished (step 4).",
    )
    dry_run_seen: bool | None = Field(
        default=None,
        description="True once the wizard's dry-run demo finished (step 6).",
    )
    skipped: bool | None = Field(
        default=None,
        description="True if the user clicked 'Skip' rather than 'Finish'.",
    )
    step: int | None = Field(
        default=None,
        description="The step number reached when finalising (1..6).",
        ge=0,
        le=10,
    )
    # Legacy fields preserved so an old SPA on a newly-built backend still
    # writes something sensible. Not consumed by the wizard but harmless.
    default_profile: str | None = None
    schedule: bool | None = None
    snapshot_before_apply: bool | None = None


class OnboardingCompleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    onboarded: bool = True
    completed_at: str
    path: str


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/onboarding/state", response_model=OnboardingStateResponse)
async def onboarding_state() -> OnboardingStateResponse:
    """Read the persisted onboarding state.

    Returns ``{"onboarded": false}`` on a fresh install (no file yet) so
    the SPA's ``maybeShowWizard()`` opens the modal. After the wizard is
    finished or explicitly skipped, returns the persisted payload with
    ``onboarded: true``.
    """
    state = _read_state()
    return OnboardingStateResponse.model_validate(state)


@router.post("/onboarding/complete", response_model=OnboardingCompleteResponse)
async def onboarding_complete(req: OnboardingCompleteRequest) -> OnboardingCompleteResponse:
    """Persist the wizard's final choices and mark onboarding complete."""
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload: dict[str, Any] = {
        # Strip None fields so the on-disk file stays small and readable.
        k: v
        for k, v in req.model_dump(exclude_none=True).items()
    }
    payload["onboarded"] = True
    payload["completed_at"] = completed_at
    path = _write_state(payload)
    return OnboardingCompleteResponse(
        ok=True,
        onboarded=True,
        completed_at=completed_at,
        path=str(path),
    )


__all__ = ["router", "OnboardingCompleteRequest", "OnboardingStateResponse"]
