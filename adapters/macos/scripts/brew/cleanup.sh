#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/cleanup.sh -- brew cleanup + log retention
# =============================================================================
# Real path:    runs `brew cleanup -s` (formulae + casks + downloads cache)
# Dry-run path: parses `brew cleanup --dry-run -s` output and emits one
#               status=planned item per file/cache entry that would be removed.
#               NO real `brew cleanup` call in dry-run mode.
#
# Also prunes $HOME/.ascendo/logs/runs/* directories older than 60 days
# (best-effort; dry-run emits planned items, does NOT delete them).
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            (emit planned items only; NO real cleanup)
#   [--filter id1,id2,...] (accepted for parity; not applied in cleanup)
#
# Exit codes (per docs/agents/contract.md):
#   0  success
#   1  warn (brew not on PATH)
#   2  bad usage (missing required args / unknown flag)
#   30 apply-fail-unknown (unexpected failure)
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
        *) printf 'cleanup.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'cleanup.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
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
json_init "cleanup" "brew" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "brew" "$TOOL_VERSION" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- preconditions -------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
    json_add_message "warn" "brew not found on PATH — cleanup is a no-op"
    exit 1
fi

# -- brew cleanup --------------------------------------------------------------
if [ "$DRY_RUN" = "true" ]; then
    # Dry-run: parse `brew cleanup --dry-run -s` output and emit planned items.
    # Lines look like: "Would remove: /path/to/cache/file (12.3MB)"
    # We extract the path portion (before the size annotation in parentheses).
    _planned_count=0
    while IFS= read -r _line; do
        [ -z "$_line" ] && continue
        # Strip the leading "Would remove: " prefix.
        _path="${_line#Would remove: }"
        # Strip the trailing " (size)" annotation.
        _path="${_path%% (*}"
        _path="${_path%% \(*}"
        if [ -n "$_path" ] && [ "$_path" != "$_line" ]; then
            json_add_item "$_path" "" "" "planned" "brew" "cleanup"
            _planned_count=$((_planned_count + 1))
        fi
    done < <(brew cleanup --dry-run -s 2>/dev/null || true)
    json_add_message "info" "dry-run: brew cleanup --dry-run -s (${_planned_count} item(s) would be removed)"
else
    # Real path: run brew cleanup -s.
    if brew cleanup -s >/dev/null 2>&1; then
        json_add_message "info" "brew cleanup -s completed successfully"
    else
        json_add_message "warn" "brew cleanup -s returned non-zero (cache may be partially cleaned)"
    fi
fi

# -- 60-day log retention prune ------------------------------------------------
# Prune $HOME/.ascendo/logs/runs/* directories older than 60 days.
# This mirrors the log retention policy in update_brew.sh (legacy macOS repo).
# Best-effort: any individual deletion failure is silently ignored.
# macOS `find -mtime +N` counts complete 24-hour periods; +60 means >60 days old.
LOG_RETAIN_DAYS=60
LOG_ROOT="$HOME/.ascendo/logs/runs"

if [ -d "$LOG_ROOT" ]; then
    _pruned=0
    while IFS= read -r -d '' _d; do
        if [ "$DRY_RUN" = "true" ]; then
            json_add_item "$_d" "" "" "planned" "brew" "log_prune"
        else
            rm -rf -- "$_d" && _pruned=$((_pruned + 1)) || true
        fi
    done < <(find "$LOG_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime "+${LOG_RETAIN_DAYS}" -print0 2>/dev/null)
    if [ "$DRY_RUN" != "true" ]; then
        json_add_message "info" \
            "pruned ${_pruned} run dir(s) older than ${LOG_RETAIN_DAYS} days from ${LOG_ROOT}"
    fi
fi

exit 0
