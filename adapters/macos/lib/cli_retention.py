"""lib/python/cli_retention.py — Vendor CLI version retention / pruning."""

from __future__ import annotations

import os
import re
import shutil
from typing import List, Optional, Set, Tuple


CODEX_RELEASE_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-[a-z0-9_]+-apple-darwin$")
CURSOR_AGENT_VERSION_RE = re.compile(r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9a-f]+$")
AGY_OLD_RE = re.compile(r"^agy\.[0-9]+\.old$")


def _protected_version_dirs(root_real: str, link_paths: List[str]) -> Optional[Set[str]]:
    """Top-level version dirs under root that any link resolves into.
    Returns None when a link exists but resolves outside root (caller must then prune nothing)."""
    protected: Set[str] = set()
    for p in link_paths:
        if not (os.path.exists(p) or os.path.islink(p)):
            continue
        target = os.path.realpath(p)
        if not target.startswith(root_real + os.sep):
            return None
        first = os.path.relpath(target, root_real).split(os.sep, 1)[0]
        protected.add(os.path.join(root_real, first))
    return protected


def get_dir_size(path: str) -> int:
    """Calculate recursive directory size in bytes."""
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def prune_codex(home: str) -> Tuple[int, int]:
    """Prune old Codex releases.

    Root: $HOME/.codex/packages/standalone/releases
    Current: realpath of $HOME/.codex/packages/standalone/current
    Candidates: directories matching CODEX_RELEASE_RE.
    Retention: keep protected (current) + 1 newest by mtime, delete rest.
    Returns (pruned_count, freed_bytes).
    """
    root = os.path.join(home, ".codex", "packages", "standalone", "releases")
    if not os.path.isdir(root):
        return 0, 0

    root_real = os.path.realpath(root)
    current_symlink = os.path.join(home, ".codex", "packages", "standalone", "current")
    protected = _protected_version_dirs(root_real, [current_symlink])
    if protected is None:
        return 0, 0

    candidates: List[Tuple[float, str, str]] = []
    try:
        entries = os.listdir(root)
    except OSError:
        return 0, 0

    for name in entries:
        if not CODEX_RELEASE_RE.match(name):
            continue
        full_path = os.path.join(root, name)
        if not os.path.isdir(full_path):
            continue
        cand_real = os.path.realpath(full_path)
        # Safety check: must reside inside root
        if not (cand_real == root_real or cand_real.startswith(root_real + os.sep)):
            continue
        try:
            mtime = os.path.getmtime(full_path)
        except OSError:
            mtime = 0.0
        candidates.append((mtime, full_path, cand_real))

    non_protected: List[Tuple[float, str, str]] = [
        c for c in candidates if c[2] not in protected
    ]
    # Sort newest first by mtime
    non_protected.sort(key=lambda c: c[0], reverse=True)

    # Keep 1 newest previous version
    to_delete = non_protected[1:]

    pruned_count = 0
    freed_bytes = 0
    for _, path_to_del, real_to_del in to_delete:
        if real_to_del in protected:
            continue
        if not (real_to_del == root_real or real_to_del.startswith(root_real + os.sep)):
            continue
        sz = get_dir_size(path_to_del)
        try:
            shutil.rmtree(path_to_del)
            pruned_count += 1
            freed_bytes += sz
        except OSError:
            pass

    return pruned_count, freed_bytes


def prune_cursor_agent(home: str) -> Tuple[int, int]:
    """Prune old cursor-agent versions.

    Root: $HOME/.local/share/cursor-agent/versions
    Protected: symlink targets of $HOME/.local/bin/cursor-agent and $HOME/.local/bin/agent
    Candidates: directories matching CURSOR_AGENT_VERSION_RE.
    Retention: keep protected + 1 newest by mtime, delete rest.
    Returns (pruned_count, freed_bytes).
    """
    root = os.path.join(home, ".local", "share", "cursor-agent", "versions")
    if not os.path.isdir(root):
        return 0, 0

    root_real = os.path.realpath(root)
    bin_paths = [
        os.path.join(home, ".local", "bin", "cursor-agent"),
        os.path.join(home, ".local", "bin", "agent"),
    ]
    protected = _protected_version_dirs(root_real, bin_paths)
    if protected is None:
        return 0, 0

    candidates: List[Tuple[float, str, str]] = []
    try:
        entries = os.listdir(root)
    except OSError:
        return 0, 0

    for name in entries:
        if not CURSOR_AGENT_VERSION_RE.match(name):
            continue
        full_path = os.path.join(root, name)
        if not os.path.isdir(full_path):
            continue
        cand_real = os.path.realpath(full_path)
        if not (cand_real == root_real or cand_real.startswith(root_real + os.sep)):
            continue
        try:
            mtime = os.path.getmtime(full_path)
        except OSError:
            mtime = 0.0
        candidates.append((mtime, full_path, cand_real))

    non_protected: List[Tuple[float, str, str]] = [
        c for c in candidates if c[2] not in protected
    ]
    non_protected.sort(key=lambda c: c[0], reverse=True)

    to_delete = non_protected[1:]

    pruned_count = 0
    freed_bytes = 0
    for _, path_to_del, real_to_del in to_delete:
        if real_to_del in protected:
            continue
        if not (real_to_del == root_real or real_to_del.startswith(root_real + os.sep)):
            continue
        sz = get_dir_size(path_to_del)
        try:
            shutil.rmtree(path_to_del)
            pruned_count += 1
            freed_bytes += sz
        except OSError:
            pass

    return pruned_count, freed_bytes


def prune_agy(home: str, is_working: bool) -> Tuple[int, int]:
    """Prune agy.<digits>.old files in $HOME/.local/bin if agy is working.

    Returns (pruned_count, freed_bytes).
    """
    if not is_working:
        return 0, 0

    local_bin = os.path.join(home, ".local", "bin")
    if not os.path.isdir(local_bin):
        return 0, 0

    try:
        entries = os.listdir(local_bin)
    except OSError:
        return 0, 0

    pruned_count = 0
    freed_bytes = 0
    for name in entries:
        if not AGY_OLD_RE.match(name):
            continue
        file_path = os.path.join(local_bin, name)
        if not os.path.isfile(file_path):
            continue
        try:
            sz = os.path.getsize(file_path)
            os.remove(file_path)
            pruned_count += 1
            freed_bytes += sz
        except OSError:
            pass

    return pruned_count, freed_bytes


def bytes_to_mb(b: int) -> int:
    return max(0, round(b / (1024 * 1024)))


def prune_all(home: str, is_agy_working: bool = True) -> List[Tuple[str, int, int]]:
    """Prune all vendor CLIs and return list of (name, count, freed_mb)."""
    results: List[Tuple[str, int, int]] = []

    c_cnt, c_bytes = prune_codex(home)
    if c_cnt > 0:
        results.append(("Codex", c_cnt, bytes_to_mb(c_bytes)))

    a_cnt, a_bytes = prune_cursor_agent(home)
    if a_cnt > 0:
        results.append(("cursor-agent", a_cnt, bytes_to_mb(a_bytes)))

    g_cnt, g_bytes = prune_agy(home, is_agy_working)
    if g_cnt > 0:
        results.append(("agy", g_cnt, bytes_to_mb(g_bytes)))

    return results
