#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/web/plan.sh -- side-effect-free apply preview
# =============================================================================
# Same probe logic as check.sh, but emits only items that apply would touch:
#   - up_to_date items dropped
#   - sparkle/github_dmg/squirrel + running -> skipped, deferred_app_in_use
#   - builtin -> skipped, manual_required (always)
#   - keystone/msupdate/docker non-defer -> planned regardless of running
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
        *) printf 'plan.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'plan.sh: missing required args\n' >&2
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

json_init "plan" "web" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
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

in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in
        (*",$1,"*) return 0 ;;
        (*) return 1 ;;
    esac
}

COUNT_PLANNED=0
COUNT_SKIPPED=0

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

    LATEST=""
    case "$HANDLER" in
        sparkle)     LATEST=$(sparkle_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        github_dmg)  LATEST=$(github_dmg_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        keystone)    LATEST=$(keystone_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        msupdate)    LATEST=$(msupdate_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        docker)      LATEST=$(docker_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        squirrel|builtin) LATEST="" ;;
    esac
    LATEST=$(printf '%s' "$LATEST" | tr -d '[:space:]')

    IS_RUNNING=0
    _web_is_running "$BUNDLE_ID" && IS_RUNNING=1

    case "$HANDLER" in
        builtin)
            json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
            json_add_message "info" "${SLUG}: manual_required"
            COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
            ;;
        squirrel)
            if [ $IS_RUNNING -eq 1 ]; then
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: deferred_app_in_use"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
            else
                json_add_item "web:${SLUG}" "$INSTALLED" "" "planned" "web" "$HANDLER"
                COUNT_PLANNED=$((COUNT_PLANNED + 1))
            fi
            ;;
        sparkle|github_dmg)
            if [ -z "$LATEST" ]; then
                continue   # probe broken; surfaced in check, not plan
            fi
            if ! _version_gt "$LATEST" "$INSTALLED"; then
                continue   # up-to-date
            fi
            if [ $IS_RUNNING -eq 1 ]; then
                json_add_item "web:${SLUG}" "$INSTALLED" "$LATEST" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: deferred_app_in_use"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
            else
                json_add_item "web:${SLUG}" "$INSTALLED" "$LATEST" "planned" "web" "$HANDLER"
                COUNT_PLANNED=$((COUNT_PLANNED + 1))
            fi
            ;;
        keystone|msupdate|docker)
            # Non-defer: emit planned if outdated OR if probe returned empty
            # (the daemon will reconcile during apply).
            if [ -n "$LATEST" ] && ! _version_gt "$LATEST" "$INSTALLED"; then
                continue
            fi
            json_add_item "web:${SLUG}" "$INSTALLED" "${LATEST:-}" "planned" "web" "$HANDLER"
            COUNT_PLANNED=$((COUNT_PLANNED + 1))
            ;;
    esac
done < <(python3 "$REG_SHIM" "${_reg_args[@]}" --list-slugs 2>/dev/null)

json_add_message "info" "web plan: ${COUNT_PLANNED} planned, ${COUNT_SKIPPED} skipped"
exit 0
