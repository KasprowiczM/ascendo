#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/softwareupdate/plan.sh -- read-only OS-update plan phase
# =============================================================================
# Side-effect-free: identical to check.sh but emits the sidecar with phase=plan
# so the orchestrator's per-(phase,category) accounting works uniformly.
# Only planned items are emitted; no up_to_date sweep is performed.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]   (no-op for plan; accepted for build_argv parity)
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
# shellcheck source=../../lib/ascendo_softwareupdate.sh
. "$ADAPTER_LIB/ascendo_softwareupdate.sh"

# -- configurable binary (overridable in tests via SU_BIN) --------------------
SU_BIN="${SU_BIN:-/usr/sbin/softwareupdate}"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""
TRIGGER=""
PROFILE_NAME=""
OUTPUT_DIR=""
DRY_RUN=0   # plan is already read-only; --dry-run accepted for parity

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN=1;         shift ;;
        # Defense in depth: the orchestrator only sends --filter to phases that
        # act on it; a stray one must never break a read-only/cleanup phase.
        --filter)     shift; [ $# -gt 0 ] && shift ;;
        *) printf 'plan.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'plan.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
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
json_init "plan" "softwareupdate" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "softwareupdate" "$SU_VER" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid)
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- run softwareupdate -l --include-config-data (C locale; Tahoe omits XProtect without it)
ascendo_su_list
if [ "$SU_RC" -ne 0 ]; then
    if [ "$SU_RC" -eq 124 ]; then
        json_add_message "error" "softwareupdate -l timed out after ${ASCENDO_SU_LIST_TIMEOUT:-300}s"
    else
        json_add_message "error" "softwareupdate -l failed (exit $SU_RC): $SU_OUT"
    fi
    json_add_item "softwareupdate:list-error" "" "" "failed" "softwareupdate"
    exit 30
fi

# -- parse + classify (su_classify.py): SAME / OTHER -> planned, MAJOR -> skipped
CUR_OS="$(ascendo_su_current_os)"
SU_RECORDS="$(ascendo_su_records "$SU_OUT" "$CUR_OS")" || {
    json_add_message "error" "softwareupdate listing could not be parsed (su_classify.py failed)"
    json_add_item "softwareupdate:list-error" "" "" "failed" "softwareupdate"
    exit 30
}

# A non-empty listing with no parseable record (and no "No new software
# available") is UNKNOWN state, not "nothing to update".
if ascendo_su_listing_unparseable "$SU_OUT" "$SU_RECORDS"; then
    json_add_message "warn" "softwareupdate -l returned a listing that could not be parsed; pending update state unknown"
    json_add_item "softwareupdate:list-unknown" "" "" "skipped" "softwareupdate"
    exit 0
fi

NEEDS_REBOOT=0
ascendo_su_emit_pending "$SU_RECORDS" "$CUR_OS"

if [ "$NEEDS_REBOOT" -eq 1 ]; then
    json_set_needs_reboot true
fi

exit 0
