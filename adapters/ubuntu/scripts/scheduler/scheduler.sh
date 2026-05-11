#!/usr/bin/env bash
# =============================================================================
# adapters/ubuntu/scripts/scheduler/scheduler.sh
# systemd --user timer driver. Mirrors the macOS launchd scheduler.sh
# JSON-in / JSON-out contract.
#
# Actions:
#   --action install   register/overwrite a per-user systemd timer+service.
#                      Reads ScheduleSpec from --payload-path as JSON.
#   --action uninstall remove an Ascendo-owned timer+service by name.
#   --action list      enumerate Ascendo-owned timers (JSON array).
#   --action get       return one entry by name (JSON object or {"error":...}).
#   --action trigger   run a registered service immediately (oneshot start).
#
# Bash 5.0+. systemctl --user. Per-user; no root.
# =============================================================================
set -o pipefail

UNIT_PREFIX="ascendo-"

# Day-of-week translation (long systemd token form).
_weekday_to_systemd() {
    case "$1" in
        SUN|sun) echo "Sun" ;;
        MON|mon) echo "Mon" ;;
        TUE|tue) echo "Tue" ;;
        WED|wed) echo "Wed" ;;
        THU|thu) echo "Thu" ;;
        FRI|fri) echo "Fri" ;;
        SAT|sat) echo "Sat" ;;
        *) return 1 ;;
    esac
}

# DSL -> globals (SD_ONCALENDAR / SD_ONUNITACTIVE).
# Translation:
#   DAILY HH:MM        -> OnCalendar=*-*-* HH:MM:00
#   WEEKLY DAY HH:MM   -> OnCalendar=<Day> *-*-* HH:MM:00
#   MONTHLY HH:MM      -> OnCalendar=*-*-01 HH:MM:00
#   MONTHLY DD HH:MM   -> OnCalendar=*-*-DD HH:MM:00
#   HOURLY :MM         -> OnCalendar=*-*-* *:MM:00
#   MINUTE N           -> OnUnitActiveSec=Nmin + OnBootSec=1min
_parse_expression() {
    local _expr="$1"
    SD_ONCALENDAR=""
    SD_ONUNITACTIVE=""

    local _norm
    _norm="$(printf '%s' "$_expr" | tr -s ' ' | sed -e 's/^ *//' -e 's/ *$//')"

    set -- $_norm
    local _kw="$1"
    _kw="$(printf '%s' "$_kw" | tr '[:lower:]' '[:upper:]')"

    local _hh _mm _wd _dd

    case "$_kw" in
        DAILY)
            [ "$#" -eq 2 ] || return 2
            _split_time "$2" || return 2
            _hh="$CAL_HOUR"; _mm="$CAL_MINUTE"
            SD_ONCALENDAR="$(printf '*-*-* %02d:%02d:00' "$_hh" "$_mm")"
            ;;
        WEEKLY)
            [ "$#" -eq 3 ] || return 2
            _wd="$(_weekday_to_systemd "$2")" || return 2
            _split_time "$3" || return 2
            _hh="$CAL_HOUR"; _mm="$CAL_MINUTE"
            SD_ONCALENDAR="$(printf '%s *-*-* %02d:%02d:00' "$_wd" "$_hh" "$_mm")"
            ;;
        MONTHLY)
            if [ "$#" -eq 2 ]; then
                _dd=1
                _split_time "$2" || return 2
            elif [ "$#" -eq 3 ]; then
                case "$2" in ''|*[!0-9]*) return 2 ;; esac
                if [ "$2" -lt 1 ] || [ "$2" -gt 31 ]; then return 2; fi
                _dd="$2"
                _split_time "$3" || return 2
            else
                return 2
            fi
            _hh="$CAL_HOUR"; _mm="$CAL_MINUTE"
            SD_ONCALENDAR="$(printf '*-*-%02d %02d:%02d:00' "$_dd" "$_hh" "$_mm")"
            ;;
        HOURLY)
            [ "$#" -eq 2 ] || return 2
            case "$2" in
                :*) ;;
                *) return 2 ;;
            esac
            local _mm2="${2#:}"
            case "$_mm2" in ''|*[!0-9]*) return 2 ;; esac
            if [ "$_mm2" -lt 0 ] || [ "$_mm2" -gt 59 ]; then return 2; fi
            _mm2=$((10#$_mm2))
            SD_ONCALENDAR="$(printf '*-*-* *:%02d:00' "$_mm2")"
            ;;
        MINUTE)
            [ "$#" -eq 2 ] || return 2
            case "$2" in ''|*[!0-9]*) return 2 ;; esac
            if [ "$2" -lt 1 ]; then return 2; fi
            SD_ONUNITACTIVE="${2}min"
            ;;
        *)
            return 2
            ;;
    esac
    return 0
}

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
    _hh=$((10#$_hh))
    _mm=$((10#$_mm))
    if [ "$_hh" -lt 0 ] || [ "$_hh" -gt 23 ]; then return 1; fi
    if [ "$_mm" -lt 0 ] || [ "$_mm" -gt 59 ]; then return 1; fi
    CAL_HOUR="$_hh"
    CAL_MINUTE="$_mm"
    return 0
}

# Sourced-by-tests escape hatch.
if [ "${PARSE_EXPR_ONLY:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# Argument parsing
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

# Path overrides for tests. Real runs use $XDG_CONFIG_HOME/$XDG_DATA_HOME.
HOME_BASE="${ASCENDO_HOME_OVERRIDE:-$HOME}"
XDG_CONFIG="${ASCENDO_XDG_CONFIG_OVERRIDE:-${XDG_CONFIG_HOME:-$HOME_BASE/.config}}"
XDG_DATA="${ASCENDO_XDG_DATA_OVERRIDE:-${XDG_DATA_HOME:-$HOME_BASE/.local/share}}"

UNITS_DIR="$XDG_CONFIG/systemd/user"
SCHEDULES_DIR="$XDG_DATA/ascendo/schedules"

# systemctl override for tests (a fake on PATH that just echoes args).
SYSTEMCTL_BIN="${ASCENDO_SYSTEMCTL_BIN:-systemctl}"

# ascendo binary lookup (best effort; tests don't care).
ASCENDO_BIN="${ASCENDO_BIN_OVERRIDE:-}"
if [ -z "$ASCENDO_BIN" ]; then
    ASCENDO_BIN="$(command -v ascendo 2>/dev/null || true)"
fi
if [ -z "$ASCENDO_BIN" ]; then
    ASCENDO_BIN="/usr/bin/env ascendo"
fi

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
# Payload helpers
# ---------------------------------------------------------------------------
_read_payload() {
    if [ -z "$PAYLOAD_PATH" ]; then echo ""; return 0; fi
    if [ ! -f "$PAYLOAD_PATH" ]; then echo ""; return 0; fi
    cat "$PAYLOAD_PATH"
}

PAYLOAD="$(_read_payload)"

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

# name: ^[a-z0-9-]+$
_validate_name() {
    case "$1" in
        ''|*[!a-z0-9-]*) return 1 ;;
    esac
    return 0
}

# profile: ^[a-zA-Z0-9_-]+$
_validate_profile() {
    case "$1" in
        ''|*[!a-zA-Z0-9_-]*) return 1 ;;
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
        if ! _validate_profile "$PROFILE"; then
            emit_error "invalid profile: '$PROFILE'"
            exit 2
        fi
        ENABLED="$(_payload_get_bool enabled true)"
        DESCRIPTION="$(_payload_get description)"

        mkdir -p "$UNITS_DIR" "$SCHEDULES_DIR"

        SERVICE_UNIT="$UNITS_DIR/${UNIT_PREFIX}${NAME}.service"
        TIMER_UNIT="$UNITS_DIR/${UNIT_PREFIX}${NAME}.timer"
        SIDECAR="$SCHEDULES_DIR/${NAME}.json"

        # Build [Timer] body.
        if [ -n "$SD_ONUNITACTIVE" ]; then
            TIMER_BODY="OnBootSec=1min
OnUnitActiveSec=${SD_ONUNITACTIVE}"
        else
            TIMER_BODY="OnCalendar=${SD_ONCALENDAR}"
        fi

        # .service
        cat > "$SERVICE_UNIT" <<SVC_EOF
[Unit]
Description=Ascendo scheduled run: ${NAME}

[Service]
Type=oneshot
ExecStart=${ASCENDO_BIN} run --profile ${PROFILE}
SVC_EOF

        # .timer
        cat > "$TIMER_UNIT" <<TIM_EOF
[Unit]
Description=Ascendo timer: ${NAME}

[Timer]
${TIMER_BODY}
Persistent=true
Unit=${UNIT_PREFIX}${NAME}.service

[Install]
WantedBy=timers.target
TIM_EOF

        # Sidecar JSON.
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
    "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, indent=2))
PY_EOF

        # systemctl daemon-reload + enable --now (or just daemon-reload if disabled).
        "$SYSTEMCTL_BIN" --user daemon-reload >/dev/null 2>&1 || true
        if [ "$ENABLED" = "true" ]; then
            "$SYSTEMCTL_BIN" --user enable --now "${UNIT_PREFIX}${NAME}.timer" >/dev/null 2>&1 || true
        else
            "$SYSTEMCTL_BIN" --user disable --now "${UNIT_PREFIX}${NAME}.timer" >/dev/null 2>&1 || true
        fi

        emit_json "{\"ok\": true, \"name\": \"$NAME\", \"expression\": $(printf '%s' "$EXPR" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')}"
        exit 0
        ;;

    uninstall)
        NAME="$(_payload_get name)"
        if ! _validate_name "$NAME"; then
            emit_error "invalid name: must match ^[a-z0-9-]+\$"
            exit 2
        fi
        SERVICE_UNIT="$UNITS_DIR/${UNIT_PREFIX}${NAME}.service"
        TIMER_UNIT="$UNITS_DIR/${UNIT_PREFIX}${NAME}.timer"
        SIDECAR="$SCHEDULES_DIR/${NAME}.json"

        "$SYSTEMCTL_BIN" --user disable --now "${UNIT_PREFIX}${NAME}.timer" >/dev/null 2>&1 || true
        rm -f "$SERVICE_UNIT" "$TIMER_UNIT" "$SIDECAR"
        "$SYSTEMCTL_BIN" --user daemon-reload >/dev/null 2>&1 || true

        emit_json "{\"ok\": true, \"name\": \"$NAME\"}"
        exit 0
        ;;

    list)
        if [ ! -d "$UNITS_DIR" ]; then
            emit_json '{"schedules": []}'
            exit 0
        fi
        export UNITS_DIR UNIT_PREFIX SCHEDULES_DIR SYSTEMCTL_BIN
        RESULT="$(python3 - <<'PY_EOF'
import json, os, pathlib, subprocess
units_dir = pathlib.Path(os.environ["UNITS_DIR"])
prefix = os.environ["UNIT_PREFIX"]
schedules_dir = pathlib.Path(os.environ["SCHEDULES_DIR"])
systemctl = os.environ["SYSTEMCTL_BIN"]

out = []
if units_dir.is_dir():
    for timer_path in sorted(units_dir.glob(f"{prefix}*.timer")):
        name = timer_path.stem[len(prefix):]
        sidecar_path = schedules_dir / f"{name}.json"
        if sidecar_path.is_file():
            try:
                meta = json.loads(sidecar_path.read_text())
            except (OSError, json.JSONDecodeError):
                meta = {}
        else:
            meta = {}

        # Probe systemctl is-enabled (silent on failure).
        try:
            r = subprocess.run(
                [systemctl, "--user", "is-enabled", f"{prefix}{name}.timer"],
                capture_output=True, text=True, timeout=5,
            )
            enabled_now = (r.stdout.strip() == "enabled") if r.returncode == 0 else False
        except (OSError, subprocess.TimeoutExpired):
            enabled_now = bool(meta.get("enabled", True))

        out.append({
            "name": name,
            "expression": meta.get("expression", ""),
            "profile": meta.get("profile", "full"),
            "enabled": bool(meta.get("enabled", enabled_now)),
            "description": meta.get("description"),
        })
print(json.dumps({"schedules": out}))
PY_EOF
)"
        emit_json "$RESULT"
        exit 0
        ;;

    get)
        NAME="$(_payload_get name)"
        if ! _validate_name "$NAME"; then
            emit_error "invalid name: must match ^[a-z0-9-]+\$"
            exit 2
        fi
        TIMER_UNIT="$UNITS_DIR/${UNIT_PREFIX}${NAME}.timer"
        if [ ! -f "$TIMER_UNIT" ]; then
            emit_error "no such schedule: $NAME"
            exit 30
        fi
        export SCHEDULES_DIR ASCENDO_GET_NAME="$NAME" SYSTEMCTL_BIN UNIT_PREFIX
        RESULT="$(python3 - <<'PY_EOF'
import json, os, pathlib, subprocess
name = os.environ["ASCENDO_GET_NAME"]
schedules_dir = pathlib.Path(os.environ["SCHEDULES_DIR"])
systemctl = os.environ["SYSTEMCTL_BIN"]
prefix = os.environ["UNIT_PREFIX"]

sidecar = schedules_dir / f"{name}.json"
meta = {}
if sidecar.is_file():
    try:
        meta = json.loads(sidecar.read_text())
    except (OSError, json.JSONDecodeError):
        meta = {}

def _probe(verb):
    try:
        r = subprocess.run(
            [systemctl, "--user", verb, f"{prefix}{name}.timer"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""

active_text = _probe("is-active")
enabled_text = _probe("is-enabled")

print(json.dumps({
    "name": name,
    "expression": meta.get("expression", ""),
    "profile": meta.get("profile", "full"),
    "enabled": bool(meta.get("enabled", enabled_text == "enabled")),
    "description": meta.get("description"),
    "active": active_text == "active",
}))
PY_EOF
)"
        emit_json "$RESULT"
        exit 0
        ;;

    trigger)
        NAME="$(_payload_get name)"
        if ! _validate_name "$NAME"; then
            emit_error "invalid name: must match ^[a-z0-9-]+\$"
            exit 2
        fi
        SERVICE_UNIT="$UNITS_DIR/${UNIT_PREFIX}${NAME}.service"
        if [ ! -f "$SERVICE_UNIT" ]; then
            emit_error "no such schedule: $NAME"
            exit 30
        fi
        if ! "$SYSTEMCTL_BIN" --user start "${UNIT_PREFIX}${NAME}.service" >/dev/null 2>&1; then
            emit_error "systemctl start failed for ${UNIT_PREFIX}${NAME}.service"
            exit 30
        fi
        emit_json "{\"ok\": true, \"name\": \"$NAME\"}"
        exit 0
        ;;

    *)
        printf 'scheduler.sh: unknown action: %s\n' "$ACTION" >&2
        exit 2
        ;;
esac
