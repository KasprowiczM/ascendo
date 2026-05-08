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
for _h in sparkle github_dmg keystone squirrel builtin msupdate docker release_feed; do
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

# -- iterate apps via discovery layer ------------------------------------------
COUNT_PLANNED=0
COUNT_UTD=0
COUNT_SKIPPED=0
COUNT_FAILED=0

while IFS= read -r DISC_LINE; do
    [ -z "$DISC_LINE" ] && continue

    BUNDLE_ID=$(ASCENDO_WEB_LINE="$DISC_LINE" python3 -c '
import json, os
d = json.loads(os.environ["ASCENDO_WEB_LINE"])
print(d.get("bundle_id", ""))')
    [ -z "$BUNDLE_ID" ] && continue
    APP_PATH=$(ASCENDO_WEB_LINE="$DISC_LINE" python3 -c '
import json, os
d = json.loads(os.environ["ASCENDO_WEB_LINE"])
print(d.get("app_path", ""))')
    INSTALLED=$(ASCENDO_WEB_LINE="$DISC_LINE" python3 -c '
import json, os
d = json.loads(os.environ["ASCENDO_WEB_LINE"])
print(d.get("version", ""))')
    DISPLAY_NAME=$(ASCENDO_WEB_LINE="$DISC_LINE" python3 -c '
import json, os
d = json.loads(os.environ["ASCENDO_WEB_LINE"])
print(d.get("display_name", ""))')
    DISC_HANDLER=$(ASCENDO_WEB_LINE="$DISC_LINE" python3 -c '
import json, os
d = json.loads(os.environ["ASCENDO_WEB_LINE"])
print(d.get("fingerprint_handler", "builtin"))')

    [ -z "$INSTALLED" ] && continue

    CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" --get-app-by-bundle-id "$BUNDLE_ID" 2>/dev/null || true)
    if [ -n "$CFG" ]; then
        SLUG=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("slug",""))')
        HANDLER=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("handler",""))')
    else
        SLUG=$(printf '%s' "$DISPLAY_NAME" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-')
        [ -z "$SLUG" ] && SLUG="bundle-$(printf '%s' "$BUNDLE_ID" | tr '.' '-')"
        HANDLER="$DISC_HANDLER"
        # Synthesize a CFG that carries discovery's extracted URL/ID
        # (appcast_url for sparkle, ksadmin_product_id for keystone) so
        # the handler has what it needs to actually probe.
        CFG=$(SLUG="$SLUG" HANDLER="$HANDLER" APP_PATH="$APP_PATH" \
              ASCENDO_DISC_LINE="$DISC_LINE" \
              python3 -c '
import json, os
disc = json.loads(os.environ["ASCENDO_DISC_LINE"])
out = {
    "slug":         os.environ["SLUG"],
    "bundle_id":    disc.get("bundle_id", ""),
    "display_name": disc.get("display_name", ""),
    "handler":      os.environ["HANDLER"],
    "app_path":     os.environ["APP_PATH"],
}
# Carry handler-specific fields extracted by discovery.
if disc.get("appcast_url"):
    out["appcast_url"] = disc["appcast_url"]
if disc.get("ksadmin_product_id"):
    out["ksadmin_product_id"] = disc["ksadmin_product_id"]
print(json.dumps(out))
')
    fi

    in_filter "$SLUG" || continue

    LATEST=""
    case "$HANDLER" in
        sparkle)      LATEST=$(sparkle_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        github_dmg)   LATEST=$(github_dmg_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        keystone)     LATEST=$(keystone_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        msupdate)     LATEST=$(msupdate_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        docker)       LATEST=$(docker_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        release_feed) LATEST=$(release_feed_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        squirrel|builtin) LATEST="" ;;
    esac
    LATEST=$(printf '%s' "$LATEST" | tr -d '[:space:]')

    # Rate-limit sentinel from github_dmg handler: classify as skipped
    # (transient, will resolve when GH window resets or GITHUB_TOKEN set).
    if [ "$LATEST" = "__GH_RATE_LIMITED__" ]; then
        json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
        json_add_message "warn" "${SLUG}: GitHub API rate-limited (60/hr unauthenticated). Set GITHUB_TOKEN or wait ~1h."
        COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
        continue
    fi

    if [ -z "$LATEST" ]; then
        case "$HANDLER" in
            squirrel|keystone)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: vendor_opaque (Tier-B handler — apply will trigger vendor agent)"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            builtin)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: manual_required — open app and use Help → Check for Updates"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            msupdate|docker)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: ${HANDLER} not available on this host"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            *)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${SLUG}: ${HANDLER} probe returned empty (network or vendor change?)"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
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
done < <(bash "$ADAPTER_LIB/web_discovery.sh" --emit-json 2>/dev/null)

json_add_message "info" "web: ${COUNT_PLANNED} outdated, ${COUNT_UTD} up-to-date, ${COUNT_SKIPPED} skipped, ${COUNT_FAILED} failed"
exit 0
