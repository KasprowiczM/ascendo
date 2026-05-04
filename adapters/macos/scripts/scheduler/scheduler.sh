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

# ---------------------------------------------------------------------------
# Payload helpers (used by install and other mutating actions).
# ---------------------------------------------------------------------------

# Read the payload file (if any) into PAYLOAD variable.
_read_payload() {
    if [ -z "$PAYLOAD_PATH" ]; then echo ""; return 0; fi
    if [ ! -f "$PAYLOAD_PATH" ]; then echo ""; return 0; fi
    cat "$PAYLOAD_PATH"
}

PAYLOAD="$(_read_payload)"

# Extract a string field from PAYLOAD via python3 (jq not guaranteed on
# every Mac; python3 is shipped on macOS 12.3+ and required by all
# Ascendo bash drivers).
_payload_get() {
    local _field="$1"
    if [ -z "$PAYLOAD" ]; then echo ""; return 0; fi
    printf '%s' "$PAYLOAD" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read() or '{}')
v = d.get('$_field', '')
if v is None: v = ''
print(v)
"
}

_payload_get_bool() {
    local _field="$1"
    local _default="$2"
    if [ -z "$PAYLOAD" ]; then echo "$_default"; return 0; fi
    printf '%s' "$PAYLOAD" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read() or '{}')
v = d.get('$_field')
if v is True:  print('true')
elif v is False: print('false')
else: print('$_default')
"
}

# Validate name: must match ^[a-z0-9-]+$ (no uppercase, no spaces, no special chars).
_validate_name() {
    case "$1" in
        ''|*[!a-z0-9-]*)
            return 1
            ;;
    esac
    return 0
}

# ---------------------------------------------------------------------------
# Action dispatch
# ---------------------------------------------------------------------------
case "$ACTION" in

    install)
        NAME="$(_payload_get name)"
        if ! _validate_name "$NAME"; then
            emit_error "invalid name: must match ^[a-z0-9-]+\$"
            exit 2
        fi
        EXPR="$(_payload_get expression)"
        if ! _parse_expression "$EXPR"; then
            emit_error "unsupported expression: $EXPR"
            exit 2
        fi
        PROFILE="$(_payload_get profile)"
        if [ -z "$PROFILE" ]; then PROFILE="full"; fi
        # Defense-in-depth: profile content guard. Pydantic ProfileName
        # already constrains this to ^[a-zA-Z0-9_-]+$ on the Python side,
        # but the bash driver is also a standalone executable and may
        # be invoked directly with crafted payloads.
        case "$PROFILE" in
            *[!a-zA-Z0-9_-]*) emit_error "invalid profile: '$PROFILE'"; exit 2 ;;
        esac
        ENABLED="$(_payload_get_bool enabled true)"
        DESCRIPTION="$(_payload_get description)"

        mkdir -p "$LAUNCH_AGENTS_DIR" "$LOGS_DIR" "$SCHEDULES_DIR"

        PLIST="$LAUNCH_AGENTS_DIR/${LABEL_PREFIX}${NAME}.plist"
        SIDECAR="$SCHEDULES_DIR/${NAME}.json"
        LABEL="${LABEL_PREFIX}${NAME}"
        LOG_FILE="$LOGS_DIR/scheduler-${NAME}.log"

        # Build StartCalendarInterval / StartInterval block.
        if [ -n "$CAL_INTERVAL_SEC" ]; then
            INTERVAL_BLOCK="    <key>StartInterval</key>
    <integer>$CAL_INTERVAL_SEC</integer>"
        else
            INTERVAL_BLOCK="    <key>StartCalendarInterval</key>
    <dict>"
            [ -n "$CAL_HOUR" ]    && INTERVAL_BLOCK="$INTERVAL_BLOCK
        <key>Hour</key>
        <integer>$CAL_HOUR</integer>"
            [ -n "$CAL_MINUTE" ]  && INTERVAL_BLOCK="$INTERVAL_BLOCK
        <key>Minute</key>
        <integer>$CAL_MINUTE</integer>"
            [ -n "$CAL_WEEKDAY" ] && INTERVAL_BLOCK="$INTERVAL_BLOCK
        <key>Weekday</key>
        <integer>$CAL_WEEKDAY</integer>"
            [ -n "$CAL_DAY" ]     && INTERVAL_BLOCK="$INTERVAL_BLOCK
        <key>Day</key>
        <integer>$CAL_DAY</integer>"
            INTERVAL_BLOCK="$INTERVAL_BLOCK
    </dict>"
        fi

        # Disabled key only when enabled=false.
        if [ "$ENABLED" = "false" ]; then
            DISABLED_BLOCK="    <key>Disabled</key>
    <true/>"
        else
            DISABLED_BLOCK=""
        fi

        cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>ascendo</string>
        <string>run</string>
        <string>--profile</string>
        <string>${PROFILE}</string>
    </array>
${INTERVAL_BLOCK}
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>
${DISABLED_BLOCK}
</dict>
</plist>
PLIST_EOF

        # Sidecar: stores description + expression + profile + enabled
        # + installed_at (launchd plists have no free-form notes channel).
        python3 - "$DESCRIPTION" <<PY_EOF
import json, datetime, pathlib, sys
desc_arg = sys.argv[1] if sys.argv[1:] else None
desc = desc_arg if desc_arg else None
p = pathlib.Path("$SIDECAR")
p.parent.mkdir(parents=True, exist_ok=True)
enabled_val = True if "$ENABLED" == "true" else False
p.write_text(json.dumps({
    "name": "$NAME",
    "expression": "$EXPR",
    "profile": "$PROFILE",
    "enabled": enabled_val,
    "description": desc,
    "installed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, indent=2))
PY_EOF

        # bootout any prior load (silent on "no such service") then bootstrap.
        launchctl bootout "gui/${UID_VAL}/${LABEL}" >/dev/null 2>&1 || true
        if [ "$ENABLED" = "true" ]; then
            launchctl bootstrap "gui/${UID_VAL}" "$PLIST" >/dev/null 2>&1 || true
        fi
        emit_json '{"ok": true}'
        exit 0
        ;;

    *)
        # uninstall, list, get, trigger land in subsequent tasks.
        emit_json '{"ok": true}'
        exit 0
        ;;
esac
