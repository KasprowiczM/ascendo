# adapters/macos/lib/handlers/msupdate.sh
# Microsoft AutoUpdate handler.
# Wraps `msupdate --list` (check) and `sudo msupdate --install` (apply).
#
# Supports per-app targeting via `app.msupdate.app_id` in web_apps.toml
# (e.g. XCEL2019 for Excel, MSWD2019 for Word). When `app_id` is set,
# check returns the installed version from MAU's config, and apply runs
# `msupdate --install --apps <ID>` so only that app updates. When `app_id`
# is empty, check returns "pending" iff any updates are pending and apply
# runs the global `msupdate --install`.

# msupdate isn't on PATH by default — it ships inside Microsoft AutoUpdate.app.
_msupdate_bin() {
    if /usr/bin/command -v msupdate >/dev/null 2>&1; then
        echo msupdate
        return 0
    fi
    local fallback="/Library/Application Support/Microsoft/MAU2.0/Microsoft AutoUpdate.app/Contents/MacOS/msupdate"
    if [ -x "$fallback" ]; then
        echo "$fallback"
        return 0
    fi
    return 1
}

# _msupdate_app_id <cfg_json> -> echoes app_id from [app.msupdate] subtable
# or empty if unset.
_msupdate_app_id() {
    /usr/bin/printf '%s' "$1" | /usr/bin/python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
sub=d.get("msupdate") or {}
print(sub.get("app_id") or "")
'
}

# _msupdate_installed_version <app_id>
# Echoes "Last Updated Version" from `msupdate --config` for the given
# Application ID, or empty if not registered.
_msupdate_installed_version() {
    local app_id="$1"
    [ -z "$app_id" ] && return 1
    local bin
    bin=$(_msupdate_bin) || return 1
    "$bin" --config 2>/dev/null | /usr/bin/python3 -c '
import sys,re
target=sys.argv[1]
text=sys.stdin.read()
# Walk through "Application ID" / "Last Updated Version" pairs in the
# bracket-delimited config dump. We rely on the canonical formatting MAU
# emits (one app per block, sorted by app path).
blocks=re.split(r"\}\s*;", text)
for b in blocks:
    if f"Application ID\" = {target};" in b or f"\"Application ID\" = {target};" in b:
        m=re.search(r"\"Last Updated Version\" = \"?([^\";\n]+)\"?;", b)
        if m:
            print(m.group(1).strip())
            break
' "$app_id"
}

# _msupdate_has_pending_for <app_id>
# Echoes "yes" if msupdate --list reports an update for this app id,
# else echoes nothing. Empty app_id => global pending check.
_msupdate_has_pending_for() {
    local app_id="$1"
    local bin
    bin=$(_msupdate_bin) || return 1
    local out
    out=$("$bin" --list 2>/dev/null || true)
    if [ -n "$app_id" ]; then
        # MAU lists pending updates as "AppID: vX.Y.Z" lines (and a header).
        if /usr/bin/printf '%s' "$out" | /usr/bin/grep -qE "(^|[[:space:]])${app_id}([[:space:]]|:|/|$)"; then
            echo "yes"
            return 0
        fi
        return 0
    fi
    if /usr/bin/printf '%s' "$out" | /usr/bin/grep -qiE 'pending|update available|update is available'; then
        echo "yes"
    fi
}

msupdate_check() {
    local slug="$1" cfg="$2"
    local bin
    bin=$(_msupdate_bin) || return 0

    local app_id pending
    app_id=$(_msupdate_app_id "$cfg")
    pending=$(_msupdate_has_pending_for "$app_id")

    if [ -n "$app_id" ]; then
        # Echo the bundle's CFBundleShortVersionString when no update
        # pending, so apply.sh's pre-dispatch comparison
        # (CAND == INSTALLED) classifies this app as up_to_date and
        # skips the redundant `msupdate --install` round-trip.
        if [ -z "$pending" ]; then
            local app_path display_name plist short_ver
            app_path=$(/usr/bin/printf '%s' "$cfg" | /usr/bin/python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
print((d.get("app_path") or "") or "")
')
            if [ -z "$app_path" ]; then
                display_name=$(/usr/bin/printf '%s' "$cfg" | /usr/bin/python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
print(d.get("display_name") or "")
')
                app_path="/Applications/${display_name}.app"
            fi
            plist="$app_path/Contents/Info.plist"
            if [ -f "$plist" ]; then
                short_ver=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$plist" 2>/dev/null || true)
                if [ -n "$short_ver" ]; then
                    echo "$short_ver"
                    return 0
                fi
            fi
            return 0
        fi
        # Pending — return a non-matching sentinel so apply runs.
        echo "pending"
        return 0
    fi

    # No app_id — global behavior preserved (legacy "ms365" entry).
    if [ -n "$pending" ]; then
        echo "pending"
        return 0
    fi
    local mau_ver
    mau_ver=$("$bin" --config 2>/dev/null | /usr/bin/python3 -c '
import sys, re
text = sys.stdin.read()
m = re.search(r"AutoUpdateVersion\s*=\s*\"([^\"]+)\";", text)
if m:
    print(m.group(1))
')
    if [ -n "$mau_ver" ]; then
        echo "$mau_ver"
    else
        echo "up_to_date"
    fi
}

msupdate_apply() {
    local slug="$1" cfg="$2"
    local bin
    bin=$(_msupdate_bin) || return 30

    local app_id
    app_id=$(_msupdate_app_id "$cfg")

    # The user specifically requested to remove msupdate from silent updates
    # because `msupdate --install` can hang or take too long silently.
    # We return 95 to trigger the "needs manual update" UI flow in Ascendo.
    printf "Otwórz aplikację 'Microsoft AutoUpdate', aby sprawdzić i zainstalować aktualizacje.\n" >&2
    return 95
}
