#!/usr/bin/env python3
"""Verify EN ↔ PL key parity in app/frontend/i18n.js.

Run standalone:

    python3 scripts/check-i18n-parity.py

Exits 0 when both locales declare the exact same key paths, 1 otherwise.
Used by tests/contract/test_i18n_parity.py and validate-*.sh Stage 14.

Uses node(1) to evaluate the JS object literal directly rather than re-
implementing a JS parser. That's the same execution path the SPA itself
follows, so we can't drift from runtime semantics.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


_FRONTEND = Path(__file__).resolve().parent.parent / "app" / "frontend"
# Sesja 78: locale DATA is split into i18n.en.js + i18n.pl.js (i18n.js
# is now loader/helpers only). Eval both data files to rebuild I18N.
I18N_DATA_PATHS = (_FRONTEND / "i18n.en.js", _FRONTEND / "i18n.pl.js")


def _flatten(obj: dict, prefix: str = "") -> set[str]:
    """Return every dotted leaf path under ``obj``."""
    out: set[str] = set()
    for k, v in obj.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out |= _flatten(v, f"{path}.")
        else:
            out.add(path)
    return out


def _load_via_node() -> dict:
    """Spawn node to evaluate the locale data files + dump I18N as JSON."""
    node = shutil.which("node")
    if not node:
        raise SystemExit("node binary not on PATH; install Node ≥ 18 to run parity check")
    requires = "".join(
        f"require({json.dumps(str(p))});" for p in I18N_DATA_PATHS
    )
    script = (
        "const window={};"
        f"{requires}"
        "process.stdout.write(JSON.stringify(window.I18N));"
    )
    r = subprocess.run(
        [node, "-e", script],
        check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return json.loads(r.stdout)


def parity_check() -> tuple[set[str], set[str], int, int]:
    data = _load_via_node()
    if "en" not in data or "pl" not in data:
        raise SystemExit("expected window.I18N to contain en + pl objects")
    en = _flatten(data["en"])
    pl = _flatten(data["pl"])
    return en - pl, pl - en, len(en), len(pl)


def main() -> int:
    only_en, only_pl, n_en, n_pl = parity_check()
    if not only_en and not only_pl:
        print(f"i18n parity OK ({n_en} EN keys == {n_pl} PL keys)")
        return 0
    if only_en:
        print(f"keys present in EN but missing in PL ({len(only_en)}):")
        for k in sorted(only_en):
            print(f"  - {k}")
    if only_pl:
        print(f"keys present in PL but missing in EN ({len(only_pl)}):")
        for k in sorted(only_pl):
            print(f"  - {k}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
