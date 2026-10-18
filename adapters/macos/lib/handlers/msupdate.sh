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

# ---------------------------------------------------------------------------
# Listing: ONE serialized, bounded, validated `msupdate --list` per run
# (Spec B). The phase scripts call msupdate_prepare_listing before the parallel
# probe; every msupdate_check then reads the shared file instead of spawning its
# own msupdate (concurrent msupdate processes interrupt each other and hang).
# ---------------------------------------------------------------------------

# Probe note for the phase script: "<code>\t<text>" (single line).
_msupdate_note() {
    [ -n "${ASCENDO_WEB_PROBE_NOTE_FILE:-}" ] || return 0
    printf '%s\t%s\n' "$1" "$(printf '%s' "$2" | tr '\t\r\n' '   ')" \
        > "$ASCENDO_WEB_PROBE_NOTE_FILE" 2>/dev/null || true
    return 0
}

_msupdate_output_has_interruption() {
    _msupdate_sanitize_output "$1" \
        | grep -qE 'Update Assistant terminated|XPC Connection to updater invalidated|Connection to updater invalidated'
}

_msupdate_interruption_reason() {
    local clean
    clean="$(_msupdate_sanitize_output "$1")"
    if printf '%s\n' "$clean" | grep -q 'Update Assistant terminated'; then
        echo "Update Assistant terminated"
    elif printf '%s\n' "$clean" | grep -q 'XPC Connection to updater invalidated'; then
        echo "XPC Connection to updater invalidated"
    elif printf '%s\n' "$clean" | grep -qE 'Connection.*invalidated'; then
        echo "Connection to updater invalidated"
    else
        echo "Updater process interrupted"
    fi
}

# _msupdate_is_valid_listing <rc> <output>  (port of mau_is_valid_listing)
_msupdate_is_valid_listing() {
    local rc="$1" out="$2" clean
    [ "$rc" -eq 0 ] 2>/dev/null || return 1
    if _msupdate_output_has_interruption "$out"; then return 1; fi
    clean="$(_msupdate_sanitize_output "$out")"
    [ -n "$clean" ] || return 1
    if _msupdate_has_no_updates_sentinel "$out"; then return 0; fi
    [ -n "$(_msupdate_parse_pending_ids "$out")" ] && return 0
    return 1
}

# _msupdate_invalid_reason <rc> <output> -> short human reason
_msupdate_invalid_reason() {
    local rc="$1" out="$2"
    if [ "$rc" = "124" ]; then echo "timed out"; return 0; fi
    if _msupdate_output_has_interruption "$out"; then _msupdate_interruption_reason "$out"; return 0; fi
    if [ "$rc" != "0" ]; then echo "non-zero exit"; return 0; fi
    if [ -z "$(_msupdate_sanitize_output "$out")" ]; then echo "empty output"; return 0; fi
    echo "unrecognized output"
}

# Sets _MSU_LIST_RC / _MSU_LIST_OUT from the shared per-run file when exported,
# else runs one bounded listing itself (unit tests / standalone use).
_msupdate_load_listing() {
    local bin="$1" f="${ASCENDO_MSUPDATE_LIST_FILE:-}" rcf
    _MSU_LIST_RC=0
    _MSU_LIST_OUT=""
    if [ -n "$f" ] && [ -f "$f" ]; then
        rcf="${f%.txt}.rc"
        _MSU_LIST_OUT="$(cat "$f" 2>/dev/null)"
        _MSU_LIST_RC="$(cat "$rcf" 2>/dev/null)"
        [ -n "$_MSU_LIST_RC" ] || _MSU_LIST_RC=1
        return 0
    fi
    _msupdate_run_listing "$bin"
}

_msupdate_run_listing() {
    local bin="$1"
    _MSU_LIST_RC=0
    _MSU_LIST_OUT="$(ascendo_run_with_timeout "${ASCENDO_MSUPDATE_CHECK_TIMEOUT:-120}" "$bin" --list 2>&1)" \
        || _MSU_LIST_RC=$?
    return 0
}

# ---- preferences (every read/mutation on an exported COPY) ------------------
# ASCENDO_MAU_PREFS_FILE (fixture seam): when set, the real domain is never read
# and any "import" writes back into that file only.
_msupdate_prefs_export() {
    if [ -n "${ASCENDO_MAU_PREFS_FILE:-}" ]; then
        [ -f "$ASCENDO_MAU_PREFS_FILE" ] || return 1
        cp "$ASCENDO_MAU_PREFS_FILE" "$1" >/dev/null 2>&1
    else
        defaults export com.microsoft.autoupdate2 "$1" >/dev/null 2>&1
    fi
}

_msupdate_prefs_import() {
    if [ -n "${ASCENDO_MAU_PREFS_FILE:-}" ]; then
        cp "$1" "$ASCENDO_MAU_PREFS_FILE" >/dev/null 2>&1
    else
        defaults import com.microsoft.autoupdate2 "$1" >/dev/null 2>&1
    fi
}

# MAU's own AppVersions register entry for a bundle path (port of
# mau_prefs_read_app_version).
_msupdate_prefs_read_app_version() {
    local src="com.microsoft.autoupdate2"
    [ -n "${ASCENDO_MAU_PREFS_FILE:-}" ] && src="$ASCENDO_MAU_PREFS_FILE"
    defaults read "$src" AppVersions 2>/dev/null \
        | awk -v app="\"$1\"" '
            index($0, app) == 0 { next }
            {
                line = $0
                sub(/^[^=]*=[[:space:]]*/, "", line)
                gsub(/[";]/, "", line)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
                print line
                exit
            }'
    return 0
}

_msupdate_installed_short() {
    [ -n "$1" ] && [ -f "$1/Contents/Info.plist" ] || return 0
    /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$1/Contents/Info.plist" 2>/dev/null || true
}

# _msupdate_app_path <cfg_json> -> bundle path (registry app_path, else display name)
_msupdate_app_path() {
    local root="${ASCENDO_WEB_APPS_ROOT:-/Applications}"; root="${root%%:*}"
    /usr/bin/printf '%s' "$1" | ROOT="$root" /usr/bin/python3 -c '
import json,os,sys
try: d=json.load(sys.stdin)
except Exception: d={}
p=d.get("app_path") or ""
if not p:
    p=os.path.join(os.environ["ROOT"], (d.get("display_name") or "")+".app")
print(p)
'
}

# Office DeferralDays awareness (F2) -- read-only.
_msupdate_deferral_days_for() {
    local id="$1" tmp val=""
    tmp="$(mktemp "${TMPDIR:-/tmp}/ascendo_mau_prefs.XXXXXX")" || return 0
    if _msupdate_prefs_export "$tmp"; then
        val="$(plutil -extract "OptionalUpdatesDeferrals.DeferralDays.$id" raw -o - "$tmp" 2>/dev/null || true)"
    fi
    rm -f "$tmp"
    # Only an integer > 0 is a quarantine (0 = no deferral).
    case "$val" in ''|*[!0-9]*) val="" ;; esac
    [ -n "$val" ] && [ "$val" -gt 0 ] || val=""
    printf '%s' "$val"
    return 0
}

_msupdate_current_channel() {
    local ch="" tmp bin
    if [ -n "${ASCENDO_MAU_PREFS_FILE:-}" ]; then
        tmp="$(mktemp "${TMPDIR:-/tmp}/ascendo_mau_prefs.XXXXXX")" || { printf 'unknown'; return 0; }
        if _msupdate_prefs_export "$tmp"; then
            ch="$(plutil -extract ChannelName raw -o - "$tmp" 2>/dev/null || true)"
        fi
        rm -f "$tmp"
    else
        ch="$(defaults read /Library/Preferences/com.microsoft.autoupdate2 ChannelName 2>/dev/null || true)"
        [ -n "$ch" ] || ch="$(defaults read com.microsoft.autoupdate2 ChannelName 2>/dev/null || true)"
    fi
    if [ -z "$ch" ]; then
        bin="$(_msupdate_bin 2>/dev/null || true)"
        if [ -n "$bin" ]; then
            ch="$(ascendo_run_with_timeout 20 "$bin" --config 2>/dev/null \
                | awk -F': *' '/ChannelName/{print $2; exit}' || true)"
        fi
    fi
    printf '%s' "${ch:-unknown}"
    return 0
}

# Offered short version for a product id in a listing (port of mau_offered_version).
_msupdate_offered_version() {
    _msupdate_sanitize_output "$1" | awk -v id="$2" '
        index($0, id) == 0 { next }
        {
            for (i = NF; i >= 1; i--) {
                tok = $i
                gsub(/[()]/, "", tok)
                if (tok ~ /^[0-9]+(\.[0-9]+)+$/) { print tok; exit }
            }
        }'
}

# ---- TEAMS21 DeferralVersions pin (F1) --------------------------------------
# The pin caps the maximum version MAU will offer. A pin EQUAL to the installed
# build is MAU's own bookkeeping (leave it); only a strictly OLDER pin blocks
# updates. Release is opt-in (ASCENDO_MAU_MUTATE_PREFS=1) and follows the
# export -> backup -> edit copy -> lint -> import -> re-export/verify protocol.
# Never killall cfprefsd.
#
# Result in _MSU_PIN_NOTE as "<code>\t<text>" (empty when nothing to say).
_msupdate_run_dir() {
    local d=""
    if [ -n "${ASCENDO_MSUPDATE_LIST_FILE:-}" ]; then
        d="$(dirname "$ASCENDO_MSUPDATE_LIST_FILE")"
    elif [ -n "${OUTPUT_DIR:-}" ] && [ -n "${RUN_ID:-}" ]; then
        d="$OUTPUT_DIR/$RUN_ID"
    fi
    [ -n "$d" ] || d="$(mktemp -d "${TMPDIR:-/tmp}/ascendo_mau_run.XXXXXX")"
    mkdir -p "$d" 2>/dev/null || true
    printf '%s' "$d"
}

_msupdate_teams_pin_release() {
    local _root="${ASCENDO_WEB_APPS_ROOT:-/Applications}"
    local app_path="${1:-${_root%%:*}/Microsoft Teams.app}"
    local key="OptionalUpdatesDeferrals.DeferralVersions.TEAMS21"
    local tmp work check pin installed rel dir backup
    _MSU_PIN_NOTE=""
    tmp="$(mktemp "${TMPDIR:-/tmp}/ascendo_mau_prefs.XXXXXX")" || return 0
    if ! _msupdate_prefs_export "$tmp"; then rm -f "$tmp"; return 0; fi
    pin="$(plutil -extract "$key" raw -o - "$tmp" 2>/dev/null || true)"
    if [ -z "$pin" ]; then rm -f "$tmp"; return 0; fi
    installed="$(_msupdate_prefs_read_app_version "$app_path")"
    [ -n "$installed" ] || installed="$(_msupdate_installed_short "$app_path")"
    if [ -z "$installed" ] || [ "$pin" = "$installed" ]; then rm -f "$tmp"; return 0; fi
    rel="$(_web_version_cmp "$installed" "$pin")"
    if [ "$rel" != "newer" ]; then rm -f "$tmp"; return 0; fi
    # Ruling 42: mutation needs BOTH the opt-in env and a real apply phase
    # (_MSU_PIN_MUTATE_ALLOWED=1); check/plan/dry-run only ever diagnose.
    if [ "${ASCENDO_MAU_MUTATE_PREFS:-0}" != "1" ] || [ "${_MSU_PIN_MUTATE_ALLOWED:-0}" != "1" ]; then
        _MSU_PIN_NOTE="mau_pin_stale"$'\t'"TEAMS21 DeferralVersions pin ${pin} is older than installed ${installed} and caps what MAU offers; set ASCENDO_MAU_MUTATE_PREFS=1 to release it (read-only diagnosis only)"
        rm -f "$tmp"
        return 0
    fi
    dir="$(_msupdate_run_dir)"
    backup="$dir/com.microsoft.autoupdate2.pre-pin-release.plist"
    cp "$tmp" "$backup" 2>/dev/null || { rm -f "$tmp"; return 0; }
    work="$(mktemp "${TMPDIR:-/tmp}/ascendo_mau_prefs.XXXXXX")" || { rm -f "$tmp"; return 0; }
    cp "$tmp" "$work"
    if plutil -remove "$key" "$work" >/dev/null 2>&1 \
       && plutil -lint "$work" >/dev/null 2>&1 \
       && _msupdate_prefs_import "$work"; then
        check="$(mktemp "${TMPDIR:-/tmp}/ascendo_mau_prefs.XXXXXX")" || check=""
        if [ -n "$check" ] && _msupdate_prefs_export "$check" \
           && [ -z "$(plutil -extract "$key" raw -o - "$check" 2>/dev/null || true)" ]; then
            _MSU_PIN_NOTE="mau_pin_released"$'\t'"TEAMS21 DeferralVersions pin ${pin} (older than installed ${installed}) released; backup ${backup}"
        else
            _MSU_PIN_NOTE="mau_pin_recreated"$'\t'"TEAMS21 DeferralVersions pin ${pin} reappeared after release (MAU rewrote it); not claiming it is released; backup ${backup}"
        fi
        [ -z "$check" ] || rm -f "$check"
    else
        _MSU_PIN_NOTE="mau_pin_stale"$'\t'"TEAMS21 DeferralVersions pin ${pin} could not be edited safely (nothing was changed); backup ${backup}"
    fi
    rm -f "$tmp" "$work"
    return 0
}

# msupdate_prepare_listing <results_dir>
# Phase-script entry (before the parallel probe): when any listed app uses the
# msupdate handler, release the stale Teams pin (opt-in AND only when the 2nd
# argument is 1, i.e. a real non-dry-run apply), then run ONE listing
# and publish it to $OUTPUT_DIR/$RUN_ID/_msupdate_list.{txt,rc}.
msupdate_prepare_listing() {
    local rdir="$1" mutate_ok="${2:-0}" f any=0 bin dir
    _MSU_PIN_MUTATE_ALLOWED=0
    [ "$mutate_ok" = "1" ] && _MSU_PIN_MUTATE_ALLOWED=1
    for f in "$rdir"/*.handler; do
        [ -f "$f" ] || continue
        if [ "$(cat "$f" 2>/dev/null)" = "msupdate" ]; then any=1; break; fi
    done
    [ "$any" = 1 ] || return 0
    bin="$(_msupdate_bin)" || return 0
    dir="$(_msupdate_run_dir)"
    mkdir -p "$dir" 2>/dev/null || true
    export ASCENDO_MSUPDATE_LIST_FILE="$dir/_msupdate_list.txt"
    rm -f "$dir/_msupdate_list.pin" 2>/dev/null || true
    if grep -ql 'TEAMS21' "$rdir"/*.cfg.json 2>/dev/null; then
        _msupdate_teams_pin_release
        if [ -n "$_MSU_PIN_NOTE" ]; then
            printf '%s\n' "$_MSU_PIN_NOTE" > "$dir/_msupdate_list.pin" 2>/dev/null || true
        fi
    fi
    _msupdate_run_listing "$bin"
    printf '%s\n' "$_MSU_LIST_OUT" > "$ASCENDO_MSUPDATE_LIST_FILE"
    printf '%s\n' "$_MSU_LIST_RC" > "$dir/_msupdate_list.rc"
    _MSU_PIN_MUTATE_ALLOWED=0
    return 0
}

# ---- Teams oracle (F5) -------------------------------------------------------
# MAU never offers TEAMS21 reliably; the Homebrew cask is the public oracle.
_msupdate_teams_cask_version() {
    local url="${ASCENDO_MSUPDATE_TEAMS_CASK_URL-https://formulae.brew.sh/api/cask/microsoft-teams.json}" body
    [ -n "$url" ] || return 0
    body="$(ascendo_run_with_timeout 12 curl --proto '=https' -fsS --max-time 8 --max-filesize 262144 "$url" 2>/dev/null)" || return 0
    printf '%s' "$body" | /usr/bin/python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
v=d.get("version") if isinstance(d,dict) else None
if isinstance(v,str):
    print(v.split(",")[0].strip())
' 2>/dev/null || true
    return 0
}

# _msupdate_has_pending_for <app_id>  (kept for callers: uses the shared listing)
_msupdate_has_pending_for() {
    local app_id="$1" bin ids
    bin=$(_msupdate_bin) || return 1
    _msupdate_load_listing "$bin"
    _msupdate_is_valid_listing "$_MSU_LIST_RC" "$_MSU_LIST_OUT" || return 0
    ids="$(_msupdate_parse_pending_ids "$_MSU_LIST_OUT")"
    if [ -n "$app_id" ]; then
        if printf '%s\n' "$ids" | grep -qx "$app_id"; then echo "yes"; fi
        return 0
    fi
    if [ -n "$ids" ]; then echo "yes"; fi
    return 0
}

msupdate_check() {
    local slug="$1" cfg="$2"
    local bin app_id ids
    bin=$(_msupdate_bin) || return 0

    _msupdate_load_listing "$bin"
    if ! _msupdate_is_valid_listing "$_MSU_LIST_RC" "$_MSU_LIST_OUT"; then
        _msupdate_note error_soft "${slug}: mau_check_failed (exit ${_MSU_LIST_RC}: $(_msupdate_invalid_reason "$_MSU_LIST_RC" "$_MSU_LIST_OUT")) — status unknown, NOT current"
        echo "__MAU_UNKNOWN__"
        return 0
    fi
    ids="$(_msupdate_parse_pending_ids "$_MSU_LIST_OUT")"
    app_id=$(_msupdate_app_id "$cfg")

    if [ -n "$app_id" ]; then
        local app_path
        app_path="$(_msupdate_app_path "$cfg")"
        if printf '%s\n' "$ids" | grep -qx "$app_id"; then
            local offered installed_short
            offered="$(_msupdate_offered_version "$_MSU_LIST_OUT" "$app_id")"
            installed_short="$(_msupdate_installed_short "$app_path")"
            if [ -n "$offered" ] && [ -n "$installed_short" ] \
               && [ "$(_web_version_cmp "$installed_short" "$offered")" = "newer" ]; then
                _msupdate_note error_soft "${slug}: MAU offers ${offered} ≤ installed ${installed_short} (upstream package regression, channel=$(_msupdate_current_channel)); not actionable"
                echo "__MAU_REGRESSED__"
                return 0
            fi
            # Offered short == installed short is a legitimate build-only update:
            # surface it as available (`pending`), never as the installed version.
            if [ -n "$offered" ] && [ "$offered" = "$installed_short" ]; then
                _msupdate_note update_available "${slug}: MAU offers a newer build of ${offered} (installed short version is the same)"
                echo "pending"
                return 0
            fi
            echo "${offered:-pending}"
            return 0
        fi

        # Not offered. An active DeferralDays quarantine makes an empty list
        # meaningless (F2): unknown, never current.
        case " MSWD2019 XCEL2019 PPT32019 OPIM2019 ONMC2019 " in
            *" $app_id "*)
                local days
                days="$(_msupdate_deferral_days_for "$app_id")"
                if [ -n "$days" ]; then
                    _msupdate_note error_soft "${slug}: held by MAU DeferralDays=${days} — an empty list is not evidence of current"
                    echo "__MAU_QUARANTINED__"
                    return 0
                fi
                ;;
        esac

        local short_ver
        short_ver="$(_msupdate_installed_short "$app_path")"

        if [ "$app_id" = "TEAMS21" ] && [ -n "$short_ver" ]; then
            local pin_note="" pf cask rel pin_text=""
            pf=""
            [ -n "${ASCENDO_MSUPDATE_LIST_FILE:-}" ] && pf="${ASCENDO_MSUPDATE_LIST_FILE%.txt}.pin"
            if [ -n "$pf" ] && [ -f "$pf" ]; then
                pin_note="$(head -n 1 "$pf" 2>/dev/null)"
            else
                _msupdate_teams_pin_release "$app_path"
                pin_note="$_MSU_PIN_NOTE"
            fi
            [ -n "$pin_note" ] && pin_text=" (${pin_note#*$'\t'})"
            cask="$(_msupdate_teams_cask_version)"
            if [ -n "$cask" ]; then
                rel="$(_web_version_cmp "$cask" "$short_ver")"
                if [ "$rel" = "equal" ] || _version_eq_loose "$cask" "$short_ver"; then
                    _msupdate_note current_cask_only "${slug}: MAU is silent; Homebrew cask microsoft-teams ${cask} matches installed ${short_ver}${pin_text}"
                    echo "$short_ver"
                    return 0
                fi
                if [ "$rel" = "newer" ]; then
                    _msupdate_note update_available "${slug}: Homebrew cask microsoft-teams offers ${cask} (installed ${short_ver}); MAU does not manage TEAMS21${pin_text}"
                    echo "$cask"
                    return 0
                fi
            fi
            _msupdate_note current_verified "${slug}: MAU listing valid and silent for TEAMS21 (no newer public cask data)${pin_text}"
            echo "$short_ver"
            return 0
        fi

        if [ -n "$short_ver" ]; then
            echo "$short_ver"
        fi
        return 0
    fi

    # No app_id — global behavior preserved (legacy "ms365" entry).
    if [ -n "$ids" ]; then
        echo "pending"
        return 0
    fi
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

# ---- opt-in scoped install (F4 / Ruling 40) ----------------------------------
_msupdate_active_processes() {
    if [ -n "${ASCENDO_MAU_MOCK_ACTIVE_PROCESSES+x}" ]; then
        printf '%s' "$ASCENDO_MAU_MOCK_ACTIVE_PROCESSES"
        return 0
    fi
    pgrep -f "Microsoft Update Assistant.app/Contents/MacOS/Microsoft Update Assistant|msupdate" 2>/dev/null || true
}

_msupdate_wait_idle() {
    local max="${ASCENDO_MAU_IDLE_TIMEOUT:-15}" n=0
    while [ "$n" -lt "$max" ]; do
        [ -z "$(_msupdate_active_processes)" ] && return 0
        sleep 1
        n=$((n + 1))
    done
    [ -z "$(_msupdate_active_processes)" ]
}

# _msupdate_scoped_install <bin> <app_id> <app_path>
# rc 0 = measured (version changed, or id left a fresh valid listing);
# 10 = interrupted; 20 = could not be measured.
_msupdate_scoped_install() {
    local bin="$1" id="$2" app="$3"
    local wait before after attempt=1 irc out
    wait="${ASCENDO_MSUPDATE_INSTALL_WAIT:-600}"
    before="$(_msupdate_installed_short "$app")"
    while :; do
        irc=0
        out="$(ascendo_run_with_timeout $((wait + 60)) "$bin" --install --wait "$wait" --apps "$id" 2>&1)" || irc=$?
        _msupdate_sanitize_output "$out" | tail -n 20 >&2
        if _msupdate_output_has_interruption "$out"; then
            printf '%s: msupdate --install interrupted (%s)\n' "$id" "$(_msupdate_interruption_reason "$out")" >&2
            irc=10
        fi
        _msupdate_wait_idle || true
        if [ "$irc" -ne 10 ]; then
            _msupdate_run_listing "$bin"
            after="$(_msupdate_installed_short "$app")"
            if [ -n "$after" ] && [ "$after" != "$before" ]; then
                return 0
            fi
            if _msupdate_is_valid_listing "$_MSU_LIST_RC" "$_MSU_LIST_OUT" \
               && ! _msupdate_parse_pending_ids "$_MSU_LIST_OUT" | grep -qx "$id"; then
                return 0
            fi
        fi
        if [ "$attempt" -ge 2 ] || [ -n "$(_msupdate_active_processes)" ]; then
            if [ "$irc" -ne 0 ]; then return "$irc"; fi
            return 20
        fi
        attempt=$((attempt + 1))
    done
}

msupdate_apply() {
    local slug="$1" cfg="$2"
    local bin
    bin=$(_msupdate_bin) || return 30

    local app_id app_path
    app_id=$(_msupdate_app_id "$cfg")
    app_path="$(_msupdate_app_path "$cfg")"

    # Opt-in pin release (read-only diagnosis by default). msupdate_apply only
    # runs in a real (non-dry-run) apply, so mutation is allowed here.
    _MSU_PIN_MUTATE_ALLOWED=1 _msupdate_teams_pin_release "$app_path" || true

    # Microsoft documents TEAMS21 as not installable via msupdate --apps.
    if [ "$app_id" = "TEAMS21" ]; then
        printf "Otwórz aplikację Microsoft Teams — MAU nie zarządza aktualizacjami TEAMS21.\n" >&2
        return 95
    fi

    # Scoped silent install only when explicitly opted in, never in safe/quick.
    if [ "${ASCENDO_MAU_SCOPED_INSTALL:-0}" = "1" ] && [ -n "$app_id" ]; then
        case "${PROFILE_NAME:-}" in
            safe|quick) ;;
            *)
                local irc=0
                _msupdate_scoped_install "$bin" "$app_id" "$app_path" || irc=$?
                return "$irc"
                ;;
        esac
    fi

    # Default: the documented RC 95 GUI gate (silent install hangs on some MAU builds).
    printf "Otwórz aplikację 'Microsoft AutoUpdate', aby sprawdzić i zainstalować aktualizacje.\n" >&2
    return 95
}
