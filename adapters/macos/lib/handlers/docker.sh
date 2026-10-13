# adapters/macos/lib/handlers/docker.sh
# Docker Desktop handler (vendor-truth, Spec G).
#
# The vendor Sparkle appcast ([app.vendor_feed] kind=sparkle, artifact dmg, host
# desktop.docker.com) is the truth about the newest release. The installed
# version is the INSTALLED Docker.app CFBundleShortVersionString -- never
# `docker desktop version`, which prints the CLI plugin version (v0.4.x) and made
# every Docker look current.
#
#   docker_check  the vendor feed version (probe candidate); empty when there is
#                 no feed or it is unreachable (phase scripts: probe_unavailable).
#   docker_apply  newer + app idle + vendor-direct enabled => verified DMG
#                 install straight from the vendor (no engine start/stop, no
#                 launch). A RUNNING Docker (or an unknown running state, or a
#                 live engine) is never updated: an update restarts every
#                 container => needs_restart. No blind `docker desktop update`.
#
# Reports through ASCENDO_WEB_OUTCOME (see lib/ascendo_web.sh): rc 0 updated /
# current / feed_stale / unknown_version, 4 needs_restart, 95 behind / error_soft,
# 3 install_error, 25 probe_unavailable.
#
# Test seam: ASCENDO_WEB_DOCKER_BIN (default `docker`) for the read-only
# `docker desktop status` engine probe.

docker_check() {
    local slug="$1" cfg="$2"
    _web_vendor_check "$slug" "$cfg"
    return 0
}

# _docker_engine_running -- exit 0 iff `docker desktop status` succeeds (the
# engine is up). Read-only, bounded, silent. No docker CLI => not running.
_docker_engine_running() {
    local bin="${ASCENDO_WEB_DOCKER_BIN:-docker}"
    if [ "${bin#/}" = "$bin" ]; then
        bin="$(command -v "$bin" 2>/dev/null)" || return 1
    fi
    [ -x "$bin" ] || return 1
    ascendo_run_with_timeout 15 "$bin" desktop status >/dev/null 2>&1
}

docker_apply() {
    local slug="$1" cfg="$2"
    local app_path bundle_id I line R URL CK CS ART HOST POLICY rel state vrc=0
    _web_outcome_reset

    if ! _web_cfg_has_vendor_feed "$cfg"; then
        _web_outcome probe_unavailable "no vendor feed configured for Docker Desktop; refusing a blind 'docker desktop update'"
        return 25
    fi

    app_path="${ASCENDO_WEB_INSTALLED_APP_PATH:-}"
    [ -n "$app_path" ] || app_path=$(_web_cfg_get "$cfg" app_path)
    [ -n "$app_path" ] || app_path="$(_vd_apps_root)/Docker.app"
    bundle_id=$(_web_cfg_get "$cfg" bundle_id)
    I=$(_web_installed_version "$app_path")

    line=$(_web_vendor_lookup "$slug" "$cfg" 2>/dev/null) || {
        _web_outcome probe_unavailable "Docker vendor feed unreachable or unparseable; not treated as up to date"
        return 25
    }
    IFS='|' read -r R URL CK CS ART HOST POLICY <<VENDOR_LINE
$line
VENDOR_LINE

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

    # A running Docker is never updated (restarts every container).
    state="$(ascendo_app_running_state "$bundle_id")"
    if [ "$state" != "false" ] || _docker_engine_running; then
        _web_outcome needs_restart "update ${I} -> ${R} pending - Docker Desktop is running (or its state could not be determined); an update restarts every container. Quit it and re-run apply"
        return 4
    fi

    if [ "$ART" = "-" ] || ! _web_vendor_direct_enabled; then
        _web_outcome behind "installed ${I} < vendor ${R}; vendor-direct install is disabled or the feed has no artifact"
        return 95
    fi

    _web_vendor_direct_step "$slug" "$app_path" "$I" "$R" "$URL" "$ART" "$CK" "$CS" "$HOST" || vrc=$?
    if [ "$vrc" -eq 1 ]; then
        # Soft refusal / download failure. Docker is never launched as a
        # fallback: starting it starts the engine.
        _web_outcome error_soft "error_soft: the vendor download/verification failed; installed ${I}, vendor ${R}"
        return 95
    fi
    return "$vrc"
}
