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
LABEL_PREFIX="dev.ascendo."

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
