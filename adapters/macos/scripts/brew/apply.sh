#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/apply.sh -- brew upgrade phase (the only mutation)
# =============================================================================
# For each outdated formula / cask (filtered by --filter if given):
#   * dry-run path: emit status=planned, no brew calls, no mutations
#   * real path:    optionally quit running cask app via osascript/pkill,
#                   run `brew upgrade --formula|--cask <id>`,
#                   emit status=success or status=failed per item.
#
# Item-level failures are recorded and processing continues (no bail-out
# mid-loop). After the full loop, exits 30 if any item failed, else 0.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            (emit planned items only; NO brew calls)
#   [--filter id1,id2,...] (restrict items to listed IDs)
#
# Exit codes (per docs/agents/contract.md):
#   0  success
#   1  warn (brew not on PATH)
#   2  bad usage (missing required args / unknown flag)
#   30 apply-fail-unknown (one or more items failed, or brew precondition)
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
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN="true";    shift ;;
        --filter)     FILTER="$2";       shift 2 ;;
        *) printf 'apply.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'apply.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
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
json_init "apply" "brew" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "brew" "$TOOL_VERSION" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid).
# Also clean up the temp file for outdated JSON.
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
    json_add_message "warn" "brew not found on PATH — cannot apply upgrades"
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
# Bash 3.2-safe: uses a for loop over IFS-split instead of array operations.
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

# Global flag: set to 1 if any item fails (processing continues regardless).
ANY_FAILED=0

# Live-stream progress accounting (consumed by the SSE endpoint).
TOTAL_ITEMS=0
CURRENT_INDEX=0

# Pre-compute total count for accurate ``pct`` in the PROGRESS sentinels.
_count_targets() {
    local _feed="$1"
    ascendo_brew_parse_outdated "$TMP_OUTDATED" "$_feed" 2>/dev/null \
        | awk -F',' 'NF >= 1 && length($1) > 0 { c++ } END { print c+0 }'
}
_FORMULA_COUNT="$(_count_targets formula)"
_CASK_COUNT="$(_count_targets cask)"
TOTAL_ITEMS="$(( _FORMULA_COUNT + _CASK_COUNT ))"

# _apply_one <id> <current> <target> <feed>
# feed: "formula" or "cask"
_apply_one() {
    local _id="$1"
    local _current="$2"
    local _target="$3"
    local _feed="$4"

    # Filter check — skip if not in the requested subset.
    if ! _filter_match "$_id"; then
        return 0
    fi

    if [ "$_feed" = "cask" ] && [ -n "$_target" ] && ascendo_brew_cask_would_downgrade "$_id" "$_target"; then
        json_add_item "$_id" "$_current" "$_current" "skipped" "brew" "cask"
        json_add_message "warn" \
            "cask ${_id} target ${_target} is older than the installed app; skipped to prevent downgrade" \
            "CASK_DOWNGRADE_GUARD"
        return 0
    fi

    # -- dry-run path (no mutation) --------------------------------------------
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "$_id" "$_current" "$_target" "planned" "brew" "$_feed"
        return 0
    fi

    # -- real path -------------------------------------------------------------

    # Live-stream: bump progress and announce the current package.
    CURRENT_INDEX=$(( CURRENT_INDEX + 1 ))
    local _pct=0
    if [ "$TOTAL_ITEMS" -gt 0 ]; then
        _pct=$(( CURRENT_INDEX * 100 / TOTAL_ITEMS ))
    fi
    _stream_progress "$_pct" "upgrading $_id ($_feed) $_current -> $_target"
    _stream_emit ">>> upgrading $_id ($_feed) $_current -> $_target"

    # Gracefully quit running cask apps before upgrade.
    # Only for casks (formulae are CLI tools, no GUI app to quit).
    if [ "$_feed" = "cask" ]; then
        if ! ascendo_brew_kill_cask_apps "$_id" 2>/dev/null; then
            json_add_message "warn" \
                "could not cleanly quit app for cask '$_id'; continuing anyway" \
                "CASK_QUIT_FAIL"
        fi
    fi

    # Determine the brew feed flag.
    local _brew_flag
    case "$_feed" in
        formula) _brew_flag="--formula" ;;
        cask)    _brew_flag="--cask" ;;
        *)
            json_add_message "error" \
                "unknown feed '$_feed' for package '$_id'; skipping" \
                "UNKNOWN_FEED"
            ANY_FAILED=1
            return 1
            ;;
    esac

    # Run brew upgrade. Tee output through _stream_tee so the dashboard
    # sees every download/install line in real time.
    # PIPESTATUS[0] preserves brew's exit code despite the tee pipeline.
    local _brew_exit=0
    brew upgrade "$_brew_flag" "$_id" 2>&1 | _stream_tee >/dev/null
    _brew_exit="${PIPESTATUS[0]:-0}"

    if [ "$_brew_exit" -eq 0 ]; then
        # Resolve the version actually installed post-upgrade.
        local _resolved
        _resolved="$(brew list --versions "$_id" 2>/dev/null | awk '{print $2}' || echo "$_target")"
        if [ -z "$_resolved" ]; then
            _resolved="$_target"
        fi
        json_add_item "$_id" "$_current" "$_resolved" "success" "brew" "$_feed"
    else
        json_add_message "error" \
            "brew upgrade ${_brew_flag} ${_id} failed (exit ${_brew_exit})" \
            "BREW_UPGRADE_FAIL"
        json_add_item "$_id" "$_current" "$_target" "failed" "brew" "$_feed"
        ANY_FAILED=1
    fi
}

# Announce overall start (no pct yet; lets the SPA show the bar at 0%).
if [ "$DRY_RUN" != "true" ] && [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 0 "brew apply: $TOTAL_ITEMS package(s)"
fi

# -- iterate outdated formulae then casks --------------------------------------
while IFS=',' read -r _id _current _target; do
    [ -z "$_id" ] && continue
    _apply_one "$_id" "$_current" "$_target" "formula"
done < <(ascendo_brew_parse_outdated "$TMP_OUTDATED" "formula" 2>/dev/null || true)

while IFS=',' read -r _id _current _target; do
    [ -z "$_id" ] && continue
    _apply_one "$_id" "$_current" "$_target" "cask"
done < <(ascendo_brew_parse_outdated "$TMP_OUTDATED" "cask" 2>/dev/null || true)

# Final 100% progress so the SPA's bar lands at 100 even if the last
# item happened to round below.
if [ "$DRY_RUN" != "true" ] && [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 100 "brew apply: done"
fi

# -- final exit ----------------------------------------------------------------
if [ "$ANY_FAILED" -ne 0 ]; then
    exit 30
fi
exit 0
