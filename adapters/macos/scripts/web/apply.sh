#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/web/apply.sh -- mutating apply phase
# =============================================================================
# For each enabled registry entry that's installed:
#   - Defer-eligible handlers (sparkle/github_dmg/squirrel) check is_running
#     and skip with reason=deferred_app_in_use if true.
#   - Otherwise dispatch to <handler>_apply; capture stderr (last 12 lines)
#     into sidecar messages on failure.
#   - DRY_RUN emits status=planned without invoking handlers.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
ADAPTER_CONFIG="$SCRIPT_DIR/../../config"

. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_web.sh"
for _h in sparkle github_dmg keystone squirrel builtin msupdate docker; do
    . "$ADAPTER_LIB/handlers/${_h}.sh"
done

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""; DRY_RUN="false"; FILTER=""
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
    printf 'apply.sh: missing required args\n' >&2
    exit 2
fi

REG_PATH="${ASCENDO_WEB_REGISTRY_PATH:-$ADAPTER_CONFIG/web_apps.toml}"
USER_REG="${ASCENDO_WEB_USER_REGISTRY_PATH:-$HOME/.config/ascendo/web_apps.toml}"
[ -f "$USER_REG" ] || USER_REG=""
REG_SHIM="$ADAPTER_LIB/web_registry.py"

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then HOST_IS_ELEVATED="true"; else HOST_IS_ELEVATED="false"; fi

json_init "apply" "web" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "ascendo-web" "0.1.0" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

_reg_args=(--shipped "$REG_PATH")
[ -n "$USER_REG" ] && _reg_args+=(--user-override "$USER_REG")
if ! python3 "$REG_SHIM" "${_reg_args[@]}" --validate >/dev/null 2>&1; then
    json_add_message "error" "registry validation failed"
    exit 2
fi

# Touch-ID-first sudo warm (only if not dry run)
if [ "$DRY_RUN" = "false" ] && [ -z "${ASCENDO_SUDO_WARM_DISABLE:-}" ]; then
    _ascendo_sudo_warm 2>/dev/null || true
fi

in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in
        (*",$1,"*) return 0 ;;
        (*) return 1 ;;
    esac
}

COUNT_SUCCESS=0
COUNT_FAILED=0
COUNT_SKIPPED=0
COUNT_PLANNED=0

while IFS= read -r SLUG; do
    [ -z "$SLUG" ] && continue
    in_filter "$SLUG" || continue

    CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" --get-app "$SLUG" 2>/dev/null) || continue
    BUNDLE_ID=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("bundle_id",""))')
    HANDLER=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("handler",""))')
    DISPLAY_NAME=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("display_name",""))')
    APP_PATH=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("app_path") or "")')
    [ -z "$APP_PATH" ] && APP_PATH="/Applications/${DISPLAY_NAME}.app"

    INSTALLED=$(_web_installed_version "$APP_PATH")
    [ -z "$INSTALLED" ] && continue

    # Defer-eligible handlers: skip if running
    case "$HANDLER" in
        sparkle|github_dmg|squirrel)
            if _web_is_running "$BUNDLE_ID"; then
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: deferred_app_in_use"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
            fi
            ;;
    esac

    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "web:${SLUG}" "$INSTALLED" "" "planned" "web" "$HANDLER"
        COUNT_PLANNED=$((COUNT_PLANNED + 1))
        continue
    fi

    # Dispatch handler apply, capturing stderr
    err_log="$OUTPUT_DIR/$RUN_ID/${SLUG}.apply.err"
    mkdir -p "$(dirname "$err_log")"

    case "$HANDLER" in
        sparkle)     sparkle_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        github_dmg)  github_dmg_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        keystone)    keystone_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        squirrel)    squirrel_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        builtin)     builtin_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        msupdate)    msupdate_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        docker)      docker_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        *)           false ;;
    esac
    rc=$?

    if [ $rc -eq 0 ]; then
        if [ "$HANDLER" = "builtin" ]; then
            json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
            json_add_message "info" "${SLUG}: manual_required (app opened for user)"
            COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
        else
            json_add_item "web:${SLUG}" "$INSTALLED" "" "success" "web" "$HANDLER"
            COUNT_SUCCESS=$((COUNT_SUCCESS + 1))
        fi
    else
        # Capture last 12 non-empty stderr lines, max 1500 chars
        tail_msg=""
        if [ -s "$err_log" ]; then
            tail_msg=$(/usr/bin/tail -n 12 "$err_log" | /usr/bin/awk 'NF{print}' | /usr/bin/head -c 1500)
        fi
        json_add_item "web:${SLUG}" "$INSTALLED" "" "failed" "web" "$HANDLER"
        json_add_message "error" "${SLUG}: handler exit ${rc}: ${tail_msg}"
        COUNT_FAILED=$((COUNT_FAILED + 1))
    fi
done < <(python3 "$REG_SHIM" "${_reg_args[@]}" --list-slugs 2>/dev/null)

json_add_message "info" "web apply: ${COUNT_SUCCESS} success, ${COUNT_FAILED} failed, ${COUNT_SKIPPED} skipped, ${COUNT_PLANNED} planned (dry-run)"
exit 0
