#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/web/verify.sh -- post-apply reconciliation
# =============================================================================
# For each item in the sibling apply__web.json with status=success:
#   - sparkle/github_dmg/msupdate/docker: re-read installed version; success
#     iff installed > pre-apply (or installed == target if known).
#   - keystone: sleep 10s, then re-read (daemon applies async).
#   - squirrel (feedless): sleep 30s, then re-read (Squirrel auto-update on
#     relaunch).
#   - feed-backed squirrel / docker ([app.vendor_feed]): MEASURED, no sleep and
#     no escalation. The apply item carries the vendor's target version; the
#     installed bundle must have reached it. `success` stays success only on
#     that measurement (else failed); `triggered` is confirmed to success when
#     the version was reached, otherwise it stays `triggered` (the vendor's own
#     updater may still be running -- unverified is not a failure).
#   - builtin: no-op skipped (manual_verify_required).
#
# The installed bundle path is resolved from the registry (app_path /
# display_name / bundle id), never reconstructed from the item id.
#
# When no apply sidecar exists in the run dir, emit empty verify (no-op).
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
ADAPTER_CONFIG="$SCRIPT_DIR/../../config"

. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_web.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""; DRY_RUN="false"; FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2";       shift 2 ;;
        --trigger)    TRIGGER="$2";      shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2";   shift 2 ;;
        --dry-run)    DRY_RUN="true";    shift ;;
        --filter)     FILTER="$2";       shift 2 ;;
        *) printf 'verify.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if [ -z "$RUN_ID" ] || [ -z "$TRIGGER" ] || [ -z "$PROFILE_NAME" ] || [ -z "$OUTPUT_DIR" ]; then
    printf 'verify.sh: missing required args\n' >&2
    exit 2
fi

REG_PATH="${ASCENDO_WEB_REGISTRY_PATH:-$ADAPTER_CONFIG/web_apps.toml}"
USER_REG="${ASCENDO_WEB_USER_REGISTRY_PATH:-$HOME/.config/ascendo/web_apps.toml}"
[ -f "$USER_REG" ] || USER_REG=""
REG_SHIM="$ADAPTER_LIB/web_registry.py"
_reg_args=(--shipped "$REG_PATH")
[ -n "$USER_REG" ] && _reg_args+=(--user-override "$USER_REG")

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
if [ "${EUID:-$(id -u)}" -eq 0 ]; then HOST_IS_ELEVATED="true"; else HOST_IS_ELEVATED="false"; fi

json_init "verify" "web" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "ascendo-web" "0.1.0" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

APPLY_SIDECAR="$OUTPUT_DIR/$RUN_ID/apply__web.json"
if [ ! -f "$APPLY_SIDECAR" ]; then
    json_add_message "info" "no apply sidecar at $APPLY_SIDECAR; verify is no-op"
    exit 0
fi

# Test hook: ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE forces sleeps to 0
SLEEP_OVERRIDE="${ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE:-}"

# For each successful item, decide sleep + re-probe.
COUNT_OK=0
COUNT_FAILED=0

# _verify_resolve <slug>  -> sets CFG, APP_PATH (the real installed bundle)
_verify_resolve() {
    local slug="$1" bundle_id="" display="" reg_path=""
    CFG=""; APP_PATH=""
    CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" --get-app "$slug" 2>/dev/null) || CFG=""
    [ -n "$CFG" ] || return 0
    eval "$(printf '%s' "$CFG" | python3 -c '
import json, sys, shlex
d = json.load(sys.stdin)
for k, v in (("bundle_id", d.get("bundle_id", "")), ("display", d.get("display_name", "")),
             ("reg_path", d.get("app_path") or "")):
    print(f"{k}={shlex.quote(v)}")
' 2>/dev/null)"
    APP_PATH="$reg_path"
    [ -z "$APP_PATH" ] && [ -n "$display" ] && APP_PATH="/Applications/${display}.app"
    # Same fallback as apply.sh: the registry guess can miss a bundle that is
    # installed under its shorter on-disk name.
    if [ -z "$(_web_installed_version "$APP_PATH")" ] && [ -n "$bundle_id" ]; then
        local resolved
        resolved=$(_web_resolve_bundle_path "$bundle_id" "$APP_PATH")
        [ -n "$resolved" ] && APP_PATH="$resolved"
    fi
    return 0
}

while IFS=$'\t' read -r ITEM_ID HANDLER PRE_VERSION TARGET ITEM_STATUS; do
    [ -z "$ITEM_ID" ] && continue
    [ "$HANDLER" = "-" ] && HANDLER=""
    [ "$PRE_VERSION" = "-" ] && PRE_VERSION=""
    [ "$TARGET" = "-" ] && TARGET=""
    SLUG="${ITEM_ID#web:}"
    _verify_resolve "$SLUG"

    # Feed-backed squirrel / docker: measure against the vendor's version.
    if { [ "$HANDLER" = "squirrel" ] || [ "$HANDLER" = "docker" ]; } && _web_feed_backed "$HANDLER" "$CFG"; then
        POST=$(_web_installed_version "$APP_PATH")
        reached=0
        if [ -n "$POST" ]; then
            if [ -n "$TARGET" ]; then
                _rel=$(_web_version_cmp "$TARGET" "$POST")
                { [ "$_rel" = "equal" ] || [ "$_rel" = "older" ]; } && reached=1
            elif [ "$ITEM_STATUS" = "success" ]; then
                reached=1
            fi
        fi
        if [ "$reached" -eq 1 ]; then
            json_add_item "$ITEM_ID" "$POST" "$TARGET" "success" "web" "$HANDLER"
            json_add_message "info" "${SLUG}: verified by measurement (installed ${POST}${TARGET:+, vendor ${TARGET}})"
            COUNT_OK=$((COUNT_OK + 1))
        elif [ "$ITEM_STATUS" = "success" ]; then
            json_add_item "$ITEM_ID" "${POST:-$PRE_VERSION}" "$TARGET" "failed" "web" "$HANDLER"
            json_add_message "error" "${SLUG}: apply reported success but the installed version is ${POST:-unreadable}, not the vendor's ${TARGET:-?}"
            COUNT_FAILED=$((COUNT_FAILED + 1))
        else
            json_add_item "$ITEM_ID" "${POST:-$PRE_VERSION}" "$TARGET" "triggered" "web" "$HANDLER"
            json_add_message "info" "${SLUG}: triggered_pending (installed ${POST:-unreadable}, vendor ${TARGET:-?}; not yet applied, unverified)"
            COUNT_OK=$((COUNT_OK + 1))
        fi
        continue
    fi

    case "$HANDLER" in
        builtin)
            json_add_item "$ITEM_ID" "$PRE_VERSION" "" "skipped" "web" "$HANDLER"
            json_add_message "info" "${SLUG}: manual_verify_required"
            COUNT_OK=$((COUNT_OK + 1))
            continue
            ;;
        squirrel)
            sleep_for="${SLEEP_OVERRIDE:-30}"
            ;;
        keystone)
            sleep_for="${SLEEP_OVERRIDE:-10}"
            ;;
        *)
            sleep_for="${SLEEP_OVERRIDE:-0}"
            ;;
    esac

    [ "$sleep_for" -gt 0 ] && sleep "$sleep_for"

    POST=$(_web_installed_version "$APP_PATH")

    case "$HANDLER" in
        squirrel|keystone)
            # Tier-B async -- The apply phase already counts these as success/triggered.
            # Verify just confirms whether the vendor agent caught up yet.
            if [ -n "$POST" ] && [ "$POST" != "$PRE_VERSION" ]; then
                json_add_item "$ITEM_ID" "$POST" "" "triggered" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: triggered_confirmed (${PRE_VERSION} -> ${POST})"
                COUNT_OK=$((COUNT_OK + 1))
            else
                # W4/W13: Escalate to failed so it doesn't hide behind 'triggered_pending' forever.
                json_add_item "$ITEM_ID" "${POST:-$PRE_VERSION}" "" "failed" "web" "$HANDLER"
                json_add_message "error" "${SLUG}: action_required (vendor agent did not apply update within timeout; relaunch app or check daemon)"
                COUNT_FAILED=$((COUNT_FAILED + 1))
            fi
            ;;
        *)
            # Tier-A handlers -- success iff bytes were swapped
            if [ -n "$POST" ]; then
                json_add_item "$ITEM_ID" "$POST" "" "success" "web" "$HANDLER"
                COUNT_OK=$((COUNT_OK + 1))
            else
                json_add_item "$ITEM_ID" "$PRE_VERSION" "" "failed" "web" "$HANDLER"
                json_add_message "error" "${SLUG}: app no longer reports a version; install may have failed"
                COUNT_FAILED=$((COUNT_FAILED + 1))
            fi
            ;;
    esac
done < <(python3 - "$APPLY_SIDECAR" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    apply = json.load(fh)
def f(v):
    return v if v else "-"
for item in apply.get("items", []):
    # Re-verify both 'success' (Tier-A) and 'triggered' (Tier-B) items.
    if item.get("status") not in ("success", "triggered"):
        continue
    src = item.get("source") or {}
    print("\t".join([
        f(item.get("id")), f(src.get("feed")),
        f(item.get("current_version")), f(item.get("target_version")),
        item.get("status") or "-",
    ]))
PY
)

json_add_message "info" "web verify: ${COUNT_OK} ok, ${COUNT_FAILED} failed"
exit 0
