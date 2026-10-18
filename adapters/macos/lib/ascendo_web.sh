#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_web.sh -- shared helpers for WebManager
# =============================================================================
# Sourced by adapters/macos/scripts/web/{check,plan,apply,verify,cleanup}.sh
# and lib/handlers/*.sh. Bash 3.2 compatible (no `local -A`, no mapfile,
# no readarray). Helpers do NOT call `set -e` themselves -- they're sourced
# and inherit shell options from their caller.
#
# Reserved exit codes (web-namespace, handler level):
#   25-28, 32 probe / feed errors (see the handlers)
#   29 generic web-handler failure (reserved; not used here)
# Installer codes (_web_install_dmg / vendor_direct_install, replace the old
# 20-24 download/attach/no-app/spctl/cp set):
#   0 ok  1 refused or download/verify failure (soft)  2 dry-run
#   3 swap failure (hard)  4 deferred: app running or running state unknown
#
# Public helpers (all `_web_*` to signal private-ish, package-internal):
#   _web_ensure_cache_dir
#   _web_installed_version <app_path>
#   _version_gt <a> <b>
#   _web_is_running <bundle_id>                  (explicit-true wrapper; gates use
#                                                 ascendo_app_running_state)
#   _web_extract_sparkle_latest_version          (reads stdin)
#   _web_extract_sparkle_enclosure_url           (reads stdin)
#   _web_version_cmp <candidate> <installed>     (newer|equal|older|unknown)
#   _web_vendor_lookup <slug> <cfg_json>         (7-field vendor line)
#   _web_vendor_check <slug> <cfg_json>          (feed version = the probe candidate)
#   _web_cfg_has_vendor_feed / _web_cfg_vendor_field / _web_feed_backed
#   _web_vendor_direct_enabled / _web_safe_mode / _web_stage_wait / _web_sleep
#   _web_open_hidden / _web_vendor_direct_step / _web_fallback_launch
#   _web_outcome_reset / _web_outcome <code> [message]   (handler -> apply.sh)
#   _web_fetch_feed <https-url>
#   _web_download <url> <dest>
#   _web_verify_signature <app_path>
#   _web_install_dmg <slug> <archive_url> <app_path> [ck cs]
#   (ascendo_vendor_direct.sh) _web_fetch_to_file, vendor_direct_install,
#   _web_vendor_install, copy_verified_app
#   _web_run_apply_cli <slug> <argv_json>
# =============================================================================
# shellcheck shell=bash

[ -n "${_ASCENDO_PROC_LOADED:-}" ] || . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ascendo_proc.sh"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ascendo_vendor_direct.sh"

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
for h in sparkle github_dmg keystone squirrel builtin msupdate docker release_feed omaha chromium_updater; do
    # shellcheck source=/dev/null
    . "$ADAPTER_LIB/handlers/${h}.sh"
done
cfg=$(cat "$cfg_file")
latest=""
# Handlers may leave an explanation for the phase script ("<code>\t<text>") here.
rm -f "$RESULTS_DIR/${idx}.note" 2>/dev/null
export ASCENDO_WEB_PROBE_NOTE_FILE="$RESULTS_DIR/${idx}.note"
case "$handler" in
    sparkle)      latest=$(sparkle_check "$slug" "$cfg" 2>/dev/null || true) ;;
    github_dmg)   latest=$(github_dmg_check "$slug" "$cfg" 2>/dev/null || true) ;;
    keystone)     latest=$(keystone_check "$slug" "$cfg" 2>/dev/null || true) ;;
    msupdate)     latest=$(msupdate_check "$slug" "$cfg" 2>/dev/null || true) ;;
    docker)       latest=$(docker_check "$slug" "$cfg" 2>/dev/null || true) ;;
    release_feed) latest=$(release_feed_check "$slug" "$cfg" 2>/dev/null || true) ;;
    omaha)        latest=$(omaha_check "$slug" "$cfg" 2>/dev/null || true) ;;
    chromium_updater) latest=$(chromium_updater_check "$slug" "$cfg" 2>/dev/null || true) ;;
    squirrel)     latest=$(squirrel_check "$slug" "$cfg" 2>/dev/null || true) ;;
    builtin)      latest="" ;;
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

# _web_read_probe_note <results_dir> <idx>
# Echoes the handler's probe note ("<code>\t<text>") or nothing.
_web_read_probe_note() {
    local f="$1/${2}.note"
    [ -f "$f" ] && /usr/bin/head -n 1 "$f" || true
}

# _web_emit_probe_note <slug> <note>
# Emits the note as an info message with the machine-readable status code
# (current_vendor / current_verified, ...). No-op for an empty note.
_web_emit_probe_note() {
    local slug="$1" note="$2" code text
    [ -n "$note" ] || return 0
    code=${note%%$'\t'*}
    text=${note#*$'\t'}
    [ -n "$code" ] && [ "$code" != "$note" ] || return 0
    json_add_message "info" "${slug}: ${text} | $(ascendo_status_token "$code")" "$code"
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
# Exit 0 iff the app is EXPLICITLY known to be running. Thin wrapper over the
# shared tri-state probe (ascendo_proc.sh): use it for messages only. Any gate
# that copies, swaps or upgrades a bundle must test
# `[ "$(ascendo_app_running_state "$bid")" = false ]` instead -- `unknown`
# (lsappinfo missing/failing/empty) is not idle and must defer (fail closed).
_web_is_running() {
    [ "$(ascendo_app_running_state "$1")" = "true" ]
}

# _web_is_frontmost <bundle_id>
# rc 0 = the app is the frontmost application, 1 = positively NOT frontmost,
# 2 = cannot tell. Compares the app's ASN with `lsappinfo front`; the numeric
# part of the ASN (ASN:0x0-0x...) is the only thing compared. Callers that
# would quit an app treat 2 like 0 ("when in doubt, do not quit").
_web_is_frontmost() {
    local bin a b
    bin="${ASCENDO_LSAPPINFO_BIN:-}"
    if [ -z "$bin" ]; then bin="$(command -v lsappinfo 2>/dev/null)" || bin=""; fi
    [ -n "$bin" ] && [ -x "$bin" ] || return 2
    a="$(ascendo_run_with_timeout 10 "$bin" find "bundleid=$1" 2>/dev/null \
        | /usr/bin/grep -oE 'ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+' | /usr/bin/head -n 1)"
    b="$(ascendo_run_with_timeout 10 "$bin" front 2>/dev/null \
        | /usr/bin/grep -oE 'ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+' | /usr/bin/head -n 1)"
    [ -n "$a" ] && [ -n "$b" ] || return 2
    [ "$a" = "$b" ] && return 0
    return 1
}

# _web_quit_gracefully <bundle_id>
# Ask the app to quit (AppleScript `quit`, never a signal) and wait for it to
# be explicitly gone: rc 0 = not running (already, or after the request),
# rc 1 = still running / state unknown when ASCENDO_WEB_QUIT_WAIT (default 60,
# clamp 0-300) seconds are up. Only ever called for an app recorded in the run's
# toolkit_launched.txt.
_web_quit_gracefully() {
    local bid="$1" wait elapsed=0
    case "$bid" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
    [ "$(ascendo_app_running_state "$bid")" = "true" ] || return 0
    ascendo_run_with_timeout 20 osascript -e "tell application id \"$bid\" to quit" >/dev/null 2>&1
    wait="$(_web_bounded_int "${ASCENDO_WEB_QUIT_WAIT:-60}" 60 300)"
    while :; do
        [ "$(ascendo_app_running_state "$bid")" = "false" ] && return 0
        [ "$elapsed" -ge "$wait" ] && return 1
        _web_sleep 2
        elapsed=$((elapsed + 2))
    done
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

# _web_cfg_has_vendor_feed <cfg_json>
# Exit 0 iff the app config carries an [app.vendor_feed] table.
_web_cfg_has_vendor_feed() {
    ASCENDO_WEB_CFG="$1" _web_feed_py vendor-cfg >/dev/null 2>&1
}

# _web_cfg_get <cfg_json> <key>
# Echoes one top-level scalar of the app config (empty when absent / null).
_web_cfg_get() {
    ASCENDO_WEB_CFG="$1" _web_feed_py cfg-field "$2" 2>/dev/null
}

# _web_cfg_vendor_field <cfg_json> <field>
# Echoes one [app.vendor_feed] field (booleans as true|false); rc 1 when the
# app has no vendor_feed table or no such field.
_web_cfg_vendor_field() {
    ASCENDO_WEB_CFG="$1" _web_feed_py vendor-field "$2" 2>/dev/null
}

# _web_feed_backed <handler> [cfg_json]
# Exit 0 when the handler's candidate comes from a vendor feed whose
# relation to the installed version must be classified three-way
# (newer/equal/older/unknown) instead of "skip vs proceed".
# sparkle always; squirrel / docker only when the app config carries an
# [app.vendor_feed] table (a feedless squirrel app stays a blind trigger).
_web_feed_backed() {
    case "$1" in
        sparkle) return 0 ;;
        squirrel|docker) [ -n "${2:-}" ] && _web_cfg_has_vendor_feed "$2" ;;
        *) return 1 ;;
    esac
}

# _web_feed_stale_message <slug> <installed> <vendor>
_web_feed_stale_message() {
    printf '%s: vendor feed %s is older than installed %s | ASCENDO-STATUS code=feed_stale' \
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
# Vendor-truth building blocks (squirrel / docker handlers, Spec F + G)
# ============================================================
#
# Handler -> apply.sh protocol. The feed-backed handlers (squirrel_apply,
# docker_apply with an [app.vendor_feed]) run in the SAME shell as apply.sh, so
# they report WHAT happened through two globals and WHETHER it is a failure
# through the return code:
#
#   ASCENDO_WEB_OUTCOME      updated | triggered | current | feed_stale |
#                            unknown_version | needs_restart | rollout_hold |
#                            behind | error_soft | launch_failed |
#                            install_error | probe_unavailable
#   ASCENDO_WEB_OUTCOME_MSG  one human line (no secrets, no newlines)
#   rc  0 updated/triggered/current/feed_stale/unknown_version
#       4 needs_restart (app running / state unknown / installer deferred)
#       95 not applied but not a failure (behind, rollout_hold, error_soft,
#          launch_failed) -- apply.sh reports `skipped`
#       3 hard install failure (install_error) -- apply.sh reports `failed`
#       25 probe_unavailable (vendor feed unreachable / not configured)
# A launch is NEVER followed by a quit: Ascendo does not track launches, so it
# cannot know it owns the process (Global Constraint 4).

ASCENDO_WEB_OUTCOME=""
ASCENDO_WEB_OUTCOME_MSG=""

_web_outcome_reset() {
    ASCENDO_WEB_OUTCOME=""
    ASCENDO_WEB_OUTCOME_MSG=""
}

# _web_outcome <code> [message]
_web_outcome() {
    ASCENDO_WEB_OUTCOME="$1"
    ASCENDO_WEB_OUTCOME_MSG="${2:-}"
}

# _web_vendor_check <slug> <cfg_json>
# The probe candidate of a feed-backed handler: the vendor feed's (normalised)
# version. Empty (rc 0) when the app has no vendor_feed table or the feed is
# unreachable / unparseable -- the phase scripts then report probe_unavailable.
_web_vendor_check() {
    local slug="$1" cfg="$2" line
    _web_cfg_has_vendor_feed "$cfg" || return 0
    line=$(_web_vendor_lookup "$slug" "$cfg" 2>/dev/null) || return 0
    printf '%s\n' "${line%%|*}"
}

# ASCENDO_WEB_VENDOR_DIRECT=0 disables the vendor-direct install step only.
_web_vendor_direct_enabled() {
    [ "${ASCENDO_WEB_VENDOR_DIRECT:-1}" != "0" ]
}

# ASCENDO_SAFE_MODE=true never launches an app GUI (Global Constraint 4).
_web_safe_mode() {
    [ "${ASCENDO_SAFE_MODE:-false}" = "true" ]
}

# _web_bounded_int <value> <default> <max>
# Plain non-negative integer clamped to 0..max; anything else (empty, sign,
# fraction, text) -> default. Leading zeros are decimal ("007" -> 7).
_web_bounded_int() {
    local w="$1" def="$2" max="$3"
    case "$w" in
        ''|*[!0-9]*) printf '%s' "$def"; return 0 ;;
    esac
    w="${w#"${w%%[!0]*}"}"
    [ -n "$w" ] || w=0
    if [ "${#w}" -gt 4 ] || [ "$w" -gt "$max" ]; then
        printf '%s' "$max"
        return 0
    fi
    printf '%s' "$w"
}

# _web_stage_wait
# Native-updater wait in seconds: ASCENDO_WEB_STAGE_WAIT, default 90, clamped to
# 0-600; anything that is not a plain non-negative integer -> 90.
_web_stage_wait() {
    _web_bounded_int "${ASCENDO_WEB_STAGE_WAIT:-90}" 90 600
}

# _web_sleep <seconds>  -- ASCENDO_WEB_SLEEP_BIN is the test seam (no real waits).
_web_sleep() {
    "${ASCENDO_WEB_SLEEP_BIN:-/bin/sleep}" "$1"
}

# _web_record_launch <bundle_id>
# Remember that ASCENDO started this app (it was explicitly idle before), so the
# verify phase can quit it again once the settle window (ASCENDO_WEB_STAGE_WAIT)
# is over. One line per app: "<bundle id><TAB><launch epoch>" (the epoch is what
# the settle window is measured from). No-op without ASCENDO_WEB_LAUNCH_TRACK_FILE
# or for a malformed id; ids are de-duplicated. The `builtin` handler never
# records: it opens the app FOR the user (Help -> Check for Updates), so it must
# stay open.
# Callers must only record launches of apps whose running state was an explicit
# `false` beforehand -- `unknown` counts as running and is never recorded.
_web_record_launch() {
    local bid="${1:-}" f="${ASCENDO_WEB_LAUNCH_TRACK_FILE:-}"
    [ -n "$f" ] || return 0
    case "$bid" in ''|*[!A-Za-z0-9._-]*) return 0 ;; esac
    mkdir -p "$(dirname "$f")" 2>/dev/null || return 0
    /usr/bin/grep -q -- "^${bid}	" "$f" 2>/dev/null && return 0
    printf '%s\t%s\n' "$bid" "${ASCENDO_WEB_NOW_EPOCH:-$(/bin/date +%s)}" >> "$f"
}

# _web_open_tracked <bundle_id> <command...>
# Run a launching command (`/usr/bin/env open ...`); if it succeeds AND the app
# was explicitly idle just before, record the launch. Returns the command's rc.
_web_open_tracked() {
    local bid="$1" was rc=0
    shift
    was="$(ascendo_app_running_state "$bid")"
    "$@" || rc=$?
    [ "$rc" -eq 0 ] && [ "$was" = "false" ] && _web_record_launch "$bid"
    return "$rc"
}

# _web_open_hidden <app_path> [bundle_id]
# Hidden launch (`open -gjF`, no Dock bounce, no focus). Deliberately no
# foreground `open -a` fallback: a vendor-truth launch must never raise a window.
# With a bundle id the caller asserts the app was explicitly idle (it just
# probed it) and a successful launch is recorded for the verify-phase quit.
_web_open_hidden() {
    local app="$1" bid="${2:-}"
    if /usr/bin/env open -gjF "$app" 2>/dev/null \
        || /usr/bin/env open -gj "$app" 2>/dev/null; then
        [ -n "$bid" ] && _web_record_launch "$bid"
        return 0
    fi
    return 1
}

# _web_vendor_direct_step <slug> <app_path> <installed> <R> <URL> <ART> <CK> <CS> <HOST>
# Verified vendor-direct install of feed release R (Spec B installer). Sets the
# outcome and returns the handler rc for every installer result EXCEPT the soft
# refusal / download failure (rc 1), which returns 1 with NO outcome so the
# caller can choose its own fallback.
_web_vendor_direct_step() {
    local slug="$1" app="$2" inst="$3" R="$4" url="$5" art="$6" ck="$7" cs="$8" host="$9"
    local vrc=0 post rel
    _web_vendor_install "$slug" "$url" "$art" "$ck" "$cs" "$host" "$R" "$app" || vrc=$?
    case "$vrc" in
        0)
            post=$(_web_installed_version "$app")
            rel=$(_web_version_cmp "$R" "$post")
            case "$rel" in
                equal|older)
                    _web_outcome updated "installed ${post} from the verified vendor artifact"
                    return 0 ;;
                *)
                    _web_outcome behind "installed ${inst} < vendor ${R}; after the vendor install the app reports ${post:-no version}"
                    return 95 ;;
            esac
            ;;
        4)
            _web_outcome needs_restart "update ${inst} -> ${R} pending - quit the app (it is running or its state could not be determined) and re-run apply"
            return 4 ;;
        3)
            _web_outcome install_error "vendor install of ${R} failed (swap/identity); the previous app was kept or restored"
            return 3 ;;
        *)
            return 1 ;;
    esac
}

# _web_fallback_launch <app_path> <bundle_id>
# ONE hidden launch as the native-updater recovery path after a soft
# vendor-direct failure. Never in safe mode, never unless the running probe
# answers an explicit `false`, never followed by a wait or a quit.
# rc 0 launched; 1 not allowed; 2 `open` failed. A short reason is left in
# _WEB_LAUNCH_NOTE.
_WEB_LAUNCH_NOTE=""
_web_fallback_launch() {
    local app="$1" bid="$2" state
    _WEB_LAUNCH_NOTE=""
    if _web_safe_mode; then
        _WEB_LAUNCH_NOTE="native updater not launched (safe mode; set ASCENDO_WEB_ALLOW_GUI=1 to allow a hidden launch)"
        return 1
    fi
    state="$(ascendo_app_running_state "$bid")"
    if [ "$state" != "false" ]; then
        _WEB_LAUNCH_NOTE="native updater not launched (the app is running or its state could not be determined)"
        return 1
    fi
    if ! _web_open_hidden "$app" "$bid"; then
        _WEB_LAUNCH_NOTE="hidden launch failed"
        return 2
    fi
    return 0
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
# Download + verify + install (zip / tar.gz / dmg)
# ============================================================
#
# The pipeline lives in lib/ascendo_vendor_direct.sh (sourced at the top):
# https + host allowlist, bounded fetch, checksum, verified read-only DMG
# mount, bundle-id / Team-ID identity, atomic backup-swap with rollback, and
# deferral (rc 4) for any app that is running or whose state is unknown.

# _web_download <url> <dest>
# Bounded https fetch (<= 4 GiB, 1800 s). Progress line via _stream_emit when
# the Run Center helper is loaded (bare scripts won't have it).
_web_download() {
    local url="$1" dest="$2"
    _web_ensure_cache_dir || return 1
    if command -v _stream_emit >/dev/null 2>&1; then
        _stream_emit "downloading $url"
    fi
    _web_fetch_to_file "$url" "$dest" 1800 4294967296
}

# _web_verify_signature <app_path>
# Exit 0 iff Gatekeeper accepts the bundle (notarised + signed). Stdout +
# stderr captured to caller for diagnostics.
_web_verify_signature() {
    local app_path="$1"
    "$(_vd_tool spctl)" --assess --type execute --verbose "$app_path" 2>&1
}

# _web_install_dmg <slug> <archive_url> <app_path> [checksum_kind checksum]
# Legacy entry for the sparkle / github_dmg / release_feed handlers: the
# archive kind is sniffed (zip / tar.gz / dmg) and the download host is not
# pinned (https only). Everything else -- checksum when given, exactly one
# app with the INSTALLED app's bundle id, spctl on source and staging,
# bundle-id + Team-ID equality, atomic swap with automatic rollback, no touch
# of a running/unknown-state app -- is the shared hardened pipeline.
#
# The installed bundle comes from <app_path>, or (when that is empty or not on
# disk) from ASCENDO_WEB_INSTALLED_APP_PATH, which scripts/web/apply.sh exports
# per app after resolving the bundle by its CFBundleIdentifier.
#
# Return codes: 0 ok, 1 refused/download/verify failure (soft), 3 swap failure
# (hard), 4 deferred (running / state unknown => the caller reports
# needs_restart). See ascendo_vendor_direct.sh.
_web_install_dmg() {
    local slug="$1" url="$2" app_path="${3:-}" ck="${4:--}" cs="${5:--}"
    if [ -z "$app_path" ] || [ ! -d "$app_path" ]; then
        if [ -n "${ASCENDO_WEB_INSTALLED_APP_PATH:-}" ]; then
            app_path="$ASCENDO_WEB_INSTALLED_APP_PATH"
        fi
    fi
    _web_vendor_install "$slug" "$url" auto "$ck" "$cs" "-" "" "$app_path"
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
