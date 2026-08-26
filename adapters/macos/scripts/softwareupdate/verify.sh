#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/softwareupdate/verify.sh -- read-only OS-update verify phase
# =============================================================================
# Reads sibling apply__softwareupdate.json, re-runs softwareupdate -l, and for
# each apply-success item checks whether the same Label still appears in the
# output. If it does, the update is still pending -> verify failed. If not,
# the update is confirmed installed -> verify success.
#
# Soft no-op (exit 0, success sidecar) when apply sidecar is absent (verify
# can run after check-only).
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]   (no-op; accepted for build_argv parity)
#
# Exit codes:
#   0   success (per-item failures encoded in sidecar; overall exit still 0)
#   2   bad usage (missing required args / unknown flag)
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
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN=1;         shift ;;
        *) printf 'verify.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'verify.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
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
json_init "verify" "softwareupdate" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "softwareupdate" "$SU_VER" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid)
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- locate sibling apply sidecar ----------------------------------------------
APPLY_SIDECAR="${OUTPUT_DIR}/${RUN_ID}/apply__softwareupdate.json"

if [ ! -f "$APPLY_SIDECAR" ]; then
    # Soft no-op: verify can run after check-only. Emit info and exit 0.
    json_add_message "info" "apply__softwareupdate.json not found; no apply sidecar to verify against. Skipping."
    exit 0
fi

# -- extract apply success items (ids where status == "success") ---------------
APPLY_SUCCESS_IDS="$(jq -r '.items[]? | select(.status=="success") | .id' "$APPLY_SIDECAR" 2>/dev/null)"

if [ -z "$APPLY_SUCCESS_IDS" ]; then
    json_add_message "info" "No successful apply items found in apply__softwareupdate.json; nothing to verify."
    exit 0
fi

# -- re-run softwareupdate -l --include-config-data to build still-pending set
SU_RC=0
SU_OUT="$("$SU_BIN" -l --include-config-data 2>&1)" || SU_RC=$?
if [ "$SU_RC" -ne 0 ]; then
    json_add_message "error" "softwareupdate -l failed during verify (exit $SU_RC): $SU_OUT"
    # Emit all applied items as failed -- cannot confirm installation
    printf '%s\n' "$APPLY_SUCCESS_IDS" | while IFS= read -r _id; do
        [ -n "$_id" ] || continue
        json_add_item "$_id" "" "" "failed" "softwareupdate"
    done
    exit 0
fi

# Build a newline-delimited list of labels still reported by softwareupdate -l
# (each "* Label: <X>" line becomes just <X>)
STILL_PENDING="$(printf '%s\n' "$SU_OUT" | sed -n 's/^\* Label: //p')"

# -- emit one verify item per apply success id ---------------------------------
# Iterate apply success items and check whether each label is still pending in
# `softwareupdate -l` output. NOTE: this `printf | while` subshell is safe because
# json_add_item persists each item to disk via JSON_BUFDIR (set by json_init); we
# don't rely on any bash variable mutation surviving the subshell.
printf '%s\n' "$APPLY_SUCCESS_IDS" | while IFS= read -r _id; do
    [ -n "$_id" ] || continue
    # Look up resolved_version from apply sidecar
    RESOLVED="$(jq -r --arg id "$_id" \
        '.items[]? | select(.id==$id) | (.resolved_version // .target_version // "")' \
        "$APPLY_SIDECAR" 2>/dev/null)"
    # Check whether this label is still pending
    if printf '%s\n' "$STILL_PENDING" | grep -qxF "$_id"; then
        json_add_item "$_id" "" "" "failed" "softwareupdate"
    else
        json_add_item "$_id" "" "$RESOLVED" "success" "softwareupdate"
    fi
done

exit 0
