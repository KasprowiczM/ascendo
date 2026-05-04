#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/softwareupdate/check.sh -- read-only OS-update phase
# =============================================================================
# Runs `softwareupdate -l`, parses the output for available updates, and emits
# an ascendo/v1 sidecar at <output-dir>/<run-id>/check__softwareupdate.json on
# every code path via EXIT trap.
#
# Each "* Label: <X>" block becomes one planned item; if the block has
# "Action: restart" the needs_reboot flag is set to true on the sidecar.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]   (no-op for check; accepted for build_argv parity)
#
# Exit codes (per docs/agents/contract.md):
#   0   success (zero or more planned items in sidecar)
#   2   bad usage (missing required args / unknown flag)
#   30  softwareupdate -l returned non-zero
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"

# -- configurable binary (overridable in tests via SU_BIN) --------------------
SU_BIN="${SU_BIN:-/usr/sbin/softwareupdate}"

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
        *) printf 'check.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'check.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
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
SU_VER="$("$SU_BIN" --help 2>&1 | head -1 | sed 's/.*softwareupdate/softwareupdate/' || echo unknown)"
if [ -z "$SU_VER" ]; then
    SU_VER="unknown"
fi

# -- init sidecar --------------------------------------------------------------
json_init "check" "softwareupdate" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "softwareupdate" "$SU_VER" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid)
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- run softwareupdate -l -----------------------------------------------------
SU_RC=0
SU_OUT="$("$SU_BIN" -l 2>&1)" || SU_RC=$?
if [ "$SU_RC" -ne 0 ]; then
    json_add_message "error" "softwareupdate -l failed (exit $SU_RC): $SU_OUT"
    json_add_item "softwareupdate:list-error" "" "" "failed" "softwareupdate"
    exit 30
fi

# -- empty case ----------------------------------------------------------------
if printf '%s\n' "$SU_OUT" | grep -q "No new software available\."; then
    # Zero items; status heuristic in lib emits success
    exit 0
fi

# -- parse `* Label:` blocks --------------------------------------------------
# State machine: "* Label: <X>" sets CURRENT_LABEL; the next TAB-prefixed line
# carries Title/Version/Action attributes.
#
# Use a here-string (<<< "$SU_OUT") so the while loop runs in the CURRENT
# shell, not a subshell -- this lets NEEDS_REBOOT mutations survive.
# bash 3.2 supports <<< since 2.05b.
NEEDS_REBOOT=0
CURRENT_LABEL=""

while IFS= read -r line; do
    case "$line" in
        '* Label: '*)
            CURRENT_LABEL="${line#'* Label: '}"
            ;;
        "	"*)
            # Line begins with a literal TAB: attribute block for current label
            [ -n "$CURRENT_LABEL" ] || continue
            attrs="${line#	}"   # strip leading TAB
            # Extract Version field: "Version: X.Y.Z,"
            version="$(printf '%s\n' "$attrs" | sed -n 's/.*Version: \([^,]*\),.*/\1/p' | head -1)"
            # Trim surrounding whitespace
            version="${version# }"
            version="${version% }"
            # Detect Action: restart
            if printf '%s\n' "$attrs" | grep -q "Action: restart"; then
                NEEDS_REBOOT=1
            fi
            json_add_item "$CURRENT_LABEL" "$version" "$version" "planned" "softwareupdate"
            CURRENT_LABEL=""
            ;;
    esac
done <<< "$SU_OUT"

if [ "$NEEDS_REBOOT" -eq 1 ]; then
    json_set_needs_reboot true
fi

exit 0
