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

# MAU --list writes a spinner with CR overwrites:
#   Checking for updates... / Update Assistant: Idle / No updates available
# Pre-2026-08-19 parsers treated "Update Assistant: Idle" as a pending
# update. Ported from macOS_updates mau_sanitize_output / mau_parse_pending.
_MSUPDATE_KNOWN_IDS="MSau04 MSWD2019 XCEL2019 PPT32019 OPIM2019 ONMC2019 TEAMS21 WDAVSHIM OLIC02"

_msupdate_sanitize_output() {
    local esc
    esc="$(printf '\033')"
    printf '%s\n' "$1" \
        | tr '\r' '\n' \
        | sed "s/${esc}\\[[0-9;?]*[0-9A-Za-z]//g" \
        | tr -d '\001-\010\013\014\016-\037\177' \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d'
}

_msupdate_has_no_updates_sentinel() {
    _msupdate_sanitize_output "$1" | grep -qi '^no updates available$'
}

_msupdate_parse_pending_ids() {
    if _msupdate_has_no_updates_sentinel "$1"; then
        return 0
    fi
    _msupdate_sanitize_output "$1" | awk -v known=" $_MSUPDATE_KNOWN_IDS " '
        function is_product_id(tok) {
            if (tok == "") return 0
            if (index(known, " " tok " ") > 0) return 1
            if (length(tok) < 5 || length(tok) > 8) return 0
            if (tok !~ /^[A-Z]/) return 0
            if (tok !~ /[0-9]/) return 0
            if (tok ~ /^[A-Z][A-Z0-9]*$/) return 1
            return 0
        }
        {
            if ($0 ~ /^[Uu]pdate [Aa]ssistant:/ \
                || $0 ~ /^Checking for updates/ \
                || $0 ~ /^Detecting and downloading/ \
                || $0 ~ /^Found the following/ \
                || $0 ~ /[Nn]o updates available/) next
            rest = $0
            while (match(rest, /\([A-Za-z0-9]+\)/)) {
                tok = substr(rest, RSTART + 1, RLENGTH - 2)
                if (is_product_id(tok)) { print tok; next }
                rest = substr(rest, RSTART + RLENGTH)
            }
            n = split($0, parts, /[^A-Za-z0-9]+/)
            for (i = 1; i <= n; i++) {
                if (is_product_id(parts[i])) { print parts[i]; next }
            }
        }
    ' | awk '!seen[$0]++'
}

# DeferralVersions.TEAMS21 pins the MAXIMUM version MAU will ever offer.
# A pin equal to the installed build blocks Teams forever. Tests must set
# ASCENDO_MAU_MUTATE_PREFS=0 so we never touch the operator's real domain.
_msupdate_clear_stale_teams_deferral() {
    [ "${ASCENDO_MAU_MUTATE_PREFS:-1}" = "1" ] || return 0
    local tmp
    tmp="$(mktemp "${TMPDIR:-/tmp}/ascendo_mau_prefs.XXXXXX")" || return 0
    if ! defaults export com.microsoft.autoupdate2 "$tmp" >/dev/null 2>&1; then
        rm -f "$tmp"
        return 0
    fi
    if plutil -extract "OptionalUpdatesDeferrals.DeferralVersions.TEAMS21" raw -o - "$tmp" >/dev/null 2>&1; then
        plutil -remove "OptionalUpdatesDeferrals.DeferralVersions.TEAMS21" "$tmp" >/dev/null 2>&1 || true
        defaults import com.microsoft.autoupdate2 "$tmp" >/dev/null 2>&1 || true
    fi
    rm -f "$tmp"
    return 0
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
    if _msupdate_has_no_updates_sentinel "$out"; then
        return 0
    fi
    local ids
    ids="$(_msupdate_parse_pending_ids "$out")"
    if [ -n "$app_id" ]; then
        if printf '%s\n' "$ids" | grep -qx "$app_id"; then
            echo "yes"
        fi
        return 0
    fi
    if [ -n "$ids" ]; then
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
    
    # No pending updates globally. Just output CFBundleShortVersionString
    # of the MAU app so it matches the inventory and shows as up_to_date
    # instead of reporting a fake update against the longer AutoUpdateVersion.
    local plist="/Library/Application Support/Microsoft/MAU2.0/Microsoft AutoUpdate.app/Contents/Info.plist"
    if [ -f "$plist" ]; then
        local short_ver
        short_ver=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$plist" 2>/dev/null || true)
        if [ -n "$short_ver" ]; then
            echo "$short_ver"
            return 0
        fi
    fi
    echo "up_to_date"
}

msupdate_apply() {
    local slug="$1" cfg="$2"
    local bin
    bin=$(_msupdate_bin) || return 30

    local app_id
    app_id=$(_msupdate_app_id "$cfg")

    # Break the Teams DeferralVersions deadlock even when apply stays
    # GUI-gated (RC 95). Clearing the pin is safe and read-repairing.
    _msupdate_clear_stale_teams_deferral || true

    # Microsoft documents TEAMS21 as not installable via msupdate --apps.
    # Keep the GUI sentinel so we never hang on a scoped Teams install.
    if [ "$app_id" = "TEAMS21" ]; then
        printf "Otwórz aplikację Microsoft Teams — MAU nie zarządza aktualizacjami TEAMS21.\n" >&2
        return 95
    fi

    # The silent `msupdate --install` path still hangs on some MAU builds
    # even after sanitizing --list. Keep the documented RC 95 GUI gate
    # for Word/Excel/etc.; check is now honest (noise is not pending).
    printf "Otwórz aplikację 'Microsoft AutoUpdate', aby sprawdzić i zainstalować aktualizacje.\n" >&2
    return 95
}
