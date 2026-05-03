#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/mas/check.sh -- read-only Mac App Store inventory phase
# =============================================================================
# Sign-in probe first; on failure emits a single failed item (mas:not-signed-in)
# and exits 0. On success: walks mas_outdated_json for planned items and
# mas_list_json minus outdated for up_to_date items. Emits one ascendo/v1
# sidecar at <output-dir>/<run-id>/check__mas.json on every code path via
# EXIT trap.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            (no-op for check; accepted for parity)
#   [--filter id1,id2,...] (restrict planned items to listed IDs)
#
# Exit codes (per docs/agents/contract.md):
#   0  success (or sign-in failure captured in sidecar; exit is still 0)
#   2  bad usage (missing required args / unknown flag)
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# Source libs (order: json first, then mas)
# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck source=../../lib/ascendo_mas.sh
. "$ADAPTER_LIB/ascendo_mas.sh"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""
TRIGGER=""
PROFILE_NAME=""
OUTPUT_DIR=""
DRY_RUN=0
FILTER_CSV=""

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN=1;         shift ;;
        --filter)     FILTER_CSV="$2";   shift 2 ;;
        *) printf 'check.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'check.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
    exit 2
fi

# -- host info (mirrors brew check.sh) ----------------------------------------
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
MAS_VER="$("$MAS_BIN" version 2>/dev/null || echo unknown)"
if [ -z "$MAS_VER" ]; then
    MAS_VER="unknown"
fi

# -- init sidecar --------------------------------------------------------------
json_init "check" "mas" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "mas" "$MAS_VER" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid)
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- sign-in probe -------------------------------------------------------------
if ! mas_signed_in; then
    json_add_message "error" "Not signed into Mac App Store. Open App Store.app and sign in."
    json_add_item "mas:not-signed-in" "" "" "failed" "mas"
    exit 0
fi

# -- helper: returns 0 if FILTER_CSV is empty OR id is in the CSV list --------
# Bash 3.2-safe: case glob with leading/trailing comma sentinels avoids
# false positives from substring matches (e.g. "10" inside "1011").
in_filter() {
    [ -z "$FILTER_CSV" ] && return 0
    case ",$FILTER_CSV," in
        (*",$1,"*) return 0 ;;
        (*) return 1 ;;
    esac
}

# -- planned (outdated apps) ---------------------------------------------------
OUTDATED_JSON="$(mas_outdated_json)"

# Collect outdated ids as a space-delimited string for the up_to_date loop.
# Bash 3.2-safe: no arrays, use plain string with space sentinels.
OUTDATED_IDS=" $(printf '%s' "$OUTDATED_JSON" | jq -r '.[].id' 2>/dev/null | tr '\n' ' ') "

printf '%s' "$OUTDATED_JSON" | jq -r '.[] | [.id, .current_version, .target_version] | @tsv' 2>/dev/null | \
while IFS="$(printf '\t')" read -r _id _cur _tgt; do
    [ -n "$_id" ] || continue
    if in_filter "$_id"; then
        json_add_item "$_id" "$_cur" "$_tgt" "planned" "mas"
    fi
done

# -- up_to_date (installed minus outdated) -------------------------------------
LIST_JSON="$(mas_list_json)"

printf '%s' "$LIST_JSON" | jq -r '.[] | [.id, .version] | @tsv' 2>/dev/null | \
while IFS="$(printf '\t')" read -r _id _ver; do
    [ -n "$_id" ] || continue
    # Skip any id that is in the outdated set
    case "$OUTDATED_IDS" in
        (*" $_id "*) continue ;;
    esac
    json_add_item "$_id" "$_ver" "$_ver" "up_to_date" "mas"
done

exit 0
