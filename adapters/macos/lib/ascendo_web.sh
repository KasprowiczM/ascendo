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
#   _web_version_cmp <candidate> <installed>     (newer|equal|older|unknown)
#   _web_vendor_lookup <slug> <cfg_json>         (7-field vendor line)
#   _web_fetch_feed <https-url>
#   _web_download <url> <dest>
#   _web_verify_signature <app_path>
#   _web_install_dmg <slug> <dmg_url> <app_path>
#   _web_run_apply_cli <slug> <argv_json>
# =============================================================================
# shellcheck shell=bash

[ -n "${_ASCENDO_PROC_LOADED:-}" ] || . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ascendo_proc.sh"

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
# Exit 0 iff a > b (strict). Compares dotted version strings via Python utils.
# Equal versions return 1 (not strictly greater).
_version_gt() {
    local a="$1" b="$2"
    [ "$a" = "$b" ] && return 1
    # W11: Python-based version comparison to handle PEP-440 instead of sort -V
    python3 -c "import sys; from ascendo.utils.version import version_gt; sys.exit(0 if version_gt(sys.argv[1], sys.argv[2]) else 1)" "$a" "$b" 2>/dev/null
    return $?
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
# Vendor truth: feeds, Sparkle parsing, three-way compare (Spec A/D/E)
# ============================================================
#
# All parsing lives in lib/vendor_feeds.py (byte-identical upstream copy);
# lib/web_feed.py is the Ascendo glue (arm64/x86 Sparkle filter, 7-field
# vendor line, newer|equal|older|unknown). Bash only moves bytes.

_ASCENDO_WEB_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# _web_feed_py <web_feed.py args...>  -- stdin/stdout pass-through.
_web_feed_py() {
    PYTHONPATH="$_ASCENDO_WEB_LIB_DIR${PYTHONPATH:+:$PYTHONPATH}" \
        python3 "$_ASCENDO_WEB_LIB_DIR/web_feed.py" "$@"
}

# _web_curl <curl args...>  -- ASCENDO_WEB_CURL_BIN is the test hook.
_web_curl() {
    "${ASCENDO_WEB_CURL_BIN:-/usr/bin/curl}" "$@"
}

# _web_host_os_version
# Host macOS version for minimumSystemVersion / MinimumOsVersion filtering.
# ASCENDO_WEB_HOST_OS_VERSION overrides (tests must never depend on sw_vers).
_web_host_os_version() {
    if [ -n "${ASCENDO_WEB_HOST_OS_VERSION:-}" ]; then
        printf '%s' "$ASCENDO_WEB_HOST_OS_VERSION"
    else
        /usr/bin/sw_vers -productVersion 2>/dev/null || true
    fi
}

# _web_version_cmp <candidate> <installed>
# Echoes newer|equal|older|unknown. Unparseable input on either side is
# `unknown` -- never a string-order guess, never "newer".
_web_version_cmp() {
    _web_feed_py cmp "$1" "$2" 2>/dev/null || printf 'unknown\n'
}

# _web_feed_backed <handler> <cfg_json>
# Exit 0 when the handler's candidate comes from a vendor feed whose
# relation to the installed version must be classified three-way
# (newer/equal/older/unknown) instead of "skip vs proceed".
# Today: sparkle only. squirrel/docker with [app.vendor_feed] join this in
# the vendor-truth task once their *_check returns the feed version.
_web_feed_backed() {
    [ "$1" = "sparkle" ]
}

# _web_feed_stale_message <slug> <installed> <vendor>
_web_feed_stale_message() {
    printf '%s: feed_stale (vendor feed %s < installed %s) - version not verified | ASCENDO-STATUS code=feed_stale' \
        "$1" "$3" "$2"
}

# _web_unknown_version_message <slug> <installed> <candidate>
_web_unknown_version_message() {
    printf '%s: unknown_version (installed %s vs vendor %s cannot be compared) - not treated as up to date' \
        "$1" "${2:-?}" "${3:-?}"
}

# _web_fetch_feed <https-url>
# Echoes the body (<= 5 MiB). https only (also for redirects); bounded time
# and size. Non-zero rc = unreachable / refused (callers treat as unknown).
_web_fetch_feed() {
    local url="$1" tmp rc=0
    case "$url" in
        https://?*) ;;
        *) return 1 ;;
    esac
    tmp=$(/usr/bin/mktemp "${TMPDIR:-/tmp}/ascendo-feed.XXXXXX") || return 1
    _web_curl -fsSL --proto =https --proto-redir =https --max-time 15 --retry 2 --retry-delay 1 \
        --max-filesize 5242880 -o "$tmp" "$url" 2>/dev/null || rc=$?
    if [ "$rc" -eq 0 ]; then
        /usr/bin/head -c 5242880 "$tmp"
    fi
    /bin/rm -f "$tmp"
    return "$rc"
}

# _web_vendor_lookup <slug> <cfg_json>
# Evaluate the app's [app.vendor_feed] table. Echoes ONE line:
#   version|url|checksum_kind|checksum|artifact|host|policy
# ('-' for an absent field; policy = eligible|rollout_hold, always LAST).
# Non-zero rc: no vendor_feed table, or the feed is unreachable/unparseable.
# Test hook: ASCENDO_WEB_VENDOR_FEED_DIR=<dir> reads <dir>/<slug>.feed
# instead of fetching (curl --proto =https would reject file://).
_web_vendor_lookup() {
    local slug="$1" cfg="$2" cfg_out feed_url feed_json body
    cfg_out=$(ASCENDO_WEB_CFG="$cfg" _web_feed_py vendor-cfg 2>/dev/null) || return 1
    feed_url=${cfg_out%%$'\n'*}
    feed_json=${cfg_out#*$'\n'}
    [ -n "$feed_url" ] && [ -n "$feed_json" ] || return 1
    if [ -n "${ASCENDO_WEB_VENDOR_FEED_DIR:-}" ]; then
        [ -f "$ASCENDO_WEB_VENDOR_FEED_DIR/$slug.feed" ] || return 1
        body=$(/bin/cat "$ASCENDO_WEB_VENDOR_FEED_DIR/$slug.feed") || return 1
    else
        body=$(_web_fetch_feed "$feed_url") || return 1
    fi
    printf '%s' "$body" \
        | ASCENDO_WEB_VENDOR_FEED_JSON="$feed_json" _web_feed_py vendor --os-version "$(_web_host_os_version)"
}

# ============================================================
# Sparkle appcast parsing
# ============================================================

# _web_extract_sparkle_latest_version   (appcast XML on stdin)
# Newest qualifying item's version (channel {none,stable}, minimumSystemVersion
# <= host, shortVersionString -> sparkle:version fallback, max by version key,
# prerelease < final) among items built for THIS Mac's architecture. Empty
# output = no candidate (unparseable XML is never guessed at).
_web_extract_sparkle_latest_version() {
    _web_feed_py sparkle --field version --os-version "$(_web_host_os_version)"
}

# _web_extract_sparkle_enclosure_url    (appcast XML on stdin)
# Enclosure URL of that same item (spaces -> %20).
_web_extract_sparkle_enclosure_url() {
    _web_feed_py sparkle --field url --os-version "$(_web_host_os_version)"
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

# _web_install_dmg <slug> <archive_url> <app_path>
# Full pipeline: download -> mount/extract -> spctl -> remove-old + cp -R
# -> xattr strip -> unmount. Re-tries cp -R with sudo -A on EACCES
# (askpass-cached elevation, the same pattern used by mas + softwareupdate
# apply scripts).
#
# Sesja 73: now ALSO handles .zip archives (Codex / opencode publish a
# ZIP in their Sparkle appcast, not a DMG; previously hdiutil silently
# failed with exit 21 and no diagnostic). Format detected via
# ``/usr/bin/file -b`` so vendors who serve the archive with the wrong
# extension still work.
_web_install_dmg() {
    local slug="$1" url="$2" app_path="$3"
    local archive="$ASCENDO_WEB_CACHE_DIR/${slug}.archive"
    local mount_point=""
    local extract_dir=""
    local rc=0

    _web_download "$url" "$archive" || return 20

    local archive_type
    archive_type=$(/usr/bin/file -b "$archive" 2>/dev/null)
    local is_zip="false"
    case "$archive_type" in
        *"Zip archive"*|*"Java archive"*)
            is_zip="true"
            ;;
    esac
    case "$url" in
        *.zip|*.ZIP) is_zip="true" ;;
    esac

    local src_app
    if [ "$is_zip" = "true" ]; then
        extract_dir="$ASCENDO_WEB_CACHE_DIR/${slug}.extract"
        /bin/rm -rf "$extract_dir" 2>/dev/null || true
        /bin/mkdir -p "$extract_dir"
        # `ditto -x -k` is Apple's canonical ZIP extractor — preserves
        # resource forks + extended attributes, unlike `unzip(1)`.
        if ! /usr/bin/ditto -x -k "$archive" "$extract_dir" 2>/dev/null; then
            printf 'ZIP extract failed for %s\n' "$slug" >&2
            return 21
        fi
        # The ZIP may contain the .app at the top level or inside a single
        # parent directory — search up to two levels deep.
        src_app=$(/usr/bin/find "$extract_dir" -maxdepth 2 -name '*.app' -type d 2>/dev/null | /usr/bin/head -n 1)
    else
        # Pipe 'yes' to auto-accept any EULAs (e.g. Google Chrome DMG) which
        # otherwise cause hdiutil to block indefinitely waiting for stdin.
        mount_point=$(yes | /usr/bin/hdiutil attach -nobrowse -plist "$archive" 2>/dev/null \
            | /usr/bin/grep -oE '/Volumes/[^<]+' \
            | /usr/bin/head -n 1)
        if [ -z "$mount_point" ]; then
            printf 'hdiutil attach failed for %s (archive: %s)\n' "$slug" "$archive_type" >&2
            return 21
        fi
        src_app=$(/bin/ls -d "$mount_point"/*.app 2>/dev/null | /usr/bin/head -n 1)
    fi

    if [ -z "$src_app" ]; then
        [ -n "$mount_point" ] && /usr/bin/hdiutil detach -force "$mount_point" >/dev/null 2>&1 || true
        [ -n "$extract_dir" ] && /bin/rm -rf "$extract_dir" 2>/dev/null || true
        printf 'no .app bundle found inside archive for %s\n' "$slug" >&2
        return 22
    fi

    if ! _web_verify_signature "$src_app" >/dev/null 2>&1; then
        [ -n "$mount_point" ] && /usr/bin/hdiutil detach -force "$mount_point" >/dev/null 2>&1 || true
        [ -n "$extract_dir" ] && /bin/rm -rf "$extract_dir" 2>/dev/null || true
        printf 'spctl signature check rejected %s\n' "$slug" >&2
        return 23
    fi

    if [ -z "$app_path" ]; then
        app_path="/Applications/$(/usr/bin/basename "$src_app")"
    fi

    local target_dir
    target_dir="$(dirname "$app_path")"

    # Sesja 73: explicit remove-then-copy. Plain ``cp -R src dst/`` leaves
    # stale files behind when the new bundle has fewer items than the old
    # one, and worse: when a running app's bundle is locked the cp can
    # report exit 0 while NOT actually replacing the binary (Ledger Live /
    # Warp silent-refusal pattern). Removing first forces a real bundle
    # swap and surfaces any permission / lock failure as a hard error.
    if [ -e "$app_path" ]; then
        if ! /bin/rm -rf "$app_path" 2>/dev/null; then
            if ! _ascendo_sudo /bin/rm -rf "$app_path"; then
                printf 'failed to remove existing bundle at %s (is the app running?)\n' "$app_path" >&2
                rc=24
            fi
        fi
    fi
    if [ $rc -eq 0 ]; then
        if ! /bin/cp -R "$src_app" "$target_dir/" 2>/dev/null; then
            # _ascendo_sudo (ascendo_json.sh) picks -A vs plain sudo by env so
            # the TTY-PAM / Touch-ID-only flow works alongside the SPA-askpass
            # flow. apply.sh has already called _ascendo_sudo_warm so creds
            # are hot.
            if ! _ascendo_sudo /bin/cp -R "$src_app" "$target_dir/"; then
                printf 'cp -R failed for %s -> %s/\n' "$src_app" "$target_dir" >&2
                rc=24
            fi
        fi
    fi

    /usr/bin/xattr -dr com.apple.quarantine "$app_path" 2>/dev/null || true

    [ -n "$mount_point" ] && /usr/bin/hdiutil detach -force "$mount_point" >/dev/null 2>&1 || true
    [ -n "$extract_dir" ] && /bin/rm -rf "$extract_dir" 2>/dev/null || true
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

    # Shared bounded runner (ascendo_proc.sh): the CLI runs in its own process
    # group; on the deadline only THAT group gets TERM then KILL (never other
    # processes -- e.g. a user's already-running app window). A TERM-ignoring
    # CLI can no longer hang the phase. Returns 124 on timeout.
    ascendo_run_with_timeout "$timeout" /bin/sh -c "$cmd"
    return $?
}
