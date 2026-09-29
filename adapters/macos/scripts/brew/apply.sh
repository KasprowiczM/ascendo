#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/apply.sh -- brew upgrade phase (the only mutation)
# =============================================================================
# For each outdated formula / cask (filtered by --filter if given):
#   * dry-run path: emit status=planned, no brew calls, no mutations
#   * real path:    a cask is skipped (brew not called) when its downgrade /
#                   orphan guard fires, when its app is running (never quit or
#                   killed; action-required "quit it and re-run"), or when it
#                   needs administrator rights and no SUDO_ASKPASS is wired;
#                   otherwise run `brew upgrade --formula|--cask <id>`,
#                   emit status=success or status=failed per item.
# The outdated set is `brew outdated` united with the greedy auto-updating
# casks of config/brew_greedy_casks.txt (deduplicated by token).
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
#   10 precondition failed (Xcode licence not accepted; never auto-accepted)
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
    # Capture the script's exit code first: the rm/if below would clobber $?
    # and the sidecar would record exit_code 0 for a failed/gated phase.
    local _rc=$?
    if [ -n "$TMP_OUTDATED" ] && [ -f "$TMP_OUTDATED" ]; then
        rm -f "$TMP_OUTDATED"
    fi
    (exit "$_rc")
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

# Xcode licence gate: with Xcode.app selected and its licence unaccepted
# every brew command fails. Never auto-accept; skip with the remedy.
ascendo_brew_xcode_license_gate || exit 10

# -- enumerate outdated --------------------------------------------------------
TMP_OUTDATED="$(mktemp "${TMPDIR:-/tmp}/ascendo_brew_outdated_XXXXXX")"

# Greedy: brew-managed auto-updating casks that plain `brew outdated` never lists.
GREEDY_TOKENS="$(ascendo_brew_greedy_tokens)"
# shellcheck disable=SC2086
ascendo_brew_outdated_or_fail "$TMP_OUTDATED" $GREEDY_TOKENS || exit 30

# Cask facts for the outdated casks: real .app targets (running-app + orphan +
# downgrade checks), quit bundle ids, sudo flag. One batched `brew info`.
ascendo_brew_prepare_cask_facts "$TMP_OUTDATED"

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

    if [ "$_feed" = "cask" ] && [ -n "$_target" ]; then
        local _disp
        _disp="$(ascendo_brew_cask_disposition "$_id" "$_target")"
        # downgrade / orphan: never upgraded (an orphan would reinstall an app
        # the user deleted; a downgrade would replace a newer app).
        if ascendo_brew_emit_cask_guard_skip "${_disp%%|*}" "$_id" "$_current" "$_target" "${_disp#*|}"; then
            return 0
        fi
        if [ "${_disp%%|*}" = "scheme_unknown" ]; then
            json_add_message "info" \
                "${_id}: ${_disp#*|} -- deferring to Homebrew's own record" \
                "CASK_VERSION_SCHEME"
        fi
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
    # Never close an app the user has open. `brew upgrade --cask` replaces the
    # bundle underneath a running app (and some casks' `uninstall quit:` stanza
    # makes Homebrew itself close it), so a cask whose app is running is
    # deferred: skipped + action-required, brew is NOT called.
    if [ "$_feed" = "cask" ] && ascendo_brew_cask_app_running "$_id"; then
        _stream_progress "$_pct" "skipping $_id (app is running)"
        _stream_emit ">>> skipping $_id (cask app is running -- quit it and re-run)"
        # Keep the candidate ($_target): apply outranks check in the inventory
        # flush, so dropping it would hide the still-outdated cask.
        json_add_item "$_id" "$_current" "$_target" "skipped" "brew" "cask"
        json_add_message "info" \
            "${_id}: skipped -- app in use (quit it and re-run apply to upgrade ${_current} -> ${_target}) | $(ascendo_action_required_token app_in_use "$_current" "$_target")" \
            "CASK_APP_RUNNING"
        return 0
    fi

    # Fail-safe: with no facts (brew info failed even per token) and no table
    # entry we cannot tell whether the app is open, and `brew upgrade --cask`
    # replaces the bundle underneath a running app. Never upgrade blind.
    if [ "$_feed" = "cask" ] && ! ascendo_brew_cask_running_state_known "$_id"; then
        _stream_progress "$_pct" "skipping $_id (cannot determine whether its app is running)"
        _stream_emit ">>> skipping $_id (cask facts unavailable -- cannot tell if the app is running)"
        json_add_item "$_id" "$_current" "$_target" "skipped" "brew" "cask"
        json_add_message "warn" \
            "${_id}: skipped -- could not read the cask's app facts (brew info failed), so Ascendo cannot tell whether the app is running; not upgraded to avoid closing it. Re-run apply or run: brew upgrade --cask ${_id} | $(ascendo_action_required_token app_facts_unavailable "$_current" "$_target")" \
            "APP_FACTS_UNAVAILABLE"
        return 0
    fi

    # A pkg/installer cask needs administrator rights. Dashboard runs are
    # TTY-less, so without an askpass helper (SUDO_ASKPASS) brew's sudo would
    # fail or hang: defer it instead. Keep the candidate so inventory still
    # shows the cask as outdated.
    if [ "$_feed" = "cask" ] && ascendo_brew_cask_needs_admin "$_id" && ! ascendo_brew_admin_available; then
        _stream_progress "$_pct" "skipping $_id (needs administrator rights)"
        _stream_emit ">>> skipping $_id (cask needs administrator rights -- authorise in Ascendo or run: brew upgrade --cask $_id)"
        json_add_item "$_id" "$_current" "$_target" "skipped" "brew" "cask"
        json_add_message "warn" \
            "${_id}: skipped -- needs administrator rights; authorise in Ascendo or run: brew upgrade --cask ${_id} | $(ascendo_action_required_token cask_needs_admin "$_current" "$_target")" \
            "CASK_NEEDS_ADMIN"
        return 0
    fi

    _stream_progress "$_pct" "upgrading $_id ($_feed) $_current -> $_target"
    _stream_emit ">>> upgrading $_id ($_feed) $_current -> $_target"

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
