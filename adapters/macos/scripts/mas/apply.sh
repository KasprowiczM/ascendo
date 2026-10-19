#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/mas/apply.sh -- mutating Mac App Store upgrade phase
# =============================================================================
# CVE-2025-43411: `mas upgrade` ALWAYS runs under sudo (`sudo -A` when the
# dashboard wired an askpass helper, plain `sudo` for the TTY-PAM / Touch-ID
# flow) -- never bare. MAS_NO_AUTO_INDEX=1 is passed THROUGH sudo
# (`sudo env MAS_NO_AUTO_INDEX=1 mas upgrade ...`) because sudo's env_reset
# would otherwise strip it and the noisy Spotlight-indexing stderr returns.
#
# Unattended / cold-cache safety (Task 16): after the sudo warm-up a gate
# (_ascendo_sudo_gate) defers every targeted app SOFTLY -- `skipped` +
# SUDO_UNAVAILABLE, exit 0, no sudo command, no user-session retry -- when
# ASCENDO_NO_SUDO=1 (even with a hot cache) or no askpass helper is wired and
# `sudo -n true` fails. With the gate open and no askpass, `sudo -n` is used so
# a lapsed cache FAILS instead of prompting (never inside the timeout wrapper).
#
# Honesty (a run only says "success" for what it MEASURED):
#   * `mas outdated` failing/timing out is UNKNOWN: one `skipped` item with a
#     reason, no sudo, never "nothing to upgrade".
#   * After the sudo pass `mas outdated` runs AGAIN: an id that is gone =>
#     `success`; still listed => `failed` (mas can exit 0 while skipping an app);
#     the re-measurement itself failing => `skipped` (unknown) when the upgrade
#     command exited 0, `failed` otherwise.
#   * ASCENDO_MAS_USER_RETRY=1 (default OFF) allows ONE retry per leftover id in
#     the invoking user's session WITHOUT sudo -- the only place `mas upgrade`
#     runs outside sudo, because App Store receipts belong to the user, not root.
#   * `mas upgrade` is bounded by ASCENDO_MAS_UPGRADE_TIMEOUT (1800 s) and the
#     probes by ASCENDO_MAS_CHECK_TIMEOUT (120 s); a timeout is rc 124.
#   * iPad/iOS-on-Mac apps (which mas cannot update) are REPORT-ONLY: pending
#     ones are `skipped` `ipad:<name>` items with an ACTION-REQUIRED token; the
#     App Store GUI is never driven.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]            emits planned items; NEVER invokes sudo
#   [--filter a,b,...]     restrict upgrades to listed mas IDs or app names
#                          (per-id loop; the CSV delimiter is ',', so a name
#                          containing ',' can only be selected by its id)
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
# `mas upgrade` under sudo, bounded, with MAS_NO_AUTO_INDEX=1 delivered to the
# sudo'd process. `_ascendo_sudo_prefix` (ascendo_json.sh) yields "sudo -A" when
# an askpass helper is wired (dashboard flow) or plain "sudo" (Touch-ID / TTY
# flow); _ascendo_sudo_warm has already cached credentials by then.
# ascendo_run_with_timeout needs an executable, hence the prefix instead of the
# _ascendo_sudo shell function.
_sudo_mas_upgrade() {
    # shellcheck disable=SC2046  # the prefix is intentionally word-split
    ascendo_run_with_timeout "$(ascendo_mas_upgrade_timeout)" \
        $(_ascendo_sudo_prefix) env MAS_NO_AUTO_INDEX=1 "$MAS_BIN" upgrade "$@"
}

# User-session retry (opt-in only, see header). NO sudo by design.
_user_mas_upgrade() {
    ascendo_run_with_timeout "$(ascendo_mas_upgrade_timeout)" \
        env MAS_NO_AUTO_INDEX=1 "$MAS_BIN" upgrade "$@"
}

# -- sign-in probe (fail-fast; no sudo invocation on signed-out host) ----------
if ! mas_signed_in; then
    mas_report_signin_failure
    exit 0
fi

# -- helper: returns 0 if FILTER_CSV is empty OR id/name is in the CSV list ---
# Bash 3.2-safe: case glob with leading/trailing comma sentinels avoids false
# positives from substring matches (e.g. "10" inside "1011").
in_filter() {
    mas_filter_match "$FILTER_CSV" "$1" "${2:-}"
}

# -- enumerate outdated apps ---------------------------------------------------
# UNKNOWN (probe failed / timed out) is not "nothing to upgrade": report it,
# do not touch sudo, still report the (independent) iPad apps.
if ! mas_probe_outdated; then
    mas_report_outdated_unknown
    mas_report_ipad_pending "skipped" "$FILTER_CSV"
    exit 0
fi
OUTDATED_JSON="$MAS_OUTDATED_JSON"

# iPad apps: report-only, both for dry-run and real apply (never upgraded here).
mas_report_ipad_pending "skipped" "$FILTER_CSV"

# -- dry-run path: planned items only, never invoke sudo ----------------------
if [ "$DRY_RUN" -eq 1 ]; then
    printf '%s' "$OUTDATED_JSON" | jq -r '.[] | [.id, (if (.name // "") == "" then .id else .name end), .current_version, .target_version] | @tsv' 2>/dev/null | \
    while IFS="$(printf '\t')" read -r _id _name _cur _tgt; do
        [ -n "$_id" ] || continue
        in_filter "$_id" "$_name" || continue
        json_add_item "$_id" "$_cur" "$_tgt" "planned" "mas"
    done
    json_add_message "info" "dry-run: no real upgrades performed"
    exit 0
fi

# -- real apply path -----------------------------------------------------------
# Touch-ID-first sudo warming. Asks the OS-native auth dialog (Touch ID
# preferred when pam_tid is configured) BEFORE falling back to the
# dashboard's askpass cache. After this returns, the sudo calls below use the
# warmed timestamp and don't re-prompt. Best-effort.
_ascendo_sudo_warm || true   # rc 1 = unattended + cold cache (sets ASCENDO_NO_SUDO)

# Collect target ids + version pairs as space-separated strings (Bash 3.2-safe;
# no declare -A). IDs are numeric-only so spaces are safe delimiters.
TARGET_IDS="$(printf '%s' "$OUTDATED_JSON" | jq -r '.[] | [.id, (if (.name // "") == "" then .id else .name end), .current_version, .target_version] | @tsv' 2>/dev/null | \
    while IFS="$(printf '\t')" read -r _id _name _cur _tgt; do
        [ -n "$_id" ] || continue
        in_filter "$_id" "$_name" || continue
        printf '%s ' "$_id"
    done)"
TARGET_IDS="${TARGET_IDS%% }"   # strip trailing space

TARGET_PAIRS="$(printf '%s' "$OUTDATED_JSON" | jq -r '.[] | [.id, (if (.name // "") == "" then .id else .name end), .current_version, .target_version] | @tsv' 2>/dev/null | \
    while IFS="$(printf '\t')" read -r _id _name _cur _tgt; do
        [ -n "$_id" ] || continue
        in_filter "$_id" "$_name" || continue
        printf '%s|%s|%s ' "$_id" "$_cur" "$_tgt"
    done)"
TARGET_PAIRS="${TARGET_PAIRS%% }"

if [ -z "$TARGET_IDS" ]; then
    json_add_message "info" "nothing to upgrade"
    exit 0
fi

# Post-warm authorization gate (F1): `mas upgrade` always needs root
# (CVE-2025-43411). Without ASCENDO_NO_SUDO unset AND (askpass wired OR a hot
# `sudo -n true`) every targeted app is deferred SOFTLY -- `skipped`, exit 0,
# no sudo command invoked, no user-session retry (that stays opt-in).
if ! _ascendo_sudo_gate; then
    for _p in $TARGET_PAIRS; do
        IFS='|' read -r _id _cur _tgt <<< "$_p"
        json_add_item "$_id" "$_cur" "$_tgt" "skipped" "mas"
        json_add_message "warn" "$_id: skipped -- ${ASCENDO_SUDO_GATE_TEXT} (${ASCENDO_SUDO_GATE_CODE})" "SUDO_UNAVAILABLE"
    done
    exit 0
fi
# UNATTENDED runs only: every sudo call is `sudo -n` (unless an askpass helper
# is wired) so a lapsed cache fails the install instead of prompting in a
# background process group. Interactive runs keep plain `sudo`: a TTY-less
# sudo ticket is keyed on the PARENT pid, and these installs run from a
# pipeline subshell / under `timeout`, so `-n` would miss the warmed ticket.
if _ascendo_sudo_unattended; then
    export ASCENDO_SUDO_NONINTERACTIVE=1
fi

# Total count for live-stream progress accounting.
TOTAL_ITEMS=0
for _id in $TARGET_IDS; do
    [ -n "$_id" ] && TOTAL_ITEMS=$(( TOTAL_ITEMS + 1 ))
done
CURRENT_INDEX=0
_stream_progress 0 "mas apply: $TOTAL_ITEMS app(s)"

# _pair_field <id> <1|2|3>: current/target of an id from TARGET_PAIRS.
_pair_field() {
    local _want="$1" _n="$2" _p
    for _p in $TARGET_PAIRS; do
        case "$_p" in
            "${_want}|"*) printf '%s' "$_p" | awk -F'|' -v n="$_n" '{print $n}'; return 0 ;;
        esac
    done
}

# ---- upgrade (per-id when --filter is set; bulk otherwise) -------------------
# UPGRADE_RCS holds " <id>:<rc>" for every targeted id (bulk => same rc).
UPGRADE_RCS=""
if [ -n "$FILTER_CSV" ]; then
    for _id in $TARGET_IDS; do
        _cur="$(_pair_field "$_id" 2)"
        _tgt="$(_pair_field "$_id" 3)"
        CURRENT_INDEX=$(( CURRENT_INDEX + 1 ))
        _pct=$(( CURRENT_INDEX * 100 / TOTAL_ITEMS * 8 / 10 ))
        _stream_progress "$_pct" "upgrading mas:$_id $_cur -> $_tgt"
        _sudo_mas_upgrade "$_id" 2>&1 | _stream_tee >/dev/null
        _rc="${PIPESTATUS[0]:-0}"
        UPGRADE_RCS="$UPGRADE_RCS $_id:$_rc"
    done
else
    _stream_progress 10 "mas upgrade (bulk): $TOTAL_ITEMS app(s)"
    # shellcheck disable=SC2086  # IDs are numeric, word splitting is intended
    _sudo_mas_upgrade $TARGET_IDS 2>&1 | _stream_tee >/dev/null
    _rc="${PIPESTATUS[0]:-0}"
    for _id in $TARGET_IDS; do
        UPGRADE_RCS="$UPGRADE_RCS $_id:$_rc"
    done
fi

# _rc_of <id>: the sudo-pass exit code recorded for an id.
_rc_of() {
    local _e
    for _e in $UPGRADE_RCS; do
        case "$_e" in "$1:"*) printf '%s' "${_e#*:}"; return 0 ;; esac
    done
    printf '1'
}

# ---- second measurement ------------------------------------------------------
_stream_progress 85 "mas apply: re-measuring (mas outdated)"
REMEASURED=1
STILL_OUTDATED=""
_remeasure() {
    if mas_probe_outdated; then
        REMEASURED=1
        STILL_OUTDATED=" $(printf '%s' "$MAS_OUTDATED_JSON" | jq -r '.[].id' 2>/dev/null | tr '\n' ' ') "
    else
        REMEASURED=0
        STILL_OUTDATED=""
    fi
}
_remeasure

# ---- optional user-session retry (ASCENDO_MAS_USER_RETRY=1, default OFF) ------
# One retry per leftover id whose sudo pass exited 0 (mas said "done" but the app
# is still outdated: a root-context skip), run as the invoking USER, then one more
# measurement. Never a loop; never for ids whose sudo pass failed or timed out.
USER_RETRIED=" "
if [ "${ASCENDO_MAS_USER_RETRY:-0}" = "1" ] && [ "$REMEASURED" -eq 1 ]; then
    for _id in $TARGET_IDS; do
        case "$STILL_OUTDATED" in (*" $_id "*) ;; (*) continue ;; esac
        [ "$(_rc_of "$_id")" = "0" ] || continue
        json_add_message "info" "mas:$_id still outdated after sudo mas upgrade; retrying once in the user session (ASCENDO_MAS_USER_RETRY=1)"
        _stream_item "mas user-session retry: $_id"
        _user_mas_upgrade "$_id" 2>&1 | _stream_tee >/dev/null
        USER_RETRIED="$USER_RETRIED$_id "
    done
    if [ "$USER_RETRIED" != " " ]; then
        _remeasure
    fi
fi

# ---- items: success only for what the last measurement proves ----------------
for _pair in $TARGET_PAIRS; do
    _id="$(printf '%s' "$_pair" | awk -F'|' '{print $1}')"
    _cur="$(printf '%s' "$_pair" | awk -F'|' '{print $2}')"
    _tgt="$(printf '%s' "$_pair" | awk -F'|' '{print $3}')"
    _rc="$(_rc_of "$_id")"
    if [ "$REMEASURED" -ne 1 ]; then
        if [ "$_rc" = "0" ]; then
            json_add_item "$_id" "$_cur" "$_tgt" "skipped" "mas"
            json_add_message "warn" "mas:$_id: upgrade exited 0 but the re-measurement (mas outdated $(mas_probe_reason)) failed; outcome unknown"
        else
            json_add_item "$_id" "$_cur" "$_tgt" "failed" "mas"
            json_add_message "error" "mas:$_id: sudo mas upgrade $([ "$_rc" = 124 ] && printf 'timed out after %ss' "$(ascendo_mas_upgrade_timeout)" || printf 'exited %s' "$_rc") and the re-measurement failed"
        fi
        continue
    fi
    case "$STILL_OUTDATED" in
        (*" $_id "*)
            json_add_item "$_id" "$_cur" "$_tgt" "failed" "mas"
            if [ "$_rc" = "124" ]; then
                json_add_message "error" "mas:$_id: sudo mas upgrade timed out after $(ascendo_mas_upgrade_timeout)s; still outdated"
            elif [ "$_rc" != "0" ]; then
                json_add_message "error" "mas:$_id: sudo mas upgrade exited $_rc; still outdated"
            else
                json_add_message "error" "mas:$_id: still outdated after sudo mas upgrade (rc=0)"
            fi
            ;;
        (*)
            json_add_item "$_id" "$_cur" "$_tgt" "success" "mas"
            case "$USER_RETRIED" in
                (*" $_id "*) json_add_message "info" "mas:$_id: upgraded by the user-session retry (sudo pass left it outdated)" ;;
                (*) [ "$_rc" = "0" ] || json_add_message "warn" "mas:$_id: mas upgrade exited $_rc but the app is no longer outdated" ;;
            esac
            ;;
    esac
done

_stream_progress 100 "mas apply: done"

exit 0
