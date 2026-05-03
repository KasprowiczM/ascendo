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
#     ascendo_brew_kill_cask_apps <cask_token>
#                                          -- graceful osascript quit + force pkill after 5s
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

# Run `brew outdated --json=v2 [--greedy]` and print the JSON to stdout.
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
    # brew exits 0 even when there are no outdated packages
    brew outdated --json=v2 ${greedy_flag} 2>/dev/null || printf '{"formulae":[],"casks":[]}\n'
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
        spotify)              echo "Spotify" ;;
        notion)               echo "Notion" ;;
        zoom)                 echo "zoom.us" ;;
        iterm2)               echo "iTerm" ;;
        docker)               echo "Docker" ;;
        rectangle)            echo "Rectangle" ;;
        *)                    echo "" ;;
    esac
}

# Gracefully quit a cask's app via osascript, then force-kill via pkill if
# the app is still running after 5 seconds.
# Returns 0 on success (app quit or was not running), 1 if force-kill failed.
ascendo_brew_kill_cask_apps() {
    local cask="$1"
    local app
    app="$(ascendo_brew_cask_app_name "$cask")"

    # No app mapping -> nothing to kill
    if [ -z "$app" ]; then
        return 0
    fi

    # Not running -> nothing to do
    if ! pgrep -x "$app" >/dev/null 2>&1; then
        return 0
    fi

    # Graceful quit via osascript
    osascript -e "tell application \"$app\" to quit" >/dev/null 2>&1 || true

    # Wait up to 5 seconds for graceful exit
    local i=0
    while [ $i -lt 5 ]; do
        if ! pgrep -x "$app" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done

    # Force kill via pkill
    pkill -f "/Applications/${app}.app/" >/dev/null 2>&1 || true
    sleep 1

    if pgrep -x "$app" >/dev/null 2>&1; then
        echo "ascendo_brew_kill_cask_apps: failed to kill '$app'" >&2
        return 1
    fi
    return 0
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
