"""Native-install proof and conservative duplicate discovery for vendor CLIs
(claude / codex / opencode) that also ship as npm packages or Homebrew copies.

Ported from macOS_updates lib/python/cli_duplicates.py (v1.5.4): Task 8 took
``supported_native_path``; Task 8b adds the planner (``known_npm_prefixes``,
``npm_duplicates``, ``removal_allowed``) plus a small command line used by the
npm phase scripts. Differences from upstream: the version compare is Ascendo's
``brew_casks.version_compare`` (no ``vendor_feeds``), and Homebrew copies are
only ever *reported* (``brew_copies``), never removable.

Stdlib only. Bash calls it as ``python3 cli_duplicates.py <mode> ...``:

  list HOME TOOLCHAIN BREW_PREFIXES COMMAND NATIVE_PATH NATIVE_VERSION
      ``prefix package version removable`` per npm duplicate (removable is
      ``1`` only when ``removal_allowed`` holds against the current process
      list; the native path must pass ``supported_native_path``).
  removal-check COMMAND NATIVE_VERSION PACKAGE_VERSION
      exit 0 when ``removal_allowed`` holds right now (fresh process list).
  brew BREW_DIRS COMMAND
      ``kind name version`` per Homebrew-owned copy (report only).

Records are one per line, fields joined by the ASCII unit separator (0x1f) so
an empty field (unknown version) cannot collapse in bash ``read``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:  # same directory; on sys.path when run as a script / via PYTHONPATH
    from brew_casks import version_compare
except ImportError:  # pragma: no cover - only when imported from elsewhere
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from brew_casks import version_compare  # type: ignore

US = "\x1f"

NATIVE_NPM_PACKAGES = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "opencode": "opencode-ai",
}


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


def known_npm_prefixes(home: str, toolchain: str, brew_prefix: str = "") -> list[Path]:
    """Inspect known package roots; never walk arbitrary user directories.

    ``brew_prefix`` may be a single prefix or a colon-separated list.
    """
    roots = [Path(toolchain) / "npm-global", Path(toolchain) / "node"]
    roots.extend(sorted((Path(home) / ".nvm/versions/node").glob("v*")))
    roots.extend(Path(p) for p in brew_prefix.split(":") if p)
    return list(dict.fromkeys(roots))


def npm_duplicates(prefixes: list[Path], command: str) -> list[tuple[Path, str]]:
    package = NATIVE_NPM_PACKAGES.get(command)
    if not package:
        return []
    result = []
    for prefix in prefixes:
        manifest = prefix / "lib/node_modules" / package / "package.json"
        try:
            # npm cleanup must not follow a package or node_modules symlink
            # into a different user-owned tree.
            if manifest.resolve().parent != prefix.resolve() / "lib/node_modules" / package:
                continue
            data = json.loads(manifest.read_text())
            if data.get("name") == package:
                result.append((prefix, str(data.get("version", ""))))
        except (OSError, ValueError):
            continue
    return result


def removal_allowed(native_version: str, package_version: str,
                    process_listing: str | None, command: str) -> bool:
    """Uncertain versions/process state retain the fallback install."""
    relation = version_compare(native_version, package_version)
    if relation is None or relation < 0 or process_listing is None:
        return False
    # Match command/path tokens, not substrings (Codex desktop helpers do not
    # own npm CLI packages). Also catches npm's node-hosted wrapper scripts.
    package = NATIVE_NPM_PACKAGES.get(command, "")
    for line in process_listing.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        executable = Path(tokens[0]).name
        if executable in (command, command + ".exe"):
            return False
        # Shell -c arguments can mention a command without running it. Only
        # actual interpreter script arguments prove ownership of npm files.
        if executable in ("node", "bun", "npm"):
            # Runtime flags can precede the wrapper (node --flag /path/bin).
            if any(Path(token).name in (command, command + ".exe")
                   or (package and f"node_modules/{package}/" in token)
                   for token in tokens[1:]):
                return False
        if executable in ("bash", "sh", "zsh") and "-c" not in tokens[1:]:
            scripts = [token for token in tokens[1:] if not token.startswith("-")]
            if scripts and (Path(scripts[0]).name in (command, command + ".exe")
                            or (package and f"node_modules/{package}/" in scripts[0])):
                return False
    return True


def process_listing(timeout: int = 10) -> str | None:
    """``ps -axo command=`` output, or None when it cannot be read (which
    ``removal_allowed`` treats as "retain the fallback")."""
    try:
        probe = subprocess.run(["ps", "-axo", "command="], capture_output=True,
                               text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return probe.stdout if probe.returncode == 0 else None


def brew_copies(brew_dirs: list[str], command: str) -> list[tuple[str, str, str]]:
    """Homebrew-owned copies of ``command``: ``(kind, name, version)``.

    ``kind`` is ``formula`` or ``cask``; name/version come from the Cellar /
    Caskroom component of the RESOLVED path. A path that does not really resolve
    into a keg is not claimed, and neither is anything inside a ``node_modules``
    tree: ``/opt/homebrew/bin/claude -> lib/node_modules/.../cli.js`` is an npm
    global under the brew prefix (found by ``npm_duplicates``), not a formula.
    Nothing is executed. Homebrew copies are never removable by Ascendo.
    """
    found: list[tuple[str, str, str]] = []
    for directory in brew_dirs:
        if not directory:
            continue
        candidate = Path(directory) / command
        if not (candidate.exists() or candidate.is_symlink()):
            continue
        parts = Path(os.path.realpath(candidate)).parts
        if "node_modules" in parts:
            continue
        entry: tuple[str, str, str] | None = None
        for marker, kind in (("Cellar", "formula"), ("Caskroom", "cask")):
            if marker in parts:
                idx = parts.index(marker)
                if idx + 1 < len(parts):
                    version = parts[idx + 2] if idx + 2 < len(parts) else ""
                    entry = (kind, parts[idx + 1], version)
                break
        if entry is not None and entry not in found:
            found.append(entry)
    return found


def _list(argv: list[str]) -> int:
    home, toolchain, brew, command, native, version = argv
    if not supported_native_path(home, command, native):
        return 0
    processes = process_listing()
    package = NATIVE_NPM_PACKAGES.get(command)
    for prefix, previous in npm_duplicates(known_npm_prefixes(home, toolchain, brew), command):
        package_root = prefix / "lib/node_modules" / str(package)
        # A symlinked npm wrapper in ~/.local/bin is not a native install.
        if os.path.realpath(native).startswith(str(package_root.resolve()) + os.sep):
            continue
        allowed = removal_allowed(version, previous, processes, command)
        print(US.join((str(prefix), str(package), previous, "1" if allowed else "0")))
    return 0


def _removal_check(argv: list[str]) -> int:
    command, native_version, package_version = argv
    return 0 if removal_allowed(native_version, package_version,
                                process_listing(), command) else 1


def _brew(argv: list[str]) -> int:
    dirs, command = argv
    for kind, name, version in brew_copies(dirs.split(":"), command):
        print(US.join((kind, name, version)))
    return 0


def main(argv: list[str]) -> int:
    modes = {"list": (_list, 6), "removal-check": (_removal_check, 3), "brew": (_brew, 2)}
    if not argv or argv[0] not in modes or len(argv) - 1 != modes[argv[0]][1]:
        print("usage: cli_duplicates.py list|removal-check|brew ...", file=sys.stderr)
        return 2
    return modes[argv[0]][0](argv[1:])


if __name__ == "__main__":  # pragma: no cover - exercised through bash
    sys.exit(main(sys.argv[1:]))
