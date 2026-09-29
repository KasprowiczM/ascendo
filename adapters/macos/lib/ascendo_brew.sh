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
#     ascendo_brew_outdated_json [--greedy] [greedy_token ...]
#                                          -- print `brew outdated --json=v2`; with tokens also
#                                             `--cask --greedy-auto-updates <tokens>`, merged
#                                             and de-duplicated by token
#     ascendo_brew_greedy_tokens           -- brew-managed auto-updating casks to query greedily
#                                             (config/brew_greedy_casks.txt ∩ installed casks)
#     ascendo_brew_parse_outdated <json_file_or_dash> <formula|cask>
#                                          -- emit CSV rows: id,current_version,target_version
#     ascendo_brew_load_cask_facts <token...>
#                                          -- one batched `brew info --json=v2 --cask` -> facts
#                                             (targets, quit ids, sudo flag, recorded version)
#                                             + orphan set, via brew_casks.py
#     ascendo_brew_prepare_cask_facts <outdated_json> [all]
#                                          -- load facts for the outdated casks (and, with
#                                             `all`, every installed cask)
#     ascendo_brew_cask_app_name <cask_token>
#                                          -- LAST-RESORT table: bundle name (e.g. "Slack") or empty
#     ascendo_brew_cask_app_paths <cask_token>
#                                          -- installed .app path(s) of a cask, one per line, from
#                                             the cask's real artifact targets (string or {target:},
#                                             pkg-cask uninstall.delete hints); table only when
#                                             no facts are available
#     ascendo_brew_cask_app_running <cask_token>
#                                          -- rc 0 when any process runs from one of those bundles
#                                             or a `quit:` bundle id is running (read-only probe;
#                                             NEVER quits or kills anything)
#     ascendo_brew_cask_running_state_known <token>
#                                          -- rc 0 when facts (or the table) let us tell whether the
#                                             app is running; else apply must not upgrade it
#     ascendo_brew_cask_disposition <token> <candidate>
#                                          -- print "<proceed|downgrade|orphan|scheme_unknown>|<detail>"
#                                             (recorded-version + bundle-relation downgrade guard)
#     ascendo_brew_cask_is_orphan <token>  -- rc 0: app artifact(s) declared but none on disk
#     ascendo_brew_cask_needs_admin <token>-- rc 0: pkg/installer/launchctl/kext cask (needs sudo)
#     ascendo_brew_admin_available         -- rc 0 when SUDO_ASKPASS is an executable file
#     ascendo_brew_emit_cask_guard_skip <disposition> <id> <cur> <cand> <detail>
#                                          -- record a downgrade/orphan skip; rc 0 = handled
#     ascendo_brew_xcode_license_ok        -- rc 1 only when Xcode.app is selected and its
#                                             licence is not accepted
#     ascendo_brew_xcode_license_gate      -- emit XCODE_LICENSE message (no item), rc 1
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

# Directory of this library (brew_casks.py lives next to it).
_ASCENDO_BREW_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ -n "${_ASCENDO_PROC_LOADED:-}" ] || . "$_ASCENDO_BREW_LIB_DIR/ascendo_proc.sh"

# Seconds each `brew info` (facts) call may take before it is killed, so N
# sequential per-token fallbacks cannot hang a phase. ASCENDO_BREW_INFO_TIMEOUT overrides.
_ascendo_brew_info() {
    ascendo_run_with_timeout "${ASCENDO_BREW_INFO_TIMEOUT:-60}" brew info --json=v2 --cask "$@" 2>/dev/null
}

# Run the pure cask-facts module (system python 3.9 is enough).
_ascendo_brew_casks_py() {
    local _py=/usr/bin/python3
    [ -x "$_py" ] || _py=python3
    "$_py" "$_ASCENDO_BREW_LIB_DIR/brew_casks.py" "$@"
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
# With greedy tokens, additionally run
#   brew outdated --json=v2 --cask --greedy-auto-updates <tokens>
# (the casks the toolkit manages that self-update / are `version :latest`,
# which plain `brew outdated` never lists) and print the union, de-duplicated
# by cask name.
# stderr is NEVER merged into stdout: brew writes progress chatter
# ("==> Downloading Homebrew API data", "✔︎ JSON API ...") to stderr,
# and capturing it with 2>&1 made post-upgrade verification treat that
# chatter as outstanding packages (macOS_updates 2026-08-19 regression).
# For EACH query rc 1 without an "Error:" stderr line is success (brew's
# "something is outdated" exit, notably for named queries); a real error
# returns non-zero with the stderr text forwarded to fd 2 and the empty
# JSON stub on stdout.
# If brew is not installed, prints an empty JSON stub and returns 0.
ascendo_brew_outdated_json() {
    if ! command -v brew >/dev/null 2>&1; then
        printf '{"formulae":[],"casks":[]}\n'
        return 0
    fi
    local greedy_flag=""
    if [ "${1:-}" = "--greedy" ]; then
        greedy_flag="--greedy"
        shift
    fi
    local err_file rc out q first_json="" second_json=""
    err_file="$(mktemp "${TMPDIR:-/tmp}/ascendo_brew_outdated.XXXXXX")" || {
        printf '{"formulae":[],"casks":[]}\n'
        return 1
    }
    # q1: everything outdated; q2 (only when tokens were given): the greedy
    # auto-updating casks. Keep stderr off stdout so a later 2>&1 regression
    # cannot poison the JSON.
    q=1
    while [ "$q" -le 2 ]; do
        if [ "$q" -eq 2 ] && [ $# -eq 0 ]; then
            break
        fi
        if [ "$q" -eq 1 ]; then
            # shellcheck disable=SC2086
            out="$(brew outdated --json=v2 ${greedy_flag} 2>"$err_file")"
        else
            out="$(brew outdated --json=v2 --cask --greedy-auto-updates "$@" 2>"$err_file")"
        fi
        rc=$?
        # rc 1 WITHOUT an "Error:" line on stderr is success: `brew outdated`
        # exits 1 when it found outdated items. Only a real "Error:" line (or
        # any other rc) is a failure, and then the stderr is passed on so the
        # caller can report the actual reason.
        if [ "$rc" -eq 1 ] && ! grep -Eq '^[[:space:]]*Error:' "$err_file" 2>/dev/null; then
            rc=0
        fi
        if [ "$rc" -ne 0 ]; then
            cat "$err_file" >&2
            rm -f "$err_file" 2>/dev/null || true
            printf '{"formulae":[],"casks":[]}\n'
            return "$rc"
        fi
        # Defensive: drop any progress lines that leaked onto stdout.
        out="$(printf '%s\n' "$out" | grep -v '^==>' | grep -v '^✔')" || out=""
        if [ "$q" -eq 1 ]; then
            first_json="$out"
        else
            second_json="$out"
        fi
        q=$((q + 1))
    done
    rm -f "$err_file" 2>/dev/null || true
    [ -n "$first_json" ] || first_json='{"formulae":[],"casks":[]}'
    if [ -z "$second_json" ] || ! command -v jq >/dev/null 2>&1; then
        printf '%s\n' "$first_json"
        return 0
    fi
    # Union by cask name (string, or first element of the name array).
    printf '%s\n%s\n' "$first_json" "$second_json" | jq -c -s '
        {formulae: (.[0].formulae // []),
         casks: (((.[0].casks // []) + (.[1].casks // []))
                 | unique_by(if (.name | type) == "array" then .name[0] else .name end))}
    ' 2>/dev/null || printf '%s\n' "$first_json"
}

# Brew-managed auto-updating casks that must be queried with
# `--greedy-auto-updates` (plain `brew outdated` never reports them), limited
# to the ones that are actually installed. Prints them space-separated.
# Config: config/brew_greedy_casks.txt (one token per line, '#' comments);
# ASCENDO_BREW_GREEDY_FILE overrides the path (tests).
ascendo_brew_greedy_tokens() {
    local file="${ASCENDO_BREW_GREEDY_FILE:-$_ASCENDO_BREW_LIB_DIR/../config/brew_greedy_casks.txt}"
    local installed tok out=""
    [ -f "$file" ] || return 0
    installed="$(ascendo_brew_cask_versions 2>/dev/null | awk 'NF {print $1}')" || installed=""
    [ -n "$installed" ] || return 0
    while IFS= read -r tok; do
        tok="${tok%%#*}"
        tok="$(printf '%s' "$tok" | tr -d '[:space:]')"
        [ -n "$tok" ] || continue
        if printf '%s\n' "$installed" | grep -Fxq -- "$tok"; then
            case " $out " in
                (*" $tok "*) ;;
                (*) out="${out:+$out }$tok" ;;
            esac
        fi
    done < "$file"
    printf '%s\n' "$out"
    return 0
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

# LAST-RESORT table: map a cask token to its /Applications bundle name (without
# .app suffix). Only consulted when the cask's real artifact facts are not
# available (see ascendo_brew_cask_app_paths). Returns empty for unknown casks.
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

# ---------------------------------------------------------------------------
# Cask facts (one `brew info --json=v2 --cask` per phase, parsed by brew_casks.py)
# ---------------------------------------------------------------------------
# ASCENDO_BREW_FACTS  : lines "token|version|installed|targets|quit_ids|sudo"
#                       (targets / quit_ids are ';'-joined)
# ASCENDO_BREW_ORPHANS: newline-separated orphan cask tokens
# ASCENDO_BREW_FACTS_LOADED=1 marks that a phase loaded them, so a token with
# no line is "unknown" and is NOT re-queried one by one on every helper call.
ASCENDO_BREW_FACTS=""
ASCENDO_BREW_ORPHANS=""
ASCENDO_BREW_FACTS_LOADED=""

# Application directories searched for a cask's app targets.
# ASCENDO_BREW_APPLICATIONS_DIR overrides them (tests) -- then it is the only one.
_ascendo_brew_app_dirs() {
    if [ -n "${ASCENDO_BREW_APPLICATIONS_DIR:-}" ]; then
        printf '%s\n' "$ASCENDO_BREW_APPLICATIONS_DIR"
    else
        printf '%s\n' "/Applications" "$HOME/Applications"
    fi
}

# Append facts + orphans for the casks in one brew-info JSON document.
_ascendo_brew_absorb_info_json() {
    local json="$1" lines orphans _dirs _d
    [ -n "$json" ] || return 0
    lines="$(printf '%s' "$json" | _ascendo_brew_casks_py facts 2>/dev/null)" || lines=""
    _dirs=()
    while IFS= read -r _d; do
        [ -n "$_d" ] && _dirs+=("$_d")
    done <<EOF_DIRS
$(_ascendo_brew_app_dirs)
EOF_DIRS
    orphans="$(printf '%s' "$json" | _ascendo_brew_casks_py orphans "${_dirs[@]}" 2>/dev/null)" || orphans=""
    if [ -n "$lines" ]; then
        ASCENDO_BREW_FACTS="${ASCENDO_BREW_FACTS:+$ASCENDO_BREW_FACTS
}$lines"
    fi
    if [ -n "$orphans" ]; then
        ASCENDO_BREW_ORPHANS="${ASCENDO_BREW_ORPHANS:+$ASCENDO_BREW_ORPHANS
}$orphans"
    fi
    return 0
}

# ascendo_brew_load_cask_facts <token...>
# One batched `brew info --json=v2 --cask <tokens>`; if that fails or leaves a
# token without facts (a single unknown token makes brew reject the whole
# call), those tokens are queried one by one. Facts are best-effort: anything
# that cannot be loaded simply has no line (callers fall back / stay
# conservative). Must be called in the phase shell, NOT inside $(...).
ascendo_brew_load_cask_facts() {
    ASCENDO_BREW_FACTS=""
    ASCENDO_BREW_ORPHANS=""
    ASCENDO_BREW_FACTS_LOADED=1
    [ $# -gt 0 ] || return 0
    command -v brew >/dev/null 2>&1 || return 0
    local json tok
    json="$(_ascendo_brew_info "$@")" || json=""
    _ascendo_brew_absorb_info_json "$json"
    for tok in "$@"; do
        if printf '%s\n' "$ASCENDO_BREW_FACTS" | awk -F'|' -v t="$tok" '$1 == t {f=1} END {exit f ? 0 : 1}'; then
            continue
        fi
        json="$(_ascendo_brew_info "$tok")" || json=""
        _ascendo_brew_absorb_info_json "$json"
    done
    return 0
}

# ascendo_brew_prepare_cask_facts <outdated_json_file> [all]
# Load facts for every outdated cask in the file; with `all` also for every
# installed cask (check.sh needs them to spot orphan casks among the
# up-to-date ones).
ascendo_brew_prepare_cask_facts() {
    local file="$1" scope="${2:-}" tokens="" t list=()
    tokens="$(ascendo_brew_parse_outdated "$file" cask 2>/dev/null | cut -d, -f1)" || tokens=""
    if [ "$scope" = "all" ]; then
        tokens="$(printf '%s\n%s\n' "$tokens" "$(ascendo_brew_cask_versions 2>/dev/null | awk 'NF {print $1}')")"
    fi
    while IFS= read -r t; do
        [ -n "$t" ] && list+=("$t")
    done <<EOF_TOK
$(printf '%s\n' "$tokens" | awk 'NF && !seen[$0]++')
EOF_TOK
    if [ "${#list[@]}" -gt 0 ]; then
        ascendo_brew_load_cask_facts "${list[@]}"
    else
        ASCENDO_BREW_FACTS=""
        ASCENDO_BREW_ORPHANS=""
        ASCENDO_BREW_FACTS_LOADED=1
    fi
    return 0
}

# Print the facts line for a cask ("" when unknown). Uses the phase-loaded
# facts; only when none were loaded does it query brew for this one token.
ascendo_brew_cask_facts_line() {
    local token="$1" line=""
    [ -n "$token" ] || return 0
    if [ -n "$ASCENDO_BREW_FACTS" ]; then
        line="$(printf '%s\n' "$ASCENDO_BREW_FACTS" | awk -F'|' -v t="$token" '$1 == t {print; exit}')"
    fi
    if [ -z "$line" ] && [ -z "$ASCENDO_BREW_FACTS_LOADED" ] && command -v brew >/dev/null 2>&1; then
        line="$(_ascendo_brew_info "$token" | _ascendo_brew_casks_py facts 2>/dev/null | head -n1)" || line=""
    fi
    printf '%s' "$line"
}

# Field N (1-based) of a facts line; empty fields are preserved.
_ascendo_brew_fact_field() {
    printf '%s' "$1" | cut -d'|' -f"$2"
}

# Print the installed .app bundle path(s) of a cask, one per line, derived from
# the cask's REAL facts: `app` artifact targets (string or {target:}) or, for
# pkg casks, the uninstall.delete /Applications/X.app hints. Only when no facts
# are available at all does the small token->bundle-name table apply.
# Prints nothing when no bundle of the cask is on disk.
ascendo_brew_cask_app_paths() {
    local token="$1"
    local line targets t d
    line="$(ascendo_brew_cask_facts_line "$token")"
    if [ -z "$line" ]; then
        local app
        app="$(ascendo_brew_cask_app_name "$token")"
        [ -n "$app" ] || return 0
        while IFS= read -r d; do
            [ -n "$d" ] || continue
            if [ -d "$d/${app}.app" ]; then
                printf '%s\n' "$d/${app}.app"
                return 0
            fi
        done <<EOF_DIRS
$(_ascendo_brew_app_dirs)
EOF_DIRS
        return 0
    fi
    targets="$(_ascendo_brew_fact_field "$line" 4)"
    [ -n "$targets" ] || return 0
    while IFS= read -r t; do
        [ -n "$t" ] || continue
        while IFS= read -r d; do
            [ -n "$d" ] || continue
            if [ -d "$d/$t" ]; then
                printf '%s\n' "$d/$t"
            fi
        done <<EOF_DIRS
$(_ascendo_brew_app_dirs)
EOF_DIRS
    done <<EOF_TARGETS
$(printf '%s' "$targets" | tr ';' '\n')
EOF_TARGETS
    return 0
}

# Exit 0 when Ascendo can tell whether the cask's app is running: real facts
# were loaded for it, or (last resort) the token table knows its bundle. When
# neither holds (brew info failed even per token AND no table entry) the
# running-app probe would silently answer "not running"; callers must then NOT
# upgrade (never-close-user-apps outranks coverage).
ascendo_brew_cask_running_state_known() {
    [ -n "$(ascendo_brew_cask_facts_line "$1")" ] && return 0
    [ -n "$(ascendo_brew_cask_app_name "$1")" ]
}

# Exit 0 iff an app with this bundle id is running (LaunchServices).
ascendo_brew_bundle_running() {
    local id="$1" n
    command -v lsappinfo >/dev/null 2>&1 || return 1
    n="$(lsappinfo list 2>/dev/null | /usr/bin/grep -c "bundleID=\"$id\"")"
    [ "${n:-0}" -gt 0 ]
}

# Exit 0 when a process is running from any .app bundle of the cask
# (main binary or a helper under <bundle>/Contents/), or when a bundle id
# from the cask's uninstall/zap `quit:` stanza is running. Read-only: this
# never quits, signals or kills anything -- an app the user has open is
# left alone (the caller defers the upgrade instead).
ascendo_brew_cask_app_running() {
    local token="$1"
    local _path _esc _id _line
    while IFS= read -r _path; do
        [ -n "$_path" ] || continue
        # Regex-escape the path for pgrep -f (extended regex). Inside a
        # POSIX bracket expression (BSD sed on macOS and GNU sed alike) a
        # backslash is a LITERAL member, not an escape: `]` must come first
        # and `\` / `/` are listed as plain members -- do not "fix" this.
        _esc="$(printf '%s' "$_path" | sed 's/[][\.*^$+?(){}|\/]/\\&/g')"
        if pgrep -f "^${_esc}/Contents/" >/dev/null 2>&1; then
            return 0
        fi
    done <<EOF_PATHS
$(ascendo_brew_cask_app_paths "$token")
EOF_PATHS
    _line="$(ascendo_brew_cask_facts_line "$token")"
    if [ -n "$_line" ]; then
        while IFS= read -r _id; do
            [ -n "$_id" ] || continue
            if ascendo_brew_bundle_running "$_id"; then
                return 0
            fi
        done <<EOF_IDS
$(_ascendo_brew_fact_field "$_line" 5 | tr ';' '\n')
EOF_IDS
    fi
    return 1
}

# Exit 0 when the cask declares app artifact(s) and none is on disk (the user
# deleted the app; Homebrew still has the record). Needs the facts loaded by
# ascendo_brew_prepare_cask_facts / ascendo_brew_load_cask_facts.
ascendo_brew_cask_is_orphan() {
    [ -n "$1" ] && [ -n "$ASCENDO_BREW_ORPHANS" ] || return 1
    printf '%s\n' "$ASCENDO_BREW_ORPHANS" | grep -Fxq -- "$1"
}

# Exit 0 when upgrading the cask needs administrator rights (pkg/installer
# artifacts, or an uninstall stanza with pkgutil/launchctl/kext).
ascendo_brew_cask_needs_admin() {
    local line
    line="$(ascendo_brew_cask_facts_line "$1")"
    [ -n "$line" ] || return 1
    [ "$(_ascendo_brew_fact_field "$line" 6)" = "1" ]
}

# Exit 0 when a sudo askpass helper is wired (Homebrew's SystemCommand then
# adds `sudo -A`). Dashboard runs are TTY-less: without it a sudo cask cannot
# be upgraded non-interactively.
ascendo_brew_admin_available() {
    [ -n "${SUDO_ASKPASS:-}" ] && [ -x "${SUDO_ASKPASS}" ]
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
# When the licence is not accepted it records ONE warn message carrying the
# XCODE_LICENSE code and the action-required token, and returns 1. It adds
# no item (a synthetic item would show up as a fake brew package in the
# inventory); the exit-10 / zero-item sidecar resolves to status `skipped`
# in _json_emit.py. Requires ascendo_json.sh to be sourced.
ascendo_brew_xcode_license_gate() {
    if ascendo_brew_xcode_license_ok; then
        return 0
    fi
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

# Print the installed .app short version of a cask (first bundle found), or empty.
# ASCENDO_BREW_APPLICATIONS_DIR overrides /Applications (tests).
ascendo_brew_cask_installed_app_version() {
    local token="$1" path v
    path="$(ascendo_brew_cask_app_paths "$token" | head -n1)"
    [ -n "$path" ] || return 1
    v="$(_ascendo_brew_casks_py bundle-version "$path" 2>/dev/null)"
    [ -n "$v" ] || return 1
    printf '%s\n' "$v"
}

# Scheme-aware installed-vs-cask relation: newer | current | unknown.
# (Brave's bundle "151.1.93.138" vs cask "1.93.138.0" is `current`, not newer.)
ascendo_brew_app_vs_package_relation() {
    _ascendo_brew_casks_py relation "$1" "$2" 2>/dev/null || printf 'unknown\n'
}

# Downgrade / orphan guard for one outdated cask. Prints one line
#   <disposition>|<detail>
# and always returns 0. Dispositions:
#   downgrade      -- Homebrew's own record, or the installed bundle, is provably
#                     newer than the cask version: upgrading would downgrade
#   orphan         -- the cask declares app target(s) and none exists on disk
#                     (the user deleted the app): do not reinstall it
#   scheme_unknown -- bundle and cask version schemes are not comparable; proceed
#                     (Homebrew's recorded version already agreed) and note it
#   proceed        -- nothing speaks against the upgrade
# Order (macOS_updates v1.5.0): (1) recorded-vs-cask version; (2) locate the
# bundle from the cask's real .app targets (else Caskroom, else the last-resort
# table); (3) scheme-aware bundle-vs-cask relation.
ascendo_brew_cask_disposition() {
    local token="$1" target="${2:-}"
    local line ver="" recorded="" targets="" path="" t d found room prefix bver rel
    line="$(ascendo_brew_cask_facts_line "$token")"
    if [ -n "$line" ]; then
        ver="$(_ascendo_brew_fact_field "$line" 2)"
        recorded="$(_ascendo_brew_fact_field "$line" 3)"
        targets="$(_ascendo_brew_fact_field "$line" 4)"
    fi
    [ -n "$ver" ] || ver="$target"

    # (1) Homebrew's own record of what it installed is the only like-for-like
    # operand for the cask version; the bundle version may use another scheme.
    if [ -n "$recorded" ] && [ -n "$ver" ]; then
        if [ "$(_ascendo_brew_casks_py cmp "$recorded" "$ver" 2>/dev/null)" = "newer" ]; then
            printf 'downgrade|Homebrew recorded %s, newer than cask %s\n' "$recorded" "$ver"
            return 0
        fi
    fi

    # (2) find the bundle.
    if [ -n "$targets" ]; then
        found=0
        while IFS= read -r t; do
            [ -n "$t" ] || continue
            while IFS= read -r d; do
                [ -n "$d" ] || continue
                if [ -d "$d/$t" ]; then
                    path="$d/$t"
                    found=1
                    break
                fi
            done <<EOF_DIRS
$(_ascendo_brew_app_dirs)
EOF_DIRS
            [ "$found" -eq 1 ] && break
        done <<EOF_TARGETS
$(printf '%s' "$targets" | tr ';' '\n')
EOF_TARGETS
        if [ "$found" -eq 0 ]; then
            printf 'orphan|%s\n' "${targets%%;*}"
            return 0
        fi
    elif [ -n "$line" ]; then
        # Only casks without app targets use the Caskroom fallback.
        prefix="$(ascendo_brew_prefix)"
        [ -n "$prefix" ] || prefix="/opt/homebrew"
        room="$prefix/Caskroom/$token"
        if [ -d "$room" ]; then
            path="$(find "$room" -maxdepth 3 -name '*.app' 2>/dev/null | head -n1)"
        fi
    else
        # Facts unavailable: last-resort token->bundle table.
        path="$(ascendo_brew_cask_app_paths "$token" | head -n1)"
    fi

    # (3) bundle-vs-cask relation.
    if [ -n "$path" ] && [ -d "$path" ] && [ -n "$ver" ]; then
        bver="$(_ascendo_brew_casks_py bundle-version "$path" 2>/dev/null)"
        if [ -n "$bver" ]; then
            rel="$(ascendo_brew_app_vs_package_relation "$bver" "$ver")"
            case "$rel" in
                newer)
                    printf 'downgrade|installed app %s is newer than cask %s\n' "$bver" "$ver"
                    return 0 ;;
                unknown)
                    printf 'scheme_unknown|app %s and cask %s use different version schemes\n' "$bver" "$ver"
                    return 0 ;;
            esac
        fi
    fi
    printf 'proceed|\n'
    return 0
}

# Back-compat wrapper: exit 0 only when the guard says upgrading would downgrade.
ascendo_brew_cask_would_downgrade() {
    local token="$1" target="${2:-}" disp
    [ -n "$token" ] && [ -n "$target" ] || return 1
    disp="$(ascendo_brew_cask_disposition "$token" "$target")"
    [ "${disp%%|*}" = "downgrade" ]
}

# Record a guard skip (downgrade / orphan) for a cask as a `skipped` item plus
# a coded message; used identically by check/plan/apply. Returns 0 when the
# cask was recorded (caller must not process it further), 1 otherwise.
# Needs ascendo_json.sh. The item keeps the candidate version for an orphan
# (the cask IS outdated per brew -- inventory must still show it); a downgrade
# keeps current == target (the "candidate" is older than what is installed).
ascendo_brew_emit_cask_guard_skip() {
    local disp="$1" id="$2" cur="$3" cand="$4" detail="$5"
    case "$disp" in
        downgrade)
            json_add_item "$id" "$cur" "$cur" "skipped" "brew" "cask"
            json_add_message "warn" \
                "${id}: skipped -- cask ${cand} would downgrade the installed app (${detail})" \
                "CASK_DOWNGRADE_GUARD"
            return 0 ;;
        orphan)
            json_add_item "$id" "$cur" "$cand" "skipped" "brew" "cask"
            json_add_message "warn" \
                "${id}: skipped -- app ${detail} was removed manually; remove the record with: brew uninstall --cask --force ${id} | $(ascendo_action_required_token cask_orphan "$cur" "$cand")" \
                "CASK_ORPHAN"
            return 0 ;;
    esac
    return 1
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
