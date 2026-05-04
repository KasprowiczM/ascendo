#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/snapshot/list.sh -- read-only snapshot enumeration
# =============================================================================
# Runs `tmutil listlocalsnapshots /`, parses APFS Time Machine local snapshot
# names, and emits an ascendo/v1 sidecar at
# <output-dir>/<run-id>/check__snapshot.json on every code path via EXIT trap.
#
# Each valid `com.apple.TimeMachine.*` entry becomes one success item.
# Malformed lines (including the header) are silently skipped.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]   (no-op for check; accepted for build_argv parity)
#
# Exit codes (per docs/agents/contract.md):
#   0   success (zero or more snapshot items in sidecar)
#   2   bad usage (missing required args / unknown flag)
#   30  tmutil listlocalsnapshots / returned non-zero
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"

# -- configurable binary (overridable in tests via TMUTIL_BIN) ----------------
TMUTIL_BIN="${TMUTIL_BIN:-/usr/bin/tmutil}"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""
TRIGGER=""
PROFILE_NAME=""
OUTPUT_DIR=""
DRY_RUN=0   # check is already read-only; --dry-run accepted for parity

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN=1;         shift ;;
        *) printf 'list.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'list.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
    exit 2
fi

# -- host info -----------------------------------------------------------------
HOST_NAME="$(scutil --get ComputerName 2>/dev/null || hostname 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    HOST_IS_ELEVATED="true"
else
    HOST_IS_ELEVATED="false"
fi

# -- tool info -----------------------------------------------------------------
# tmutil version subcommand may not exist on all macOS versions; fall back gracefully
TOOL_NAME="tmutil"
TOOL_VERSION="$("$TMUTIL_BIN" version 2>/dev/null | head -1 || echo unknown)"
if [ -z "$TOOL_VERSION" ]; then
    TOOL_VERSION="unknown"
fi

# -- init sidecar --------------------------------------------------------------
json_init "check" "snapshot" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "$TOOL_NAME" "$TOOL_VERSION" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid)
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- run tmutil listlocalsnapshots ---------------------------------------------
TM_RC=0
TM_OUT="$("$TMUTIL_BIN" listlocalsnapshots / 2>&1)" || TM_RC=$?
if [ "$TM_RC" -ne 0 ]; then
    json_add_message "error" "tmutil listlocalsnapshots / failed (exit $TM_RC): $TM_OUT"
    json_add_item "snapshot:tmutil-error" "" "" "failed" "snapshot"
    exit 30
fi

# -- parse snapshot names ------------------------------------------------------
# Skip the "Snapshots for disk /:" header line via tail -n +2.
# Use here-string (<<< "$TM_OUT") so the while loop runs in the CURRENT shell,
# avoiding bash 3.2 subshell variable scoping issues (same pattern as check.sh).
# Only accept lines matching com.apple.TimeMachine.*-*-*-*.local; skip all else.
while IFS= read -r snap; do
    [ -n "$snap" ] || continue
    case "$snap" in
        com.apple.TimeMachine.*-*-*-*.local)
            json_add_item "$snap" "" "" "success" "snapshot"
            ;;
        *)
            # Skip header line and malformed entries silently
            ;;
    esac
done <<< "$(printf '%s\n' "$TM_OUT" | tail -n +2)"

exit 0
