#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/check.sh -- read-only brew inventory phase
# =============================================================================
# Lists outdated formulae + casks via `brew outdated --json=v2`. Side-effect
# free (no `brew update`, no upgrades, no cleanups). Emits one ascendo/v1
# sidecar at <output-dir>/<run-id>/check__brew.json on every code path via
# EXIT trap.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            (no-op for check; accepted for parity)
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
        *) printf 'check.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'check.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
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
json_init "check" "brew" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
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

# Build a set of outdated ids (space-padded sentinels) for O(1) membership
# tests in the up_to_date loop. Bash 3.2-safe: no associative arrays.
OUTDATED_IDS=" "
_collect_outdated_ids() {
    local _bucket="$1"
    local _id _cur _tgt
    while IFS=',' read -r _id _cur _tgt; do
        [ -z "$_id" ] && continue
        OUTDATED_IDS="$OUTDATED_IDS$_id "
    done < <(ascendo_brew_parse_outdated "$TMP_OUTDATED" "$_bucket" 2>/dev/null || true)
}
_collect_outdated_ids "formula"
_collect_outdated_ids "cask"

# Emit one `planned` item per outdated package in the given bucket.
_emit_outdated() {
    local _bucket="$1"
    local _count=0
    while IFS=',' read -r _id _current _target; do
        [ -z "$_id" ] && continue
        if _filter_match "$_id"; then
            json_add_item "$_id" "$_current" "$_target" "planned" "brew" "$_bucket"
            _count=$((_count + 1))
        fi
    done < <(ascendo_brew_parse_outdated "$TMP_OUTDATED" "$_bucket" 2>/dev/null || true)
    json_add_message "info" "outdated ${_bucket}: ${_count}"
}

# Emit one `up_to_date` item per installed package NOT in the outdated set.
# Formulae: `brew list --formula --versions`. Casks: NEVER call
# `brew list --cask --versions` directly (Cask::CaskLoader regression
# 2026-08-19) — go through ascendo_brew_cask_versions, which falls back
# to Caskroom/<token>/<version>.
_emit_up_to_date() {
    local _bucket="$1"
    local _count=0
    local _id _ver _line
    while IFS= read -r _line; do
        [ -z "$_line" ] && continue
        _id="${_line%% *}"
        _ver="${_line#* }"
        # Some casks have no version reported; treat the whole line as id.
        if [ "$_id" = "$_line" ]; then
            _ver=""
        fi
        # Skip if id is in the outdated set (already emitted as `planned`).
        case "$OUTDATED_IDS" in
            (*" $_id "*) continue ;;
        esac
        if _filter_match "$_id"; then
            json_add_item "$_id" "$_ver" "$_ver" "up_to_date" "brew" "$_bucket"
            _count=$((_count + 1))
        fi
    done < <(
        if [ "$_bucket" = "cask" ]; then
            ascendo_brew_cask_versions 2>/dev/null || true
        else
            ascendo_brew_formula_versions 2>/dev/null || true
        fi
    )
    json_add_message "info" "installed ${_bucket}: ${_count} up-to-date"
}

_emit_outdated "formula"
_emit_outdated "cask"
_emit_up_to_date "formula"
_emit_up_to_date "cask"

exit 0
