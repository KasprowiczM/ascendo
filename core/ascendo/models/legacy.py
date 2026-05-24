"""Legacy sidecar translator: ascendo/v1 -> ascendo/v1.

Pre-rename emitters in checked-out copies of Ascendo continue
to write the older schema. ADR-0003 promises the reader accepts both.
This module bridges the gap by converting a legacy payload (dict) into a
payload that satisfies the ascendo/v1 :class:`~ascendo.models.sidecar.Sidecar`
model.

Why a translator instead of a polymorphic model?

The legacy v1 shape and the ascendo v1 shape look superficially similar
but diverge in several important ways:

* Legacy ``host`` is a string. ascendo ``host`` is a :class:`HostInfo`
  object with mandatory ``os``, ``arch``, ``user``, ``is_elevated`` fields.
* Legacy ``kind`` -> ascendo ``phase``.
* Legacy ``ended_at`` -> ascendo ``finished_at``.
* Legacy ``exit_code`` -> ascendo ``status`` (a string enum).
* Legacy ``summary`` is ``{ok, warn, err}``. ascendo ``summary`` is
  ``{total, success, up_to_date, failed, skipped, planned, partial,
  duration_ms, exit_code}``.
* Legacy ``items[].action`` / ``from`` / ``to`` -> ascendo ``items[]``
  with ``current_version`` / ``target_version`` / ``status``.
* Legacy ``diagnostics[]`` -> ascendo top-level ``messages[]``.
* Legacy ``log_path`` and ``needs_reboot`` have no v1 equivalents and
  are dropped (``needs_reboot`` will resurface as a top-level field in
  v2 — see ADR-0003).

When the legacy payload lacks a required ascendo/v1 field, sensible
defaults are synthesized:

* ``run.id`` -> uuid5 from (host + started_at) for stable replay.
* ``run.trigger`` -> ``"unknown"``.
* ``run.profile`` -> ``"full"``.
* ``host.os`` -> derived from host string (assume ``"linux_ubuntu"`` for
  legacy emitters, since they were Ascendo-only).
* ``host.is_elevated`` -> ``False`` (legacy didn't track this).
* ``tool.name`` -> ``category``, ``tool.version`` -> ``"unknown"``.

Information loss notes:

* ``summary.warn`` is mapped to ``summary.skipped`` because that's the
  closest semantic match in the new vocabulary. This is lossy: legacy
  "warn" really meant "completed with warnings", which in ascendo
  terminology is closer to ``partial``. We chose ``skipped`` because
  the new model already requires summary totals to match items[]
  cardinality, and ``partial`` carries stronger guarantees we cannot
  retroactively assert.
* ``log_path`` is discarded. Operators looking at a translated legacy
  sidecar lose the original log location.
* ``needs_reboot`` is discarded.
"""

from __future__ import annotations

import uuid
from typing import Any

# Stable namespace for uuid5 derivation. Picked once and never changed
# so legacy payloads consistently round-trip to the same run.id across
# repeated translations of the same input.
_LEGACY_NAMESPACE = uuid.UUID("6f1d4c0e-2b7a-4a2e-9b9d-1ad6e0f9ba01")

# Legacy schema literal we accept.
_LEGACY_SCHEMA = "ascendo/v1"

# Mapping for legacy item.result -> ascendo item.status.
_RESULT_TO_STATUS: dict[str, str] = {
    "ok": "success",
    "warn": "skipped",     # see information-loss note in module docstring
    "skipped": "skipped",
    "failed": "failed",
    "noop": "up_to_date",
}

# Mapping for legacy diagnostics.level -> ascendo Message.level.
_DIAG_LEVEL_TO_MESSAGE_LEVEL: dict[str, str] = {
    "info": "info",
    "warn": "warn",
    "error": "error",
}

# Map legacy category -> ascendo SourceType + ItemSource.type.
# Missing entries pass through untouched (and Pydantic will raise if the
# value isn't a recognized SourceType, which is the desired behavior —
# we don't want to silently coerce unknown legacy categories).
_CATEGORY_TO_SOURCE_TYPE: dict[str, str] = {
    "apt": "apt",
    "snap": "snap",
    "brew": "brew_formula",
    "npm": "npm",
    "pip": "pip",
    "flatpak": "flatpak",
    "drivers": "drivers",
    "inventory": "unknown",
    "web": "web",
}


def is_legacy_v1(payload: dict[str, Any]) -> bool:
    """Return True if ``payload`` looks like a legacy v1 sidecar.

    Detection key is the ``schema`` string. We deliberately don't sniff
    field shape — the schema string is the contract. If a future emitter
    wants to share field shape but flag itself as ascendo/v1, it should
    do so.
    """
    return isinstance(payload, dict) and payload.get("schema") == _LEGACY_SCHEMA


def _category_to_source_type(category: str) -> str:
    """Translate a legacy category id to an ascendo SourceType value.

    Plugin categories (``plugin:<id>``) collapse to ``"plugin"``. Unknown
    built-in categories pass through, letting downstream Pydantic
    validation reject anything genuinely bogus.
    """
    if category.startswith("plugin:"):
        return "plugin"
    return _CATEGORY_TO_SOURCE_TYPE.get(category, category)


def _synthesize_run_id(host: str, started_at: str) -> str:
    """Derive a stable run.id from (host, started_at).

    uuid5 is deterministic, so the same legacy payload always translates
    to the same run.id. This matters for cleanup and reconciliation:
    re-importing the same legacy sidecars must not produce duplicate
    run rows.
    """
    return str(uuid.uuid5(_LEGACY_NAMESPACE, f"{host}|{started_at}"))


def _translate_items(
    legacy_items: list[dict[str, Any]],
    *,
    category: str,
    source_type: str,
) -> list[dict[str, Any]]:
    """Translate legacy items[] into ascendo items[]."""
    out: list[dict[str, Any]] = []
    for legacy in legacy_items:
        result = legacy.get("result", "ok")
        status = _RESULT_TO_STATUS.get(result, "failed")

        details = legacy.get("details")
        per_item_messages: list[dict[str, Any]] = []
        if details:
            per_item_messages.append({"level": "info", "text": str(details)})

        item: dict[str, Any] = {
            "id": legacy["id"],
            # Legacy didn't have a separate display name; reuse id.
            "name": legacy.get("id"),
            "category": source_type,
            "source": {"type": source_type, "feed": None, "url": None},
            "current_version": legacy.get("from"),
            "target_version": legacy.get("to"),
            "resolved_version": None,
            "status": status,
            "exit_code": None,
            "duration_ms": legacy.get("duration_ms"),
            "evidence": None,
            "rollback": None,
            "messages": per_item_messages,
        }
        out.append(item)
    return out


def _translate_summary(
    legacy_summary: dict[str, int],
    *,
    items_count: int,
    exit_code: int,
) -> dict[str, int]:
    """Translate legacy summary {ok, warn, err} -> ascendo Summary."""
    return {
        "total": items_count,
        "success": int(legacy_summary.get("ok", 0)),
        "up_to_date": 0,
        "failed": int(legacy_summary.get("err", 0)),
        # See module docstring: "warn" maps to "skipped" because it's
        # the closest semantic match in the new vocabulary.
        "skipped": int(legacy_summary.get("warn", 0)),
        "planned": 0,
        "partial": 0,
        "duration_ms": None,
        "exit_code": exit_code,
    }


def _translate_diagnostics(legacy_diags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate legacy diagnostics[] -> ascendo top-level messages[].

    Legacy diagnostics carried a ``code`` field (e.g. "DRIFT-CODE") which
    has no analog in the v1 Message model. We prefix the message text
    with ``[code]`` so the information isn't entirely lost.
    """
    out: list[dict[str, Any]] = []
    for diag in legacy_diags:
        level = _DIAG_LEVEL_TO_MESSAGE_LEVEL.get(diag.get("level", "info"), "info")
        code = diag.get("code")
        msg = diag.get("msg", "")
        text = f"[{code}] {msg}" if code else msg
        if not text:
            text = "(empty diagnostic)"
        out.append({"level": level, "text": text})
    return out


def translate_legacy_v1(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a legacy ascendo/v1 payload into an ascendo/v1 payload.

    The returned dict is suitable for ``Sidecar.model_validate(...)``.
    All synthesized fields are documented in the module docstring.

    Args:
        payload: A dict matching the legacy v1 schema. The caller is
            responsible for any pre-validation (JSON parse, etc.).

    Returns:
        A dict that satisfies the ascendo/v1 :class:`Sidecar` model.

    Raises:
        KeyError: ``payload`` is missing a required legacy field. The
            translator deliberately doesn't synthesize required legacy
            fields — a payload without ``host`` or ``kind`` is broken
            and should fail loudly.
    """
    # Required legacy fields — fail loudly if missing.
    legacy_host = payload["host"]
    legacy_kind = payload["kind"]
    legacy_category = payload["category"]
    started_at = payload["started_at"]
    ended_at = payload["ended_at"]
    exit_code = int(payload.get("exit_code", 0))

    legacy_items = payload.get("items", []) or []
    legacy_summary = payload.get("summary", {}) or {}
    legacy_diags = payload.get("diagnostics", []) or []

    source_type = _category_to_source_type(legacy_category)
    # Legacy exit-code semantics (docs/agents/contract.md):
    #   0  ok        -> success
    #   1  warn      -> success (non-critical advisories; sidecar items[] are clean)
    #   75 already-running -> skipped
    #   anything else -> failed
    if exit_code == 0 or exit_code == 1:
        status = "success"
    elif exit_code == 75:
        status = "skipped"
    else:
        status = "failed"

    run_id = _synthesize_run_id(legacy_host, started_at)

    # Wrap legacy host string as a HostInfo dict with synthesized defaults.
    host_block: dict[str, Any] = {
        "hostname": legacy_host,
        "os": "linux_ubuntu",
        "os_version": "unknown",
        "arch": "unknown",
        "user": "unknown",
        "is_elevated": False,
        "elevation_method": "none",
        "locale": None,
    }

    run_block: dict[str, Any] = {
        "id": run_id,
        "trigger": "unknown",
        "profile": "full",
        "dry_run": False,
        "started_at": started_at,
        "finished_at": ended_at,
        "invocation": None,
    }

    tool_block: dict[str, Any] = {
        "name": legacy_category if not legacy_category.startswith("plugin:")
        else legacy_category[len("plugin:") :],
        "version": "unknown",
        "binary_path": None,
    }

    items = _translate_items(legacy_items, category=legacy_category, source_type=source_type)
    summary = _translate_summary(legacy_summary, items_count=len(items), exit_code=exit_code)
    messages = _translate_diagnostics(legacy_diags)

    return {
        # Emit the new canonical schema literal — downstream model
        # validates against the SidecarSchema enum, which still accepts
        # both, but we want translated payloads to be indistinguishable
        # from natively-emitted ascendo/v1 sidecars after translation.
        "schema": "ascendo/v1",
        "run": run_block,
        "host": host_block,
        "tool": tool_block,
        "phase": legacy_kind,
        "category": source_type,
        "started_at": started_at,
        "finished_at": ended_at,
        "status": status,
        "items": items,
        "summary": summary,
        "messages": messages,
    }
