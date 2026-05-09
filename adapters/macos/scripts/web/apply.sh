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
for _h in sparkle github_dmg keystone squirrel builtin msupdate docker release_feed omaha; do
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
COUNT_TRIGGERED=0
COUNT_FAILED=0
COUNT_SKIPPED=0
COUNT_PLANNED=0
COUNT_UPTODATE=0

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

    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "web:${SLUG}" "$INSTALLED" "" "planned" "web" "$HANDLER"
        COUNT_PLANNED=$((COUNT_PLANNED + 1))
        continue
    fi

    # ── Step 1: probe candidate version (Tier-A handlers only) ──────────
    # Order matters: candidate probe BEFORE the defer-if-running check,
    # so apps that are already up-to-date don't get misleadingly marked
    # "deferred_app_in_use" just because the user has them open.
    # Tier-B handlers (keystone/squirrel/builtin) have no synchronous
    # candidate — daemon reconciles, so we always invoke them.
    CAND=""
    case "$HANDLER" in
        sparkle|github_dmg|release_feed|docker|msupdate|omaha)
            case "$HANDLER" in
                sparkle)      CAND=$(sparkle_check      "$SLUG" "$CFG" 2>/dev/null) ;;
                github_dmg)   CAND=$(github_dmg_check   "$SLUG" "$CFG" 2>/dev/null) ;;
                release_feed) CAND=$(release_feed_check "$SLUG" "$CFG" 2>/dev/null) ;;
                docker)       CAND=$(docker_check       "$SLUG" "$CFG" 2>/dev/null) ;;
                msupdate)     CAND=$(msupdate_check     "$SLUG" "$CFG" 2>/dev/null) ;;
                omaha)        CAND=$(omaha_check        "$SLUG" "$CFG" 2>/dev/null) ;;
            esac
            # Skip apply when installed >= candidate OR candidate is a
            # pre-release the user didn't ask for. Common cases:
            #   * Spotify 1.2.89.539 installed; brew API still on 1.2.88.483
            #     (vendor auto-updater is ahead of livecheck) → don't
            #     downgrade.
            #   * Firefox Developer Edition 151.0 stable installed; Mozilla
            #     product-details still on 151.0b8 beta → don't replace
            #     stable with beta.
            # Without this guard we'd attempt the DOWNGRADE, destroy user
            # data, and silently report "success".
            if _should_skip_upgrade "$INSTALLED" "$CAND"; then
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "up_to_date" "web" "$HANDLER"
                COUNT_UPTODATE=$((COUNT_UPTODATE + 1))
                continue
            fi
            # Empty probe (rate-limit / network blip / vendor 502) →
            # skip with explicit "probe_unavailable" reason, not failed.
            # Tier-A handlers all rely on external HTTPS; when the probe
            # fails the apply call would hit the same upstream and produce
            # the same misleading "handler exit 26" failure.
            #
            # github_dmg specifically emits the sentinel "__GH_RATE_LIMITED__"
            # when the GitHub API returns a 403 with rate-limit headers
            # (anonymous quota = 60 requests/hour). Treat that the same as
            # an empty probe — surface as skipped probe_unavailable.
            if [ -z "$CAND" ] || [ "$CAND" = "__GH_RATE_LIMITED__" ]; then
                _reason="vendor endpoint unreachable / rate limited; retry later"
                if [ "$CAND" = "__GH_RATE_LIMITED__" ]; then
                    _reason="GitHub API anonymous rate limit (60/hr) hit; retry in ~1 hour or set GITHUB_TOKEN env var"
                fi
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: probe_unavailable (${_reason})"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
            fi
            ;;
    esac

    # ── Step 2: defer-if-running ────────────────────────────────────────
    # An update IS available. If the user has the app open, we can't
    # safely replace its bundle. Sparkle/Squirrel/release_feed/github_dmg
    # all do bundle-swap copies; running app would either crash or roll
    # back the swap on next launch. Defer with a clear actionable reason.
    case "$HANDLER" in
        sparkle|github_dmg|squirrel|release_feed)
            if _web_is_running "$BUNDLE_ID"; then
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: deferred_app_in_use (quit ${DISPLAY_NAME:-$SLUG} and re-run apply to upgrade ${INSTALLED} → ${CAND:-latest})"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
            fi
            ;;
    esac

    # Dispatch handler apply, capturing stderr
    err_log="$OUTPUT_DIR/$RUN_ID/${SLUG}.apply.err"
    mkdir -p "$(dirname "$err_log")"

    case "$HANDLER" in
        sparkle)      sparkle_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        github_dmg)   github_dmg_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        keystone)     keystone_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        squirrel)     squirrel_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        builtin)      builtin_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        msupdate)     msupdate_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        docker)       docker_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        release_feed) release_feed_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        omaha)        omaha_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        *)            false ;;
    esac
    rc=$?

    if [ $rc -eq 0 ]; then
        case "$HANDLER" in
            keystone|squirrel|builtin|omaha)
                # Tier-B: vendor's update agent triggered; outcome is async.
                # Status 'triggered' (not 'success') so the operator + verify
                # phase know to expect post-apply reconciliation.
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "triggered" "web" "$HANDLER"
                case "$HANDLER" in
                    keystone) json_add_message "info" "${SLUG}: ksadmin update queued; daemon will reconcile" ;;
                    squirrel) json_add_message "info" "${SLUG}: app relaunched; Squirrel will self-update on next quit/relaunch" ;;
                    builtin)  json_add_message "info" "${SLUG}: app opened for user (manual update path)" ;;
                    omaha)    json_add_message "info" "${SLUG}: Omaha update triggered; vendor daemon (Keystone/CometUpdater) will reconcile" ;;
                esac
                COUNT_TRIGGERED=$((COUNT_TRIGGERED + 1))
                ;;
            *)
                # Tier-A: synchronous swap completed. Re-read CFBundle to
                # confirm the swap actually took (some apps refuse to be
                # replaced — the apply may have exited 0 but the bundle
                # is unchanged on disk).
                _post_installed=$(_web_installed_version "$APP_PATH")
                if [ -n "$_post_installed" ] && [ "$_post_installed" = "$INSTALLED" ] \
                       && [ -n "$CAND" ] && [ "$CAND" != "$INSTALLED" ]; then
                    # Bundle unchanged — apply was a no-op. Don't lie.
                    json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "failed" "web" "$HANDLER"
                    json_add_message "error" "${SLUG}: apply reported success but on-disk version unchanged (${INSTALLED}) — likely a silent install refusal; try quitting the app first"
                    COUNT_FAILED=$((COUNT_FAILED + 1))
                else
                    json_add_item "web:${SLUG}" "${_post_installed:-$INSTALLED}" "$CAND" "success" "web" "$HANDLER"
                    COUNT_SUCCESS=$((COUNT_SUCCESS + 1))
                fi
                ;;
        esac
    else
        # Capture last 12 non-empty stderr lines, max 1500 chars
        tail_msg=""
        if [ -s "$err_log" ]; then
            tail_msg=$(/usr/bin/tail -n 12 "$err_log" | /usr/bin/awk 'NF{print}' | /usr/bin/head -c 1500)
        fi
        json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "failed" "web" "$HANDLER"
        json_add_message "error" "${SLUG}: handler exit ${rc}: ${tail_msg}"
        COUNT_FAILED=$((COUNT_FAILED + 1))
    fi
done < <(python3 "$REG_SHIM" "${_reg_args[@]}" --list-slugs 2>/dev/null)

if [ "$DRY_RUN" = "true" ]; then
    json_add_message "info" "web apply (dry-run): ${COUNT_PLANNED} planned, ${COUNT_SKIPPED} skipped"
else
    json_add_message "info" "web apply: ${COUNT_SUCCESS} success, ${COUNT_TRIGGERED} triggered, ${COUNT_UPTODATE} up_to_date, ${COUNT_FAILED} failed, ${COUNT_SKIPPED} skipped"
fi
exit 0
