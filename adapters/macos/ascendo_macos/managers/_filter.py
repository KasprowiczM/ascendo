"""Shared ``--filter`` argv helpers for the macOS managers.

The orchestrator hands every manager the identifiers *that category* matches
on (see ``ascendo.orchestrator.runner._filter_for``). A manager must forward
``--filter`` only to phase scripts that actually act on it:

* brew / mas: check, plan, apply
* softwareupdate: apply only (labels; app names are never labels)

Scripts additionally tolerate (ignore) a stray ``--filter`` as defence in
depth, but the manager must not rely on that.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from ascendo.models.run import Phase

_PHASE_BY_STEM = {p.value: p for p in Phase}

# softwareupdate labels look like ``Safari17.4-17.4`` or
# ``macOS Sequoia 15.1-24B83`` (letters/digits/space/._- then ``-<version>``).
SU_LABEL_RE = re.compile(r"^[A-Za-z0-9 ._-]+-[0-9A-Za-z.]+$")


def clean_filter(item_filter: Iterable[str] | None) -> list[str]:
    """Strip/blank-drop a filter iterable. ``None``/empty -> ``[]``."""
    if item_filter is None:
        return []
    return [s.strip() for s in item_filter if s and isinstance(s, str) and s.strip()]


def phase_of(phase: Phase | None, script_path: Path) -> Phase | None:
    """The phase being run (explicit, else inferred from ``<phase>.sh``)."""
    if phase is not None:
        return phase
    return _PHASE_BY_STEM.get(Path(script_path).stem)


def softwareupdate_filter_tokens(cleaned: list[str]) -> list[str]:
    """Label-shaped tokens only; when none is label-shaped keep the raw
    tokens so the script selects nothing (exact-match against pending
    labels) instead of silently degrading to "install everything"."""
    labels = [t for t in cleaned if SU_LABEL_RE.match(t)]
    return labels or cleaned
