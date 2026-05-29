"""W11: Python-based version comparison to replace ``sort -V`` in shell scripts.

Shell scripts currently use ``printf '%s\\n' "$a" "$b" | sort -V | tail -1``
to compare versions. This works on GNU coreutils but has two problems:

1. macOS ``sort -V`` was only added in 10.13; older BSD sort lacks it.
2. ``sort -V`` is lexicographic-with-version-awareness — it doesn't handle
   pre-release tags (``1.0.0a1 < 1.0.0``) or epoch prefixes the way pip
   or npm do.

This module provides a Python-native comparison that:

- Uses ``packaging.version.Version`` (PEP-440) when both strings parse
- Falls back to a dotted-integer comparison when they don't
- Never raises — returns ``False`` on incomparable garbage

The shell scripts can call this via ``python3 -c "from ascendo.utils.version
import version_gt; print(version_gt('$a', '$b'))"`` or the orchestrator
can invoke it directly for dashboard-side comparisons.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Sequence


def version_gt(a: str, b: str) -> bool:
    """Return True if version string ``a`` is strictly greater than ``b``.

    Strategy (in order):

    1. Try ``packaging.version.Version`` (PEP-440). Handles semver,
       pre-release, post-release, dev releases, epochs.
    2. Fall back to dotted-integer comparison. Each segment is compared
       as an integer; missing trailing segments default to 0.
    3. If both fail, compare as plain strings (deterministic but
       semantically meaningless — caller should fix their data).

    Never raises.
    """
    if not a or not b:
        return False

    # Strategy 1: PEP-440
    try:
        from packaging.version import Version  # type: ignore[import-untyped]

        va = Version(a)
        vb = Version(b)
        return va > vb
    except Exception:  # noqa: BLE001 — packaging may not be installed, or strings may not be PEP-440
        pass

    # Strategy 2: dotted-integer comparison
    try:
        sa = _parse_dotted(a)
        sb = _parse_dotted(b)
        if sa is not None and sb is not None:
            return _compare_segments(sa, sb) > 0
    except Exception:  # noqa: BLE001
        pass

    # Strategy 3: string fallback
    return a > b


def version_gte(a: str, b: str) -> bool:
    """Return True if ``a >= b``."""
    return a == b or version_gt(a, b)


def version_lt(a: str, b: str) -> bool:
    """Return True if ``a < b``."""
    return version_gt(b, a)


# ── Internals ────────────────────────────────────────────────────────

_DOTTED_RE = re.compile(r"^[\d]+(?:\.[\d]+)*$")


def _parse_dotted(s: str) -> tuple[int, ...] | None:
    """Parse a dotted-integer string like '1.2.3' into a tuple of ints.

    Returns None if the string doesn't match the pattern.
    """
    # Strip common prefixes
    cleaned = s.strip()
    if cleaned.startswith("v") or cleaned.startswith("V"):
        cleaned = cleaned[1:]
    if not _DOTTED_RE.match(cleaned):
        return None
    return tuple(int(x) for x in cleaned.split("."))


def _compare_segments(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    """Compare two version tuples segment-by-segment.

    Returns >0 if a>b, <0 if a<b, 0 if equal.
    Missing trailing segments are treated as 0.
    """
    max_len = max(len(a), len(b))
    for i in range(max_len):
        sa = a[i] if i < len(a) else 0
        sb = b[i] if i < len(b) else 0
        if sa != sb:
            return sa - sb
    return 0
