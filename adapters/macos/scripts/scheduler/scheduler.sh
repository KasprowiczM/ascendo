#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/scheduler/scheduler.sh
# launchd LaunchAgent driver. Mirrors the M3.13 Windows Task Scheduler
# pattern (scheduler.ps1) — JSON in, JSON out, single dispatch on --action.
#
# Actions:
#   --action install   register/overwrite a per-user LaunchAgent.
#                      Reads ScheduleSpec from --payload-path as JSON.
#   --action uninstall remove an Ascendo-owned LaunchAgent by name.
#   --action list      enumerate Ascendo-owned LaunchAgents (JSON array).
#   --action get       return one entry by name (JSON object or null).
#   --action trigger   run a registered agent immediately.
#
# Bash 3.2 compatible (no declare -A, mapfile, readarray).
# =============================================================================
set -o pipefail

LABEL_PREFIX="dev.ascendo."

# Day-of-week table (Sun=0). Bash 3.2: case statement, not associative array.
_weekday_to_int() {
    case "$1" in
        SUN|sun) echo 0 ;;
        MON|mon) echo 1 ;;
        TUE|tue) echo 2 ;;
        WED|wed) echo 3 ;;
        THU|thu) echo 4 ;;
        FRI|fri) echo 5 ;;
        SAT|sat) echo 6 ;;
        *) return 1 ;;
    esac
}

# DSL -> globals (CAL_HOUR / CAL_MINUTE / CAL_WEEKDAY / CAL_DAY /
# CAL_INTERVAL_SEC).  Returns 0 on success, 2 on parse failure.
# Globals are unset (empty) when not applicable.
_parse_expression() {
    local _expr="$1"
    CAL_HOUR=""
    CAL_MINUTE=""
    CAL_WEEKDAY=""
    CAL_DAY=""
    CAL_INTERVAL_SEC=""

    # Normalise: collapse runs of spaces, trim.
    local _norm
    _norm="$(printf '%s' "$_expr" | tr -s ' ' | sed -e 's/^ *//' -e 's/ *$//')"

    # Split into tokens via positional params (bash 3.2 friendly).
    set -- $_norm
    local _kw="$1"

    # Uppercase the keyword for matching.
    _kw="$(printf '%s' "$_kw" | tr '[:lower:]' '[:upper:]')"

    case "$_kw" in
        DAILY)
            # DAILY HH:MM
            [ "$#" -eq 2 ] || return 2
            _split_time "$2" || return 2
            ;;
        WEEKLY)
            # WEEKLY DAY HH:MM
            [ "$#" -eq 3 ] || return 2
            CAL_WEEKDAY="$(_weekday_to_int "$2")" || return 2
            _split_time "$3" || return 2
            ;;
        MONTHLY)
            # MONTHLY HH:MM         -> day=1
            # MONTHLY DAY HH:MM     -> day=DAY (1..31)
            if [ "$#" -eq 2 ]; then
                CAL_DAY=1
                _split_time "$2" || return 2
            elif [ "$#" -eq 3 ]; then
                case "$2" in
                    ''|*[!0-9]*) return 2 ;;
                esac
                if [ "$2" -lt 1 ] || [ "$2" -gt 31 ]; then return 2; fi
                CAL_DAY="$2"
                _split_time "$3" || return 2
            else
                return 2
            fi
            ;;
        HOURLY)
            # HOURLY :MM
            [ "$#" -eq 2 ] || return 2
            case "$2" in
                :*) ;;
                *) return 2 ;;
            esac
            local _mm="${2#:}"
            case "$_mm" in
                ''|*[!0-9]*) return 2 ;;
            esac
            if [ "$_mm" -lt 0 ] || [ "$_mm" -gt 59 ]; then return 2; fi
            CAL_MINUTE="$_mm"
            ;;
        MINUTE)
            # MINUTE N -> StartInterval=N*60 (N>=1)
            [ "$#" -eq 2 ] || return 2
            case "$2" in
                ''|*[!0-9]*) return 2 ;;
            esac
            if [ "$2" -lt 1 ]; then return 2; fi
            CAL_INTERVAL_SEC="$(($2 * 60))"
            ;;
        *)
            return 2
            ;;
    esac
    return 0
}

# Helper: split HH:MM into CAL_HOUR + CAL_MINUTE (no leading-zero tolerance
# beyond what bash's arithmetic accepts; "03" is fine, "3" is fine, "00" is 0).
_split_time() {
    local _t="$1"
    case "$_t" in
        *:*) ;;
        *) return 1 ;;
    esac
    local _hh="${_t%%:*}"
    local _mm="${_t##*:}"
    case "$_hh$_mm" in
        ''|*[!0-9]*) return 1 ;;
    esac
    # Strip leading zeros to avoid octal interpretation in arithmetic
    # (bash treats 08, 09 as bad octal). Convert via 10# prefix.
    _hh=$((10#$_hh))
    _mm=$((10#$_mm))
    if [ "$_hh" -lt 0 ] || [ "$_hh" -gt 23 ]; then return 1; fi
    if [ "$_mm" -lt 0 ] || [ "$_mm" -gt 59 ]; then return 1; fi
    CAL_HOUR="$_hh"
    CAL_MINUTE="$_mm"
    return 0
}

# When sourced by tests with PARSE_EXPR_ONLY=1, return now (skip dispatch).
if [ "${PARSE_EXPR_ONLY:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# Argument parsing + validation (skipped when sourced with PARSE_EXPR_ONLY=1)
# ---------------------------------------------------------------------------
ACTION=""
OUTPUT_PATH=""
PAYLOAD_PATH=""

while [ $# -gt 0 ]; do
    case "$1" in
        --action)       ACTION="$2";       shift 2 ;;
        --output-path)  OUTPUT_PATH="$2";  shift 2 ;;
        --payload-path) PAYLOAD_PATH="$2"; shift 2 ;;
        *) printf 'scheduler.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$ACTION" ] || [ -z "$OUTPUT_PATH" ]; then
    printf 'scheduler.sh: missing required args (--action, --output-path)\n' >&2
    exit 2
fi

# Test override: ASCENDO_HOME_OVERRIDE redirects all reads/writes away
# from the operator's real ~/Library. Real runs leave it unset.
HOME_BASE="${ASCENDO_HOME_OVERRIDE:-$HOME}"
LAUNCH_AGENTS_DIR="$HOME_BASE/Library/LaunchAgents"
LOGS_DIR="$HOME_BASE/Library/Logs/Ascendo"
SCHEDULES_DIR="$HOME_BASE/Library/Application Support/Ascendo/schedules"

UID_VAL="$(id -u)"

emit_json() {
    local _payload="$1"
    local _dir
    _dir="$(dirname "$OUTPUT_PATH")"
    [ -d "$_dir" ] || mkdir -p "$_dir"
    printf '%s\n' "$_payload" > "$OUTPUT_PATH"
}

emit_error() {
    emit_json "{\"error\": $(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')}"
}

case "$ACTION" in
    install|uninstall|list|get|trigger)
        ;;
    *)
        printf 'scheduler.sh: unknown action: %s\n' "$ACTION" >&2
        exit 2
        ;;
esac

# Action handlers land in subsequent tasks.
emit_json '{"ok": true}'
exit 0
