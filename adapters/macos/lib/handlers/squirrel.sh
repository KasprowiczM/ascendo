# adapters/macos/lib/handlers/squirrel.sh
# Squirrel.Mac auto-on-relaunch handler.
#
# Two modes, chosen per app by the presence of an [app.vendor_feed] table:
#
#   feedless  (legacy)  squirrel_check is empty; squirrel_apply = hidden
#                       `open -gjF` and the app self-updates in the background.
#   feed-backed         the vendor feed is the observable truth (port of
#                       macOS_updates internet_handler_vendor_truth, Spec F):
#                       squirrel_check echoes the feed version; squirrel_apply
#                       runs the vendor-truth algorithm below and reports its
#                       result through ASCENDO_WEB_OUTCOME (see ascendo_web.sh).
#
# Vendor-truth algorithm (feed-backed squirrel_apply), I = installed version,
# R = feed version:
#   1. feed unreachable                       -> probe_unavailable (rc 25), no action
#   2. R equal/older/unknown vs I             -> no launch, no wait (current /
#                                                feed_stale / unknown_version)
#   3. R newer and the app is running OR its running state is unknown
#                                             -> needs_restart (rc 4) BEFORE any
#                                                launch; nothing is touched
#   4. policy rollout_hold                    -> never vendor-direct (native
#                                                updater only), code rollout_hold
#   5. direct_first + artifact + idle         -> straight to the verified
#                                                vendor-direct install (no launch)
#   6. otherwise, unless ASCENDO_SAFE_MODE=true or STAGE_WAIT=0: one hidden
#      launch, then poll the installed version every 1 s up to
#      ASCENDO_WEB_STAGE_WAIT; `updated` the moment it reaches R. `open`
#      failing is launch_failed immediately (no wait, no quit). If the wait
#      ends without the update, report `launched` (rc 0, apply => triggered):
#      the app is running because Ascendo started it, so no install follows.
#   7. (no launch happened) an artifact exists and ASCENDO_WEB_VENDOR_DIRECT!=0
#                                             -> verified vendor-direct install
#                                                (works in safe mode: an idle app
#                                                is replaced without any GUI)
#   8. direct download/verify failure on a direct_first app -> error_soft plus
#      ONE hidden launch (idle, not safe mode) reported `triggered`
#   9. nothing worked -> rollout_hold (staged release) or behind
# The app is never quit: Ascendo cannot prove it owns the process it launched.

# _squirrel_get <key> — heredoc-via-env JSON parser (same pattern as
# sparkle/_gh_get/_keystone_get; consolidation deferred to milestone-final).
_squirrel_get() {
    local key="$1"
    local cfg
    cfg="$(cat)"
    ASCENDO_WEB_CFG="$cfg" ASCENDO_WEB_KEY="$key" /usr/bin/python3 <<'PY_EOF'
import json
import os
import re
import sys

raw = os.environ.get("ASCENDO_WEB_CFG", "")
key = os.environ.get("ASCENDO_WEB_KEY", "")


def _coerce(s):
    try:
        v = json.loads(s)
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return v
    except Exception:
        pass
    t = s.strip()
    if len(t) >= 2 and t.startswith('"') and t.endswith('"'):
        t = t[1:-1]
    for _ in range(4):
        try:
            return json.loads(t)
        except Exception:
            pass
        new_t = re.sub(r'\\(.)', r'\1', t)
        if new_t == t:
            break
        t = new_t
    try:
        return json.loads(t)
    except Exception:
        return None


data = _coerce(raw)
if not isinstance(data, dict):
    print("")
    sys.exit(0)

v = data.get(key)
if v is None or v is False:
    print("")
elif v is True:
    print("true")
elif isinstance(v, (list, dict)):
    print(json.dumps(v))
else:
    print(v)
PY_EOF
}

# _squirrel_app_path <cfg>
# The installed bundle: the path apply.sh resolved by bundle id (exported), else
# the registry's app_path, else /Applications/<display_name>.app.
_squirrel_app_path() {
    local cfg="$1" p display_name
    p="${ASCENDO_WEB_INSTALLED_APP_PATH:-}"
    [ -n "$p" ] || p=$(/usr/bin/printf '%s' "$cfg" | _squirrel_get app_path)
    if [ -z "$p" ]; then
        display_name=$(/usr/bin/printf '%s' "$cfg" | _squirrel_get display_name)
        p="/Applications/${display_name}.app"
    fi
    /usr/bin/printf '%s' "$p"
}

# squirrel_check -- with a vendor feed: the feed version (the probe candidate;
# phase scripts classify it newer/equal/older/unknown against the installed
# version). Without one: Squirrel.Mac is opaque from outside the app process,
# so echo empty ("skipped, will trigger via apply"). Unreachable feed: empty.
squirrel_check() {
    local slug="$1" cfg="$2"
    _web_vendor_check "$slug" "$cfg"
    return 0
}

# squirrel_apply -- feed-backed: vendor-truth algorithm; feedless: legacy open.
squirrel_apply() {
    local slug="$1" cfg="$2"
    if _web_cfg_has_vendor_feed "$cfg"; then
        _squirrel_vendor_truth "$slug" "$cfg"
        return $?
    fi
    _squirrel_apply_legacy "$slug" "$cfg"
}

# _squirrel_apply_legacy -- Just open the app. Squirrel runs in-process and
# downloads the new version in the background; the next time the user quits +
# relaunches they'll be on the new version.
_squirrel_apply_legacy() {
    local slug="$1" cfg="$2"
    local app_path
    app_path=$(/usr/bin/printf '%s' "$cfg" | _squirrel_get app_path)
    if [ -z "$app_path" ]; then
        local display_name
        display_name=$(/usr/bin/printf '%s' "$cfg" | _squirrel_get display_name)
        app_path="/Applications/${display_name}.app"
    fi
    # Hidden launch matches macOS_updates silent_launch_app (`open -gjF`).
    # Safe mode still launches hidden for FEEDLESS apps -- that is their only
    # update channel. Feed-backed apps never launch in safe mode.
    if [ -d "$app_path" ]; then
        /usr/bin/env open -gjF "$app_path" 2>/dev/null \
            || /usr/bin/env open -gj "$app_path" 2>/dev/null \
            || /usr/bin/env open -a "$app_path"
        return $?
    fi
    /usr/bin/env open -gjF -a "$app_path" 2>/dev/null \
        || /usr/bin/env open -gj -a "$app_path" 2>/dev/null \
        || /usr/bin/env open -a "$app_path"
}

# _squirrel_vendor_truth <slug> <cfg>  -- the algorithm in the file header.
_squirrel_vendor_truth() {
    local slug="$1" cfg="$2"
    local app_path bundle_id I line R URL CK CS ART HOST POLICY rel state
    local direct_first=0 staged=0 direct_ok=0 bypass=0 wait elapsed rel2 I2 vrc
    local launch_skipped="" lrc launched=0
    _web_outcome_reset

    app_path=$(_squirrel_app_path "$cfg")
    bundle_id=$(/usr/bin/printf '%s' "$cfg" | _squirrel_get bundle_id)
    I=$(_web_installed_version "$app_path")

    line=$(_web_vendor_lookup "$slug" "$cfg" 2>/dev/null) || {
        _web_outcome probe_unavailable "vendor feed unreachable or unparseable; not treated as up to date"
        return 25
    }
    IFS='|' read -r R URL CK CS ART HOST POLICY <<VENDOR_LINE
$line
VENDOR_LINE
    [ "$(_web_cfg_vendor_field "$cfg" direct_first)" = "true" ] && direct_first=1

    rel=$(_web_version_cmp "$R" "$I")
    case "$rel" in
        newer) ;;
        equal)
            _web_outcome current "installed ${I} matches the vendor feed"
            return 0 ;;
        older)
            _web_outcome feed_stale "$(_web_feed_stale_message "$slug" "$I" "$R")"
            return 0 ;;
        *)
            _web_outcome unknown_version "$(_web_unknown_version_message "$slug" "$I" "$R")"
            return 0 ;;
    esac

    # (3) running / unknown => needs_restart before ANY launch or install.
    state="$(ascendo_app_running_state "$bundle_id")"
    if [ "$state" != "false" ]; then
        _web_outcome needs_restart "update ${I} -> ${R} pending - quit the app (it is running or its state could not be determined) so its updater can install it"
        return 4
    fi

    # (4) staged rollout: native updater only, never the public artifact.
    if [ "${POLICY:-eligible}" = "rollout_hold" ]; then
        staged=1
        ART="-"
    fi
    if [ "$ART" != "-" ] && _web_vendor_direct_enabled; then
        direct_ok=1
    fi
    # (5) verified-artifact apps skip the launch cycle when idle.
    if [ "$direct_first" -eq 1 ] && [ "$direct_ok" -eq 1 ]; then
        bypass=1
    fi

    # (6) native launch cycle.
    if [ "$bypass" -eq 0 ]; then
        wait=$(_web_stage_wait)
        if _web_safe_mode; then
            launch_skipped="safe mode: the app's own updater was not launched; set ASCENDO_WEB_ALLOW_GUI=1 to allow a hidden launch"
        elif [ "$wait" -gt 0 ]; then
            if ! _web_open_hidden "$app_path"; then
                _web_outcome launch_failed "hidden launch of the app failed; its own updater could not run"
                return 95
            fi
            launched=1
            elapsed=0
            while :; do
                I2=$(_web_installed_version "$app_path")
                rel2=$(_web_version_cmp "$R" "$I2")
                if [ "$rel2" = "equal" ] || [ "$rel2" = "older" ]; then
                    _web_outcome updated "the app's own updater installed ${I2}"
                    return 0
                fi
                [ "$elapsed" -ge "$wait" ] && break
                _web_sleep 1
                elapsed=$((elapsed + 1))
            done
        fi
    fi

    # Ascendo launched the app itself and its updater did nothing within the
    # wait. The app is now running (because of us), so the verified install
    # would only defer with a misleading "quit the app"; and we cannot quit it
    # (Ascendo does not track launches). Report the truth: launched, unverified.
    if [ "$launched" -eq 1 ]; then
        if [ "$staged" -eq 1 ]; then
            _web_outcome rollout_hold "rollout_hold: installed ${I}, vendor ${R} is a staged release; Ascendo launched the app hidden so its native updater can run, and the verified artifact is not used"
        else
            _web_outcome launched "installed ${I} < vendor ${R} after ${wait} s; Ascendo launched the app hidden so its own updater can run (unverified until the next check; the app was not replaced because it is now running)"
        fi
        return $([ "$staged" -eq 1 ] && echo 95 || echo 0)
    fi

    # (7)/(8) verified vendor-direct install.
    if [ "$direct_ok" -eq 1 ]; then
        vrc=0
        _web_vendor_direct_step "$slug" "$app_path" "$I" "$R" "$URL" "$ART" "$CK" "$CS" "$HOST" || vrc=$?
        if [ "$vrc" -ne 1 ]; then
            return "$vrc"
        fi
        # Soft failure (download / checksum / identity refusal).
        if [ "$bypass" -eq 1 ]; then
            lrc=0
            _web_fallback_launch "$app_path" "$bundle_id" || lrc=$?
            if [ "$lrc" -eq 0 ]; then
                _web_outcome triggered "error_soft: the vendor download/verification failed; launched the app hidden so its own updater can run (unverified until the next check)"
                return 0
            fi
            _web_outcome error_soft "error_soft: the vendor download/verification failed and ${_WEB_LAUNCH_NOTE}; installed ${I}, vendor ${R}"
            return 95
        fi
        _web_outcome error_soft "error_soft: the vendor download/verification failed; installed ${I}, vendor ${R}${launch_skipped:+ ($launch_skipped)}"
        return 95
    fi

    # (9) nothing applied it.
    if [ "$staged" -eq 1 ]; then
        _web_outcome rollout_hold "rollout_hold: installed ${I}, vendor ${R} is a staged release; keeping the app's native updater${launch_skipped:+ ($launch_skipped)}"
    else
        _web_outcome behind "installed ${I} < vendor ${R}; the app's own updater did not apply it${launch_skipped:+ ($launch_skipped)}"
    fi
    return 95
}
