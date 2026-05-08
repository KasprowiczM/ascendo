#!/usr/bin/env python3
"""CLI shim for the WebRegistry Pydantic model.

Used by bash phase scripts that can't import Python directly.
Always exits 0 on success, 2 on validation failure or unknown slug.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from pydantic import ValidationError

from ascendo_macos.web_registry import WebRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description="WebRegistry CLI shim")
    parser.add_argument("--shipped", required=True, type=Path,
                        help="Shipped web_apps.toml path")
    parser.add_argument("--user-override", type=Path, default=None,
                        help="Optional user override TOML")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-slugs", action="store_true",
                   help="Print active slugs newline-delimited")
    g.add_argument("--list-bundle-ids", action="store_true",
                   help="Print active bundle_ids newline-delimited")
    g.add_argument("--get-app", metavar="SLUG",
                   help="Print single-line JSON for one app")
    g.add_argument("--get-app-by-bundle-id", metavar="BUNDLE_ID",
                   help="Print single-line JSON for one app (by bundle_id)")
    g.add_argument("--validate", action="store_true",
                   help="Validate registry; exit 0 on ok, 2 on error")
    args = parser.parse_args()

    try:
        reg = WebRegistry.load(args.shipped, args.user_override)
    except FileNotFoundError as exc:
        print(f"web_registry: {exc}", file=sys.stderr)
        return 2
    except tomllib.TOMLDecodeError as exc:
        print(f"web_registry: malformed TOML: {exc}", file=sys.stderr)
        return 2
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        print(f"web_registry: validation failed at {loc}: {first['msg']}",
              file=sys.stderr)
        return 2

    if args.list_slugs:
        for app in reg.active_apps():
            print(app.slug)
        return 0
    if args.list_bundle_ids:
        for app in reg.active_apps():
            print(app.bundle_id)
        return 0
    if args.get_app:
        app = reg.find(args.get_app)
        if app is None:
            print(f"web_registry: slug not found: {args.get_app}",
                  file=sys.stderr)
            return 2
        # Pydantic v2 model_dump with mode='json' coerces HttpUrl + Path to str
        print(json.dumps(app.model_dump(mode="json"), separators=(",", ":")))
        return 0
    if args.get_app_by_bundle_id:
        app = reg.find_by_bundle_id(args.get_app_by_bundle_id)
        if app is None:
            print(f"web_registry: bundle_id not found: {args.get_app_by_bundle_id}",
                  file=sys.stderr)
            return 2
        print(json.dumps(app.model_dump(mode="json"), separators=(",", ":")))
        return 0
    if args.validate:
        return 0
    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
