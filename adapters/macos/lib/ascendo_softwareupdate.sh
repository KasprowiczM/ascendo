#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_softwareupdate.sh -- shared softwareupdate helpers
# =============================================================================
# Sourced (after ascendo_json.sh) by scripts/softwareupdate/{check,plan,apply,
# verify}.sh so every phase parses and classifies `softwareupdate -l` the same
# way (su_classify.py, a port of macOS_updates lib/python/system_updates.py).
#
# Bash 3.2 compatible: no associative arrays, no mapfile; list values are
# newline-delimited strings and every label is handled as ONE whole string
# (labels contain spaces, e.g. "macOS Sequoia 15.1-24B83").
#
#   ascendo_su_list            run `softwareupdate -l --include-config-data`
#                              (C locale, bounded); sets SU_OUT and SU_RC
#                              (SU_RC 124 = timed out)
#   ascendo_su_current_os      print the installed macOS version ("" if unknown)
#   ascendo_su_records OUT CUR print classifier records (one per update; fields
#                              joined by $ASCENDO_SU_US -- see su_classify.py)
#   ascendo_su_major_allowed   true when ASCENDO_ALLOW_MAJOR_UPGRADE=1
#   ascendo_su_emit_pending    check/plan body: emit one item per record
#
# Record fields, in order: KIND label title version restart recommended
#   KIND = SAME | OTHER | MAJOR   restart/recommended = 1 | 0
# =============================================================================
# shellcheck shell=bash

if [ -n "${_ASCENDO_SU_LOADED:-}" ]; then
    return 0 2>/dev/null || true
fi
_ASCENDO_SU_LOADED=1

_ASCENDO_SU_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ASCENDO_SU_CLASSIFY="$_ASCENDO_SU_DIR/su_classify.py"
# ASCII unit separator: a NON-whitespace IFS char so `read` keeps empty fields.
ASCENDO_SU_US="$(printf '\037')"

SU_OUT=""
SU_RC=0

# Seconds allowed for one `softwareupdate -l` probe. (Installs are NOT bounded
# here: a restart label may legitimately run for a long time; the Python
# manager's own wall-clock phase timeout is the outer bound.)
ascendo_su_list() {
    SU_RC=0
    SU_OUT="$(ascendo_run_with_timeout "${ASCENDO_SU_LIST_TIMEOUT:-300}" \
        env LANG=C LC_ALL=C "${SU_BIN:-/usr/sbin/softwareupdate}" -l --include-config-data 2>&1)" || SU_RC=$?
    return "$SU_RC"
}

ascendo_su_current_os() {
    sw_vers -productVersion 2>/dev/null | head -1 | tr -d '[:space:]'
}

ascendo_su_records() {
    printf '%s\n' "$1" | python3 "$_ASCENDO_SU_CLASSIFY" --current "$2"
}

ascendo_su_major_allowed() {
    [ "${ASCENDO_ALLOW_MAJOR_UPGRADE:-0}" = "1" ]
}

# ascendo_su_emit_pending <records> <current_os>
#   Emits the check/plan view of the pending updates and sets NEEDS_REBOOT
#   (global) when an update that WOULD be installed needs a restart.
#   SAME/OTHER -> planned (current=target=offered version, as before).
#   MAJOR      -> skipped + ACTION-REQUIRED token (never a plannable upgrade)
#                 unless ASCENDO_ALLOW_MAJOR_UPGRADE=1.
ascendo_su_emit_pending() {
    local _recs="$1" _cur="$2"
    local _kind _label _title _ver _restart _rec
    NEEDS_REBOOT=0
    [ -n "$_recs" ] || return 0
    while IFS="$ASCENDO_SU_US" read -r _kind _label _title _ver _restart _rec; do
        [ -n "$_label" ] || continue
        if [ "$_kind" = "MAJOR" ] && ! ascendo_su_major_allowed; then
            json_add_item "$_label" "$_cur" "$_ver" "skipped" "softwareupdate"
            json_add_message "warn" \
                "${_label}: skipped -- major macOS upgrade is never installed automatically | $(ascendo_action_required_token major_upgrade "$_cur" "$_ver")"
            continue
        fi
        json_add_item "$_label" "$_ver" "$_ver" "planned" "softwareupdate"
        if [ "$_restart" = "1" ] || [ "$_kind" = "MAJOR" ]; then
            NEEDS_REBOOT=1
        fi
    done <<< "$_recs"
}
