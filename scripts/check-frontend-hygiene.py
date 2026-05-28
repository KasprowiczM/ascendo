#!/usr/bin/env python3
"""Frontend hygiene guard for the Ascendo SPA.

Catches the bug classes this project has actually shipped before:

  1. i18n key referenced by ``data-i18n*`` (HTML) or ``tr("…")`` (JS)
     but missing from the EN and/or PL locale — the user then sees the
     raw ``foo.bar`` key. (HARD FAIL.)
  2. EN ↔ PL parity (delegates to the same node-eval the SPA uses).
     (HARD FAIL.)
  3. Hardcoded hex colours in style.css / components.css that aren't a
     ``--token:`` definition — they don't adapt to the light theme.
     (REPORTED as a warning; some are legitimate always-dark terminal
     surfaces, so this never fails the build — it's a review aid.)
  4. Defined-but-never-referenced i18n keys (INFO count only — many
     keys are resolved dynamically, so this is not actionable noise).

Run:  python3 scripts/check-frontend-hygiene.py
Exit: 0 clean · 1 a hard-fail category tripped.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "app" / "frontend"
# Sesja 78: locale data split out of i18n.js into per-locale files.
I18N_DATA_PATHS = (FRONTEND / "i18n.en.js", FRONTEND / "i18n.pl.js")
HTML_FILES = [FRONTEND / "index.html"]
JS_FILES = [
    FRONTEND / "app.js",
    FRONTEND / "shell.js",
    FRONTEND / "components.js",
    FRONTEND / "ui-components.js",
    FRONTEND / "platform.js",
]
CSS_FILES = [FRONTEND / "style.css", FRONTEND / "components.css"]

DATA_I18N_RE = re.compile(r'data-i18n(?:-title|-placeholder|-aria-label)?="([^"]+)"')
TR_CALL_RE = re.compile(r'\btr\(\s*["\'`]([^"\'`]+)["\'`]')
HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
TOKEN_DEF_RE = re.compile(r"^\s*--[\w-]+\s*:")


def _flatten(obj: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in obj.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out |= _flatten(v, f"{path}.")
        else:
            out.add(path)
    return out


def _load_i18n() -> dict:
    node = shutil.which("node")
    if not node:
        raise SystemExit("node binary not on PATH; install Node >= 18")
    requires = "".join(
        f"require({json.dumps(str(p))});" for p in I18N_DATA_PATHS
    )
    script = (
        "const window={};"
        f"{requires}"
        "process.stdout.write(JSON.stringify(window.I18N));"
    )
    r = subprocess.run(
        [node, "-e", script], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return json.loads(r.stdout)


def _referenced_keys() -> set[str]:
    keys: set[str] = set()
    for f in HTML_FILES:
        if f.exists():
            keys |= set(DATA_I18N_RE.findall(f.read_text(encoding="utf-8")))
    for f in JS_FILES:
        if f.exists():
            keys |= set(TR_CALL_RE.findall(f.read_text(encoding="utf-8")))
    # Drop dynamically-built keys: trailing "." from tr("a."+x), template
    # literals with ${…} interpolation, and any non-ASCII fragment — these
    # are resolved at runtime and can't be statically verified here.
    return {
        k for k in keys
        if k and not k.endswith(".") and "${" not in k and k.isascii()
    }


def _hardcoded_hex() -> list[str]:
    hits: list[str] = []
    for f in CSS_FILES:
        if not f.exists():
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if TOKEN_DEF_RE.match(line):
                continue  # a token *definition* may legitimately hold a hex
            if HEX_RE.search(line):
                hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    return hits


def main() -> int:
    data = _load_i18n()
    if "en" not in data or "pl" not in data:
        raise SystemExit("window.I18N must contain en + pl")
    en = _flatten(data["en"])
    pl = _flatten(data["pl"])
    refs = _referenced_keys()

    fail = False

    # (2) parity
    only_en, only_pl = en - pl, pl - en
    if only_en or only_pl:
        fail = True
        print(f"[FAIL] EN/PL parity off (EN-only {len(only_en)}, PL-only {len(only_pl)})")
        for k in sorted(only_en)[:20]:
            print(f"  EN-only: {k}")
        for k in sorted(only_pl)[:20]:
            print(f"  PL-only: {k}")
    else:
        print(f"[OK]   i18n parity ({len(en)} EN == {len(pl)} PL)")

    # (1) referenced-but-missing (the raw-key-to-user bug)
    missing_en = sorted(k for k in refs if k not in en)
    missing_pl = sorted(k for k in refs if k not in pl)
    if missing_en:
        fail = True
        print(f"[FAIL] {len(missing_en)} referenced key(s) missing from EN:")
        for k in missing_en[:30]:
            print(f"  - {k}")
    if missing_pl:
        fail = True
        print(f"[FAIL] {len(missing_pl)} referenced key(s) missing from PL:")
        for k in missing_pl[:30]:
            print(f"  - {k}")
    if not missing_en and not missing_pl:
        print(f"[OK]   {len(refs)} referenced i18n keys all defined in EN+PL")

    # (4) defined-but-unused — INFO only (dynamic resolution is common)
    unused = en - refs
    print(f"[INFO] {len(unused)} EN keys not statically referenced "
          f"(dynamic/partial — not actionable on its own)")

    # (3) hardcoded hex — WARN only (some are legit always-dark surfaces)
    hex_hits = _hardcoded_hex()
    print(f"[WARN] {len(hex_hits)} hardcoded hex line(s) in style/components "
          f"CSS (review for light-theme adaptation; terminal surfaces OK)")
    for h in hex_hits[:15]:
        print(f"  {h}")
    if len(hex_hits) > 15:
        print(f"  … +{len(hex_hits) - 15} more")

    print("\n" + ("HYGIENE: FAIL" if fail else "HYGIENE: PASS"))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
