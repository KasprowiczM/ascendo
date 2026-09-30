#!/usr/bin/env python3
"""adapters/macos/lib/appstore_lookup.py -- iPad/iOS-on-Mac App Store helpers.

Port of macOS_updates ``lib/python/appstore_lookup.py`` plus the scan that lived
inline in its ``lib/appstore_ios.sh``. Pure functions (no network, no side
effects) with a tiny CLI used by ``ascendo_appstore_ios.sh``.

An iPad app that is installed from the Mac App Store has no
``Contents/_MASReceipt`` and no ``Contents/Info.plist``; it is a *wrapper*
bundle whose identity lives in ``Wrapper/iTunesMetadata.plist``. ``mas`` cannot
see or update these, so Ascendo only *reports* the ones whose store version
(iTunes Lookup API) is newer than the installed one.

Python 3.9 compatible (macOS system python): deferred annotations, no ``match``.

CLI (records are joined by the ASCII unit separator, 0x1f -- NOT a tab: bash
``read`` collapses whitespace IFS runs, which would shift columns whenever a
field such as the version is empty)::

    appstore_lookup.py scan [--dirs A:B]
        -> name US itemId US version US path        (dedup by itemId)
    appstore_lookup.py pending --scan-file F --lookup-file G
        -> name US itemId US installed US store US path   (store strictly newer)
        exit 2 when G is not a valid iTunes Lookup response (unknown, NOT "none")
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys
from typing import Any, Dict, Iterable, List, Optional

US = "\x1f"

try:  # same directory; on sys.path when run as a script
    from brew_casks import version_compare
except ImportError:  # pragma: no cover - only when imported from elsewhere
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from brew_casks import version_compare  # type: ignore


def default_app_dirs() -> List[str]:
    """Roots scanned for wrapper bundles. ``ASCENDO_APP_DIRS`` (colon list)
    overrides them -- used by tests and by callers with non-standard installs."""
    override = os.environ.get("ASCENDO_APP_DIRS", "").strip()
    if override:
        return [p for p in override.split(":") if p]
    return ["/Applications", os.path.expanduser("~/Applications")]


def is_app_store_managed(app_path: str) -> bool:
    """True if the bundle is App Store-managed: an iPad wrapper
    (``Wrapper/iTunesMetadata.plist``) or a Mac App Store app
    (``Contents/_MASReceipt``)."""
    if not app_path:
        return False
    return (
        os.path.isfile(os.path.join(app_path, "Wrapper", "iTunesMetadata.plist"))
        or os.path.isdir(os.path.join(app_path, "Contents", "_MASReceipt"))
    )


def read_itunes_metadata(plist_bytes: bytes) -> Optional[Dict[str, str]]:
    """Extract ``itemId`` / ``bundleShortVersionString`` / ``itemName`` from the
    bytes of an ``iTunesMetadata.plist``. ``None`` on error or missing itemId."""
    if not plist_bytes:
        return None
    try:
        data = plistlib.loads(plist_bytes)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    item_id = data.get("itemId")
    if item_id is None:
        return None
    version = str(data.get("bundleShortVersionString") or data.get("bundleVersion") or "")
    name = str(data.get("itemName") or data.get("bundleDisplayName") or "")
    return {"itemId": str(item_id), "bundleShortVersionString": version, "itemName": name}


def scan_ipad_apps(roots: Optional[Iterable[str]] = None) -> List[Dict[str, str]]:
    """Installed iPad wrapper apps: dicts ``name``, ``itemId``, ``version``,
    ``path``; deduplicated by itemId (first wins), sorted by app file name."""
    seen = set()
    found: List[Dict[str, str]] = []
    for root in (default_app_dirs() if roots is None else list(roots)):
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root), key=lambda x: x.casefold())
        except OSError:
            continue
        for entry in entries:
            if not entry.endswith(".app"):
                continue
            app_path = os.path.join(root, entry)
            meta_path = os.path.join(app_path, "Wrapper", "iTunesMetadata.plist")
            if not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path, "rb") as fh:
                    meta = read_itunes_metadata(fh.read())
            except OSError:
                continue
            if not meta or meta["itemId"] in seen:
                continue
            seen.add(meta["itemId"])
            found.append({
                "name": meta["itemName"] or entry[:-4],
                "itemId": meta["itemId"],
                "version": meta["bundleShortVersionString"],
                "path": app_path,
            })
    return found


def lookup_is_valid(json_obj: Any) -> bool:
    """True when the payload is an iTunes Lookup response (dict with a
    ``results`` list). An empty list is valid: the store knows none of the ids."""
    if isinstance(json_obj, str):
        try:
            json_obj = json.loads(json_obj)
        except Exception:
            return False
    return isinstance(json_obj, dict) and isinstance(json_obj.get("results"), list)


def parse_lookup(json_obj: Any) -> Dict[str, str]:
    """iTunes Lookup response -> ``{trackId: version}``."""
    if isinstance(json_obj, str):
        try:
            json_obj = json.loads(json_obj)
        except Exception:
            return {}
    if not isinstance(json_obj, dict):
        return {}
    results = json_obj.get("results", [])
    if not isinstance(results, list):
        return {}
    mapping: Dict[str, str] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        track_id = entry.get("trackId")
        version = entry.get("version")
        if track_id is not None and version is not None:
            mapping[str(track_id)] = str(version)
    return mapping


def pending_ios(installed: List[Dict[str, Any]], store: Dict[str, str]) -> List[Dict[str, Any]]:
    """Installed apps whose store version is strictly newer. An unparseable
    version on either side is NOT pending (never invent a difference)."""
    pending: List[Dict[str, Any]] = []
    for item in installed:
        item_id = str(item.get("itemId", ""))
        installed_version = str(item.get("version", ""))
        store_version = store.get(item_id)
        if not store_version:
            continue
        if version_compare(store_version, installed_version) == 1:
            pending.append({
                "name": item.get("name", ""),
                "itemId": item_id,
                "installed": installed_version,
                "store": store_version,
                "path": item.get("path", ""),
            })
    return pending


def _emit(fields: List[str]) -> None:
    # Field separators must not appear in the data itself.
    print(US.join(f.replace(US, " ").replace("\n", " ") for f in fields))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--dirs", default=None, help="colon-separated roots")
    p_pending = sub.add_parser("pending")
    p_pending.add_argument("--scan-file", required=True)
    p_pending.add_argument("--lookup-file", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "scan":
        roots = [p for p in args.dirs.split(":") if p] if args.dirs else None
        for app in scan_ipad_apps(roots):
            _emit([app["name"], app["itemId"], app["version"], app["path"]])
        return 0

    installed: List[Dict[str, Any]] = []
    with open(args.scan_file, encoding="utf-8") as fh:
        for line in fh.read().splitlines():
            parts = line.split(US)
            if len(parts) >= 4 and parts[1]:
                installed.append({"name": parts[0], "itemId": parts[1],
                                  "version": parts[2], "path": parts[3]})
    with open(args.lookup_file, encoding="utf-8", errors="replace") as fh:
        lookup_text = fh.read()
    if not lookup_is_valid(lookup_text):
        print("appstore_lookup: not a valid iTunes Lookup response", file=sys.stderr)
        return 2
    for p in pending_ios(installed, parse_lookup(lookup_text)):
        _emit([p["name"], p["itemId"], p["installed"], p["store"], p["path"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
