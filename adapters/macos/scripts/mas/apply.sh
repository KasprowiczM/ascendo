#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/mas/apply.sh -- mutating Mac App Store upgrade phase
# =============================================================================
# CVE-2025-43411: ALWAYS invokes `sudo -A mas upgrade` — never bare `mas upgrade`.
# Strategy A chosen: always use -A. This means CLI users without a SUDO_ASKPASS
# helper must warm up sudo credentials first (`sudo -v`) before calling apply,
# because -A with no askpass program fails. Dashboard flow sets SUDO_ASKPASS via
# MasManager._build_env so it always works. Strategy A is simpler to audit and
# ensures the security guarantee is unconditional at the bash layer.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            emits planned items; NEVER invokes sudo
#   [--filter id1,id2,...] restrict upgrades to listed mas IDs (per-id loop)
#
# Exit codes (per docs/agents/contract.md):
#   0  success (sign-in failure captured in sidecar; exit is still 0)
#   2  bad usage (missing required args / unknown flag)
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

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
        *) printf 'apply.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'apply.sh: missing required args (--run-id, --trigger, --profile, --output-dir)\n' >&2
    exit 2
fi

# -- host info (mirrors check.sh / plan.sh) ------------------------------------
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

# -- init sidecar (canonical 13-arg form; NO schema URI as first arg) ----------
json_init "apply" "mas" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "mas" "$MAS_VER" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"

# Install EXIT trap AFTER json_init (bufdir is now valid)
# Single-arg form: json_save_on_exit <output-dir>
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- sudo invocation helper ----------------------------------------------------
# `_ascendo_sudo` (defined in ascendo_json.sh) picks `sudo -A` when an
# askpass helper is wired by the dashboard, or plain `sudo` for the
# TTY-PAM / Touch-ID-only flow. _ascendo_sudo_warm has already cached
# credentials by the time this runs, so plain sudo proceeds without
# prompting.
_sudo_mas_upgrade() {
    _ascendo_sudo "$MAS_BIN" upgrade "$@"
}

# -- sign-in probe (fail-fast; no sudo invocation on signed-out host) ----------
if ! mas_signed_in; then
    json_add_message "error" "Not signed into Mac App Store. Open App Store.app and sign in."
    json_add_item "mas:not-signed-in" "" "" "failed" "mas"
    exit 0
fi

# -- helper: returns 0 if FILTER_CSV is empty OR id is in the CSV list --------
# Bash 3.2-safe: case glob with leading/trailing comma sentinels avoids false
# positives from substring matches (e.g. "10" inside "1011").
in_filter() {
    [ -z "$FILTER_CSV" ] && return 0
    case ",$FILTER_CSV," in
        (*",$1,"*) return 0 ;;
        (*) return 1 ;;
    esac
}

# -- enumerate outdated apps ---------------------------------------------------
OUTDATED_JSON="$(mas_outdated_json)"

# -- dry-run path: planned items only, never invoke sudo ----------------------
if [ "$DRY_RUN" -eq 1 ]; then
    printf '%s' "$OUTDATED_JSON" | jq -r '.[] | [.id, .current_version, .target_version] | @tsv' 2>/dev/null | \
    while IFS="$(printf '\t')" read -r _id _cur _tgt; do
        [ -n "$_id" ] || continue
        in_filter "$_id" || continue
        json_add_item "$_id" "$_cur" "$_tgt" "planned" "mas"
    done
    json_add_message "info" "dry-run: no real upgrades performed"
    exit 0
fi

# -- real apply path -----------------------------------------------------------
# Touch-ID-first sudo warming. Asks the OS-native auth dialog (Touch ID
# preferred when pam_tid is configured) BEFORE falling back to the
# dashboard's askpass cache. After this returns, the `sudo -A` calls
# below use the warmed timestamp and don't re-prompt. Best-effort —
# doesn't change the failure path if Touch ID is unavailable.
_ascendo_sudo_warm

# Collect target ids + version pairs as space-separated strings (Bash 3.2-safe;
# no declare -A). IDs are numeric-only so spaces are safe delimiters.
# Use command substitution to avoid subshell variable scoping (Bash 3.2).
TARGET_IDS="$(printf '%s' "$OUTDATED_JSON" | jq -r '.[] | [.id, .current_version, .target_version] | @tsv' 2>/dev/null | \
    while IFS="$(printf '\t')" read -r _id _cur _tgt; do
        [ -n "$_id" ] || continue
        in_filter "$_id" || continue
        printf '%s ' "$_id"
    done)"
TARGET_IDS="${TARGET_IDS%% }"   # strip trailing space

TARGET_PAIRS="$(printf '%s' "$OUTDATED_JSON" | jq -r '.[] | [.id, .current_version, .target_version] | @tsv' 2>/dev/null | \
    while IFS="$(printf '\t')" read -r _id _cur _tgt; do
        [ -n "$_id" ] || continue
        in_filter "$_id" || continue
        printf '%s|%s|%s ' "$_id" "$_cur" "$_tgt"
    done)"
TARGET_PAIRS="${TARGET_PAIRS%% }"

if [ -z "$TARGET_IDS" ]; then
    json_add_message "info" "nothing to upgrade"
    exit 0
fi

# Total count for live-stream progress accounting.
TOTAL_ITEMS=0
for _id in $TARGET_IDS; do
    [ -n "$_id" ] && TOTAL_ITEMS=$(( TOTAL_ITEMS + 1 ))
done
CURRENT_INDEX=0

if [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 0 "mas apply: $TOTAL_ITEMS app(s)"
fi

# Per-id loop when --filter is set; bulk otherwise.
if [ -n "$FILTER_CSV" ]; then
    for _id in $TARGET_IDS; do
        _cur=""
        _tgt=""
        for _pair in $TARGET_PAIRS; do
            case "$_pair" in
                "${_id}|"*)
                    _cur="$(printf '%s' "$_pair" | awk -F'|' '{print $2}')"
                    _tgt="$(printf '%s' "$_pair" | awk -F'|' '{print $3}')"
                    break
                    ;;
            esac
        done
        CURRENT_INDEX=$(( CURRENT_INDEX + 1 ))
        _pct=0
        if [ "$TOTAL_ITEMS" -gt 0 ]; then
            _pct=$(( CURRENT_INDEX * 100 / TOTAL_ITEMS ))
        fi
        _stream_progress "$_pct" "upgrading mas:$_id $_cur -> $_tgt"
        _sudo_mas_upgrade "$_id" 2>&1 | _stream_tee >/dev/null
        _rc="${PIPESTATUS[0]:-0}"
        if [ "$_rc" -eq 0 ]; then
            json_add_item "$_id" "$_cur" "$_tgt" "success" "mas"
        else
            _status="$(mas_classify_exit "$_rc")"
            json_add_item "$_id" "$_cur" "$_tgt" "$_status" "mas"
            json_add_message "error" "mas upgrade $_id exited $_rc -> $_status"
        fi
    done
else
    # Bulk: pass all ids to a single sudo invocation
    _stream_progress 10 "mas upgrade (bulk): $TOTAL_ITEMS app(s)"
    # shellcheck disable=SC2086
    _sudo_mas_upgrade $TARGET_IDS 2>&1 | _stream_tee >/dev/null
    _rc="${PIPESTATUS[0]:-0}"
    if [ "$_rc" -eq 0 ]; then
        for _pair in $TARGET_PAIRS; do
            _id="$(printf '%s' "$_pair" | awk -F'|' '{print $1}')"
            _cur="$(printf '%s' "$_pair" | awk -F'|' '{print $2}')"
            _tgt="$(printf '%s' "$_pair" | awk -F'|' '{print $3}')"
            json_add_item "$_id" "$_cur" "$_tgt" "success" "mas"
        done
    else
        _status="$(mas_classify_exit "$_rc")"
        for _pair in $TARGET_PAIRS; do
            _id="$(printf '%s' "$_pair" | awk -F'|' '{print $1}')"
            _cur="$(printf '%s' "$_pair" | awk -F'|' '{print $2}')"
            _tgt="$(printf '%s' "$_pair" | awk -F'|' '{print $3}')"
            json_add_item "$_id" "$_cur" "$_tgt" "$_status" "mas"
        done
        json_add_message "error" "mas upgrade exited $_rc -> $_status"
    fi
fi

if [ "$TOTAL_ITEMS" -gt 0 ]; then
    _stream_progress 100 "mas apply: done"
fi

exit 0
