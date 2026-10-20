#!/usr/bin/env bash
# =============================================================================
# bin/check-version-lockstep.sh -- Local lockstep verification guard
# =============================================================================
# Compares core/ascendo/__version__.py with ../macOS_updates/VERSION when present.
# Returns:
#   0 - versions match (or check skipped because sibling repo is absent)
#   1 - version mismatch
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ASCENDO_VERSION_FILE="$REPO_ROOT/core/ascendo/__version__.py"
SIBLING_DIR="${MACOS_UPDATES_DIR:-"$REPO_ROOT/../macOS_updates"}"
SIBLING_VERSION_FILE="${1:-"$SIBLING_DIR/VERSION"}"

if [ ! -f "$SIBLING_VERSION_FILE" ]; then
    echo "Sibling version file not found ($SIBLING_VERSION_FILE); version lockstep check skipped."
    exit 0
fi

if [ ! -f "$ASCENDO_VERSION_FILE" ]; then
    echo "Error: Ascendo version file not found at $ASCENDO_VERSION_FILE" >&2
    exit 1
fi

ASCENDO_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ASCENDO_VERSION_FILE")"
MACOS_UPDATES_VERSION="$(tr -d '[:space:]' < "$SIBLING_VERSION_FILE")"

echo "Ascendo version:       $ASCENDO_VERSION"
echo "macOS_updates version: $MACOS_UPDATES_VERSION"

if [ "$ASCENDO_VERSION" = "$MACOS_UPDATES_VERSION" ]; then
    echo "Version lockstep check PASSED (both at $ASCENDO_VERSION)."
    exit 0
else
    echo "Version lockstep MISMATCH: Ascendo ($ASCENDO_VERSION) != macOS_updates ($MACOS_UPDATES_VERSION)" >&2
    exit 1
fi
