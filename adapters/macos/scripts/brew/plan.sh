#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/plan.sh -- side-effect-free upgrade plan phase
# =============================================================================
# Side-effect-free upgrade plan; lists what apply WOULD touch. Emits one
# ascendo/v1 sidecar at <output-dir>/<run-id>/plan__brew.json on every code
# path via EXIT trap.
#
# Semantically distinct from check.sh: the orchestrator treats
# "apply on failed plan" as unsafe (stop_on_failure logic).
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            (no-op for plan; accepted for parity)
#   [--filter id1,id2,...] (restrict items to listed IDs)
#
# Exit codes (per docs/agents/contract.md):
#   0  success
#   1  warn (brew not on PATH)
#   2  bad usage (missing required args / unknown flag)
#   30 apply-fail-unknown (unexpected brew or jq failure)
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

# Source libs (order: json first, then brew)
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
        --run-id)     RUN_ID="$2";     shift 2 ;;
        --trigger)    TRIGGER="$2";    shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN="true";  shift ;;
        --filter)     FILTER="$2";     shift 2 ;;
        *) printf 'plan.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'plan.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
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
json_init "plan" "brew" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "brew" "$TOOL_VERSION" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid)
# json_save_on_exit captures $? internally, so we just pass OUTPUT_DIR.
TMP_OUTDATED=""
_cleanup() {
    if [ -n "$TMP_OUTDATED" ] && [ -f "$TMP_OUTDATED" ]; then
        rm -f "$TMP_OUTDATED"
    fi
    json_save_on_exit "$OUTPUT_DIR"
}
trap '_cleanup' EXIT

# -- preconditions -------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
    json_add_message "warn" "brew not found on PATH — skipping inventory"
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    json_add_message "error" \
        "jq is required for the brew adapter (install: brew install jq)" \
        "JQ_MISSING"
    exit 30
fi

# -- enumerate outdated --------------------------------------------------------
TMP_OUTDATED="$(mktemp "${TMPDIR:-/tmp}/ascendo_brew_outdated_XXXXXX")"

if ! ascendo_brew_outdated_json > "$TMP_OUTDATED" 2>/dev/null; then
    json_add_message "error" "brew outdated --json=v2 failed unexpectedly" "BREW_OUTDATED_FAIL"
    exit 30
fi

# Helper: returns 0 (match) if FILTER is empty OR id is in the CSV FILTER list.
# Bash 3.2-safe: uses a while-read loop instead of array operations.
_filter_match() {
    local _id="$1"
    if [ -z "$FILTER" ]; then
        return 0
    fi
    local _f
    local _IFS_SAVE="$IFS"
    IFS=','
    for _f in $FILTER; do
        if [ "$_f" = "$_id" ]; then
            IFS="$_IFS_SAVE"
            return 0
        fi
    done
    IFS="$_IFS_SAVE"
    return 1
}

# Emit one sidecar item per outdated package in the given bucket.
# bucket: "formula" or "cask"
_emit_outdated() {
    local _bucket="$1"
    local _count=0
    # ascendo_brew_parse_outdated emits CSV: id,current_version,target_version
    # Process substitution (<(...)) is available in bash 3.2 on macOS.
    while IFS=',' read -r _id _current _target; do
        [ -z "$_id" ] && continue
        if _filter_match "$_id"; then
            json_add_item "$_id" "$_current" "$_target" "planned" "brew" "$_bucket"
            _count=$((_count + 1))
        fi
    done < <(ascendo_brew_parse_outdated "$TMP_OUTDATED" "$_bucket" 2>/dev/null || true)
    json_add_message "info" "outdated ${_bucket}: ${_count}"
}

_emit_outdated "formula"
_emit_outdated "cask"

json_add_message "info" "plan: enumerated outdated formulae + casks (no mutation)"

exit 0
