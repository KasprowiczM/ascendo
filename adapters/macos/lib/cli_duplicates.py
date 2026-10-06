"""Native-install proof for vendor CLIs that also ship as npm packages.

Ported from macOS_updates lib/python/cli_duplicates.py (v1.5.4). Task 8 needs
only ``supported_native_path``; the duplicate planner (npm_duplicates,
removal_allowed, known_npm_prefixes) is Task 8b and extends this module.

Stdlib only; bash calls it with ``PYTHONPATH=<this dir> python3 -c``.
"""
from __future__ import annotations

from pathlib import Path


def supported_native_path(home: str, command: str, executable: str) -> bool:
    """True only when ``executable`` resolves to a regular file inside the
    vendor's own install root for ``command`` (and not under node_modules).

    An npm wrapper symlinked into ``~/.local/bin``, a Homebrew binary, an
    arbitrary user file, a dangling link, or a command we have no root for are
    all NOT native.
    """
    roots = {
        "claude": Path(home) / ".local/share/claude/versions",
        "codex": Path(home) / ".codex/packages/standalone/releases",
        "opencode": Path(home) / ".opencode/bin",
    }
    root = roots.get(command)
    if root is None:
        return False
    resolved = Path(executable).resolve()
    return (
        resolved.is_file()
        and root.resolve() in resolved.parents
        and "node_modules" not in resolved.parts
    )
