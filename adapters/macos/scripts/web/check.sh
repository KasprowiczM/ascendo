#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/web/check.sh -- read-only inventory + outdated probe
# =============================================================================
# For each enabled entry in adapters/macos/config/web_apps.toml:
#   - Read installed CFBundleShortVersionString from app bundle.
#   - If not installed: skip (no item emitted).
#   - Dispatch to <handler>_check; classify into planned / up_to_date /
#     skipped (squirrel/builtin) / failed (probe broken).
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
ADAPTER_CONFIG="$SCRIPT_DIR/../../config"

# shellcheck source=../../lib/ascendo_json.sh
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck source=../../lib/ascendo_web.sh
. "$ADAPTER_LIB/ascendo_web.sh"
for _h in sparkle github_dmg keystone squirrel builtin msupdate docker; do
    # shellcheck source=../../lib/handlers/sparkle.sh
    . "$ADAPTER_LIB/handlers/${_h}.sh"
done

# -- arg parsing ---------------------------------------------------------------
RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""; DRY_RUN="false"; FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN="true";    shift ;;
        --filter)     FILTER="$2";       shift 2 ;;
        *) printf 'check.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'check.sh: missing required args\n' >&2
    exit 2
fi

# -- registry path -------------------------------------------------------------
REG_PATH="${ASCENDO_WEB_REGISTRY_PATH:-$ADAPTER_CONFIG/web_apps.toml}"
USER_REG="${ASCENDO_WEB_USER_REGISTRY_PATH:-$HOME/.config/ascendo/web_apps.toml}"
[ -f "$USER_REG" ] || USER_REG=""
REG_SHIM="$ADAPTER_LIB/web_registry.py"

# -- host info -----------------------------------------------------------------
HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then HOST_IS_ELEVATED="true"; else HOST_IS_ELEVATED="false"; fi

# -- init sidecar --------------------------------------------------------------
json_init "check" "web" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "ascendo-web" "0.1.0" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# -- validate registry ---------------------------------------------------------
_reg_args=(--shipped "$REG_PATH")
[ -n "$USER_REG" ] && _reg_args+=(--user-override "$USER_REG")
_reg_err=""
if ! _reg_err=$(python3 "$REG_SHIM" "${_reg_args[@]}" --validate 2>&1 >/dev/null); then
    json_add_message "error" "registry validation failed: $_reg_err"
    exit 2
fi

# -- filter helper -------------------------------------------------------------
in_filter() {
    [ -z "$FILTER" ] && return 0
    case ",$FILTER," in
        (*",$1,"*) return 0 ;;
        (*) return 1 ;;
    esac
}

# -- iterate active slugs ------------------------------------------------------
COUNT_PLANNED=0
COUNT_UTD=0
COUNT_SKIPPED=0
COUNT_FAILED=0

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
    [ -z "$INSTALLED" ] && continue   # not installed; do not emit item

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

    if [ -z "$LATEST" ]; then
        case "$HANDLER" in
            squirrel)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: auto_on_relaunch — apply will relaunch app"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            builtin)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: manual_required — open app and use Help → Check for Updates"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            *)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "failed" "web" "$HANDLER"
                json_add_message "error" "${SLUG}: ${HANDLER} probe returned empty (network or vendor change?)"
                COUNT_FAILED=$((COUNT_FAILED + 1))
                ;;
        esac
        continue
    fi

    if [ "$INSTALLED" = "$LATEST" ] || ! _version_gt "$LATEST" "$INSTALLED"; then
        json_add_item "web:${SLUG}" "$INSTALLED" "$LATEST" "up_to_date" "web" "$HANDLER"
        COUNT_UTD=$((COUNT_UTD + 1))
    else
        json_add_item "web:${SLUG}" "$INSTALLED" "$LATEST" "planned" "web" "$HANDLER"
        COUNT_PLANNED=$((COUNT_PLANNED + 1))
    fi
done < <(python3 "$REG_SHIM" "${_reg_args[@]}" --list-slugs 2>/dev/null)

json_add_message "info" "web: ${COUNT_PLANNED} outdated, ${COUNT_UTD} up-to-date, ${COUNT_SKIPPED} skipped, ${COUNT_FAILED} failed"
exit 0
