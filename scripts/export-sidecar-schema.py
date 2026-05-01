#!/usr/bin/env python3
"""Export the JSON Schema for the Ascendo sidecar v1 contract.

The schema file is the canonical machine-readable contract between native
emitters (Bash on Linux/macOS, PowerShell on Windows) and the Python
adapter readers. Generating it from the Pydantic source of truth keeps
the two in sync and lets CI fail fast if a model change wasn't reflected
in the schema.

Run from the repository root:

    PYTHONPATH=core python3 scripts/export-sidecar-schema.py

For CI drift detection: re-run the script and confirm
``git diff --exit-code docs/architecture/schemas/sidecar.v1.schema.json``
is clean. The script intentionally has no argparse / CLI flags — the
canonical output path is hard-coded, which keeps the contract tamper-
resistant compared with a flag-driven entry point that could be skipped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make ``core/`` importable when this script is run directly (no install).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = _REPO_ROOT / "core"
if _CORE_DIR.is_dir() and str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

from ascendo.models.sidecar import Sidecar  # noqa: E402  (sys.path mutation)

# ── Output configuration ─────────────────────────────────────────────────

OUTPUT_PATH = _REPO_ROOT / "docs" / "architecture" / "schemas" / "sidecar.v1.schema.json"

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = "https://github.com/KasprowiczM/ascendo/schemas/sidecar.v1.schema.json"
SCHEMA_TITLE = "Ascendo Sidecar v1"
SCHEMA_DESCRIPTION = (
    "Canonical JSON Schema for Ascendo's per-phase sidecar contract "
    "(``ascendo/v1``). Native emitters write one sidecar per phase to "
    "``runs/<run-id>/<phase>__<category>.json``; Python readers in the core "
    "orchestrator validate against this schema before parsing. Generated "
    "from ``core/ascendo/models/sidecar.py`` via "
    "``scripts/export-sidecar-schema.py`` — do not hand-edit. "
    "See ADR-0003 for the schema's role in the architecture."
)


def build_schema() -> dict[str, object]:
    """Return the post-processed JSON Schema dict ready for serialisation."""
    # ``by_alias=True`` so the ``schema_`` field surfaces under its serialised
    # alias ``schema`` (Pydantic uses an underscore suffix to avoid the
    # keyword collision with ``BaseModel.model_json_schema``).
    schema: dict[str, object] = Sidecar.model_json_schema(by_alias=True)

    # Pydantic auto-fills ``title`` (= class name) and ``description`` (=
    # docstring) at the schema root. Both are too narrow for the published
    # contract; strip and replace with curated values, then merge so header
    # fields render first in the JSON output.
    body = {k: v for k, v in schema.items() if k not in {"title", "description"}}
    annotated: dict[str, object] = {
        "$schema": SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "title": SCHEMA_TITLE,
        "description": SCHEMA_DESCRIPTION,
    }
    annotated.update(body)
    return annotated


def main() -> int:
    """Write the schema to :data:`OUTPUT_PATH`. Returns the process exit code."""
    schema = build_schema()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False)
    OUTPUT_PATH.write_text(payload + "\n", encoding="utf-8")
    rel = OUTPUT_PATH.relative_to(_REPO_ROOT)
    print(f"Wrote {rel} ({len(payload):,} bytes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
