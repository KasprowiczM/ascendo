#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/verify.sh -- post-apply verification phase
# =============================================================================
# Reads the sibling apply__brew.json (same run-id), re-runs
# `brew outdated --json=v2`, and asserts each item that apply marked
# "success" is no longer in the outdated list.
#
# Mismatches  -> status=failed   (still outdated)
# Matches     -> status=success  (no longer outdated)
#
# Soft no-op when no sibling apply sidecar exists (verify can run after
# check-only without crashing — emits an info message and exits 0 with
# empty items[]).
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            (no-op for verify; accepted for parity)
#   [--filter id1,id2,...] (accepted for parity; not applied in verify)
#
# Exit codes (per docs/agents/contract.md):
#   0  success (all verified, or soft no-op)
#   1  warn (one or more items still outdated)
#   2  bad usage (missing required args / unknown flag)
#   30 apply-fail-unknown (unexpected brew or jq failure)
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck source=../../lib/ascendo_brew.sh
. "$ADAPTER_LIB/ascendo_brew.sh"

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""
TRIGGER=""
PROFILE_NAME=""
OUTPUT_DIR=""
DRY_RUN="false"
FILTER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN="true";    shift ;;
        --filter)     FILTER="$2";       shift 2 ;;
        *) printf 'verify.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'verify.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
    exit 2
fi

# -- host info -----------------------------------------------------------------
HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
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
TOOL_VERSION="$(ascendo_brew_version 2>/dev/null || true)"
if [ -z "$TOOL_VERSION" ]; then
    TOOL_VERSION="unknown"
fi

# -- init sidecar --------------------------------------------------------------
json_init "verify" "brew" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "brew" "$TOOL_VERSION" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

TMP_CURRENT=""
_cleanup() {
    if [ -n "$TMP_CURRENT" ] && [ -f "$TMP_CURRENT" ]; then
        rm -f "$TMP_CURRENT"
    fi
    json_save_on_exit "$OUTPUT_DIR"
}
trap '_cleanup' EXIT

# -- soft no-op when apply sidecar missing ------------------------------------
APPLY_SIDECAR="$OUTPUT_DIR/$RUN_ID/apply__brew.json"
if [ ! -f "$APPLY_SIDECAR" ]; then
    json_add_message "info" \
        "no sibling apply__brew.json found — verify is a soft no-op (run after check-only)"
    exit 0
fi

# -- preconditions -------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
    json_add_message "warn" "brew not found on PATH — cannot verify upgrades"
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    json_add_message "error" \
        "jq is required for the brew adapter (install: brew install jq)" \
        "JQ_MISSING"
    exit 30
fi

# -- re-query current outdated list -------------------------------------------
TMP_CURRENT="$(mktemp "${TMPDIR:-/tmp}/ascendo_brew_verify_XXXXXX")"

if ! ascendo_brew_outdated_json > "$TMP_CURRENT" 2>/dev/null; then
    json_add_message "error" "brew outdated re-query failed unexpectedly" "BREW_OUTDATED_FAIL"
    exit 30
fi

# -- verify each item apply marked success ------------------------------------
# For each id that apply wrote as status=success, check if it still appears
# in the current outdated list. Still outdated -> failed; not outdated -> success.
ANY_FAILED=0

while IFS= read -r _id; do
    [ -z "$_id" ] && continue

    # Check if $_id still appears in either formulae or casks of the
    # current outdated output. The jq filter handles both string and array
    # name fields (matching ascendo_brew_parse_outdated's jq logic).
    _still_outdated=0
    if jq -e --arg id "$_id" '
        (.formulae[] | select(
            (if (.name | type == "array") then .name[0] else .name end) == $id
        )) // empty,
        (.casks[] | select(
            (if (.name | type == "array") then .name[0] else .name end) == $id
        )) // empty
    ' "$TMP_CURRENT" >/dev/null 2>&1; then
        _still_outdated=1
    fi

    if [ "$_still_outdated" -eq 1 ]; then
        json_add_item "$_id" "" "" "failed" "brew" ""
        json_add_message "error" \
            "verify: '$_id' is still outdated after apply" \
            "STILL_OUTDATED"
        ANY_FAILED=1
    else
        json_add_item "$_id" "" "" "success" "brew" ""
    fi
done < <(jq -r '.items[]? | select(.status == "success") | .id' "$APPLY_SIDECAR" 2>/dev/null || true)

# -- final exit ----------------------------------------------------------------
if [ "$ANY_FAILED" -ne 0 ]; then
    exit 1
fi
exit 0
