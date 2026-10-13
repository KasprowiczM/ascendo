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
#                 container => needs_restart. No blind `docker desktop update`:
#                 the CLI path (Spec G step 4) runs ONLY for an idle Docker whose
#                 feed is newer, after the vendor DMG soft-failed or with
#                 ASCENDO_WEB_VENDOR_DIRECT=0, and never in safe mode (starting
#                 Docker opens its GUI): `docker desktop start` -> poll status ->
#                 `docker desktop update -q` -> poll the Docker.app version ->
#                 `docker desktop stop` (only the engine this handler started).
#                 A failed `update -q` skips the poll. success = measured version.
#
# Reports through ASCENDO_WEB_OUTCOME (see lib/ascendo_web.sh): rc 0 updated /
# current / feed_stale / unknown_version, 4 needs_restart, 95 behind / error_soft,
# 3 install_error, 25 probe_unavailable.
#
# Test seam: ASCENDO_WEB_DOCKER_BIN (default `docker`) for every docker CLI call;
# waits go through _web_sleep (ASCENDO_WEB_SLEEP_BIN). ASCENDO_WEB_DOCKER_UPDATE_WAIT
# (default 300, clamp 0-900) bounds the post-update version poll.

docker_check() {
    local slug="$1" cfg="$2"
    _web_vendor_check "$slug" "$cfg"
    return 0
}

# _docker_bin -- the docker CLI path (seam ASCENDO_WEB_DOCKER_BIN), or rc 1.
_docker_bin() {
    local bin="${ASCENDO_WEB_DOCKER_BIN:-docker}"
    if [ "${bin#/}" = "$bin" ]; then
        bin="$(command -v "$bin" 2>/dev/null)" || return 1
    fi
    [ -x "$bin" ] || return 1
    printf '%s' "$bin"
}

# _docker_engine_running -- exit 0 iff `docker desktop status` succeeds (the
# engine is up). Read-only, silent. No docker CLI => not running.
# The bound is deliberately short (5 s): with Docker Desktop QUIT the command
# prints "Could not retrieve status" but only returns at its own timeout, so a
# long bound would be paid by every Docker apply; a live engine answers at once.
_docker_engine_running() {
    local bin
    bin="$(_docker_bin)" || return 1
    ascendo_run_with_timeout 5 "$bin" desktop status >/dev/null 2>&1
}

# _docker_cli_update <slug> <app_path> <installed> <R>
# Spec G step 4. Caller guarantees: idle app, engine not running, feed newer,
# not safe mode. Sets the outcome; returns the handler rc (0 updated, 95 not).
# rc 1 = no docker CLI (caller reports).
_docker_cli_update() {
    local slug="$1" app="$2" I="$3" R="$4" bin elapsed=0 started=0
    local upd_out upd_rc post rel updated=0 wait detail
    bin="$(_docker_bin)" || return 1

    ascendo_run_with_timeout 180 "$bin" desktop start >/dev/null 2>&1 || true
    while [ "$elapsed" -lt 120 ]; do
        _web_sleep 5
        elapsed=$((elapsed + 5))
        if ascendo_run_with_timeout 15 "$bin" desktop status >/dev/null 2>&1; then
            started=1
            break
        fi
    done
    if [ "$started" -eq 0 ]; then
        # We issued the start, so the stop below is ours.
        ascendo_run_with_timeout 120 "$bin" desktop stop >/dev/null 2>&1 || true
        _web_outcome behind "installed ${I} < vendor ${R}; 'docker desktop start' did not bring the engine up within 120 s"
        return 95
    fi

    upd_out="$(ascendo_run_with_timeout 900 "$bin" desktop update -q 2>&1)"
    upd_rc=$?
    if [ "$upd_rc" -eq 0 ]; then
        wait=$(_web_bounded_int "${ASCENDO_WEB_DOCKER_UPDATE_WAIT:-300}" 300 900)
        elapsed=0
        while :; do
            post=$(_web_installed_version "$app")
            rel=$(_web_version_cmp "$R" "$post")
            if [ "$rel" = "equal" ] || [ "$rel" = "older" ]; then
                updated=1
                break
            fi
            [ "$elapsed" -ge "$wait" ] && break
            _web_sleep 10
            elapsed=$((elapsed + 10))
        done
    fi
    # Only ever stop what this handler started (it always started it: a running
    # Docker returned needs_restart before we got here).
    ascendo_run_with_timeout 120 "$bin" desktop stop >/dev/null 2>&1 || true

    if [ "$updated" -eq 1 ]; then
        _web_outcome updated "installed ${post} via 'docker desktop update'"
        return 0
    fi
    if [ "$upd_rc" -ne 0 ]; then
        # The updater reported failure: the bundle will not change, so there was
        # no version poll. Keep the exit code and the last 3 lines as the reason.
        detail=$(printf '%s\n' "$upd_out" | /usr/bin/grep -v '^Downloading' | /usr/bin/awk 'NF' \
            | /usr/bin/tail -n 3 | /usr/bin/tr '\n' ' ' | /usr/bin/head -c 400)
        _web_outcome behind "installed ${I} < vendor ${R}; 'docker desktop update -q' failed (exit=${upd_rc}): ${detail}"
        return 95
    fi
    _web_outcome behind "installed ${I} < vendor ${R}; 'docker desktop update -q' did not change Docker.app within ${wait} s"
    return 95
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

    local soft_note="" cli_rc=0
    if [ "$ART" != "-" ] && _web_vendor_direct_enabled; then
        _web_vendor_direct_step "$slug" "$app_path" "$I" "$R" "$URL" "$ART" "$CK" "$CS" "$HOST" || vrc=$?
        if [ "$vrc" -ne 1 ]; then
            return "$vrc"
        fi
        soft_note="error_soft: the vendor download/verification failed"
    else
        soft_note="vendor-direct install is disabled or the feed has no artifact"
    fi

    # Spec G step 4: the vendor CLI path (never in safe mode: it opens Docker).
    if _web_safe_mode; then
        _docker_fallback_outcome "$soft_note; the docker CLI fallback was not run (safe mode: starting Docker opens its GUI; set ASCENDO_WEB_ALLOW_GUI=1 to allow it); installed ${I}, vendor ${R}"
        return 95
    fi
    _docker_cli_update "$slug" "$app_path" "$I" "$R" || cli_rc=$?
    if [ "$cli_rc" -eq 1 ]; then
        _docker_fallback_outcome "$soft_note; docker CLI not found; installed ${I}, vendor ${R}"
        return 95
    fi
    return "$cli_rc"
}

# _docker_fallback_outcome <message> -- error_soft after a soft DMG failure, else behind.
_docker_fallback_outcome() {
    case "$1" in
        error_soft:*) _web_outcome error_soft "$1" ;;
        *) _web_outcome behind "$1" ;;
    esac
}
