#!/usr/bin/env python3
"""adapters/macos/lib/brew_casks.py -- pure helpers for Homebrew casks.

Ported from macOS_updates ``lib/python/brew_casks.py`` (v1.5.0), plus the
three-way version compare and the scheme-aware app-vs-package relation that
its downgrade guard needs (``lib/version.sh``: ``internet_version_relation``
and ``app_vs_package_version_relation``), plus ``quit:`` bundle ids so a cask
whose app is running can be recognised without a hand-written table.

Everything here is a pure function over the JSON printed by
``brew info --json=v2 --cask <token>...`` (no I/O except ``bundle_version`` and
the CLI wrapper). Python 3.9 compatible (macOS system python): annotations are
deferred, no ``match`` statements, no ``X | Y`` at runtime.

Bash callers use the CLI:

    brew info --json=v2 --cask a b | brew_casks.py facts
        -> one line per cask:  token|version|installed|targets|quit_ids|sudo
           targets / quit_ids are ';'-joined, sudo is 0/1
    brew info --json=v2 --cask a b | brew_casks.py orphans DIR [DIR...]
        -> orphan tokens, one per line
    brew_casks.py relation INSTALLED_APP_VERSION CASK_VERSION   -> newer|current|unknown
    brew_casks.py cmp A B                                       -> newer|equal|older|unknown
    brew_casks.py bundle-version /path/X.app                    -> CFBundleShortVersionString
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Cask facts
# --------------------------------------------------------------------------


def app_targets(cask: dict) -> List[str]:
    """From artifacts {"app": [...]}; string -> basename; dict -> target basename."""
    targets: List[str] = []
    for art in cask.get("artifacts", []):
        if isinstance(art, dict) and "app" in art:
            items = art["app"]
            if isinstance(items, (str, dict)):
                items = [items]
            for item in items:
                if isinstance(item, str):
                    name = os.path.basename(item)
                    if name:
                        targets.append(name)
                elif isinstance(item, dict) and "target" in item:
                    name = os.path.basename(item["target"])
                    if name:
                        targets.append(name)
    return targets


def _uninstall_entries(art: dict) -> List[dict]:
    entries = art.get("uninstall")
    if entries is None:
        return []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def pkg_app_hints(cask: dict) -> List[str]:
    """Basenames from uninstall[].delete matching ^/Applications/[^/]+\\.app$."""
    hints: List[str] = []
    for art in cask.get("artifacts", []):
        if not isinstance(art, dict):
            continue
        for entry in _uninstall_entries(art):
            deletes = entry.get("delete", [])
            if isinstance(deletes, str):
                deletes = [deletes]
            if isinstance(deletes, list):
                for d in deletes:
                    if isinstance(d, str) and re.match(r"^/Applications/[^/]+\.app$", d):
                        name = os.path.basename(d)
                        if name and name not in hints:
                            hints.append(name)
    return hints


def quit_bundle_ids(cask: dict) -> List[str]:
    """Bundle ids from ``quit:`` in the ``uninstall`` and ``zap`` stanzas.

    Homebrew quits these apps before an uninstall/upgrade, so they are the
    cask's own statement of "these processes are the app". Used to detect a
    running app for casks whose ``.app`` target is not otherwise known.
    """
    ids: List[str] = []
    for art in cask.get("artifacts", []):
        if not isinstance(art, dict):
            continue
        for key in ("uninstall", "zap"):
            entries = art.get(key)
            if entries is None:
                continue
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                quits = entry.get("quit", [])
                if isinstance(quits, str):
                    quits = [quits]
                if isinstance(quits, list):
                    for q in quits:
                        if isinstance(q, str) and re.match(r"^[A-Za-z0-9._-]+$", q) and q not in ids:
                            ids.append(q)
    return ids


def cask_requires_sudo(cask: dict) -> bool:
    """Artifacts contain key 'pkg' or 'installer', or uninstall stanza contains 'pkgutil', 'launchctl' or 'kext'."""
    for art in cask.get("artifacts", []):
        if not isinstance(art, dict):
            continue
        if "pkg" in art or "installer" in art:
            return True
        for entry in _uninstall_entries(art):
            for k in ("pkgutil", "launchctl", "kext"):
                if k in entry:
                    return True
    return False


def cask_guard_facts(text: str) -> str:
    """'version|installed|A.app;B.app' from `brew info --json=v2 --cask <t>`; '' on any parse error."""
    try:
        cask = json.loads(text)["casks"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
    version = str(cask.get("version") or "")
    installed = str(cask.get("installed") or "")
    targets = app_targets(cask) or pkg_app_hints(cask)
    return "|".join((version, installed, ";".join(targets)))


def cask_primary_app(text: str) -> str:
    """First app target name ('X.app') or ''."""
    try:
        targets = app_targets(json.loads(text)["casks"][0])
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
    return targets[0] if targets else ""


def find_orphan_casks(
    info_json: dict,
    app_dirs: List[str],
    exists: Callable[[str], bool] = os.path.exists,
) -> List[str]:
    """Cask has >= 1 app target and NONE exist in any of app_dirs.

    ``uninstall.delete`` hints are deliberately not used here: a pkg cask
    with no ``app`` artifact is never called an orphan.
    """
    orphans: List[str] = []
    for cask in info_json.get("casks", []):
        targets = app_targets(cask)
        if not targets:
            continue
        found = False
        for target in targets:
            for app_dir in app_dirs:
                if exists(os.path.join(app_dir, target)):
                    found = True
                    break
            if found:
                break
        if not found:
            token = cask.get("token")
            if token:
                orphans.append(token)
    return orphans


def cask_facts_lines(info_json: dict) -> List[str]:
    """One ``token|version|installed|targets|quit_ids|sudo`` line per cask.

    ``targets`` are the real ``app`` artifact names, falling back to the
    pkg-cask ``uninstall.delete`` hints (same rule as ``cask_guard_facts``).
    """
    lines: List[str] = []
    for cask in info_json.get("casks", []):
        token = cask.get("token")
        if not token:
            continue
        targets = app_targets(cask) or pkg_app_hints(cask)
        lines.append(
            "|".join(
                (
                    str(token),
                    str(cask.get("version") or ""),
                    str(cask.get("installed") or ""),
                    ";".join(targets),
                    ";".join(quit_bundle_ids(cask)),
                    "1" if cask_requires_sudo(cask) else "0",
                )
            )
        )
    return lines


# --------------------------------------------------------------------------
# Version comparison
# --------------------------------------------------------------------------

_PRERELEASE_RANKS = (("dev", 0), ("alpha", 1), ("a", 1), ("beta", 2), ("b", 2), ("rc", 3))


def _version_key(value: Optional[str]) -> Optional[tuple]:
    match = re.search(r"\d+(?:\.\d+)*", value or "")
    if not match:
        return None
    numbers = [int(part) for part in match.group(0).split(".")]
    numbers = (numbers + [0] * 12)[:12]
    suffix = (value[match.end():] or "").lower().split("+", 1)[0]
    prerelease = suffix.lstrip("-._")
    prerelease_rank = 4
    for marker, rank in _PRERELEASE_RANKS:
        if prerelease.startswith(marker):
            prerelease_rank = rank
            break
    suffix_numbers = [int(part) for part in re.findall(r"\d+", suffix)]
    suffix_numbers = (suffix_numbers + [0] * 4)[:4]
    return tuple(numbers + [prerelease_rank] + suffix_numbers)


def version_compare(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """Three-way compare: 1 (a > b), 0 (equal), -1 (a < b), None (unparseable)."""
    left, right = _version_key(a), _version_key(b)
    if left is None or right is None:
        return None
    return (left > right) - (left < right)


def version_relation(remote: Optional[str], local: Optional[str]) -> str:
    """'newer' only when ``remote`` is provably greater than ``local``.

    'current' covers equality and a local version ahead of the remote;
    'unknown' when either side is unparseable (never invent a difference).
    """
    cmp = version_compare(remote, local)
    if cmp is None:
        return "unknown"
    return "newer" if cmp > 0 else "current"


def _segments(value: Optional[str]) -> Optional[List[int]]:
    match = re.search(r"\d+(?:\.\d+)*", value or "")
    if not match:
        return None
    parts = [int(p) for p in match.group(0).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return parts


def _leading_run(a: List[int], b: List[int], oa: int, ob: int) -> int:
    run = 0
    while oa + run < len(a) and ob + run < len(b) and a[oa + run] == b[ob + run]:
        run += 1
    return run


def _prefix_offset(a: List[int], b: List[int]) -> Optional[Tuple[int, int]]:
    """Offsets at which one version carries an extra leading vendor segment.

    Only accepted when the shifted alignment agrees on strictly more leading
    segments than the natural offset does, so ordinary same-scheme pairs such
    as 4.15 and 4.17.1 are never realigned.
    """
    natural = _leading_run(a, b, 0, 0)
    best = None
    for oa in range(len(a)):
        for ob in range(len(b)):
            if oa == 0 and ob == 0:
                continue
            run = _leading_run(a, b, oa, ob)
            if run >= 2 and run > natural:
                candidate = (run, -(oa + ob), oa, ob)
                if best is None or candidate > best:
                    best = candidate
    return (best[2], best[3]) if best else None


def _padded(values: List[int], width: int) -> tuple:
    return tuple((values + [0] * width)[:width])


def app_vs_package_version_relation(installed: Optional[str], candidate: Optional[str]) -> str:
    """Compare an installed bundle version against a package manager's version.

    Homebrew casks and the apps they ship do not always agree on the shape of
    a version: Brave's cask is "1.93.138.0" while the bundle reports
    "151.1.93.138" (the Chromium major prefixed to the same Brave version). A
    plain numeric comparison read 151 > 1 and skipped brave-browser forever.

    Strips trailing zero segments, then looks for the alignment offset at which
    the two segment lists share the longest run of equal leading values (a run
    of two or more = same scheme with a vendor prefix) and compares the aligned
    tails. Without such a run and with different shapes the versions are not
    comparable: 'unknown', so the caller defers to Homebrew's own record.

    Returns 'newer' (installed provably ahead), 'current' (equal or behind) or
    'unknown'.
    """
    inst = _segments(installed)
    cand = _segments(candidate)
    if inst is None or cand is None:
        return "unknown"
    if inst == cand:
        return "current"
    offsets = _prefix_offset(inst, cand)
    if offsets is not None:
        inst = inst[offsets[0]:]
        cand = cand[offsets[1]:]
    elif _leading_run(inst, cand, 0, 0) == 0 and len(inst) != len(cand):
        return "unknown"
    width = max(len(inst), len(cand))
    return "newer" if _padded(inst, width) > _padded(cand, width) else "current"


# --------------------------------------------------------------------------
# Bundle version (the only filesystem read)
# --------------------------------------------------------------------------


def bundle_version(app_path: str) -> str:
    """CFBundleShortVersionString (else CFBundleVersion) of an .app, '' if unreadable."""
    import plistlib

    plist = os.path.join(app_path, "Contents", "Info.plist")
    try:
        with open(plist, "rb") as fh:
            data = plistlib.load(fh)
    except Exception:  # noqa: BLE001 - unreadable/absent plist -> unknown
        return ""
    return str(data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or "")


# --------------------------------------------------------------------------
# CLI (called from adapters/macos/lib/ascendo_brew.sh)
# --------------------------------------------------------------------------


def _load_stdin() -> Dict:
    try:
        data = json.loads(sys.stdin.read())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: brew_casks.py facts|orphans|relation|cmp|bundle-version ...\n")
        return 2
    cmd = argv[1]
    if cmd == "facts":
        for line in cask_facts_lines(_load_stdin()):
            print(line)
        return 0
    if cmd == "orphans":
        for token in find_orphan_casks(_load_stdin(), argv[2:]):
            print(token)
        return 0
    if cmd == "relation" and len(argv) == 4:
        print(app_vs_package_version_relation(argv[2], argv[3]))
        return 0
    if cmd == "cmp" and len(argv) == 4:
        result = version_compare(argv[2], argv[3])
        print({None: "unknown", -1: "older", 0: "equal", 1: "newer"}[result])
        return 0
    if cmd == "bundle-version" and len(argv) == 3:
        print(bundle_version(argv[2]))
        return 0
    sys.stderr.write("brew_casks.py: bad arguments\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
