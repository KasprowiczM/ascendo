#!/usr/bin/env python3
"""How long has an app's installed version been unchanged? (verify phase, F7)

State file: ``slug<TAB>version<TAB>first_seen_epoch`` -- one line per slug, the
version currently installed and the first time the verify phase saw it. A
different version resets the clock. Used to bound the chronic "Tier-B launch
did nothing" warning: only an app unchanged for longer than
``ASCENDO_WEB_STALE_DAYS`` (default 45) escalates to ``failed``.

    web_seen.py touch FILE SLUG VERSION [NOW_EPOCH]   -> prints whole days unchanged

Stdlib only; never raises on a damaged state file (it is rebuilt).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time


def _read(path: str) -> dict[str, tuple[str, int]]:
    rows: dict[str, tuple[str, int]] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3 and parts[2].isdigit():
                    rows[parts[0]] = (parts[1], int(parts[2]))
    except OSError:
        pass
    return rows


def touch(path: str, slug: str, version: str, now: int) -> int:
    rows = _read(path)
    cur = rows.get(slug)
    if cur is None or cur[0] != version:
        rows[slug] = (version, now)
        days = 0
    else:
        days = max(0, (now - cur[1]) // 86400)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".seen.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for s in sorted(rows):
                fh.write(f"{s}\t{rows[s][0]}\t{rows[s][1]}\n")
        os.replace(tmp, path)
    except OSError:
        pass
    return days


def main(argv: list[str]) -> int:
    if len(argv) < 5 or argv[1] != "touch":
        print("usage: web_seen.py touch FILE SLUG VERSION [NOW_EPOCH]", file=sys.stderr)
        return 2
    now = int(argv[5]) if len(argv) > 5 and argv[5].isdigit() else int(time.time())
    print(touch(argv[2], argv[3], argv[4], now))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
