#!/usr/bin/env python3
"""CLI shim around WebRegistry for the bash phase scripts.

Subcommands:
  --validate                          → exit 0 OK, non-zero with message on stderr
  --list-app-ids                      → newline-separated app_ids (active only)
  --get-app <app_id>                  → JSON of the app row
  --merge-discovery <disc_json>       → JSON of registry app merged with disc info
                                         (or empty + non-zero if no match)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_registry(shipped: Path, user_override: Path | None):
    # Add adapter source dir to path so we can import ascendo_ubuntu
    here = Path(__file__).resolve().parent
    pkg_root = here.parent  # adapters/ubuntu/
    sys.path.insert(0, str(pkg_root))
    from ascendo_ubuntu.web_registry import WebRegistry  # noqa: E402
    return WebRegistry.load(shipped, user_override)


def _row_to_dict(row) -> dict:
    """Pydantic row → plain JSON-serialisable dict."""
    return json.loads(row.model_dump_json(exclude_none=True))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--shipped", required=True, type=Path)
    p.add_argument("--user-override", type=Path, default=None)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--validate", action="store_true")
    g.add_argument("--list-app-ids", action="store_true")
    g.add_argument("--get-app", metavar="APP_ID")
    g.add_argument("--match-discovery-hint", metavar="HINT",
                   help="Find the first registry entry whose discovery_hint "
                        "matches the supplied path/name (case-insensitive substring).")
    args = p.parse_args()

    try:
        reg = _load_registry(args.shipped, args.user_override)
    except Exception as exc:  # noqa: BLE001
        print(f"registry load failed: {exc}", file=sys.stderr)
        return 2

    if args.validate:
        return 0
    if args.list_app_ids:
        for a in reg.active_apps():
            print(a.app_id)
        return 0
    if args.get_app:
        row = reg.find(args.get_app)
        if row is None:
            return 3
        print(json.dumps(_row_to_dict(row)))
        return 0
    if args.match_discovery_hint:
        needle = args.match_discovery_hint.lower()
        for a in reg.active_apps():
            hint = (a.discovery_hint or a.app_id).lower()
            if hint and hint in needle:
                print(json.dumps(_row_to_dict(a)))
                return 0
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
