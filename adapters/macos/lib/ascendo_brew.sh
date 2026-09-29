#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_brew.sh -- Homebrew helpers for ascendo phase scripts
# =============================================================================
# Sourced by phase scripts:
#     . "$ADAPTER_LIB/ascendo_brew.sh"
#
# Public API:
#     ascendo_brew_prefix                  -- print "$(brew --prefix)" or empty
#     ascendo_brew_version                 -- print first line of `brew --version`
#     ascendo_brew_outdated_json [--greedy]
#                                          -- print `brew outdated --json=v2 [--greedy]`
#     ascendo_brew_parse_outdated <json_file_or_dash> <formula|cask>
#                                          -- emit CSV rows: id,current_version,target_version
#     ascendo_brew_cask_app_name <cask_token>
#                                          -- print app bundle name (e.g. "Slack") or empty
#     ascendo_brew_cask_app_paths <cask_token>
#                                          -- print installed .app path(s) of a cask (one per line)
#     ascendo_brew_cask_app_running <cask_token>
#                                          -- rc 0 when any process runs from the cask's .app
#                                             (read-only probe; NEVER quits or kills anything)
#     ascendo_brew_xcode_license_ok        -- rc 1 only when Xcode.app is selected and its
#                                             licence is not accepted
#     ascendo_brew_xcode_license_gate      -- emit skipped item + XCODE_LICENSE message, rc 1
#                                             (callers: `... || exit 10`); needs ascendo_json.sh
#     ascendo_brew_err_tail <file>         -- last stderr lines, single line, capped
#     ascendo_brew_outdated_or_fail <out>  -- run ascendo_brew_outdated_json into <out>; on real
#                                             failure add BREW_OUTDATED_FAIL with the stderr reason
#     ascendo_brew_exit_code <brew_exit>
#                                          -- translate brew exit to ascendo phase exit
#
# Bash 3.2-safe. Requires `jq` for ascendo_brew_parse_outdated.
# No `declare -A`, no `mapfile`, no `readarray`, no `pipefail` at top-level
# (callers set -o pipefail in their own scope).
# =============================================================================

# Return brew's prefix path, or empty string if brew is not found.
ascendo_brew_prefix() {
    if command -v brew >/dev/null 2>&1; then
        brew --prefix 2>/dev/null || true
    fi
}

# Return the first line of `brew --version`, or empty if brew is not found.
ascendo_brew_version() {
    if command -v brew >/dev/null 2>&1; then
        brew --version 2>/dev/null | head -n1 || true
    fi
}

# Print one "<cask-token> <version>" line per installed cask.
# Return 0 when the inventory is trustworthy (including the empty case),
# 1 when Homebrew could not be queried at all.
# Prefer `brew list --cask --versions`; on the Cask::CaskLoader regression
# fall back to `brew list --cask` + Caskroom/<token>/<newest-dir>.
ascendo_brew_cask_versions() {
    local out names prefix room ver

    command -v brew >/dev/null 2>&1 || return 1

    # stderr discarded: the upstream error is what we are falling back from.
    out="$(brew list --cask --versions 2>/dev/null)" || true
    if [ -n "$out" ]; then
        printf '%s\n' "$out"
        return 0
    fi

    names="$(brew list --cask 2>/dev/null)" || true
    if [ -z "$names" ]; then
        if brew list --cask >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi

    prefix="$(brew --prefix 2>/dev/null)"
    [ -n "$prefix" ] || prefix="/opt/homebrew"

    printf '%s\n' "$names" | while IFS= read -r name; do
        [ -n "$name" ] || continue
        ver=""
        room="$prefix/Caskroom/$name"
        if [ -d "$room" ]; then
            # shellcheck disable=SC2010,SC2012
            ver="$(ls -1t "$room" 2>/dev/null | grep -v '^\.' | head -1)"
        fi
        printf '%s %s\n' "$name" "${ver:-latest}"
    done
    return 0
}

# Same contract as ascendo_brew_cask_versions, for formulae.
ascendo_brew_formula_versions() {
    command -v brew >/dev/null 2>&1 || return 1
    brew list --formula --versions 2>/dev/null
}

# Run `brew outdated --json=v2 [--greedy]` and print the JSON to stdout.
# stderr is NEVER merged into stdout: brew writes progress chatter
# ("==> Downloading Homebrew API data", "✔︎ JSON API ...") to stderr,
# and capturing it with 2>&1 made post-upgrade verification treat that
# chatter as outstanding packages (macOS_updates 2026-08-19 regression).
# rc 1 without an "Error:" stderr line is treated as success (brew's
# "something is outdated" exit); a real error returns non-zero with the
# stderr text forwarded to fd 2.
# If brew is not installed, prints an empty JSON stub and returns 0.
ascendo_brew_outdated_json() {
    if ! command -v brew >/dev/null 2>&1; then
        printf '{"formulae":[],"casks":[]}\n'
        return 0
    fi
    local greedy_flag=""
    if [ "${1:-}" = "--greedy" ]; then
        greedy_flag="--greedy"
    fi
    local err_file rc out
    err_file="$(mktemp "${TMPDIR:-/tmp}/ascendo_brew_outdated.XXXXXX")" || {
        printf '{"formulae":[],"casks":[]}\n'
        return 1
    }
    # brew exits 0 even when there are no outdated packages. Keep stderr
    # off stdout so a later 2>&1 regression cannot poison the JSON.
    # shellcheck disable=SC2086
    out="$(brew outdated --json=v2 ${greedy_flag} 2>"$err_file")"
    rc=$?
    # rc 1 WITHOUT an "Error:" line on stderr is success: `brew outdated`
    # exits 1 when it found outdated items (notably for named queries).
    # Only a real "Error:" line (or any other rc) is a failure, and then the
    # stderr is passed on so the caller can report the actual reason.
    if [ "$rc" -eq 1 ] && ! grep -Eq '^[[:space:]]*Error:' "$err_file" 2>/dev/null; then
        rc=0
    fi
    if [ "$rc" -ne 0 ]; then
        cat "$err_file" >&2
        rm -f "$err_file" 2>/dev/null || true
        printf '{"formulae":[],"casks":[]}\n'
        return "$rc"
    fi
    rm -f "$err_file" 2>/dev/null || true
    # Defensive: drop any progress lines that leaked onto stdout.
    if [ -z "$out" ]; then
        printf '{"formulae":[],"casks":[]}\n'
        return 0
    fi
    printf '%s\n' "$out" | grep -v '^==>' | grep -v '^✔' || printf '{"formulae":[],"casks":[]}\n'
}

# Parse a saved `brew outdated --json=v2` file (or stdin with "-") and emit
# CSV rows of the form: id,current_version,target_version
#
# Arguments:
#   $1  path to JSON file, or "-" to read from stdin
#   $2  bucket: "formula" or "cask"
#
# Handles both cases for the `name` field:
#   - string (typical for formulae): .name
#   - array  (typical for casks):    .name[0]
# Handles both cases for `installed_versions`:
#   - array: .[0]
#   - string (e.g. casks): .
ascendo_brew_parse_outdated() {
    local source="$1"
    local bucket="$2"

    if ! command -v jq >/dev/null 2>&1; then
        echo "ascendo_brew_parse_outdated: jq is required" >&2
        return 2
    fi

    local plural
    case "$bucket" in
        formula) plural="formulae" ;;
        cask)    plural="casks" ;;
        *)
            echo "ascendo_brew_parse_outdated: bucket must be 'formula' or 'cask', got: $bucket" >&2
            return 2
            ;;
    esac

    # jq filter handles both array and string for .name and .installed_versions
    local jq_filter
    # NOTE: using single quotes inside bash heredoc-style — stored in variable
    # to keep the bash script Bash 3.2-safe (no process substitution here).
    jq_filter='
        .'"$plural"'[]
        | {
            id: (if (.name | type == "array") then .name[0] else .name end),
            current: (
                if (.installed_versions | type == "array")
                then (.installed_versions[0] // "unknown")
                else (.installed_versions // "unknown")
                end
            ),
            target: (.current_version // "unknown")
          }
        | "\(.id),\(.current),\(.target)"
    '

    if [ "$source" = "-" ]; then
        jq -r "$jq_filter"
    else
        jq -r "$jq_filter" "$source"
    fi
}

# Map a cask token to its /Applications bundle name (without .app suffix).
# Returns empty string for unknown casks.
# Bash 3.2-safe: uses case statement, NOT associative array.
ascendo_brew_cask_app_name() {
    case "$1" in
        slack)                echo "Slack" ;;
        visual-studio-code)   echo "Visual Studio Code" ;;
        google-chrome)        echo "Google Chrome" ;;
        firefox)              echo "Firefox" ;;
        firefox-developer-edition) echo "Firefox Developer Edition" ;;
        spotify)              echo "Spotify" ;;
        notion)               echo "Notion" ;;
        zoom)                 echo "zoom.us" ;;
        iterm2)               echo "iTerm" ;;
        docker)               echo "Docker" ;;
        rectangle)            echo "Rectangle" ;;
        appcleaner)           echo "AppCleaner" ;;
        brave-browser)        echo "Brave Browser" ;;
        capcut)               echo "CapCut" ;;
        inkscape)             echo "Inkscape" ;;
        lm-studio)            echo "LM Studio" ;;
        megasync)             echo "MEGAsync" ;;
        obsidian)             echo "Obsidian" ;;
        perplexity)           echo "Perplexity" ;;
        protonvpn)            echo "ProtonVPN" ;;
        *)                    echo "" ;;
    esac
}

# Print the installed .app bundle path(s) of a cask, one per line.
# Currently derived from the small token->bundle-name table above (the
# cask-facts module replaces this with the cask's real artifact targets).
# ASCENDO_BREW_APPLICATIONS_DIR overrides /Applications (tests).
# Prints nothing when the token is unknown or the bundle is not on disk.
ascendo_brew_cask_app_paths() {
    local token="$1"
    local apps_dir="${ASCENDO_BREW_APPLICATIONS_DIR:-/Applications}"
    local app
    app="$(ascendo_brew_cask_app_name "$token")"
    [ -n "$app" ] || return 0
    if [ -d "$apps_dir/${app}.app" ]; then
        printf '%s\n' "$apps_dir/${app}.app"
    fi
    return 0
}

# Exit 0 when a process is running from any .app bundle of the cask
# (main binary or a helper under <bundle>/Contents/). Read-only: this
# never quits, signals or kills anything -- an app the user has open is
# left alone (the caller defers the upgrade instead).
ascendo_brew_cask_app_running() {
    local token="$1"
    local _path _esc
    while IFS= read -r _path; do
        [ -n "$_path" ] || continue
        # Regex-escape the path for pgrep -f (extended regex).
        _esc="$(printf '%s' "$_path" | sed 's/[][\.*^$+?(){}|\/]/\\&/g')"
        if pgrep -f "^${_esc}/Contents/" >/dev/null 2>&1; then
            return 0
        fi
    done <<EOF_PATHS
$(ascendo_brew_cask_app_paths "$token")
EOF_PATHS
    return 1
}

# 0 = fine (CLT selected, no Xcode.app, or licence accepted);
# 1 = Xcode.app selected and its licence has not been accepted -- every
# brew command then fails. NEVER accepts the licence (a consent action).
ascendo_brew_xcode_license_ok() {
    local dev_path
    command -v xcode-select >/dev/null 2>&1 || return 0
    dev_path="$(xcode-select -p 2>/dev/null)"
    case "$dev_path" in
        *.app/Contents/Developer*)
            if command -v xcodebuild >/dev/null 2>&1; then
                xcodebuild -license check >/dev/null 2>&1 </dev/null || return 1
            fi
            ;;
    esac
    return 0
}

# Phase-script gate: call right after the brew/jq preconditions and before
# any brew invocation:  ascendo_brew_xcode_license_gate || exit 10
# When the licence is not accepted it records one `skipped` item plus a
# message carrying the XCODE_LICENSE code and the action-required token,
# and returns 1. Requires ascendo_json.sh to be sourced.
ascendo_brew_xcode_license_gate() {
    if ascendo_brew_xcode_license_ok; then
        return 0
    fi
    json_add_item "xcode-license" "" "" "skipped" "brew" ""
    json_add_message "warn" \
        "xcode-license: Xcode licence has not been accepted - Homebrew cannot run. Run: sudo xcodebuild -license accept | $(ascendo_action_required_token xcode_license)" \
        "XCODE_LICENSE"
    return 1
}

# Last few lines of a stderr capture file as ONE line, capped, for messages.
ascendo_brew_err_tail() {
    local f="$1"
    [ -s "$f" ] || { printf '(no error output)'; return 0; }
    tail -n 5 "$f" | tr '\n' ' ' | tr -s '[:space:]' ' ' | cut -c1-500
}

# Run ascendo_brew_outdated_json into <outfile>; on failure record a
# BREW_OUTDATED_FAIL error message that carries brew's real stderr reason
# (not a bare "failed unexpectedly") and return non-zero.
# Extra args are passed through to ascendo_brew_outdated_json.
ascendo_brew_outdated_or_fail() {
    local out="$1"
    shift
    local err rc=0
    err="$(mktemp "${TMPDIR:-/tmp}/ascendo_brew_outdated_err.XXXXXX")" || err=""
    if [ -n "$err" ]; then
        ascendo_brew_outdated_json "$@" >"$out" 2>"$err" || rc=$?
    else
        ascendo_brew_outdated_json "$@" >"$out" 2>/dev/null || rc=$?
    fi
    if [ "$rc" -ne 0 ]; then
        json_add_message "error" \
            "brew outdated --json=v2 failed (exit ${rc}): $( [ -n "$err" ] && ascendo_brew_err_tail "$err" || printf 'unknown')" \
            "BREW_OUTDATED_FAIL"
    fi
    [ -z "$err" ] || rm -f "$err" 2>/dev/null || true
    return "$rc"
}

# Exit 0 if version $1 is strictly greater than version $2.
# Used to skip Homebrew casks that would downgrade a vendor-ahead app
# (Brave 151.x vs cask 1.93.x on 2026-08-25).
ascendo_brew_version_gt() {
    /usr/bin/python3 - "$1" "$2" <<'PY'
import re
import sys


def version_key(value):
    match = re.search(r"\d+(?:\.\d+)*", value or "")
    if not match:
        return None
    numbers = [int(part) for part in match.group(0).split(".")]
    numbers = (numbers + [0] * 12)[:12]
    suffix = (value[match.end():] or "").lower().split("+", 1)[0]
    prerelease = suffix.lstrip("-._")
    prerelease_rank = 4
    for marker, rank in (("dev", 0), ("alpha", 1), ("a", 1), ("beta", 2), ("b", 2), ("rc", 3)):
        if prerelease.startswith(marker):
            prerelease_rank = rank
            break
    suffix_numbers = [int(part) for part in re.findall(r"\d+", suffix)]
    suffix_numbers = (suffix_numbers + [0] * 4)[:4]
    return tuple(numbers + [prerelease_rank] + suffix_numbers)


left = version_key(sys.argv[1])
right = version_key(sys.argv[2])
if left is None or right is None:
    sys.exit(1)
sys.exit(0 if left > right else 1)
PY
}

# Print the installed .app short version for a cask token, or empty.
# ASCENDO_BREW_APPLICATIONS_DIR overrides /Applications (tests).
ascendo_brew_cask_installed_app_version() {
    local token="$1"
    local apps_dir="${ASCENDO_BREW_APPLICATIONS_DIR:-/Applications}"
    local app path plist
    app="$(ascendo_brew_cask_app_name "$token")"
    [ -n "$app" ] || return 1
    path="$apps_dir/${app}.app"
    plist="$path/Contents/Info.plist"
    [ -f "$plist" ] || return 1
    /usr/bin/python3 - "$plist" <<'PY'
import plistlib, sys
try:
    with open(sys.argv[1], "rb") as f:
        data = plistlib.load(f)
except Exception:
    sys.exit(1)
print(data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or "")
PY
}

# Exit 0 when upgrading this cask to $2 would replace a newer installed app.
ascendo_brew_cask_would_downgrade() {
    local token="$1"
    local target="$2"
    local installed
    [ -n "$token" ] && [ -n "$target" ] || return 1
    installed="$(ascendo_brew_cask_installed_app_version "$token")" || return 1
    [ -n "$installed" ] || return 1
    ascendo_brew_version_gt "$installed" "$target"
}

# Translate a brew exit code into an ascendo phase exit code.
#   0  -> 0  (success)
#   *  -> 30 (apply-fail-unknown, per docs/agents/contract.md)
ascendo_brew_exit_code() {
    local code="${1:-0}"
    case "$code" in
        0) echo 0 ;;
        *) echo 30 ;;
    esac
}
