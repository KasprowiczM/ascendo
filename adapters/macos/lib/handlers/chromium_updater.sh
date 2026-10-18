# adapters/macos/lib/handlers/chromium_updater.sh
# Chromium-style updaters: Google Chrome, Google Drive, Gemini (GoogleUpdater) and
# Comet (CometUpdater). Port of macOS_updates google_keystone_check +
# internet_handler_chromium_updater + evaluate_omaha_status (Spec C).
#
# The app is NEVER launched, quit or closed by this handler: the updater binary is
# woken with `--wake-all` (headless) and its answer is read from updater.log /
# updater_history.jsonl. Functions:
#
#   chromium_updater_check <slug> <cfg>   read-only. Echoes the installed short version
#                                         when the app is PROVEN current (updater history
#                                         or, Chrome only, Google VersionHistory), else
#                                         nothing. The reason is written to
#                                         $ASCENDO_WEB_PROBE_NOTE_FILE as "<code>\t<text>".
#   chromium_updater_apply <slug> <cfg>   proof first (no wake), else wake ONCE per run
#                                         and evaluate. Always rc 0; the verdict is
#                                         ASCENDO_WEB_OUTCOME / _MSG (see ascendo_web.sh):
#                                           current_vendor current_verified rollout_hold
#                                           updated unverified wake_failed
#                                           probe_unavailable
#
# Env (tests + operators): ASCENDO_GOOGLE_USER_LOG / _SYS_LOG / _USER_UPDATER /
# _SYS_UPDATER / _LEGACY_AGENT, ASCENDO_OMAHA_WAIT (0-180 s), ASCENDO_OMAHA_MAX_AGE_H,
# ASCENDO_OMAHA_VERIFY_WAIT (60 s) / _VERIFY_STEP (5 s).
# Needs ascendo_web.sh sourced first. Bash 3.2.

_CU_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_cu_py() { python3 "$_CU_LIB_DIR/omaha_history.py" "$@"; }

# _cu_cfg <cfg_json> <field>   resolved [app.chromium_updater] field (one value per line)
_cu_cfg() { ASCENDO_WEB_CFG="$1" _cu_py cfg "$2"; }

_cu_now() { /bin/date +%s; }

# _cu_size <file>   byte size, 0 when absent
_cu_size() {
    if [ -f "$1" ]; then
        /usr/bin/wc -c < "$1" 2>/dev/null | /usr/bin/tr -d ' '
    else
        printf '0'
    fi
}

# _cu_read_increment <log> <init_size>
# What was appended since <init_size>. A rotated log (smaller than init) means the
# old tail lives in <log>.old and everything in <log> is new.
_cu_read_increment() {
    local log="$1" init="${2:-0}" cur
    [ -f "$log" ] || return 0
    cur=$(_cu_size "$log")
    if [ "$cur" -lt "$init" ]; then
        [ -f "${log}.old" ] && /usr/bin/tail -c +$((init + 1)) "${log}.old" 2>/dev/null
        /bin/cat "$log" 2>/dev/null
    elif [ "$cur" -gt "$init" ]; then
        /usr/bin/tail -c +$((init + 1)) "$log" 2>/dev/null
    fi
    return 0
}

# _cu_app_path <cfg>  the installed bundle (apply.sh exports the resolved one).
_cu_app_path() {
    local cfg="$1" p dn bid r
    p="${ASCENDO_WEB_INSTALLED_APP_PATH:-}"
    [ -n "$p" ] || p=$(_web_cfg_get "$cfg" app_path)
    if [ -z "$p" ]; then
        dn=$(_web_cfg_get "$cfg" display_name)
        [ -n "$dn" ] && p="/Applications/${dn}.app"
    fi
    if [ ! -d "$p" ]; then
        bid=$(_web_cfg_get "$cfg" bundle_id)
        if [ -n "$bid" ]; then
            r=$(_web_resolve_bundle_path "$bid" "$p")
            [ -n "$r" ] && p="$r"
        fi
    fi
    printf '%s' "$p"
}

# _cu_history_proof <appid> <app_path> <cfg>
# Prints "<status>\t<HH:MM>\t<version>" when updater_history.jsonl(.old) next to a
# configured updater.log proves NO_UPDATE/UPDATED for exactly the installed build
# (short OR CFBundleVersion) within history_max_age_h. rc 1 otherwise.
_cu_history_proof() {
    local appid="$1" app_path="$2" cfg="$3" short build maxage files="" log dir res sub="${4:-history}"
    short=$(_web_installed_version "$app_path")
    build=$(/usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "$app_path/Contents/Info.plist" 2>/dev/null || true)
    maxage=$(_cu_cfg "$cfg" history_max_age_h)
    while IFS= read -r log; do
        [ -n "$log" ] || continue
        dir=$(dirname "$log")
        if [ -f "$dir/updater_history.jsonl.old" ]; then files="${files}${dir}/updater_history.jsonl.old"$'\n'; fi
        if [ -f "$dir/updater_history.jsonl" ]; then files="${files}${dir}/updater_history.jsonl"$'\n'; fi
    done < <(_cu_cfg "$cfg" updater_log)
    [ -n "$files" ] || return 1
    res=$(printf '%s' "$files" | _cu_py "$sub" "$appid" "${maxage:-6}" "$short" "$build")
    [ -n "$res" ] || return 1
    printf '%s\n' "$res"
}

# _cu_public_version <cfg>   sets _CU_PUB (Google VersionHistory public version; Chrome
# only -- empty without a version_history_url or when the feed is unreachable).
# Cached per process: at most one fetch per app.
_cu_public_version() {
    local url body
    _CU_PUB=""
    url=$(_cu_cfg "$1" version_history_url)
    [ -n "$url" ] || return 0
    if [ "$url" = "${_CU_PUB_URL:-}" ]; then
        _CU_PUB="$_CU_PUB_VAL"
        return 0
    fi
    body=$(_web_fetch_feed "$url" 2>/dev/null) || body=""
    _CU_PUB=$(printf '%s' "$body" | _cu_py vh)
    _CU_PUB_URL="$url"
    _CU_PUB_VAL="$_CU_PUB"
    return 0
}

# _cu_prove <cfg> <app_path>
# rc 0 + _CU_CODE/_CU_MSG when the app is provably current WITHOUT waking anything:
#   (1) updater history: current_vendor   (2) Chrome: installed >= public VersionHistory:
#   current_verified.  rc 1 = no proof.
_cu_prove() {
    local cfg="$1" app_path="$2" appid res hhmm inst rel
    _CU_CODE=""; _CU_MSG=""; _CU_UNKNOWN=""
    appid=$(_cu_cfg "$cfg" appid)
    if res=$(_cu_history_proof "$appid" "$app_path" "$cfg"); then
        hhmm=$(printf '%s' "$res" | /usr/bin/cut -f2)
        _CU_CODE="current_vendor"
        _CU_MSG="verified by updater at ${hhmm}"
        return 0
    fi
    _cu_public_version "$cfg"
    inst=$(_web_installed_version "$app_path")
    if [ -n "$_CU_PUB" ] && [ -n "$inst" ]; then
        rel=$(_web_version_cmp "$inst" "$_CU_PUB")
        case "$rel" in
            equal|newer)
                _CU_CODE="current_verified"
                _CU_MSG="installed ${inst} is current against the public Chrome ${_CU_PUB} (Google VersionHistory)"
                return 0
                ;;
            unknown)
                _CU_UNKNOWN="$(_web_unknown_version_message "${_CU_SLUG:-$appid}" "$inst" "$_CU_PUB")"
                return 1
                ;;
        esac
    fi
    # History exists for this build but its version cannot be compared with the installed one.
    if res=$(_cu_history_proof "$appid" "$app_path" "$cfg" history-unparseable); then
        _CU_UNKNOWN="$(_web_unknown_version_message "${_CU_SLUG:-$appid}" "$inst" "$res")"
    fi
    return 1
}

_cu_write_note() {
    [ -n "${ASCENDO_WEB_PROBE_NOTE_FILE:-}" ] || return 0
    printf '%s\t%s\n' "$_CU_CODE" "$_CU_MSG" > "$ASCENDO_WEB_PROBE_NOTE_FILE" 2>/dev/null
    return 0
}

chromium_updater_check() {
    local slug="$1" cfg="$2" app_path inst
    _CU_SLUG="$slug"
    app_path=$(_cu_app_path "$cfg")
    inst=$(_web_installed_version "$app_path")
    [ -n "$inst" ] || return 0
    if _cu_prove "$cfg" "$app_path"; then
        _cu_write_note
        printf '%s\n' "$inst"
    elif [ -n "$_CU_UNKNOWN" ]; then
        _CU_CODE="unknown_version"; _CU_MSG="$_CU_UNKNOWN"
        _cu_write_note
    fi
    return 0
}

# _cu_state_file <slug> <google 1|0>
# Google apps share ONE state file per run (written by the first Google app); any
# other updater (Comet) keeps its own.
_cu_state_file() {
    local dir
    if [ -n "${OUTPUT_DIR:-}" ] && [ -n "${RUN_ID:-}" ]; then
        dir="$OUTPUT_DIR/$RUN_ID"
    else
        [ -n "${_CU_FALLBACK_DIR:-}" ] || _CU_FALLBACK_DIR=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/ascendo-cu.XXXXXX")
        dir="$_CU_FALLBACK_DIR"
    fi
    mkdir -p "$dir" 2>/dev/null
    if [ "$2" = "1" ]; then
        printf '%s/_omaha_init.txt' "$dir"
    else
        printf '%s/_omaha_init_%s.txt' "$dir" "$1"
    fi
}

# _cu_wake_once <slug> <cfg>
# Wakes the updater once per run and sets _CU_USER_INIT/_CU_SYS_INIT (log sizes before
# the wake) and _CU_DEADLINE (shared wait window). rc 1 + _CU_WAKE_ERR on failure.
# Order: configured updater(s) in order, the first that exits 0 wins; the legacy
# Keystone agent only when NO updater binary is executable.
_cu_wake_once() {
    local slug="$1" cfg="$2" google sf wait args=() bins=() logs=() legacy line
    local triggered=0 any_exec=0 last_rc=0 b rc out
    google=$(_cu_cfg "$cfg" google)
    sf=$(_cu_state_file "$slug" "$google")
    if [ -f "$sf" ]; then
        _CU_USER_INIT=0; _CU_SYS_INIT=0; _CU_DEADLINE=0
        read -r _CU_USER_INIT _CU_SYS_INIT _CU_DEADLINE < "$sf" 2>/dev/null
        case "$_CU_DEADLINE" in ''|*[!0-9]*) ;; *) return 0 ;; esac
    fi
    wait=$(_cu_cfg "$cfg" wait_s)
    _CU_DEADLINE=$(( $(_cu_now) + wait ))
    while IFS= read -r line; do [ -n "$line" ] && logs+=("$line"); done < <(_cu_cfg "$cfg" updater_log)
    while IFS= read -r line; do [ -n "$line" ] && bins+=("$line"); done < <(_cu_cfg "$cfg" updater_bin)
    while IFS= read -r line; do [ -n "$line" ] && args+=("$line"); done < <(_cu_cfg "$cfg" wake_args)
    legacy=$(_cu_cfg "$cfg" legacy_agent)
    _CU_USER_INIT=0; _CU_SYS_INIT=0
    [ -n "${logs[0]:-}" ] && _CU_USER_INIT=$(_cu_size "${logs[0]}")
    [ -n "${logs[1]:-}" ] && _CU_SYS_INIT=$(_cu_size "${logs[1]}")

    for b in "${bins[@]}"; do
        [ -x "$b" ] || continue
        any_exec=1
        out=$(ascendo_run_with_timeout 60 "$b" "${args[@]}" 2>&1); rc=$?
        last_rc=$rc
        if [ "$rc" -eq 0 ]; then triggered=1; break; fi
    done
    if [ "$triggered" -eq 0 ] && [ "$any_exec" -eq 0 ] && [ -n "$legacy" ] && [ -x "$legacy" ]; then
        any_exec=1
        out=$(ascendo_run_with_timeout 60 "$legacy" --runMode ondemand 2>&1); rc=$?
        last_rc=$rc
        [ "$rc" -eq 0 ] && triggered=1
    fi
    if [ "$triggered" -eq 0 ]; then
        if [ "$any_exec" -eq 0 ]; then
            _CU_WAKE_ERR="no updater found to wake (looked for the vendor updater binary${legacy:+ and the legacy Keystone agent}); status unknown, NOT current"
        else
            _CU_WAKE_ERR="the updater wake failed (exit ${last_rc}); status unknown, NOT current"
        fi
        return 1
    fi
    printf '%s %s %s\n' "$_CU_USER_INIT" "$_CU_SYS_INIT" "$_CU_DEADLINE" > "$sf" 2>/dev/null
    return 0
}

# _cu_collect_inc <cfg>   increments of the first two updater logs since the wake
_cu_collect_inc() {
    local logs=() line
    while IFS= read -r line; do [ -n "$line" ] && logs+=("$line"); done < <(_cu_cfg "$1" updater_log)
    if [ -n "${logs[0]:-}" ]; then _cu_read_increment "${logs[0]}" "$_CU_USER_INIT"; fi
    if [ -n "${logs[1]:-}" ]; then _cu_read_increment "${logs[1]}" "$_CU_SYS_INIT"; fi
    return 0
}

# _cu_wait_status <cfg> <appid>   polls every 3 s until THIS app's updatecheck response
# appears or the shared deadline passes; sets _CU_ST (noupdate|ok|error-...|"").
_cu_wait_status() {
    local cfg="$1" appid="$2" now rem step inc
    while :; do
        now=$(_cu_now)
        [ "$now" -lt "$_CU_DEADLINE" ] || break
        inc=$(_cu_collect_inc "$cfg")
        _CU_ST=$(printf '%s\n' "$inc" | _cu_py last "$appid")
        [ -n "$_CU_ST" ] && return 0
        rem=$((_CU_DEADLINE - now))
        step=3
        [ "$rem" -lt 3 ] && step=$rem
        [ "$step" -gt 0 ] && _web_sleep "$step"
    done
    inc=$(_cu_collect_inc "$cfg")
    _CU_ST=$(printf '%s\n' "$inc" | _cu_py last "$appid")
    return 0
}

chromium_updater_apply() {
    local slug="$1" cfg="$2" app_path inst appid cur rel vw vs elapsed=0 wait
    _CU_SLUG="$slug"
    app_path=$(_cu_app_path "$cfg")
    inst=$(_web_installed_version "$app_path")
    if [ -z "$inst" ]; then
        _web_outcome probe_unavailable "installed version unreadable; nothing was woken"
        return 0
    fi
    appid=$(_cu_cfg "$cfg" appid)

    # (a) already proven current: no wake at all.
    if _cu_prove "$cfg" "$app_path"; then
        _web_outcome "$_CU_CODE" "$_CU_MSG"
        return 0
    fi
    if [ -n "$_CU_UNKNOWN" ]; then
        _web_outcome unknown_version "$_CU_UNKNOWN"
        return 0
    fi

    # (b) wake once per run (shared window for the Google apps).
    if ! _cu_wake_once "$slug" "$cfg"; then
        _web_outcome wake_failed "$_CU_WAKE_ERR"
        return 0
    fi

    # (c) wait for THIS app's response.
    _cu_wait_status "$cfg" "$appid"
    _cu_public_version "$cfg"

    # (d) evaluate.
    case "$_CU_ST" in
        noupdate)
            rel=$([ -n "$_CU_PUB" ] && _web_version_cmp "$_CU_PUB" "$inst")
            if [ "$rel" = "unknown" ]; then
                _web_outcome unknown_version "$(_web_unknown_version_message "${_CU_SLUG:-$appid}" "$inst" "$_CU_PUB")"
            elif [ "$rel" = "newer" ]; then
                _web_outcome rollout_hold "rollout_hold: ${inst} installed, ${_CU_PUB} public; the updater reports no update for this machine yet (staged rollout)"
            else
                _web_outcome current_vendor "the updater reports no update (Omaha noupdate)"
            fi
            return 0
            ;;
        ok)
            vw=$(_web_bounded_int "${ASCENDO_OMAHA_VERIFY_WAIT:-60}" 60 600)
            vs=$(_web_bounded_int "${ASCENDO_OMAHA_VERIFY_STEP:-5}" 5 60)
            [ "$vs" -ge 1 ] || vs=1
            while :; do
                cur=$(_web_installed_version "$app_path")
                if [ -n "$cur" ] && [ "$cur" != "$inst" ]; then
                    if [ "$(_web_version_cmp "$cur" "$inst")" = "unknown" ]; then
                        _web_outcome unknown_version "$(_web_unknown_version_message "${_CU_SLUG:-$appid}" "$inst" "$cur")"
                        return 0
                    fi
                    _web_outcome updated "updated ${inst} -> ${cur} by the vendor updater"
                    return 0
                fi
                [ "$elapsed" -ge "$vw" ] && break
                _web_sleep "$vs"
                elapsed=$((elapsed + vs))
            done
            _web_outcome unverified "the updater has an update for ${inst}; update in progress (the installed version has not changed yet)"
            return 0
            ;;
    esac

    # No (usable) response: history written during the wake, then the Chrome feed.
    if _cu_prove "$cfg" "$app_path"; then
        _web_outcome "$_CU_CODE" "$_CU_MSG"
        return 0
    fi
    if [ -n "$_CU_UNKNOWN" ]; then
        _web_outcome unknown_version "$_CU_UNKNOWN"
        return 0
    fi
    if [ -n "$_CU_PUB" ] && [ "$(_web_version_cmp "$_CU_PUB" "$inst")" = "newer" ]; then
        _web_outcome rollout_hold "rollout_hold: ${inst} installed, ${_CU_PUB} public; no updater response and the update is not offered to this machine yet (staged rollout)"
        return 0
    fi
    wait=$(_cu_cfg "$cfg" wait_s)
    _web_outcome unverified "the updater was woken but recorded no response within ${wait} s (unverified)"
    return 0
}
