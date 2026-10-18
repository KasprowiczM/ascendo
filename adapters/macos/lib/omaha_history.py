#!/usr/bin/env python3
"""CLI glue for the chromium_updater web handler (Spec C).

The Omaha/VersionHistory logic itself lives in vendor_feeds.py (omaha_last_status,
omaha_recent_status, omaha_history_status, version_history_public, version
helpers -- Task 7). This module only exposes it to bash and resolves the
[app.chromium_updater] configuration (defaults, ``~`` expansion, env overrides).

Subcommands (input on stdin unless stated):
  history <appid> <max_age_h> <installed>...   stdin: history file paths, one per line
                                               -> "<noupdate|updated>\t<HH:MM>\t<version>" or nothing
  last    <appid>                              stdin: log text -> last updatecheck status or nothing
  recent  <appid> <max_age_h>                  stdin: log text -> "<status> <HH:MM>" or nothing
  vh                                           stdin: VersionHistory JSON -> public version or nothing
  cfg     <field>                              env ASCENDO_WEB_CFG (app JSON) -> resolved value(s), one per line
Always exits 0 (a missing result is empty output), so bash callers stay simple.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from vendor_feeds import (  # noqa: E402,F401  (re-exported for tests and callers)
    normalize_vendor_version,
    omaha_history_status,
    omaha_last_status,
    omaha_recent_status,
    version_compare,
    version_history_public,
    version_key,
)

_GOOGLE_UPDATER_REL = "Library/Application Support/Google/GoogleUpdater"
_GOOGLE_BIN_TAIL = "GoogleUpdater/Current/GoogleUpdater.app/Contents/MacOS/GoogleUpdater"
_GOOGLE_LEGACY_AGENT = (
    "/Library/Google/GoogleSoftwareUpdate/GoogleSoftwareUpdate.bundle/Contents/"
    "Resources/GoogleSoftwareUpdateAgent.app/Contents/MacOS/GoogleSoftwareUpdateAgent"
)


def _google_default_bins() -> list[str]:
    home = os.path.expanduser("~")
    user = os.environ.get(
        "ASCENDO_GOOGLE_USER_UPDATER",
        os.path.join(home, "Library/Application Support/Google", _GOOGLE_BIN_TAIL))
    system = os.environ.get(
        "ASCENDO_GOOGLE_SYS_UPDATER",
        "/Library/Application Support/Google/" + _GOOGLE_BIN_TAIL)
    return [p for p in (user, system) if p]


def _google_default_logs() -> list[str]:
    home = os.path.expanduser("~")
    user = os.environ.get(
        "ASCENDO_GOOGLE_USER_LOG", os.path.join(home, _GOOGLE_UPDATER_REL, "updater.log"))
    system = os.environ.get(
        "ASCENDO_GOOGLE_SYS_LOG", "/Library/Application Support/Google/GoogleUpdater/updater.log")
    return [p for p in (user, system) if p]


def _bounded(value, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def resolve_cfg(app: dict) -> dict:
    """Effective chromium_updater settings for one registry app (see Spec C)."""
    sub = app.get("chromium_updater") or {}
    own_bin = sub.get("updater_bin")
    google = not own_bin
    bins = [os.path.expanduser(p) for p in own_bin] if own_bin else _google_default_bins()
    own_log = sub.get("updater_log")
    logs = [os.path.expanduser(p) for p in own_log] if own_log else (
        _google_default_logs() if google else [])
    legacy = ""
    if google:
        legacy = os.environ.get("ASCENDO_GOOGLE_LEGACY_AGENT", _GOOGLE_LEGACY_AGENT)
    max_age = sub.get("history_max_age_h", 6)
    if os.environ.get("ASCENDO_OMAHA_MAX_AGE_H"):
        max_age = os.environ["ASCENDO_OMAHA_MAX_AGE_H"]
    wait_s = sub.get("wait_s", 45)
    if os.environ.get("ASCENDO_OMAHA_WAIT"):
        wait_s = os.environ["ASCENDO_OMAHA_WAIT"]
    return {
        "appid": sub.get("appid", ""),
        "google": "1" if google else "0",
        "updater_bin": bins,
        "updater_log": logs,
        "legacy_agent": legacy,
        "wake_args": list(sub.get("wake_args") or ["--wake-all"]),
        "history_max_age_h": _bounded(max_age, 6.0, 0.0, 24.0 * 30),
        "wait_s": int(_bounded(wait_s, 45, 0, 180)),
        "version_history_url": sub.get("version_history_url") or "",
    }


def history_unparseable(history_text: str, appid: str, installed: list[str],
                        max_age_h: float, now: float | None = None) -> str:
    """Version of the latest in-age history outcome for appid when it cannot be compared
    with the installed build (event version or every installed version unparseable);
    "" otherwise (including when there is no recent event, or it is comparable)."""
    import json as _j
    import time as _t
    now = _t.time() if now is None else now
    proc_ts: dict = {}
    starts: dict = {}
    best = None
    evs = []
    for line in history_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = _j.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        t, b, tok = ev.get("eventType"), ev.get("bound"), ev.get("processToken")
        if t == "UPDATER_PROCESS" and b == "START" and tok:
            try:
                proc_ts[tok] = int(str(ev.get("timestamp", "")))
            except ValueError:
                pass
        elif t == "UPDATE" and b == "START" and tok:
            starts[(tok, str(ev.get("eventId")))] = str(ev.get("appId", ""))
        elif t == "UPDATE" and b == "END" and tok:
            evs.append(ev)
    for ev in evs:
        tok = ev.get("processToken")
        ts = proc_ts.get(tok)
        if starts.get((tok, str(ev.get("eventId")))) != appid or ts is None:
            continue
        states = ev.get("updateStates") or []
        final = states[-1].get("state") if states and isinstance(states[-1], dict) else ""
        if final not in ("NO_UPDATE", "UPDATED"):
            continue
        if best is None or ts >= best[0]:
            best = (ts, str(ev.get("nextVersion", "")))
    if best is None:
        return ""
    age_h = (now - (best[0] / 1_000_000 - 11644473600)) / 3600.0
    if age_h < 0 or age_h > max_age_h:
        return ""
    if version_key(best[1]) is None or not any(version_key(v) is not None for v in installed if v):
        return best[1] or "?"
    return ""


def _read_stdin() -> str:
    try:
        return sys.stdin.read()
    except Exception:
        return ""


def main(argv: list[str]) -> int:
    if not argv:
        return 0
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "history" and len(args) >= 3:
            appid, max_age, installed = args[0], float(args[1]), args[2:]
            chunks = []
            for path in _read_stdin().splitlines():
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        chunks.append(fh.read())
                except OSError:
                    pass
            res = omaha_history_status("\n".join(chunks), appid, installed, max_age)
            if res:
                print("\t".join(res))
        elif cmd == "history-unparseable" and len(args) >= 3:
            chunks = []
            for path in _read_stdin().splitlines():
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        chunks.append(fh.read())
                except OSError:
                    pass
            print(history_unparseable("\n".join(chunks), args[0], args[2:], float(args[1])))
        elif cmd == "last" and args:
            res = omaha_last_status(_read_stdin(), args[0])
            print(res if res else "")
        elif cmd == "recent" and len(args) >= 2:
            res = omaha_recent_status(_read_stdin(), args[0], float(args[1]))
            if res:
                print(f"{res[0]} {res[1]}")
        elif cmd == "vh":
            res = version_history_public(_read_stdin())
            print(res if res else "")
        elif cmd == "cfg" and args:
            app = json.loads(os.environ.get("ASCENDO_WEB_CFG", "") or "{}")
            val = resolve_cfg(app).get(args[0])
            if isinstance(val, list):
                for item in val:
                    print(item)
            elif val is not None:
                print(val)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
