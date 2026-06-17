"""Version comparison that understands PEP 440 / SemVer prereleases.

Ascendo versions look like ``1.0.0b1`` (PEP 440 beta) or ``1.2.3``.
We must compare these correctly so the updater never offers a "newer"
version that is actually an older prerelease, and so ``1.0.0`` is
recognised as newer than ``1.0.0b1``.

Prefer the ``packaging`` library when present (it ships with pip and is a
transitive dep of the FastAPI/pydantic stack); fall back to a compact,
dependency-free parser that covers the formats Ascendo actually uses.
"""
from __future__ import annotations

import re

__all__ = ["parse", "compare", "is_newer", "normalize"]


def normalize(v: str) -> str:
    """Strip a leading ``v`` and surrounding whitespace. ``v1.2.3`` -> ``1.2.3``."""
    v = (v or "").strip()
    if v[:1] in {"v", "V"}:
        v = v[1:]
    return v


# Stage ranking: a release with no prerelease tag is the highest.
_STAGE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2, "c": 2, "": 3}

_RE = re.compile(
    r"^(?P<rel>\d+(?:\.\d+)*)"
    r"(?:[-_.]?(?P<stage>a|b|c|rc|alpha|beta)[-_.]?(?P<pre>\d+)?)?"
    r"(?:[-_.]?(?:post|dev)[-_.]?\d+)?$",
    re.IGNORECASE,
)


def _parse_fallback(v: str) -> tuple[tuple[int, ...], int, int]:
    """Return ``(release_tuple, stage_rank, pre_number)`` for comparison."""
    v = normalize(v)
    m = _RE.match(v)
    if not m:
        # Unparseable: treat as a very old release so we never *offer* it
        # as an update and never block on it.
        return ((0,), 3, 0)
    rel = tuple(int(p) for p in m.group("rel").split("."))
    stage = (m.group("stage") or "").lower()
    stage_rank = _STAGE_RANK.get(stage, 3)
    pre = int(m.group("pre")) if m.group("pre") else 0
    return (rel, stage_rank, pre)


def parse(v: str):
    """Return an opaque, comparable version object."""
    try:
        from packaging.version import InvalidVersion, Version

        try:
            return ("pkg", Version(normalize(v)))
        except InvalidVersion:
            return ("fallback", _parse_fallback(v))
    except Exception:  # noqa: BLE001 — packaging absent
        return ("fallback", _parse_fallback(v))


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    n = max(len(a), len(b))
    return (a + (0,) * (n - len(a)), b + (0,) * (n - len(b)))


def compare(a: str, b: str) -> int:
    """Return -1 if ``a < b``, 0 if equal, 1 if ``a > b``."""
    ka, va = parse(a)
    kb, vb = parse(b)
    if ka == "pkg" and kb == "pkg":
        return (va > vb) - (va < vb)
    # Fallback comparison (coerce either side if mixed).
    fa = va if ka == "fallback" else _parse_fallback(a)
    fb = vb if kb == "fallback" else _parse_fallback(b)
    rel_a, stage_a, pre_a = fa
    rel_b, stage_b, pre_b = fb
    rel_a, rel_b = _pad(rel_a, rel_b)
    for x, y in zip(rel_a, rel_b):
        if x != y:
            return 1 if x > y else -1
    if stage_a != stage_b:
        return 1 if stage_a > stage_b else -1
    if pre_a != pre_b:
        return 1 if pre_a > pre_b else -1
    return 0


def is_newer(candidate: str, current: str) -> bool:
    """True if ``candidate`` is strictly newer than ``current``."""
    return compare(candidate, current) > 0
