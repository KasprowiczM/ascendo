#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/web/verify.sh -- post-apply reconciliation
# =============================================================================
# For each item in the sibling apply__web.json with status=success:
#   - sparkle/github_dmg/msupdate/docker: re-read installed version; success
#     iff installed > pre-apply (or installed == target if known).
#   - keystone: sleep 10s, then re-read (daemon applies async).
#   - squirrel: sleep 30s, then re-read (Squirrel auto-update on relaunch).
#   - builtin: no-op skipped (manual_verify_required).
#
# When no apply sidecar exists in the run dir, emit empty verify (no-op).
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"

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

while IFS=$'\t' read -r ITEM_ID HANDLER APP_PATH PRE_VERSION; do
    [ -z "$ITEM_ID" ] && continue
    SLUG="${ITEM_ID#web:}"

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
            # Tier-B async — The apply phase already counts these as success/triggered.
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
            # Tier-A handlers — success iff bytes were swapped
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
for item in apply.get("items", []):
    # Re-verify both 'success' (Tier-A) and 'triggered' (Tier-B) items.
    if item.get("status") not in ("success", "triggered"):
        continue
    src = item.get("source") or {}
    handler = src.get("feed") or ""
    item_id = item.get("id") or ""
    pre_version = item.get("current_version") or ""
    # We don't have app_path in the sidecar; reconstruct via /Applications/<DisplayName>.app
    name = item.get("name") or ""
    app_path = f"/Applications/{name}.app" if name else ""
    print(f"{item_id}\t{handler}\t{app_path}\t{pre_version}")
PY
)

json_add_message "info" "web verify: ${COUNT_OK} ok, ${COUNT_FAILED} failed"
exit 0
