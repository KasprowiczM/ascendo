#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/web/apply.sh -- mutating apply phase
# =============================================================================
# For each enabled registry entry that's installed:
#   - Defer-eligible handlers (sparkle/github_dmg/squirrel/release_feed) probe
#     the app with the tri-state ascendo_app_running_state and proceed ONLY on
#     an explicit `false`: running => deferred_app_in_use, unknown =>
#     deferred_app_state_unknown (both skipped, never quit).
#   - Handler rc 4 (hardened installer: app became active / state unknown)
#     => skipped + ASCENDO-ACTION-REQUIRED reason=needs_restart (soft).
#   - Feed-backed squirrel / docker ([app.vendor_feed]) decide for themselves
#     (vendor-truth algorithm) and report through ASCENDO_WEB_OUTCOME; see
#     lib/ascendo_web.sh and lib/handlers/squirrel.sh. rc 3 stays `failed`.
#   - Installer rc 1 (download / checksum / identity refusal) of sparkle /
#     github_dmg / release_feed is SOFT: skipped + code error_soft, or one hidden
#     launch (idle app, not safe mode) => triggered. Never `failed`.
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
for _h in sparkle github_dmg keystone squirrel builtin msupdate docker release_feed omaha chromium_updater; do
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

# Web update policy: never pop a visible GUI window.
# ASCENDO_SAFE_MODE is forced true for every profile unless
# ASCENDO_WEB_ALLOW_GUI=1. Effect:
#   - Silent in-place handlers (keystone / sparkle / github_dmg /
#     msupdate / docker) still update without a window.
#   - FEEDLESS squirrel apps keep macOS_updates silent_launch: `open -gjF`
#     even in safe mode (hidden, no Dock bounce) -- their only channel.
#     Feed-backed squirrel apps (Claude / Codex / Cursor / Warp / Proton /
#     OpenCode / Antigravity / RDM, [app.vendor_feed]) NEVER launch in safe
#     mode: an idle app is replaced from the verified vendor artifact instead,
#     a verify-only app (Warp) reports `behind`.
#   - builtin / omaha / release_feed-without-download still return 95
#     and land on the aggregated "needs manual update" list.
# Override with ASCENDO_WEB_ALLOW_GUI=1 to restore foreground `open -a`.
if [ "${ASCENDO_WEB_ALLOW_GUI:-0}" = "1" ]; then
    export ASCENDO_SAFE_MODE="false"
else
    export ASCENDO_SAFE_MODE="true"
fi

REG_PATH="${ASCENDO_WEB_REGISTRY_PATH:-$ADAPTER_CONFIG/web_apps.toml}"
USER_REG="${ASCENDO_WEB_USER_REGISTRY_PATH:-$HOME/.config/ascendo/web_apps.toml}"
[ -f "$USER_REG" ] || USER_REG=""
REG_SHIM="$ADAPTER_LIB/web_registry.py"
ASCENDO_WEB_BREW_PREFERRED_BIDS=$(python3 "$REG_SHIM" --shipped "$REG_PATH" --list-brew-preferred-bundle-ids 2>/dev/null | paste -sd, -)
export ASCENDO_WEB_BREW_PREFERRED_BIDS

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

# Apps ASCENDO launches itself (explicitly idle beforehand) are recorded here so
# the verify phase can quit them again; apps the user already had open are never
# recorded. Copied to the canonical run dir at the end (each phase runs in its own
# temporary --output-dir, so verify cannot see this one).
export ASCENDO_WEB_LAUNCH_TRACK_FILE="$OUTPUT_DIR/$RUN_ID/toolkit_launched.txt"
rm -f "$ASCENDO_WEB_LAUNCH_TRACK_FILE" 2>/dev/null

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

# Report-only sweep: a previous run killed mid-swap can leave a staging or
# rollback-backup directory next to the apps. Never delete another run's
# directories -- a backup may be the only copy of the previous app version.
_APPS_ROOT="${ASCENDO_WEB_APPS_ROOT:-/Applications}"
_APPS_ROOT="${_APPS_ROOT%%:*}"   # ASCENDO_WEB_APPS_ROOT may be a colon list; the first root hosts staging
_APPS_ROOT="${_APPS_ROOT%/}"
for _orphan in "$_APPS_ROOT"/.ascendo_staging.* "$_APPS_ROOT"/.ascendo_backup.*; do
    if [ -d "$_orphan" ]; then
        json_add_message "warn" "orphaned staging/backup from a previous run: $_orphan (inspect it; a backup may hold the previous app version)" "ORPHANED_STAGING"
    fi
done

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
# Registry entries whose app isn't installed on this Mac. Collected and
# reported ONCE at the end (not as N recurring skipped-items + warnings
# every run — the operator uninstalled these and doesn't want to be
# told about each, every run). run_async eviction removes their DB rows.

# Sesja 47 perf fix: parallel probe phase, then sequential dispatch.
# The probe step (HTTP) parallelizes safely; the actual install step
# (DMG mount, /Applications copy, sudo prompt) does NOT — it stays
# sequential so we don't fight ourselves on filesystem + UI.
INDICES_FILE="$OUTPUT_DIR/$RUN_ID/_web_apply_idx.list"
RESULTS_DIR="$OUTPUT_DIR/$RUN_ID/_web_apply_probes"
mkdir -p "$(dirname "$INDICES_FILE")" "$RESULTS_DIR" 2>/dev/null
: > "$INDICES_FILE"
export ASCENDO_WEB_ADAPTER_LIB="$ADAPTER_LIB"

# ── Pass 1: build per-idx work files (sequential, fast) ──────────────
WORK_IDX=0
while IFS= read -r SLUG; do
    [ -z "$SLUG" ] && continue
    in_filter "$SLUG" || continue

    CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" --get-app "$SLUG" 2>/dev/null) || continue
    # Single python3 invocation: extract all 4 fields in one fork.
    eval "$(printf '%s' "$CFG" | python3 -c '
import json, sys, shlex
d = json.load(sys.stdin)
fields = {
    "BUNDLE_ID":    d.get("bundle_id", ""),
    "HANDLER":      d.get("handler", ""),
    "DISPLAY_NAME": d.get("display_name", ""),
    "APP_PATH":     d.get("app_path") or "",
}
for k, v in fields.items():
    print(f"{k}={shlex.quote(v)}")
' 2>/dev/null)"
    case ",${ASCENDO_WEB_BREW_PREFERRED_BIDS:-}," in
        (*",$BUNDLE_ID,"*) continue ;;
    esac
    [ -z "$APP_PATH" ] && APP_PATH="/Applications/${DISPLAY_NAME}.app"

    INSTALLED=$(_web_installed_version "$APP_PATH")
    # If the registry's `app_path` guess (or the default
    # /Applications/<display>.app) misses, try to resolve the bundle by
    # its CFBundleIdentifier via mdfind + system_profiler fallback. This
    # catches the common case where the registry uses a canonical name
    # (Docker Desktop, Codex Desktop, Zoom, Ledger Live) but the user has
    # the bundle installed under its shorter on-disk name (Docker.app,
    # Codex.app, zoom.us.app, Ledger Wallet.app). Without this lookup,
    # apply silently dropped those apps with a misleading "registry
    # app_path bundle missing" message.
    if [ -z "$INSTALLED" ] && [ -n "$BUNDLE_ID" ]; then
        _resolved=$(_web_resolve_bundle_path "$BUNDLE_ID" "$APP_PATH")
        if [ -n "$_resolved" ] && [ "$_resolved" != "$APP_PATH" ]; then
            APP_PATH="$_resolved"
            INSTALLED=$(_web_installed_version "$APP_PATH")
        fi
    fi
    # App not resolvable on disk (uninstalled, moved out of the app dirs,
    # registry-only entry): silent. No item, no message (the old aggregated
    # "N registry app(s) not installed" line was dropped in v1.5.4 parity);
    # run_async's eviction handles a stale inventory.db row.
    if [ -z "$INSTALLED" ]; then
        # v1.5.4 parity (DJI Assistant): an absent registry app is SILENT --
        # no item, no message, no slug listing (check/plan behave the same).
        continue
    fi

    # DRY_RUN: feed-backed apps are classified against the vendor feed in
    # pass 3 (read-only probe, never a launch or install) so an app that is
    # current / ahead of a stale feed is not reported as "planned". Every
    # other handler keeps the blanket `planned` row.
    if [ "$DRY_RUN" = "true" ] && ! _web_feed_backed "$HANDLER" "$CFG"; then
        json_add_item "web:${SLUG}" "$INSTALLED" "" "planned" "web" "$HANDLER"
        COUNT_PLANNED=$((COUNT_PLANNED + 1))
        continue
    fi

    # Stash everything keyed by idx for pass 2 (parallel) + pass 3 (sequential).
    printf '%s' "$CFG"          > "$RESULTS_DIR/${WORK_IDX}.cfg.json"
    printf '%s' "$SLUG"         > "$RESULTS_DIR/${WORK_IDX}.slug"
    printf '%s' "$HANDLER"      > "$RESULTS_DIR/${WORK_IDX}.handler"
    printf '%s' "$INSTALLED"    > "$RESULTS_DIR/${WORK_IDX}.installed"
    printf '%s' "$BUNDLE_ID"    > "$RESULTS_DIR/${WORK_IDX}.bundle_id"
    printf '%s' "$DISPLAY_NAME" > "$RESULTS_DIR/${WORK_IDX}.display_name"
    printf '%s' "$APP_PATH"     > "$RESULTS_DIR/${WORK_IDX}.app_path"
    printf '%d\n' "$WORK_IDX"   >> "$INDICES_FILE"
    WORK_IDX=$((WORK_IDX + 1))
done < <(python3 "$REG_SHIM" "${_reg_args[@]}" --list-slugs 2>/dev/null)

# ── Pass 2: parallel HTTP probes ──────────────────────────────────────
# (dry run too: the probe is a read-only HTTP fetch and feed-backed apps need it)
if [ "$DRY_RUN" = "true" ]; then msupdate_prepare_listing "$RESULTS_DIR" 0; else msupdate_prepare_listing "$RESULTS_DIR" 1; fi
_web_probe_parallel "$INDICES_FILE" "$RESULTS_DIR"

# ── Pass 3: sequential dispatch (reads probe results + emits items) ──
i=0
while [ "$i" -lt "$WORK_IDX" ]; do
    SLUG=$(cat "$RESULTS_DIR/${i}.slug" 2>/dev/null)
    HANDLER=$(cat "$RESULTS_DIR/${i}.handler" 2>/dev/null)
    INSTALLED=$(cat "$RESULTS_DIR/${i}.installed" 2>/dev/null)
    BUNDLE_ID=$(cat "$RESULTS_DIR/${i}.bundle_id" 2>/dev/null)
    DISPLAY_NAME=$(cat "$RESULTS_DIR/${i}.display_name" 2>/dev/null)
    APP_PATH=$(cat "$RESULTS_DIR/${i}.app_path" 2>/dev/null)
    CFG=$(cat "$RESULTS_DIR/${i}.cfg.json" 2>/dev/null)
    CAND=$(_web_read_probe_result "$RESULTS_DIR" "$i")
    NOTE=$(_web_read_probe_note "$RESULTS_DIR" "$i")
    i=$((i + 1))

    # Microsoft AutoUpdate sentinels: unknown / quarantined / regressed => skipped
    # (never current, never actionable).
    if [ "$HANDLER" = "msupdate" ] && _web_mau_sentinel_item "$SLUG" "$INSTALLED" "$HANDLER" "$CAND" "$NOTE"; then
        COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
        continue
    fi

    # Feed-backed handlers (sparkle; squirrel / docker with an [app.vendor_feed]):
    # three-way relation of the vendor feed to the installed version (Spec E). Only `newer` proceeds; `equal` is
    # up_to_date; `older` is a stale feed (skipped, unverified, no target);
    # `unknown` is skipped -- never up_to_date. An empty probe falls through
    # to the generic probe_unavailable handling below.
    FEED_BACKED=0
    _web_feed_backed "$HANDLER" "$CFG" && FEED_BACKED=1
    FEED_REL=""
    if [ "$FEED_BACKED" = 1 ] && [ -n "$CAND" ] && [ "$CAND" != "__GH_RATE_LIMITED__" ]; then
        FEED_REL=$(_web_version_cmp "$CAND" "$INSTALLED")
        case "$FEED_REL" in
            newer) ;;
            equal)
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "up_to_date" "web" "$HANDLER"
                COUNT_UPTODATE=$((COUNT_UPTODATE + 1))
                continue
                ;;
            older)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "$(_web_feed_stale_message "$SLUG" "$INSTALLED" "$CAND")" "feed_stale"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
                ;;
            *)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "$(_web_unknown_version_message "$SLUG" "$INSTALLED" "$CAND")" "unknown_version"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
                ;;
        esac
    fi

    # Feed-backed squirrel with no feed answer: skipped, never a blind launch.
    if [ "$HANDLER" = "squirrel" ] && [ "$FEED_BACKED" = 1 ] && [ -z "$CAND" ]; then
        json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
        json_add_message "info" "${SLUG}: probe_unavailable (vendor feed unreachable or unparseable; not treated as up to date)" "probe_unavailable"
        COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
        continue
    fi

    # Dry run: reaching here means the feed says an update is available (or
    # the probe is empty for a non-squirrel handler). Report it; never launch.
    if [ "$DRY_RUN" = "true" ]; then
        json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "planned" "web" "$HANDLER"
        json_add_message "info" "${SLUG}: Update available ${INSTALLED} -> ${CAND:-unknown} (dry run: nothing launched or installed) | $(ascendo_status_token update_available)" "update_available"
        COUNT_PLANNED=$((COUNT_PLANNED + 1))
        continue
    fi

    case "$HANDLER" in
        chromium_updater)
            # A non-empty probe is the installed version = PROVEN current by the
            # updater's history / the public version (no wake needed). An empty probe
            # is NOT probe_unavailable: it means "no proof yet", so fall through and
            # let the handler wake the updater.
            if [ "${NOTE%%$'\t'*}" = "unknown_version" ]; then
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${NOTE#*$'\t'}" "unknown_version"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
            fi
            if [ -n "$CAND" ] && [ "$CAND" = "$INSTALLED" ]; then
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "up_to_date" "web" "$HANDLER"
                _web_emit_probe_note "$SLUG" "$NOTE"
                COUNT_UPTODATE=$((COUNT_UPTODATE + 1))
                continue
            fi
            ;;
        sparkle|github_dmg|release_feed|docker|msupdate|omaha)
            # CAND came from parallel pass 2 above (already probed).
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
            # Three-way (Ruling 24): a pair that cannot be compared is
            # skipped, never planned and never "current".
            if [ -z "$FEED_REL" ] && [ -n "$INSTALLED" ] && [ -n "$CAND" ] \
               && [ "$CAND" != "$INSTALLED" ] && [ "$CAND" != "__GH_RATE_LIMITED__" ] \
               && ! { [ "$HANDLER" = "msupdate" ] && [ "$CAND" = "pending" ]; } \
               && [ "$(_web_version_cmp "$CAND" "$INSTALLED")" = "unknown" ]; then
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "$(_web_unknown_version_message "$SLUG" "$INSTALLED" "$CAND")" "unknown_version"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
            fi
            if [ -z "$FEED_REL" ] && _should_skip_upgrade "$INSTALLED" "$CAND"; then
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
    # Feed-backed squirrel / docker probe the running state themselves, BEFORE
    # any launch or install (needs_restart); the generic gate is for the rest.
    _defer_gate="$HANDLER"
    if [ "$HANDLER" = "squirrel" ] && [ "$FEED_BACKED" = 1 ]; then
        _defer_gate="vendor_truth"
    fi
    case "$_defer_gate" in
        sparkle|github_dmg|squirrel|release_feed)
            # Fail closed: only an explicit `false` proceeds. `unknown`
            # (lsappinfo missing/failing/empty) is treated like running.
            _run_state="$(ascendo_app_running_state "$BUNDLE_ID")"
            if [ "$_run_state" = "true" ]; then
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: deferred_app_in_use (quit ${DISPLAY_NAME:-$SLUG} and re-run apply to upgrade ${INSTALLED} → ${CAND:-latest})"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
            elif [ "$_run_state" != "false" ]; then
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: deferred_app_state_unknown (could not determine whether ${DISPLAY_NAME:-$SLUG} is running; not replaced. Quit it and re-run apply to upgrade ${INSTALLED} → ${CAND:-latest}) | $(ascendo_action_required_token app_in_use "$INSTALLED" "$CAND")"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
            fi
            ;;
    esac

    # Dispatch handler apply, capturing stderr
    err_log="$OUTPUT_DIR/$RUN_ID/${SLUG}.apply.err"
    mkdir -p "$(dirname "$err_log")"
    # The installer replaces THIS bundle (resolved by bundle id above, which
    # can differ from the registry's app_path guess).
    export ASCENDO_WEB_INSTALLED_APP_PATH="$APP_PATH"
    _web_outcome_reset

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
        chromium_updater) chromium_updater_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        *)            false ;;
    esac
    rc=$?

    # chromium_updater (Chrome / Drive / Gemini / Comet): the handler woke the vendor
    # updater (never the app) and left its verdict in ASCENDO_WEB_OUTCOME; rc is 0.
    # A wake failure is error_soft/skipped, a staged rollout is rollout_hold/skipped
    # (neither is action-required nor failed); success needs a measured version change.
    if [ "$HANDLER" = "chromium_updater" ]; then
        _o_msg="$ASCENDO_WEB_OUTCOME_MSG"
        case "$ASCENDO_WEB_OUTCOME" in
            current_vendor|current_verified)
                json_add_item "web:${SLUG}" "$INSTALLED" "$INSTALLED" "up_to_date" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: ${_o_msg} | $(ascendo_status_token "$ASCENDO_WEB_OUTCOME")" "$ASCENDO_WEB_OUTCOME"
                COUNT_UPTODATE=$((COUNT_UPTODATE + 1))
                ;;
            rollout_hold)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: ${_o_msg} | $(ascendo_status_token rollout_hold)" "rollout_hold"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            updated)
                _post_installed=$(_web_installed_version "$APP_PATH")
                if [ -n "$_post_installed" ] && [ "$_post_installed" != "$INSTALLED" ]; then
                    json_add_item "web:${SLUG}" "$_post_installed" "$_post_installed" "success" "web" "$HANDLER"
                    json_add_message "info" "${SLUG}: updated (${INSTALLED} -> ${_post_installed}) | $(ascendo_status_token updated)" "updated"
                    COUNT_SUCCESS=$((COUNT_SUCCESS + 1))
                else
                    json_add_item "web:${SLUG}" "$INSTALLED" "" "triggered" "web" "$HANDLER"
                    json_add_message "info" "${SLUG}: the updater reported an update but the installed version is still ${_post_installed:-unreadable} (unverified) | $(ascendo_status_token unverified)" "unverified"
                    COUNT_TRIGGERED=$((COUNT_TRIGGERED + 1))
                fi
                ;;
            unverified)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "triggered" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: ${_o_msg} | $(ascendo_status_token unverified)" "unverified"
                COUNT_TRIGGERED=$((COUNT_TRIGGERED + 1))
                ;;
            unknown_version)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${_o_msg}" "unknown_version"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            wake_failed)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${SLUG}: error_soft: ${_o_msg} | $(ascendo_status_token error_soft)" "error_soft"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            *)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${SLUG}: error_soft: ${_o_msg:-no verdict from the chromium updater handler} | $(ascendo_status_token error_soft)" "error_soft"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
        esac
        continue
    fi

    # Feed-backed squirrel / docker: the handler ran the vendor-truth algorithm
    # and left its verdict in ASCENDO_WEB_OUTCOME (rc 3 = install_error falls
    # through to the generic failed path below).
    if [ "$FEED_BACKED" = 1 ] && { [ "$HANDLER" = "squirrel" ] || [ "$HANDLER" = "docker" ]; } \
           && [ -n "$ASCENDO_WEB_OUTCOME" ] && [ "$ASCENDO_WEB_OUTCOME" != "install_error" ]; then
        _o_msg="$ASCENDO_WEB_OUTCOME_MSG"
        case "$ASCENDO_WEB_OUTCOME" in
            updated)
                # success requires a post-action measurement.
                _post_installed=$(_web_installed_version "$APP_PATH")
                _o_rel=$(_web_version_cmp "$CAND" "$_post_installed")
                if [ "$_o_rel" = "equal" ] || [ "$_o_rel" = "older" ]; then
                    json_add_item "web:${SLUG}" "$_post_installed" "$CAND" "success" "web" "$HANDLER"
                    json_add_message "info" "${SLUG}: ${_o_msg} | $(ascendo_status_token updated)" "updated"
                    COUNT_SUCCESS=$((COUNT_SUCCESS + 1))
                else
                    json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "failed" "web" "$HANDLER"
                    json_add_message "error" "${SLUG}: reported updated but the on-disk version is ${_post_installed:-unreadable}, not the vendor's ${CAND}"
                    COUNT_FAILED=$((COUNT_FAILED + 1))
                fi
                ;;
            triggered)
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "triggered" "web" "$HANDLER"
                json_add_message "warn" "${SLUG}: ${_o_msg} | $(ascendo_status_token error_soft)" "error_soft"
                COUNT_TRIGGERED=$((COUNT_TRIGGERED + 1))
                ;;
            launched)
                # Ascendo launched the app hidden for its own updater; the
                # update is NOT measured, so this is `triggered`, never success.
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "triggered" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: Ascendo launched the app hidden -- ${_o_msg} | $(ascendo_status_token launched)" "launched"
                COUNT_TRIGGERED=$((COUNT_TRIGGERED + 1))
                ;;
            current)
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "up_to_date" "web" "$HANDLER"
                COUNT_UPTODATE=$((COUNT_UPTODATE + 1))
                ;;
            needs_restart)
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: skipped -- ${_o_msg} | $(ascendo_status_token needs_restart) | $(ascendo_action_required_token needs_restart "$INSTALLED" "$CAND")" "needs_restart"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            rollout_hold)
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: ${_o_msg} | $(ascendo_status_token rollout_hold)" "rollout_hold"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            behind)
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: behind -- ${_o_msg} | $(ascendo_status_token behind) | $(ascendo_action_required_token behind "$INSTALLED" "$CAND")" "behind"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            error_soft)
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${SLUG}: ${_o_msg} | $(ascendo_status_token error_soft) | $(ascendo_action_required_token download_error "$INSTALLED" "$CAND")" "error_soft"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            launch_failed)
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${SLUG}: error_soft: ${_o_msg} | $(ascendo_status_token error_soft) | $(ascendo_action_required_token launch_failed "$INSTALLED" "$CAND")" "error_soft"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            feed_stale)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${_o_msg}" "feed_stale"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            unknown_version)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${_o_msg}" "unknown_version"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            *)
                # probe_unavailable (and anything unforeseen): honest skip.
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${SLUG}: ${ASCENDO_WEB_OUTCOME} (${_o_msg})" "$ASCENDO_WEB_OUTCOME"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
        esac
        continue
    fi

    # Sesja 73 sentinel: exit 95 from a handler means "intentionally
    # skipped in safe-mode" (would have called ``open -a`` to surface
    # the app's in-app updater, but PROFILE_NAME=safe → silent). Use a
    # stderr tail as the user-facing message.
    if [ $rc -eq 95 ]; then
        tail_msg=""
        if [ -s "$err_log" ]; then
            tail_msg=$(/usr/bin/tail -n 6 "$err_log" | /usr/bin/awk 'NF{print}' | /usr/bin/head -c 800)
        fi
        # Deterministic, machine-parseable marker so report.py can
        # promote this into the first-class "Action required" bucket
        # (not buried in generic Deferred). Reason derivation:
        case "$HANDLER" in
            keystone|omaha|squirrel) ar_reason="self_update" ;;
            *) if [ -z "$CAND" ]; then ar_reason="probe_broken"; else ar_reason="no_silent_path"; fi ;;
        esac
        ar_token="ASCENDO-ACTION-REQUIRED reason=${ar_reason} cur=${INSTALLED:-?} cand=${CAND:-?}"
        json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
        if [ -n "$tail_msg" ]; then
            json_add_message "info" "${SLUG}: ${tail_msg} | ${ar_token}"
        else
            json_add_message "info" "${SLUG}: skipped in safe mode — open the app to apply ${CAND:-the new version} | ${ar_token}"
        fi
        COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
        continue
    fi

    # rc 4 from the hardened installer (sparkle / github_dmg / release_feed):
    # the app is running OR its running state could not be determined, so
    # nothing was downloaded-over or replaced. Soft: skipped, never failed;
    # needs_restart tells the operator to quit the app and re-run.
    if [ $rc -eq 4 ]; then
        case "$HANDLER" in
            sparkle|github_dmg|release_feed)
                json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: skipped -- ${DISPLAY_NAME:-$SLUG} is running (or its running state could not be determined); not replaced. Quit it and re-run apply to install ${CAND:-the new version} | $(ascendo_status_token needs_restart) | $(ascendo_action_required_token needs_restart "$INSTALLED" "$CAND")" "needs_restart"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
                ;;
        esac
    fi

    # rc 1 from the hardened installer (download / checksum / identity refusal)
    # is SOFT for sparkle / github_dmg / release_feed (never `failed`): one hidden
    # launch so the app's own updater can run when that is allowed (idle app, not
    # safe mode) => triggered (unverified); otherwise skipped with the reason.
    # An app whose update is a CLI (apply_cli_argv) keeps its exit code as is.
    if [ $rc -eq 1 ]; then
        case "$HANDLER" in
            sparkle|github_dmg|release_feed)
                case "$CFG" in
                    *'"apply_cli_argv":['*) ;;
                    *)
                        _soft_tail=""
                        if [ -s "$err_log" ]; then
                            _soft_tail=$(/usr/bin/tail -n 2 "$err_log" | /usr/bin/awk 'NF{print}' | /usr/bin/tr '\n' ' ' | /usr/bin/head -c 300)
                        fi
                        _soft_lrc=0
                        _web_fallback_launch "$APP_PATH" "$BUNDLE_ID" || _soft_lrc=$?
                        if [ "$_soft_lrc" -eq 0 ]; then
                            json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "triggered" "web" "$HANDLER"
                            json_add_message "warn" "${SLUG}: error_soft: the vendor download/verification failed (${_soft_tail:-no detail}); launched the app hidden so its own updater can run (unverified until the next check) | $(ascendo_status_token error_soft)" "error_soft"
                            COUNT_TRIGGERED=$((COUNT_TRIGGERED + 1))
                        else
                            json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "skipped" "web" "$HANDLER"
                            json_add_message "warn" "${SLUG}: error_soft: the vendor download/verification failed (${_soft_tail:-no detail}); ${_WEB_LAUNCH_NOTE} | $(ascendo_status_token error_soft) | $(ascendo_action_required_token download_error "$INSTALLED" "$CAND")" "error_soft"
                            COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                        fi
                        continue
                        ;;
                esac
                ;;
        esac
    fi

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
        tail_msg="${ASCENDO_WEB_OUTCOME_MSG:+${ASCENDO_WEB_OUTCOME_MSG} | }${tail_msg}"
        json_add_item "web:${SLUG}" "$INSTALLED" "$CAND" "failed" "web" "$HANDLER"
        json_add_message "error" "${SLUG}: handler exit ${rc}: ${tail_msg}"
        COUNT_FAILED=$((COUNT_FAILED + 1))
    fi
done

# Clean up temp work artefacts (keep the sidecar)
rm -rf "$RESULTS_DIR" "$INDICES_FILE" 2>/dev/null

# Hand the launch record to the verify phase (it runs in its own temp dir).
if [ -s "$ASCENDO_WEB_LAUNCH_TRACK_FILE" ] && [ "$DRY_RUN" != "true" ]; then
    _canon="${ASCENDO_RUNS_DIR:-$HOME/.ascendo/runs}/$RUN_ID"
    if mkdir -p "$_canon" 2>/dev/null; then
        cp "$ASCENDO_WEB_LAUNCH_TRACK_FILE" "$_canon/toolkit_launched.txt" 2>/dev/null || true
    fi
fi

if [ "$DRY_RUN" = "true" ]; then
    json_add_message "info" "web apply (dry-run): ${COUNT_PLANNED} planned, ${COUNT_SKIPPED} skipped"
else
    json_add_message "info" "web apply: ${COUNT_SUCCESS} success, ${COUNT_TRIGGERED} triggered, ${COUNT_UPTODATE} up_to_date, ${COUNT_FAILED} failed, ${COUNT_SKIPPED} skipped"
fi
exit 0
