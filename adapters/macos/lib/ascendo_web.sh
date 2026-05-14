#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_web.sh -- shared helpers for WebManager
# =============================================================================
# Sourced by adapters/macos/scripts/web/{check,plan,apply,verify,cleanup}.sh
# and lib/handlers/*.sh. Bash 3.2 compatible (no `local -A`, no mapfile,
# no readarray). Helpers do NOT call `set -e` themselves -- they're sourced
# and inherit shell options from their caller.
#
# Reserved exit codes (web-namespace):
#   20 download failure
#   21 hdiutil attach failed (couldn't determine mount point)
#   22 mount contained no .app bundle
#   23 spctl Gatekeeper assessment rejected the bundle
#   24 cp -R into /Applications failed even with sudo -A escalation
#   29 generic web-handler failure (reserved; not used here)
#
# Public helpers (all `_web_*` to signal private-ish, package-internal):
#   _web_ensure_cache_dir
#   _web_installed_version <app_path>
#   _version_gt <a> <b>
#   _web_is_running <bundle_id>
#   _web_extract_sparkle_latest_version          (reads stdin)
#   _web_extract_sparkle_enclosure_url           (reads stdin)
#   _web_download <url> <dest>
#   _web_verify_signature <app_path>
#   _web_install_dmg <slug> <dmg_url> <app_path>
#   _web_run_apply_cli <slug> <argv_json>
# =============================================================================
# shellcheck shell=bash

# ============================================================
# Cache directory
# ============================================================

if [ -z "${ASCENDO_WEB_CACHE_DIR:-}" ]; then
    ASCENDO_WEB_CACHE_DIR="${HOME}/Library/Caches/Ascendo/web"
fi
export ASCENDO_WEB_CACHE_DIR

_web_ensure_cache_dir() {
    mkdir -p "$ASCENDO_WEB_CACHE_DIR" 2>/dev/null || return 1
}

# ============================================================
# Version probes
# ============================================================

# _web_installed_version <app_path>
# Echoes CFBundleShortVersionString from the bundle's Info.plist, or empty
# when the bundle is missing / lacks the key. Exit 0 in both cases (missing
# is a valid "not installed" signal, not an error).
_web_installed_version() {
    local app_path="$1"
    if [ ! -d "$app_path" ]; then
        return 0
    fi
    /usr/bin/defaults read "$app_path/Contents/Info" \
        CFBundleShortVersionString 2>/dev/null || true
}

# _web_resolve_bundle_path <bundle_id> [fallback_app_path]
# Returns the on-disk path for a bundle id, regardless of how the app's
# display name on disk differs from what `web_apps.toml` declares.
#
# Why this exists: registry uses canonical names (Codex Desktop, Docker
# Desktop, Zoom, Ledger Live) but users have those installed under shorter
# / vendor-specific names (Codex.app, Docker.app, zoom.us.app, Ledger
# Wallet.app). The default "/Applications/${DISPLAY_NAME}.app" guess in
# apply.sh broke for every such case, marking installed apps as
# "skipped: not_installed_or_path_mismatch".
#
# Strategy (fastest → most thorough):
#   1. mdfind kMDItemCFBundleIdentifier — covers anything Spotlight has
#      indexed. Fastest path; works for ~70-80% of bundles.
#   2. system_profiler -json (cached for the process via ASCENDO_WEB_SP_CACHE)
#      — catches bundles Spotlight didn't index (recent installs, Spotlight
#      disabled for a path, /Library/Application Support nested apps).
#   3. Caller-supplied fallback path (the legacy /Applications/<display>.app
#      guess) — last resort, only if it actually exists.
_web_resolve_bundle_path() {
    local bundle_id="$1" fallback="$2" path=""
    if [ -z "$bundle_id" ]; then
        [ -n "$fallback" ] && [ -d "$fallback" ] && printf '%s' "$fallback"
        return 0
    fi
    # 1. mdfind — filter to .app bundles, skip test artifacts under /tmp.
    path=$(/usr/bin/mdfind -onlyin /Applications \
                "kMDItemCFBundleIdentifier == '${bundle_id}'" 2>/dev/null \
           | /usr/bin/grep -E '\.app$' | /usr/bin/head -n 1)
    if [ -z "$path" ]; then
        path=$(/usr/bin/mdfind \
                    "kMDItemCFBundleIdentifier == '${bundle_id}'" 2>/dev/null \
               | /usr/bin/grep -E '\.app$' | /usr/bin/grep -v '^/tmp/' \
               | /usr/bin/head -n 1)
    fi
    if [ -n "$path" ] && [ -d "$path" ]; then
        printf '%s' "$path"
        return 0
    fi
    # 2. system_profiler fallback — slow on cold start (~1.5 s) but cached
    # per process. Catches Docker.app, Codex.app, zoom.us.app and similar
    # bundles that Spotlight has missed on this host.
    if [ -z "${ASCENDO_WEB_SP_CACHE:-}" ] || [ ! -f "$ASCENDO_WEB_SP_CACHE" ]; then
        ASCENDO_WEB_SP_CACHE="${TMPDIR:-/tmp}/ascendo-web-sp-$$.json"
        /usr/sbin/system_profiler -json -detailLevel mini \
            SPApplicationsDataType > "$ASCENDO_WEB_SP_CACHE" 2>/dev/null || true
        export ASCENDO_WEB_SP_CACHE
    fi
    if [ -f "$ASCENDO_WEB_SP_CACHE" ] && [ -s "$ASCENDO_WEB_SP_CACHE" ]; then
        path=$(BUNDLE_ID="$bundle_id" python3 -c '
import json, os, sys
bid = os.environ["BUNDLE_ID"]
try:
    data = json.load(open(os.environ["ASCENDO_WEB_SP_CACHE"]))
except Exception:
    sys.exit(0)
for app in data.get("SPApplicationsDataType", []):
    p = app.get("path", "")
    if not p or not p.endswith(".app"):
        continue
    try:
        plist = os.path.join(p, "Contents", "Info.plist")
        # PlistBuddy via subprocess would re-fork; instead use plistlib.
        import plistlib
        with open(plist, "rb") as f:
            info = plistlib.load(f)
        if info.get("CFBundleIdentifier", "") == bid:
            print(p)
            break
    except Exception:
        continue
' 2>/dev/null)
        if [ -n "$path" ] && [ -d "$path" ]; then
            printf '%s' "$path"
            return 0
        fi
    fi
    # 3. Fallback to caller-supplied path if it exists on disk.
    if [ -n "$fallback" ] && [ -d "$fallback" ]; then
        printf '%s' "$fallback"
    fi
    return 0
}

# _version_gt <a> <b>
# Exit 0 iff a > b (strict). Compares dotted version strings via sort -V.
# Equal versions return 1 (not strictly greater).
_version_gt() {
    local a="$1" b="$2"
    [ "$a" = "$b" ] && return 1
    local higher
    higher=$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n 1)
    [ "$higher" = "$a" ]
}

# _normalize_version <v>
# Echoes a normalized form of a version string suitable for loose equality
# comparison. Two common quirks vendor feeds add that don't reflect a real
# version change:
#   1. Parenthetical build suffix:  "7.0.0 (77593)" → "7.0.0.77593"
#      (Zoom posts the same release as "7.0.0 (77593)" in its DMG bundle
#      but as "7.0.0.77593" in the version manifest. Pure-string compare
#      says they differ; semantically they're the same build.)
#   2. Trailing zero segments:      "125.0" → "125.0.0.0"
#      (Google Drive's Omaha manifest answers "125.0.0.0" but the bundle's
#      CFBundleShortVersionString is "125.0". Semver-style equivalence.)
#   3. Whitespace + Build metadata:
#      "1.2.3+build.4" → "1.2.3+build.4"  (semver build metadata kept)
#      "1.2.3 beta"    → "1.2.3-beta"     (whitespace → hyphen)
#
# Bash 3.2 compatible. No regex extensions beyond POSIX BRE.
_normalize_version() {
    local v="$1"
    # 1. " (NNN)" → ".NNN"  — Zoom-style parenthetical builds.
    v=$(printf '%s' "$v" | sed -E 's/ *\(([0-9A-Za-z._]+)\)/.\1/g')
    # 2. Whitespace squashed to single hyphen (for tags like "1.0 beta").
    v=$(printf '%s' "$v" | sed -E 's/[[:space:]]+/-/g')
    printf '%s' "$v"
}

# _version_eq_loose <a> <b>
# Exit 0 iff a == b after normalization (parenthetical build, trailing
# zero segments, whitespace tag separator). Used by check + apply to
# avoid the classic "installed=125.0 candidate=125.0.0.0 → outdated"
# false positive.
_version_eq_loose() {
    local a b
    a=$(_normalize_version "$1")
    b=$(_normalize_version "$2")
    [ "$a" = "$b" ] && return 0
    # Trailing-zero equivalence: pad both to the longer dot-count and
    # compare segment-by-segment. "125.0" vs "125.0.0.0" both pad to
    # the same canonical "125.0.0.0".
    local a_segs b_segs max i
    a_segs=$(printf '%s' "$a" | awk -F. '{print NF}')
    b_segs=$(printf '%s' "$b" | awk -F. '{print NF}')
    max=$a_segs
    [ "$b_segs" -gt "$max" ] && max=$b_segs
    while [ "$a_segs" -lt "$max" ]; do
        a="${a}.0"
        a_segs=$((a_segs + 1))
    done
    while [ "$b_segs" -lt "$max" ]; do
        b="${b}.0"
        b_segs=$((b_segs + 1))
    done
    [ "$a" = "$b" ]
}

# _is_prerelease <version>
# Exit 0 iff the version contains a pre-release marker (alpha, beta,
# rc, dev, nightly, pre, or a "b<digit>" / "a<digit>" / "rc<digit>"
# suffix). Used to detect when our slow-path candidate is a beta build
# that shouldn't replace an installed stable release.
_is_prerelease() {
    local v="$1"
    case "$v" in
        *[Aa]lpha*|*[Bb]eta*|*[Pp]re*|*[Nn]ightly*|*[Dd]ev[!a-zA-Z]*|*[Dd]ev) return 0 ;;
        *[Rr][Cc][0-9]*|*-rc[0-9]*|*-rc.[0-9]*) return 0 ;;
        *[0-9][Bb][0-9]*|*[0-9][Aa][0-9]*) return 0 ;;
    esac
    return 1
}

# _should_skip_upgrade <installed> <candidate>
# Returns 0 (true: skip the apply) when:
#   1. installed == candidate (already up-to-date)
#   2. installed > candidate by version sort (downgrade)
#   3. candidate is a pre-release marker and installed is NOT
#      (don't replace a stable build with a beta — common when our
#      brew livecheck feed lags or is tracking the wrong channel)
# Returns 1 (false: proceed with apply) otherwise.
_should_skip_upgrade() {
    local installed="$1" cand="$2"
    [ -z "$installed" ] && return 1     # no install info → let apply decide
    [ -z "$cand" ] && return 1          # no candidate → caller handles probe_unavailable
    [ "$installed" = "$cand" ] && return 0
    # Loose equality catches vendor manifest quirks (parenthetical builds,
    # trailing zero segments) before they trigger an unnecessary reinstall.
    if _version_eq_loose "$installed" "$cand"; then
        return 0
    fi
    if _version_gt "$installed" "$cand"; then
        return 0
    fi
    if _is_prerelease "$cand" && ! _is_prerelease "$installed"; then
        return 0
    fi
    return 1
}

# ============================================================
# Parallel probe helper (Sesja 47 — speed up web phase)
# ============================================================
#
# The check + apply phases each iterate ~30+ web apps and spend ~3s
# per item on serial HTTP probes. With 8-way parallelism this collapses
# to ~10-15s total instead of ~100s.
#
# Contract (callable from any script that has sourced this file + the
# handler libs):
#
#   _web_probe_parallel <indices_file> <results_dir> [parallelism]
#
# Where <indices_file> contains one numeric idx per line. Each idx must
# have these companion files in <results_dir>:
#
#     <idx>.slug         — registry slug (e.g. "brave")
#     <idx>.handler      — handler type (e.g. "release_feed")
#     <idx>.cfg.json     — per-app CFG JSON
#
# We use newline-separated indices instead of TSV rows because BSD
# xargs collapses embedded tabs to spaces when substituting {} —
# losing the field boundaries.
#
# After return, <results_dir>/<idx>.txt contains the probe output
# (latest version string, or empty / `__GH_RATE_LIMITED__`).
#
# Defaults: parallelism = $ASCENDO_WEB_PARALLEL or 8.
# Bash 3.2 compatible. Uses xargs -P (BSD + GNU support it).
# ============================================================

_web_probe_parallel() {
    local indices_file="$1"
    local results_dir="$2"
    local parallelism="${3:-${ASCENDO_WEB_PARALLEL:-8}}"
    [ -z "$indices_file" ] || [ ! -s "$indices_file" ] && return 0
    mkdir -p "$results_dir" 2>/dev/null

    # Generate a self-contained probe script that xargs re-invokes per
    # work item. We can't pass bash functions to xargs-spawned subshells;
    # the only portable surface is filesystem + argv. The script sources
    # the handler libs once per worker process then dispatches.
    #
    # All per-item state (slug, handler, cfg) comes from files keyed by
    # idx, so the only argv needed is the idx itself.
    local probe_script="$results_dir/_probe_one.sh"
    local adapter_lib="${ASCENDO_WEB_ADAPTER_LIB:-${ADAPTER_LIB:-}}"
    if [ -z "$adapter_lib" ]; then
        adapter_lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    fi
    cat >"$probe_script" <<'PROBE_EOF'
#!/usr/bin/env bash
# Per-work-item probe runner spawned by xargs. argv: <adapter_lib> <results_dir> <idx>
set -o pipefail
ADAPTER_LIB="$1"
RESULTS_DIR="$2"
idx="$3"
[ -z "$idx" ] && exit 0
slug=$(cat "$RESULTS_DIR/${idx}.slug" 2>/dev/null)
handler=$(cat "$RESULTS_DIR/${idx}.handler" 2>/dev/null)
cfg_file="$RESULTS_DIR/${idx}.cfg.json"
if [ ! -f "$cfg_file" ] || [ -z "$handler" ]; then
    : > "$RESULTS_DIR/${idx}.txt"
    exit 0
fi
# shellcheck source=/dev/null
. "$ADAPTER_LIB/ascendo_web.sh"
for h in sparkle github_dmg keystone squirrel builtin msupdate docker release_feed omaha; do
    # shellcheck source=/dev/null
    . "$ADAPTER_LIB/handlers/${h}.sh"
done
cfg=$(cat "$cfg_file")
latest=""
case "$handler" in
    sparkle)      latest=$(sparkle_check "$slug" "$cfg" 2>/dev/null || true) ;;
    github_dmg)   latest=$(github_dmg_check "$slug" "$cfg" 2>/dev/null || true) ;;
    keystone)     latest=$(keystone_check "$slug" "$cfg" 2>/dev/null || true) ;;
    msupdate)     latest=$(msupdate_check "$slug" "$cfg" 2>/dev/null || true) ;;
    docker)       latest=$(docker_check "$slug" "$cfg" 2>/dev/null || true) ;;
    release_feed) latest=$(release_feed_check "$slug" "$cfg" 2>/dev/null || true) ;;
    omaha)        latest=$(omaha_check "$slug" "$cfg" 2>/dev/null || true) ;;
    squirrel|builtin) latest="" ;;
esac
printf '%s' "$latest" | tr -d '[:space:]' > "$RESULTS_DIR/${idx}.txt"
PROBE_EOF
    chmod +x "$probe_script" 2>/dev/null

    # xargs -P parallelism. -I{} replaces {} with each line (idx).
    # newline-separated input → no tab-collapse problem.
    /usr/bin/xargs -P "$parallelism" -I{} \
        bash "$probe_script" "$adapter_lib" "$results_dir" "{}" \
        < "$indices_file" 2>/dev/null
}

# _web_read_probe_result <results_dir> <idx>
# Echo the probed candidate version (or empty) for work-item idx.
_web_read_probe_result() {
    local results_dir="$1" idx="$2"
    local f="$results_dir/${idx}.txt"
    [ -f "$f" ] && cat "$f" || true
}

# ============================================================
# Process probes
# ============================================================

# _web_is_running <bundle_id>
# Exit 0 iff a running app with this bundle id is registered with
# LaunchServices. Uses `lsappinfo` (the canonical macOS query tool for
# running GUI apps) so we don't need to inspect ps output (which would
# match any caller that happens to carry the bundle id as a literal in
# its command line, e.g. the helper-test fixture).
_web_is_running() {
    local bundle_id="$1"
    if ! command -v lsappinfo >/dev/null 2>&1; then
        return 1
    fi
    # Match `bundleID="<id>"` exactly (lsappinfo's per-app dump format) so
    # we don't false-positive on substring overlaps.
    local matches
    matches=$(lsappinfo list 2>/dev/null \
        | /usr/bin/grep -c "bundleID=\"$bundle_id\"")
    if [ "${matches:-0}" -gt 0 ]; then
        return 0
    fi
    return 1
}

# ============================================================
# Sparkle appcast parsing
# ============================================================

# _web_extract_sparkle_latest_version
# Reads stdin (appcast XML), echoes the FIRST sparkle:shortVersionString.
# Sparkle convention puts the latest item first.
#
# Handles BOTH valid Sparkle conventions:
#   1. ATTRIBUTE on <enclosure>: Brave/Opera-style
#        <enclosure ... sparkle:shortVersionString="X" />
#   2. CHILD ELEMENT of <item>:  ChatGPT Atlas-style
#        <sparkle:shortVersionString>X</sparkle:shortVersionString>
#
# Strategy: try element form first (text content between tags), fall back
# to attribute form. Element form is more common in modern Sparkle 2.x
# appcasts and unambiguous; attribute form is a Sparkle 1.x shortcut.
#
# Wire pattern: read stdin into a bash local first, then pass to python
# via env var. A naked heredoc into `python3 <<'PY'` would bind stdin to
# the python source, not the upstream pipe — same gotcha as github_dmg's
# heredoc/pipe interaction documented in the M5.6 handoff.
_web_extract_sparkle_latest_version() {
    local _xml
    _xml="$(cat)"
    ASCENDO_WEB_XML="$_xml" /usr/bin/python3 <<'PY'
import os
import re
import sys

xml = os.environ.get("ASCENDO_WEB_XML", "")

# Sparkle convention: <item> entries can appear in any order; the
# CONSUMER must select the highest version. AppCleaner publishes
# 3.4 first, 3.6 second, 3.6.8 last — naive "first match" gives 3.4.
# Brave goes newest-first. We can't rely on order; sort + pick highest.

versions = re.findall(
    r'<sparkle:shortVersionString>([^<]+)</sparkle:shortVersionString>', xml)
if not versions:
    versions = re.findall(r'sparkle:shortVersionString="([^"]+)"', xml)

if not versions:
    sys.exit(0)


def _ver_key(s: str):
    """Convert '3.6.8' -> [(3,''),(6,''),(8,'')]. Trailing alpha
    components compare low so '3.6.8b' < '3.6.8' as expected."""
    parts = re.split(r'[.\-_]', s.strip())
    out = []
    for p in parts:
        m = re.match(r'^(\d+)', p)
        if m:
            out.append((int(m.group(1)), p[m.end():] or ''))
        else:
            out.append((-1, p))
    return out


versions.sort(key=_ver_key)
print(versions[-1].strip())
PY
}

# _web_extract_sparkle_enclosure_url
# Reads stdin, echoes the FIRST <enclosure url="..."> URL.
_web_extract_sparkle_enclosure_url() {
    /usr/bin/grep -oE 'url="https?://[^"]+"' \
        | /usr/bin/head -n 1 \
        | /usr/bin/sed -E 's/url="([^"]+)"/\1/'
}

# ============================================================
# Download + verify + install (DMG)
# ============================================================

# _web_download <url> <dest>
# Curl with progress streamed via _stream_emit when available (the helper
# from ascendo_json.sh is loaded by Run Center context; bare scripts won't
# have it, hence the command -v guard).
_web_download() {
    local url="$1" dest="$2"
    _web_ensure_cache_dir || return 1
    if command -v _stream_emit >/dev/null 2>&1; then
        _stream_emit info "downloading $url"
    fi
    /usr/bin/curl -fsSL --max-time 300 -o "$dest" "$url"
}

# _web_verify_signature <app_path>
# Exit 0 iff Gatekeeper accepts the bundle (notarised + signed). Stdout +
# stderr captured to caller for diagnostics.
_web_verify_signature() {
    local app_path="$1"
    /usr/sbin/spctl --assess --type execute --verbose "$app_path" 2>&1
}

# _web_install_dmg <slug> <dmg_url> <app_path>
# Full pipeline: download -> mount -> spctl -> cp -R -> xattr strip -> unmount.
# Re-tries cp -R with sudo -A on EACCES (askpass-cached elevation, the same
# pattern used by mas + softwareupdate apply scripts).
_web_install_dmg() {
    local slug="$1" url="$2" app_path="$3"
    local dmg="$ASCENDO_WEB_CACHE_DIR/${slug}.dmg"
    local mount_point=""
    local rc=0

    _web_download "$url" "$dmg" || return 20

    mount_point=$(/usr/bin/hdiutil attach -nobrowse -plist "$dmg" 2>/dev/null \
        | /usr/bin/grep -oE '/Volumes/[^<]+' \
        | /usr/bin/head -n 1)
    [ -z "$mount_point" ] && return 21

    local src_app
    src_app=$(/bin/ls -d "$mount_point"/*.app 2>/dev/null | /usr/bin/head -n 1)
    if [ -z "$src_app" ]; then
        /usr/bin/hdiutil detach -force "$mount_point" >/dev/null 2>&1 || true
        return 22
    fi

    if ! _web_verify_signature "$src_app" >/dev/null 2>&1; then
        /usr/bin/hdiutil detach -force "$mount_point" >/dev/null 2>&1 || true
        return 23
    fi

    if [ -z "$app_path" ]; then
        app_path="/Applications/$(/usr/bin/basename "$src_app")"
    fi

    local target_dir
    target_dir="$(dirname "$app_path")"
    if ! /bin/cp -R "$src_app" "$target_dir/" 2>/dev/null; then
        # _ascendo_sudo (ascendo_json.sh) picks -A vs plain sudo by env so
        # the TTY-PAM / Touch-ID-only flow works alongside the SPA-askpass
        # flow. apply.sh has already called _ascendo_sudo_warm so creds
        # are hot.
        if ! _ascendo_sudo /bin/cp -R "$src_app" "$target_dir/"; then
            rc=24
        fi
    fi

    /usr/bin/xattr -dr com.apple.quarantine "$app_path" 2>/dev/null || true

    /usr/bin/hdiutil detach -force "$mount_point" >/dev/null 2>&1 || true
    return $rc
}

# _web_run_apply_cli <slug> <argv_json>
# Eval JSON argv (list of strings) with timeout. Passes returncode through.
# Used by handlers that delegate to a CLI subcommand instead of DMG flow
# (e.g. Claude Code installs via curl|bash; Codex via npm; etc.).
_web_run_apply_cli() {
    local slug="$1" argv_json="$2"
    local timeout="${ASCENDO_WEB_APPLY_CLI_TIMEOUT:-60}"
    local cmd
    cmd=$(printf '%s' "$argv_json" \
        | /usr/bin/python3 -c '
import json, shlex, sys
argv = json.load(sys.stdin)
print(" ".join(shlex.quote(a) for a in argv))
')

    # gtimeout (coreutils) is the safe path: kills only the spawned PID
    # tree, never escalates into other processes.
    if command -v gtimeout >/dev/null 2>&1; then
        /usr/bin/env gtimeout "$timeout" /bin/sh -c "$cmd"
        return $?
    fi

    # Fallback: spawn + watcher. SAFETY GUARD added 2026-05-06 after a
    # real-Mac incident where Brave was passed `--check-for-update`
    # (an unsupported flag), and the 60s kill-watcher killed the
    # singleton process — crashing Brave's running window.
    #
    # The fix has TWO layers: (a) we already removed apply_cli_argv that
    # invoked an app's main binary directly, and (b) here we use
    # `kill -TERM` (graceful) before -KILL, AND we bail out if the PID
    # has terminated naturally before the timeout.
    /bin/sh -c "$cmd" &
    local pid=$!
    (
        # Watcher: wait up to $timeout, then graceful TERM; if still
        # alive after 5s, SIGKILL. If pid is already gone, do nothing.
        local elapsed=0
        while [ $elapsed -lt "$timeout" ]; do
            if ! kill -0 "$pid" 2>/dev/null; then
                exit 0
            fi
            sleep 1
            elapsed=$((elapsed + 1))
        done
        kill -TERM "$pid" 2>/dev/null || exit 0
        sleep 5
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
    ) &
    local watcher=$!
    wait "$pid"
    local rc=$?
    # Watcher cleanup so we don't leave a sleep loop running.
    kill "$watcher" 2>/dev/null || true
    return $rc
}
