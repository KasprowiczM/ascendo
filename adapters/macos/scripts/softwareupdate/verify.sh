#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/softwareupdate/verify.sh -- read-only OS-update verify phase
# =============================================================================
# Reads sibling apply__softwareupdate.json, re-runs softwareupdate -l, and for
# each apply success/triggered item checks whether the same Label still appears
# in the output. If it does, the update is still pending -> verify failed
# (except a restart update while the apply run flagged needs_reboot:
# `triggered`, it completes after the reboot). If not, the update is confirmed
# installed -> success.
# If `softwareupdate -l` itself fails/times out the state is unknown ->
# `skipped` with a reason (never success).
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
# shellcheck source=../../lib/ascendo_softwareupdate.sh
. "$ADAPTER_LIB/ascendo_softwareupdate.sh"

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
        # Defense in depth: the orchestrator only sends --filter to phases that
        # act on it; a stray one must never break a read-only/cleanup phase.
        --filter)     shift; [ $# -gt 0 ] && shift ;;
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

# -- extract apply items to verify: status success OR triggered ---------------
# (`triggered` = restart label staged with -R; it completes after the reboot.)
APPLY_SUCCESS_IDS="$(jq -r '.items[]? | select(.status=="success" or .status=="triggered") | .id | select(startswith("rosetta:") | not)' "$APPLY_SIDECAR" 2>/dev/null)"

if [ -z "$APPLY_SUCCESS_IDS" ]; then
    json_add_message "info" "No successful or triggered apply items found in apply__softwareupdate.json; nothing to verify."
    exit 0
fi

# -- re-run softwareupdate -l (same parser as check/plan/apply) --------------
# A failed/timed-out probe leaves the outcome UNKNOWN: skipped + reason, never
# success (and not a fabricated failure either).
ascendo_su_list
_pending_ok=1
STILL_PENDING=""      # newline-delimited labels still offered
STILL_RESTART=""      # ...of which need a restart
if [ "$SU_RC" -ne 0 ]; then
    _pending_ok=0
    if [ "$SU_RC" -eq 124 ]; then
        json_add_message "warn" "softwareupdate -l timed out during verify; post-apply state unknown"
    else
        json_add_message "warn" "softwareupdate -l failed during verify (exit $SU_RC): $SU_OUT -- post-apply state unknown"
    fi
else
    CUR_OS="$(ascendo_su_current_os)"
    if SU_RECORDS="$(ascendo_su_records "$SU_OUT" "$CUR_OS")"; then
        if ascendo_su_listing_unparseable "$SU_OUT" "$SU_RECORDS"; then
            _pending_ok=0
            json_add_message "warn" "softwareupdate -l returned a listing that could not be parsed during verify; post-apply state unknown"
        elif [ -n "$SU_RECORDS" ]; then
            while IFS="$ASCENDO_SU_US" read -r _pk _plabel _ptitle _pver _prestart _prec; do
                [ -n "$_plabel" ] || continue
                STILL_PENDING="${STILL_PENDING}${STILL_PENDING:+
}${_plabel}"
                if [ "$_prestart" = "1" ] || [ "$_pk" = "MAJOR" ]; then
                    STILL_RESTART="${STILL_RESTART}${STILL_RESTART:+
}${_plabel}"
                fi
            done <<< "$SU_RECORDS"
        fi
    else
        _pending_ok=0
        json_add_message "warn" "softwareupdate listing could not be parsed during verify; post-apply state unknown"
    fi
fi

_list_has() {
    local _line
    while IFS= read -r _line; do
        [ "$_line" = "$2" ] && return 0
    done <<< "$1"
    return 1
}

APPLY_NEEDS_REBOOT="$(jq -r '.needs_reboot // false' "$APPLY_SIDECAR" 2>/dev/null)"

# -- emit one verify item per apply success id ---------------------------------
# Iterate in the CURRENT shell (here-string) so labels stay whole strings.
while IFS= read -r _id; do
    [ -n "$_id" ] || continue
    RESOLVED="$(jq -r --arg id "$_id" \
        '.items[]? | select(.id==$id) | (.resolved_version // .target_version // "")' \
        "$APPLY_SIDECAR" 2>/dev/null | head -1)"
    APPLY_STATUS="$(jq -r --arg id "$_id" \
        '[.items[]? | select(.id==$id) | .status][0] // ""' "$APPLY_SIDECAR" 2>/dev/null)"
    if [ "$_pending_ok" -eq 0 ]; then
        json_add_item "$_id" "" "$RESOLVED" "skipped" "softwareupdate"
    elif _list_has "$STILL_PENDING" "$_id"; then
        if [ "$APPLY_NEEDS_REBOOT" = "true" ] \
           && { [ "$APPLY_STATUS" = "triggered" ] || _list_has "$STILL_RESTART" "$_id"; }; then
            # A staged restart update stays listed until the reboot: not a
            # failure, and not yet a success either.
            json_add_item "$_id" "" "$RESOLVED" "triggered" "softwareupdate"
            json_add_message "info" "${_id}: still listed -- it is applied on restart (reboot required)"
        else
            json_add_item "$_id" "" "" "failed" "softwareupdate"
            json_add_message "error" "${_id}: still pending after install"
        fi
    else
        json_add_item "$_id" "" "$RESOLVED" "success" "softwareupdate"
    fi
done <<< "$APPLY_SUCCESS_IDS"

exit 0
