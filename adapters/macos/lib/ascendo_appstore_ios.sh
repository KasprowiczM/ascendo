# adapters/macos/lib/ascendo_appstore_ios.sh
# iPad / iOS-on-Mac App Store apps: identity scan + iTunes Lookup gate.
# Bash 3.2-safe. Port of macOS_updates lib/appstore_ios.sh.
#
# These apps are wrapper bundles (`Wrapper/iTunesMetadata.plist`, no
# Contents/Info.plist, no _MASReceipt): `mas` cannot list or update them. Ascendo
# only REPORTS the ones with a newer store version (report-only, see
# mas_report_ipad_pending in ascendo_mas.sh); it never drives the App Store GUI.
#
# ---- Public interface (also for the inventory code) ------------------------
# Records are joined by the ASCII unit separator, $ASCENDO_IOS_US (0x1f), which
# `IFS="$ASCENDO_IOS_US" read -r a b c` keeps aligned even when a field is empty.
#
#   ascendo_app_store_managed <app_path>
#       rc 0 if the bundle is App Store-managed (iPad wrapper OR Mac App Store
#       app with Contents/_MASReceipt), else 1.
#
#   ascendo_ios_apps_scan
#       stdout: one record per installed iPad app, deduplicated by itemId:
#           name <US> itemId <US> version <US> path
#       Roots: /Applications and ~/Applications; ASCENDO_APP_DIRS (colon list)
#       overrides them. rc 0 (an empty result is a valid answer).
#
#   ascendo_ios_store_country
#       stdout: lowercase two-letter App Store country from `mas config --json`
#       (`.store`), or nothing (also ASCENDO_APPSTORE_COUNTRY overrides). Empty
#       means "omit &country=" -- the lookup then uses the default storefront.
#
#   ascendo_ios_apps_pending
#       stdout: one record per iPad app whose store version is strictly newer:
#           name <US> itemId <US> installed <US> store <US> path
#       rc 0  lookup done (no output = nothing pending, or no iPad apps -- the
#             network is not touched when nothing is installed)
#       rc 2  lookup FAILED (curl error, timeout, non-JSON): state UNKNOWN,
#             callers must NOT read this as "all current".
#
# Env: ASCENDO_APP_DIRS, ASCENDO_APPSTORE_COUNTRY, MAS_BIN,
#      ASCENDO_APPSTORE_LOOKUP_TIMEOUT (seconds, default 20).
# shellcheck shell=bash

if [ -n "${_ASCENDO_IOS_LOADED:-}" ]; then
    return 0 2>/dev/null || true
fi
_ASCENDO_IOS_LOADED=1

_ASCENDO_IOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ASCENDO_IOS_PY="$_ASCENDO_IOS_DIR/appstore_lookup.py"
ASCENDO_IOS_US="$(printf '\037')"
: "${MAS_BIN:=mas}"
# shellcheck source=ascendo_proc.sh
. "$_ASCENDO_IOS_DIR/ascendo_proc.sh"

ascendo_app_store_managed() {
    local _p="${1:-}"
    [ -n "$_p" ] || return 1
    [ -f "$_p/Wrapper/iTunesMetadata.plist" ] && return 0
    [ -d "$_p/Contents/_MASReceipt" ] && return 0
    return 1
}

ascendo_ios_apps_scan() {
    python3 "$_ASCENDO_IOS_PY" scan
}

ascendo_ios_store_country() {
    local _cc="${ASCENDO_APPSTORE_COUNTRY:-}"
    if [ -z "$_cc" ]; then
        _cc="$(ascendo_run_with_timeout 30 "$MAS_BIN" config --json 2>/dev/null | python3 -c '
import json, sys
try:
    print((json.load(sys.stdin).get("store") or "").strip().lower())
except Exception:
    pass
' 2>/dev/null)" || _cc=""
    fi
    _cc="$(printf '%s' "$_cc" | tr '[:upper:]' '[:lower:]')"
    case "$_cc" in
        [a-z][a-z]) printf '%s\n' "$_cc" ;;
        *) : ;;   # unknown / malformed: omit &country=
    esac
}

ascendo_ios_apps_pending() {
    local _scan _ids _country _url _tmp _rc _t
    _scan="$(ascendo_ios_apps_scan)" || _scan=""
    [ -n "$_scan" ] || return 0

    _ids="$(printf '%s\n' "$_scan" | awk -F "$ASCENDO_IOS_US" 'NF>=2 && $2!="" {printf "%s%s", (n++?",":""), $2}')"
    [ -n "$_ids" ] || return 0

    _country="$(ascendo_ios_store_country)"
    _url="https://itunes.apple.com/lookup?id=${_ids}"
    [ -z "$_country" ] || _url="${_url}&country=${_country}"

    _t="${ASCENDO_APPSTORE_LOOKUP_TIMEOUT:-20}"
    case "$_t" in ''|*[!0-9]*|0) _t=20 ;; esac

    _tmp="$(mktemp -d "${TMPDIR:-/tmp}/ascendo-ios.XXXXXX")" || return 2
    printf '%s\n' "$_scan" > "$_tmp/scan"
    if ! curl -fsS --proto =https --connect-timeout 10 --max-time "$_t" \
            --max-filesize 2097152 "$_url" > "$_tmp/lookup" 2>/dev/null; then
        rm -rf "$_tmp"
        return 2
    fi
    python3 "$_ASCENDO_IOS_PY" pending --scan-file "$_tmp/scan" --lookup-file "$_tmp/lookup"
    _rc=$?
    rm -rf "$_tmp"
    [ "$_rc" -eq 0 ] || return 2
    return 0
}
