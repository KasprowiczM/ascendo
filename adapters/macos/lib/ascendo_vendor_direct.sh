#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_vendor_direct.sh -- hardened vendor installer
# =============================================================================
# Sourced by lib/ascendo_web.sh (never run directly). Bash 3.2 compatible.
# Port of macOS_updates lib/vendor_direct.sh + update_internet_apps.sh
# copy_verified_app (reference commit 9b697a2), adapted to Ascendo's seams.
#
# Public functions
#   vendor_direct_host_allowed <url> <host>
#   vendor_direct_verify_checksum <file> <kind> <value>
#   _web_fetch_to_file <url> <dest> [timeout_s] [max_bytes]
#   vendor_direct_install <installed_app> <url> <artifact> <ck> <cs> <host> [expected_version]
#   _web_vendor_install <slug> <url> <artifact> <ck> <cs> <host> <expected_version> <installed_app>
#   copy_verified_app <src_app> <installed_app_path|label>
#
# Return codes of vendor_direct_install / _web_vendor_install:
#   0 installed        1 refused / download / verify failure (soft), incl.
#                      identity_unverifiable (installed or staged bundle has no
#                      readable bundle id / Team ID, e.g. ad-hoc signed)
#   2 dry-run          3 swap failure (hard: copy/identity mismatch/rollback)
#   4 deferred         the app is running OR its running state could not be
#                      determined (callers: item `skipped` + needs_restart,
#                      never `failed`). Nothing was changed.
# Return codes of copy_verified_app: 0 ok, 1 refused/failed, 2 deferred, 3 soft refusal.
#
# Safety contract (Global Constraint 4):
#   * An app is replaced ONLY when `ascendo_app_running_state` answers an
#     explicit `false`, probed before download, before staging and again
#     immediately before the backup/swap. `unknown` defers (fail closed).
#   * This common path never quits, signals or kills anything. A handler that
#     itself launched the app (toolkit launch) owns its own quit logic.
#   * The bundle is replaced by an atomic same-volume swap with automatic
#     rollback; bundle id AND Team ID of old and new must be non-empty and equal.
#
# Test seams (all optional; defaults are the real system tools):
#   ASCENDO_WEB_APPS_ROOT     Applications root (default /Applications). The
#                             staging/backup dirs live inside it
#                             (<root>/.ascendo_staging.* / .ascendo_backup.*).
#   ASCENDO_WEB_TMP_DIR       scratch root for downloads/extraction/mounts
#                             (default $TMPDIR or /tmp).
#   ASCENDO_WEB_{HDIUTIL,SPCTL,CODESIGN,DITTO,MV}_BIN   tool stand-ins.
#   ASCENDO_WEB_CURL_BIN      (defined in ascendo_web.sh) curl stand-in.
#   ASCENDO_LSAPPINFO_BIN     (defined in ascendo_proc.sh) lsappinfo stand-in.
#   ASCENDO_DRY_RUN=1|true    makes the installer return 2 without any action.
#
# No EXIT trap is installed here (the sourcing phase script owns its own
# trap): every exit path of vendor_direct_install runs _vd_cleanup_work, which
# detaches leftover DMG mounts (tracked in <work>/mounts.list) before removing
# the work directory. A mount that cannot be detached is left in place with its
# directory (never `rm -rf` through a live mount).
# =============================================================================
# shellcheck shell=bash

if [ -n "${_ASCENDO_VENDOR_DIRECT_LOADED:-}" ]; then
    return 0 2>/dev/null || true
fi
_ASCENDO_VENDOR_DIRECT_LOADED=1

[ -n "${_ASCENDO_PROC_LOADED:-}" ] || . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ascendo_proc.sh"

# ---- seams ------------------------------------------------------------------

_vd_tool() {
    case "$1" in
        hdiutil)  printf '%s' "${ASCENDO_WEB_HDIUTIL_BIN:-/usr/bin/hdiutil}" ;;
        spctl)    printf '%s' "${ASCENDO_WEB_SPCTL_BIN:-/usr/sbin/spctl}" ;;
        codesign) printf '%s' "${ASCENDO_WEB_CODESIGN_BIN:-/usr/bin/codesign}" ;;
        ditto)    printf '%s' "${ASCENDO_WEB_DITTO_BIN:-/usr/bin/ditto}" ;;
        mv)       printf '%s' "${ASCENDO_WEB_MV_BIN:-/bin/mv}" ;;
        *)        return 1 ;;
    esac
}

_vd_apps_root() {
    local r="${ASCENDO_WEB_APPS_ROOT:-/Applications}"
    printf '%s' "${r%/}"
}

_vd_tmp_root() {
    local r="${ASCENDO_WEB_TMP_DIR:-${TMPDIR:-/tmp}}"
    printf '%s' "${r%/}"
}

_vd_dry_run() {
    case "${ASCENDO_DRY_RUN:-0}" in 1|true|TRUE|yes) return 0 ;; esac
    return 1
}

# stderr is what apply.sh captures into the sidecar message tail.
_vd_say() {
    printf '%s\n' "$*" >&2
    if command -v _stream_emit >/dev/null 2>&1; then
        _stream_emit ">>> $*"
    fi
    return 0
}

# _vd_try <cmd args...> -- run plain first; only on failure retry through the
# askpass-aware privilege helper (ascendo_json.sh `_ascendo_sudo`), the same
# plain-then-sudo pattern the old installer used for /Applications writes.
# Without the helper (unit tests, bare sourcing) there is no fallback.
_vd_try() {
    if "$@" 2>/dev/null; then
        return 0
    fi
    if command -v _ascendo_sudo >/dev/null 2>&1; then
        _ascendo_sudo "$@" 2>/dev/null
        return $?
    fi
    return 1
}

_vd_rm_rf() {
    local p
    for p in "$@"; do
        [ -n "$p" ] || continue
        _vd_try /bin/rm -rf "$p" || true
    done
    return 0
}

# ---- identity ---------------------------------------------------------------

# Installed bundle id, Info.plist first (the app we are asked to update).
_vd_plist_bundle_id() {
    local app="$1" bid=""
    [ -n "$app" ] && [ -e "$app" ] || return 1
    bid="$(/usr/bin/defaults read "$app/Contents/Info" CFBundleIdentifier 2>/dev/null || true)"
    if [ -z "$bid" ]; then
        bid="$("$(_vd_tool codesign)" -dv "$app" 2>&1 | sed -n 's/^Identifier=//p' | head -n 1)"
    fi
    printf '%s\n' "$bid"
}

# Signed identity: codesign Identifier first, Info.plist as the fallback.
_vd_sig_bundle_id() {
    local app="$1" bid=""
    bid="$("$(_vd_tool codesign)" -dv --verbose=4 "$app" 2>&1 | sed -n 's/^Identifier=//p' | head -n 1)"
    if [ -z "$bid" ]; then
        bid="$(/usr/bin/defaults read "$app/Contents/Info" CFBundleIdentifier 2>/dev/null || true)"
    fi
    printf '%s\n' "$bid"
}

# Apple Team ID. codesign prints `TeamIdentifier=not set` for ad-hoc/unsigned
# code; that is NOT an identity, so it reads as empty (and then refuses).
_vd_team_id() {
    local t
    t="$("$(_vd_tool codesign)" -dv --verbose=4 "$1" 2>&1 | sed -n 's/^TeamIdentifier=//p' | head -n 1)"
    case "$t" in
        'not set'|'') printf '' ;;
        *) printf '%s' "$t" ;;
    esac
}

_vd_spctl_ok() {
    "$(_vd_tool spctl)" --assess --type execute "$1" >/dev/null 2>&1
}

# Exit 0 only when the bundle at <dest> is absent or EXPLICITLY not running.
_vd_replacement_is_idle() {
    local dest="$1" bid
    [ -e "$dest" ] || [ -L "$dest" ] || return 0
    bid="$(_vd_sig_bundle_id "$dest")"
    [ "$(ascendo_app_running_state "$bid")" = "false" ]
}

# Compare the staged bundle with the installed one.
#   0 identical bundle id AND Team ID
#   3 unverifiable: an id or Team ID is empty / "not set" on either side
#     (ad-hoc, unsigned) -- reason identity_unverifiable, a SOFT refusal
#   1 mismatch (both readable, different) -- hard
_vd_verify_replacement_identity() {
    local new_app="$1" old_app="$2" label="$3"
    local nb ob nt ot
    nb="$(_vd_sig_bundle_id "$new_app")"
    ob="$(_vd_sig_bundle_id "$old_app")"
    nt="$(_vd_team_id "$new_app")"
    ot="$(_vd_team_id "$old_app")"
    if [ -z "$nb" ] || [ -z "$ob" ] || [ -z "$nt" ] || [ -z "$ot" ]; then
        _vd_say "identity_unverifiable: cannot verify bundle/team identity for $label; refusing replacement (old bundle=$ob team=$ot; new bundle=$nb team=$nt)"
        return 3
    fi
    if [ "$nb" != "$ob" ] || [ "$nt" != "$ot" ]; then
        _vd_say "Signing identity mismatch for $label; refusing replacement (old bundle=$ob team=$ot; new bundle=$nb team=$nt)"
        return 1
    fi
    return 0
}

# ---- fetch / checksum --------------------------------------------------------

# _web_fetch_to_file <url> <dest> [timeout_s=60] [max_bytes=10 MiB]
# https only (also for redirects), time- and size-bounded, written to
# <dest>.part and moved into place atomically only when non-empty and <= max.
_web_fetch_to_file() {
    local url="$1" dest="$2" timeout_s="${3:-60}" max_bytes="${4:-10485760}" tmp size
    [ -n "$url" ] && [ -n "$dest" ] || return 1
    case "$url" in
        https://?*) ;;
        *) return 1 ;;
    esac
    tmp="${dest}.part"
    /bin/rm -f "$tmp" "$dest"
    if ! _web_curl -fsSL --proto =https --proto-redir =https --max-filesize "$max_bytes" \
            --max-time "$timeout_s" --retry 2 --retry-delay 1 -o "$tmp" "$url"; then
        /bin/rm -f "$tmp"
        return 1
    fi
    if [ ! -s "$tmp" ]; then
        /bin/rm -f "$tmp"
        return 1
    fi
    size="$(/usr/bin/wc -c < "$tmp" | /usr/bin/tr -d ' ')"
    if [ "${size:-0}" -gt "$max_bytes" ]; then
        /bin/rm -f "$tmp"
        return 1
    fi
    /bin/mv "$tmp" "$dest"
}

# https + exact host match (port stripped). No suffix/prefix/userinfo tricks:
# anything between `https://` and the first `/` other than the host itself
# (e.g. `host@evil`, `host.evil.net`) fails the equality.
vendor_direct_host_allowed() {
    local url="$1" host="$2" url_host
    [ -n "$url" ] && [ -n "$host" ] || return 1
    case "$url" in
        https://?*) ;;
        *) return 1 ;;
    esac
    url_host="${url#https://}"
    url_host="${url_host%%/*}"
    url_host="${url_host%%:*}"
    [ "$url_host" = "$host" ]
}

# kind: sha256hex | sha512hex (case-insensitive hex) | sha512b64 (case-sensitive
# base64 of the binary digest). `-`/empty kind or value => nothing to verify
# (0). Unknown kind => refuse (1).
vendor_direct_verify_checksum() {
    local file="$1" kind="$2" val="$3" actual want
    [ -f "$file" ] || return 1
    case "$kind" in
        ''|'-') return 0 ;;
    esac
    case "$val" in
        ''|'-') return 0 ;;
    esac
    case "$kind" in
        sha256hex)
            actual="$(/usr/bin/shasum -a 256 "$file" 2>/dev/null | awk '{print $1}')"
            ;;
        sha512hex)
            actual="$(/usr/bin/shasum -a 512 "$file" 2>/dev/null | awk '{print $1}')"
            ;;
        sha512b64)
            # Streamed in 1 MiB chunks: downloads can be up to 4 GiB.
            python3 -c "
import base64, hashlib, sys
h = hashlib.sha512()
with open(sys.argv[1], 'rb') as f:
    for chunk in iter(lambda: f.read(1 << 20), b''):
        h.update(chunk)
sys.exit(0 if base64.b64encode(h.digest()).decode('ascii') == sys.argv[2] else 1)
" "$file" "$val"
            return $?
            ;;
        *)
            return 1
            ;;
    esac
    [ -n "$actual" ] || return 1
    actual="$(printf '%s' "$actual" | tr 'A-F' 'a-f')"
    want="$(printf '%s' "$val" | tr 'A-F' 'a-f')"
    [ "$actual" = "$want" ]
}

# ---- DMG / archive handling ---------------------------------------------------

# Detach one tracked mount (no -force). On success mark it so the final
# leftover sweep skips it.
_vd_detach_mount() {
    local mnt="$1"
    [ -n "$mnt" ] || return 1
    if "$(_vd_tool hdiutil)" detach "$mnt" -quiet >/dev/null 2>&1 </dev/null; then
        : > "${mnt}.detached" 2>/dev/null || true
        return 0
    fi
    return 1
}

# Detach every mount still recorded in <work>/mounts.list. Returns 1 when one
# could not be detached (the caller then keeps the work dir).
_vd_detach_leftover_mounts() {
    local work="$1" mnt rc=0
    [ -f "$work/mounts.list" ] || return 0
    while IFS= read -r mnt; do
        [ -n "$mnt" ] || continue
        [ -e "${mnt}.detached" ] && continue
        if ! _vd_detach_mount "$mnt"; then
            _vd_say "WARN: mount still active after cleanup: $mnt"
            rc=1
        fi
    done < "$work/mounts.list"
    return $rc
}

_vd_cleanup_work() {
    local work="$1"
    [ -n "$work" ] && [ -d "$work" ] || return 0
    if _vd_detach_leftover_mounts "$work"; then
        /bin/rm -rf "$work" 2>/dev/null || true
    fi
    return 0
}

# _vd_extract_dmg <dmg> <extract_dir> <work>
_vd_extract_dmg() {
    local dl="$1" xdir="$2" work="$3" mnt app ok=1 hd
    hd="$(_vd_tool hdiutil)"
    # stdin is /dev/null for every hdiutil call: a disk image with a licence
    # agreement must fail fast (soft rc 1), never prompt and never be
    # auto-accepted (no `yes |`).
    if ! "$hd" verify "$dl" -quiet >/dev/null 2>&1 </dev/null; then
        _vd_say "ERROR: hdiutil verify failed for the downloaded disk image"
        return 1
    fi
    mnt="$(/usr/bin/mktemp -d "$work/mnt.XXXXXX")" || return 1
    if ! "$hd" attach "$dl" -nobrowse -readonly -mountpoint "$mnt" -quiet >/dev/null 2>&1 </dev/null; then
        _vd_say "ERROR: hdiutil attach failed for the downloaded disk image"
        /bin/rmdir "$mnt" 2>/dev/null || true
        return 1
    fi
    printf '%s\n' "$mnt" >> "$work/mounts.list"
    while IFS= read -r app; do
        [ -n "$app" ] || continue
        if ! "$(_vd_tool ditto)" "$app" "$xdir/$(basename "$app")" 2>/dev/null; then
            _vd_say "ERROR: could not copy $(basename "$app") out of the disk image"
            ok=0
            break
        fi
    done <<EOF_DMG_APPS
$(find "$mnt" -maxdepth 3 -type d -name '*.app' -prune 2>/dev/null)
EOF_DMG_APPS
    _vd_detach_mount "$mnt" || _vd_say "WARN: hdiutil detach failed for $mnt"
    [ "$ok" -eq 1 ]
}

# _vd_extract <archive> <artifact> <extract_dir> <work>
_vd_extract() {
    local dl="$1" artifact="$2" xdir="$3" work="$4"
    case "$artifact" in
        zip)
            "$(_vd_tool ditto)" -x -k "$dl" "$xdir" 2>/dev/null \
                || { _vd_say "ERROR: zip extraction failed"; return 1; }
            ;;
        tar.gz)
            /usr/bin/tar -xzf "$dl" -C "$xdir" 2>/dev/null \
                || { _vd_say "ERROR: tar.gz extraction failed"; return 1; }
            ;;
        dmg)
            _vd_extract_dmg "$dl" "$xdir" "$work" || return 1
            ;;
        *)
            _vd_say "ERROR: unsupported artifact type '$artifact'"
            return 1
            ;;
    esac
    return 0
}

# Resolve `auto` from the downloaded bytes (vendors serve archives under
# neutral names; the old installer sniffed with file(1) too).
_vd_sniff_artifact() {
    local t
    t="$(/usr/bin/file -b "$1" 2>/dev/null)"
    case "$t" in
        *"Zip archive"*|*"Java archive"*) printf 'zip' ;;
        *"gzip compressed"*) printf 'tar.gz' ;;
        *) printf 'dmg' ;;
    esac
}

# ---- the atomic bundle swap -----------------------------------------------------

# copy_verified_app <src_app> <dest_app>
# Verified, atomic, rollback-protected replacement of an INSTALLED bundle.
# <dest_app> is the full path of the installed app (under the apps root, nested
# folders such as <root>/Utilities/X.app are swapped in place), or a bare
# "X.app" label meaning <root>/X.app. The destination must already exist with a
# readable signed identity: this never installs fresh and never skips the
# bundle-id/Team-ID check.
#   0 ok
#   1 refused/failed (original intact unless a CRITICAL line says otherwise)
#   2 deferred (app running or state unknown)
#   3 soft refusal: destination absent or identity unverifiable
copy_verified_app() {
    local src="$1" target="$2"
    local root dest label staging_root backup_root staging backup rejected id_rc

    if _vd_dry_run; then
        _vd_say "[DRY-RUN] skip copy: $target"
        return 0
    fi
    root="$(_vd_apps_root)"
    case "$target" in
        */*) dest="$target" ;;
        *)   dest="$root/$target" ;;
    esac
    case "$dest" in
        */../*|*/..)
            _vd_say "ERROR: refusing a destination with '..': $dest"
            return 1
            ;;
        "$root"/?*) ;;
        *)
            _vd_say "ERROR: destination must be under $root: $dest"
            return 1
            ;;
    esac
    label="$(basename "$dest")"
    case "$label" in
        *.app) ;;
        *)
            _vd_say "ERROR: invalid destination: $dest"
            return 1
            ;;
    esac
    case "$label" in .*) _vd_say "ERROR: invalid destination: $dest"; return 1 ;; esac

    # Never install fresh and never skip the identity check: the installed
    # bundle must exist and have a readable bundle id + Team ID.
    if [ ! -d "$dest" ]; then
        _vd_say "identity_unverifiable: installed app not found at $dest; refusing to install a new copy"
        return 3
    fi
    if [ -z "$(_vd_sig_bundle_id "$dest")" ] || [ -z "$(_vd_team_id "$dest")" ]; then
        _vd_say "identity_unverifiable: cannot read the signed identity (bundle id / Team ID) of the installed $label; refusing replacement"
        return 3
    fi

    # Idle gate #1 -- before anything is staged. Only an explicit `false`
    # proceeds; running OR unknown defers, and nothing is ever quit.
    if ! _vd_replacement_is_idle "$dest"; then
        _vd_say "Application is running or its state is unknown; replacement deferred: $label"
        return 2
    fi

    if ! _vd_spctl_ok "$src"; then
        _vd_say "Gatekeeper rejected the downloaded $label"
        return 1
    fi

    # Staging and backup live beside the destination: every mv stays on one
    # volume (atomic rename). mktemp avoids collisions with stale/concurrent runs.
    staging_root="$(_vd_try /usr/bin/mktemp -d "$root/.ascendo_staging.XXXXXX")"
    if [ -z "$staging_root" ]; then
        _vd_say "ERROR: cannot create a staging directory in $root"
        return 1
    fi
    backup_root="$(_vd_try /usr/bin/mktemp -d "$root/.ascendo_backup.XXXXXX")"
    if [ -z "$backup_root" ]; then
        _vd_say "ERROR: cannot create a backup directory in $root"
        _vd_rm_rf "$staging_root"
        return 1
    fi
    staging="$staging_root/$label"
    backup="$backup_root/$label"

    if ! _vd_try "$(_vd_tool ditto)" "$src" "$staging"; then
        _vd_say "ERROR: could not stage $label"
        _vd_rm_rf "$staging_root" "$backup_root"
        return 1
    fi
    if ! _vd_spctl_ok "$staging"; then
        _vd_say "Staged signature check failed for $label"
        _vd_rm_rf "$staging_root" "$backup_root"
        return 1
    fi
    id_rc=0
    _vd_verify_replacement_identity "$staging" "$dest" "$label" || id_rc=$?
    if [ "$id_rc" -ne 0 ]; then
        _vd_rm_rf "$staging_root" "$backup_root"
        return "$id_rc"
    fi

    # Idle gate #2 -- immediately before the swap: the user may have opened the
    # app while the payload was copied and verified. Ambiguous also defers.
    if ! _vd_replacement_is_idle "$dest"; then
        _vd_say "Application became active; replacement deferred: $label"
        _vd_rm_rf "$staging_root" "$backup_root"
        return 2
    fi

    if ! _vd_try "$(_vd_tool mv)" "$dest" "$backup"; then
        _vd_say "ERROR: could not move the existing $dest to the rollback backup $backup"
        _vd_rm_rf "$staging_root" "$backup_root"
        return 1
    fi
    if ! _vd_try "$(_vd_tool mv)" "$staging" "$dest"; then
        if ! _vd_try "$(_vd_tool mv)" "$backup" "$dest"; then
            _vd_say "CRITICAL: install failed and rollback is at $backup. Restore with: mv \"$backup\" \"$dest\""
            _vd_rm_rf "$staging_root"
            return 1
        fi
        _vd_say "ERROR: could not move the staged $label into place; previous version restored"
        _vd_rm_rf "$staging_root" "$backup_root"
        return 1
    fi

    # A post-swap Gatekeeper failure restores the known previous bundle.
    if ! _vd_spctl_ok "$dest"; then
        _vd_say "Post-install signature check failed for $label; rolling back"
        rejected="$staging_root/$label.rejected"
        if ! _vd_try "$(_vd_tool mv)" "$dest" "$rejected"; then
            if ! _vd_try /bin/rm -rf "$dest"; then
                _vd_say "CRITICAL: could not remove the rejected app $dest; rollback is at $backup. Restore with: mv \"$backup\" \"$dest\""
                return 1
            fi
        fi
        if ! _vd_try "$(_vd_tool mv)" "$backup" "$dest"; then
            _vd_say "CRITICAL: the rejected app is at $rejected; rollback is at $backup. Restore with: mv \"$backup\" \"$dest\""
            return 1
        fi
        _vd_rm_rf "$staging_root" "$backup_root"
        return 1
    fi

    _vd_rm_rf "$staging_root" "$backup_root"
    _vd_try /usr/bin/xattr -dr com.apple.quarantine "$dest" || true
    return 0
}

# ---- the installer ------------------------------------------------------------------

# Steps 4-10 inside a prepared work dir (see vendor_direct_install).
_vd_install_in_work() {
    local installed="$1" url="$2" artifact="$3" ck="$4" cs="$5" expected="$6" work="$7"
    local bid dl xdir candidate found="" matches=0 c_bid cand_ver installed_ver copy_rc=0

    # 4. The installed app must exist, have an identity, and be explicitly idle.
    if [ ! -d "$installed" ]; then
        _vd_say "ERROR: installed app not found: $installed"
        return 1
    fi
    bid="$(_vd_plist_bundle_id "$installed")"
    if [ -z "$bid" ]; then
        _vd_say "ERROR: cannot read the bundle id of $installed"
        return 1
    fi
    if [ "$(ascendo_app_running_state "$bid")" != "false" ]; then
        _vd_say "Application $bid is running or its state could not be determined; install deferred"
        return 4
    fi

    # 5. Bounded https fetch.
    dl="$work/download.bin"
    _vd_say "downloading $url"
    if ! _web_fetch_to_file "$url" "$dl" 1800 4294967296; then
        _vd_say "ERROR: download failed or was refused for $url"
        return 1
    fi

    # 6. Checksum from the same feed entry (when the feed has one).
    if ! vendor_direct_verify_checksum "$dl" "$ck" "$cs"; then
        _vd_say "Checksum mismatch for the downloaded artifact ($ck); refusing to install"
        return 1
    fi

    # 7. Extract (zip / tar.gz / verified read-only DMG).
    [ "$artifact" = "auto" ] && artifact="$(_vd_sniff_artifact "$dl")"
    xdir="$work/x"
    /bin/mkdir -p "$xdir" || return 1
    _vd_extract "$dl" "$artifact" "$xdir" "$work" || return 1

    # 8. Exactly one *.app, and its bundle id must equal the installed app's.
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        c_bid="$(_vd_plist_bundle_id "$candidate")"
        if [ "$c_bid" = "$bid" ]; then
            matches=$((matches + 1))
            found="$candidate"
        fi
    done <<EOF_FIND
$(find "$xdir" -maxdepth 3 -type d -name '*.app' -prune 2>/dev/null)
EOF_FIND
    if [ "$matches" -ne 1 ] || [ -z "$found" ]; then
        _vd_say "ERROR: expected 1 app bundle with id $bid, found $matches"
        return 1
    fi

    # 9. Version pre-check, before /Applications is touched: the bundle must
    # satisfy the feed's promise and be strictly newer than what is installed.
    if [ -n "$expected" ]; then
        cand_ver="$(_web_installed_version "$found")"
        installed_ver="$(_web_installed_version "$installed")"
        case "$(_web_version_cmp "$expected" "$cand_ver")" in
            equal|older) ;;
            *)
                _vd_say "ERROR: downloaded bundle version ${cand_ver:-?} does not satisfy the expected $expected"
                return 1
                ;;
        esac
        if [ "$(_web_version_cmp "$cand_ver" "$installed_ver")" != "newer" ]; then
            _vd_say "ERROR: downloaded bundle version ${cand_ver:-?} is not newer than the installed ${installed_ver:-?}"
            return 1
        fi
    fi

    # 10. Verified atomic swap. 2 = the app was opened meanwhile => deferred.
    # The swap target is the installed path itself (never <root>/<basename>).
    copy_verified_app "$found" "$installed" || copy_rc=$?
    if [ "$copy_rc" -ne 0 ]; then
        _vd_say "ERROR: copy_verified_app failed for $installed (code $copy_rc)"
        [ "$copy_rc" -eq 2 ] && return 4   # deferred
        [ "$copy_rc" -eq 3 ] && return 1   # identity_unverifiable: soft refusal
        return 3
    fi
    return 0
}

# vendor_direct_install <installed_app> <url> <artifact> <ck> <cs> <host> [expected_version]
#   artifact: zip | tar.gz | dmg | auto (sniffed)
#   host:     exact allowed download host, or `-` for "https only, no pin"
#             (legacy callers); an empty host is refused.
vendor_direct_install() {
    local installed="$1" url="$2" artifact="${3:-auto}" ck="${4:--}" cs="${5:--}" host="${6:-}" expected="${7:-}"
    local root work rc=0

    # 1. dry-run / verify-only
    if _vd_dry_run; then
        _vd_say "[DRY-RUN] vendor_direct_install $installed from $url"
        return 2
    fi

    # 2. the installed path must live directly under the Applications root
    root="$(_vd_apps_root)"
    case "$installed" in
        */../*|*/..)
            _vd_say "ERROR: refusing a path with '..': $installed"
            return 1
            ;;
        "$root"/*) ;;
        *)
            _vd_say "ERROR: vendor install requires a path under $root: $installed"
            return 1
            ;;
    esac

    # 3. https + exact host allowlist
    if [ "$host" = "-" ]; then
        case "$url" in
            https://?*) ;;
            *)
                _vd_say "ERROR: only https downloads are allowed: $url"
                return 1
                ;;
        esac
    elif ! vendor_direct_host_allowed "$url" "$host"; then
        _vd_say "host ${host:-?} rejected for $url"
        return 1
    fi

    work="$(/usr/bin/mktemp -d "$(_vd_tmp_root)/vd.XXXXXX")" || return 1
    _vd_install_in_work "$installed" "$url" "$artifact" "$ck" "$cs" "$expected" "$work" || rc=$?
    _vd_cleanup_work "$work"
    return $rc
}

# _web_vendor_install <slug> <url> <artifact> <ck> <cs> <host> <expected_version> <installed_app>
# Spec-B entry point (slug-first, installed path last); same codes as
# vendor_direct_install.
_web_vendor_install() {
    local slug="$1" url="$2" artifact="$3" ck="$4" cs="$5" host="$6" expected="$7" installed="$8"
    local rc=0
    vendor_direct_install "$installed" "$url" "$artifact" "$ck" "$cs" "$host" "$expected" || rc=$?
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
        _vd_say "$slug: vendor install returned $rc"
    fi
    return $rc
}
