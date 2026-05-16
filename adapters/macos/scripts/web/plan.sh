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

# -- iterate apps via discovery layer ------------------------------------------
COUNT_PLANNED=0
COUNT_SKIPPED=0

# Sesja 54 perf fix: plan/web used to do per-app HTTP probes SEQUENTIALLY
# (~5s per app × 30 registered apps = 150s of wall time, dominating a
# 10-minute safe-update run). check.sh + apply.sh already use the
# parallel pattern from Sesja 50; plan.sh was missed. Mirror the
# 3-pass approach: build work list → parallel probes → sequential emit.
INDICES_FILE="$OUTPUT_DIR/$RUN_ID/_web_plan_idx.list"
RESULTS_DIR="$OUTPUT_DIR/$RUN_ID/_web_plan_probes"
mkdir -p "$(dirname "$INDICES_FILE")" "$RESULTS_DIR" 2>/dev/null
: > "$INDICES_FILE"
WORK_IDX=0

# ── Pass 1: walk discovery, stash per-idx work files (no HTTP) ───────
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
if disc.get("appcast_url"):
    out["appcast_url"] = disc["appcast_url"]
if disc.get("ksadmin_product_id"):
    out["ksadmin_product_id"] = disc["ksadmin_product_id"]
print(json.dumps(out))
')
    fi

    in_filter "$SLUG" || continue

    # Stash per-idx state for Pass 3 (parallel probe + emit).
    printf '%s' "$CFG"          > "$RESULTS_DIR/${WORK_IDX}.cfg.json"
    printf '%s' "$SLUG"         > "$RESULTS_DIR/${WORK_IDX}.slug"
    printf '%s' "$HANDLER"      > "$RESULTS_DIR/${WORK_IDX}.handler"
    printf '%s' "$INSTALLED"    > "$RESULTS_DIR/${WORK_IDX}.installed"
    printf '%s' "$BUNDLE_ID"    > "$RESULTS_DIR/${WORK_IDX}.bundle_id"
    printf '%s' "$DISPLAY_NAME" > "$RESULTS_DIR/${WORK_IDX}.display_name"
    printf '%d\n' "$WORK_IDX"   >> "$INDICES_FILE"
    WORK_IDX=$((WORK_IDX + 1))
done < <(bash "$ADAPTER_LIB/web_discovery.sh" --emit-json 2>/dev/null)

# ── Pass 2: parallel HTTP probes (8-way concurrency by default) ──────
_web_probe_parallel "$INDICES_FILE" "$RESULTS_DIR"

# ── Pass 3: sequential per-handler emit using probe results ──────────
i=0
while [ "$i" -lt "$WORK_IDX" ]; do
    SLUG=$(cat "$RESULTS_DIR/${i}.slug" 2>/dev/null)
    HANDLER=$(cat "$RESULTS_DIR/${i}.handler" 2>/dev/null)
    INSTALLED=$(cat "$RESULTS_DIR/${i}.installed" 2>/dev/null)
    BUNDLE_ID=$(cat "$RESULTS_DIR/${i}.bundle_id" 2>/dev/null)
    DISPLAY_NAME=$(cat "$RESULTS_DIR/${i}.display_name" 2>/dev/null)
    CFG=$(cat "$RESULTS_DIR/${i}.cfg.json" 2>/dev/null)
    LATEST=$(_web_read_probe_result "$RESULTS_DIR" "$i")
    i=$((i + 1))

    # GH rate-limit sentinel: skip with explanation, don't plan.
    if [ "$LATEST" = "__GH_RATE_LIMITED__" ]; then
        json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
        json_add_message "warn" "${SLUG}: GitHub API rate-limited; deferring. Set GITHUB_TOKEN or wait."
        COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
        continue
    fi

    IS_RUNNING=0
    _web_is_running "$BUNDLE_ID" && IS_RUNNING=1

    case "$HANDLER" in
        builtin|squirrel|keystone|omaha)
            # Tier-B handlers: vendor-opaque update mechanism
            if [ "$HANDLER" = "squirrel" ] && [ $IS_RUNNING -eq 0 ]; then
                json_add_item "web:${SLUG}" "$INSTALLED" "" "planned" "web" "$HANDLER"
                COUNT_PLANNED=$((COUNT_PLANNED + 1))
            elif [ "$HANDLER" = "keystone" ] || [ "$HANDLER" = "omaha" ]; then
                # Keystone/Omaha apply triggers vendor daemon (async).
                # Omaha gives us a real candidate from the check probe;
                # if it equals INSTALLED we drop the row from the plan.
                if [ -n "$LATEST" ] && ! _version_gt "$LATEST" "$INSTALLED"; then
                    continue
                fi
                json_add_item "web:${SLUG}" "$INSTALLED" "${LATEST:-}" "planned" "web" "$HANDLER"
                COUNT_PLANNED=$((COUNT_PLANNED + 1))
            else
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: vendor_opaque (Tier-B handler — apply will trigger vendor agent)"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
            fi
            ;;
        sparkle|github_dmg|release_feed)
            if [ -z "$LATEST" ]; then
                continue   # probe broken; surfaced in check, not plan
            fi
            if ! _version_gt "$LATEST" "$INSTALLED"; then
                continue   # up-to-date
            fi
            # An outdated item is PLANNED regardless of whether the app
            # is currently running. "plan" = what apply will attempt;
            # deferral-because-running is an apply-phase outcome that the
            # Phase-A Action-required surface now reports. This keeps
            # plan consistent with check (both say `planned` for an
            # outdated app) — fixes the megasync check=planned /
            # plan=skipped inconsistency.
            json_add_item "web:${SLUG}" "$INSTALLED" "$LATEST" "planned" "web" "$HANDLER"
            COUNT_PLANNED=$((COUNT_PLANNED + 1))
            ;;
        msupdate)
            # msupdate binary missing -> manager not available; skip
            if ! /usr/bin/command -v msupdate >/dev/null 2>&1; then
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: msupdate not available on this host"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
            elif [ -n "$LATEST" ] && ! _version_gt "$LATEST" "$INSTALLED"; then
                continue
            else
                json_add_item "web:${SLUG}" "$INSTALLED" "${LATEST:-}" "planned" "web" "$HANDLER"
                COUNT_PLANNED=$((COUNT_PLANNED + 1))
            fi
            ;;
        docker)
            if ! /usr/bin/command -v docker >/dev/null 2>&1; then
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: docker CLI not available on this host"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
            elif [ -n "$LATEST" ] && ! _version_gt "$LATEST" "$INSTALLED"; then
                continue
            else
                json_add_item "web:${SLUG}" "$INSTALLED" "${LATEST:-}" "planned" "web" "$HANDLER"
                COUNT_PLANNED=$((COUNT_PLANNED + 1))
            fi
            ;;
    esac
done

# Cleanup per-idx probe scratch (best-effort).
rm -rf "$RESULTS_DIR" "$INDICES_FILE" 2>/dev/null

json_add_message "info" "web plan: ${COUNT_PLANNED} planned, ${COUNT_SKIPPED} skipped"
exit 0
