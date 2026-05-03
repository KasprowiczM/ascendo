# adapters/macos/lib/ascendo_mas.sh
# mas helper functions for the macOS adapter. Bash 3.2-safe.
#
# Public functions:
#   mas_signed_in                 0 if `mas list` exits 0, else 1
#   mas_list_json                 emits JSON array of installed apps to stdout
#   mas_outdated_json             emits JSON array of outdated apps to stdout
#   mas_version_at_least <major>  reads version from stdin or `mas version`
#   mas_classify_exit <code>      maps mas exit code -> ascendo status string
#
# Stdin variants (test seams; do not use in production scripts):
#   mas_list_json_from_stdin
#   mas_outdated_json_from_stdin
#
# Override knob:
#   MAS_BIN  if set, used instead of `mas` (for testing).

# shellcheck shell=bash

: "${MAS_BIN:=mas}"

mas_signed_in() {
    "$MAS_BIN" list >/dev/null 2>&1
}

# Parse `mas list` output. Each line:
#   <id> <name> (<version>)
# id is numeric (may have leading whitespace); version is inside the LAST
# parentheses on the line. Name is everything between the first token after
# id and the LAST `(`, trimmed.
mas_list_json_from_stdin() {
    awk '
        function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
        function jsesc(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return s }
        BEGIN { print "["; first = 1 }
        /[0-9]/ {
            # skip lines that have no numeric token at all
            # extract first numeric token as id
            line = $0
            sub(/^[[:space:]]+/, "", line)
            if (line !~ /^[0-9]/) next
            id = line
            sub(/[^0-9].*/, "", id)
            rest = line
            sub(/^[0-9]+[[:space:]]+/, "", rest)
            # last "(" starts the version block
            n = length(rest)
            paren = 0
            for (i = n; i > 0; i--) {
                if (substr(rest, i, 1) == "(") { paren = i; break }
            }
            if (paren == 0) next
            name = trim(substr(rest, 1, paren - 1))
            version = substr(rest, paren + 1, length(rest) - paren - 1)
            if (!first) print ","
            first = 0
            printf "{\"id\":\"%s\",\"name\":\"%s\",\"version\":\"%s\"}", \
                   id, jsesc(name), jsesc(version)
        }
        END { print "]" }
    '
}

mas_list_json() {
    "$MAS_BIN" list 2>/dev/null | mas_list_json_from_stdin
}

# Parse `mas outdated` output. Each line:
#   <id> <name> (<current> -> <target>)
mas_outdated_json_from_stdin() {
    awk '
        function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
        function jsesc(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return s }
        BEGIN { print "["; first = 1 }
        /[0-9]/ {
            line = $0
            sub(/^[[:space:]]+/, "", line)
            if (line !~ /^[0-9]/) next
            id = line
            sub(/[^0-9].*/, "", id)
            rest = line
            sub(/^[0-9]+[[:space:]]+/, "", rest)
            n = length(rest)
            paren = 0
            for (i = n; i > 0; i--) {
                if (substr(rest, i, 1) == "(") { paren = i; break }
            }
            if (paren == 0) next
            name = trim(substr(rest, 1, paren - 1))
            inner = substr(rest, paren + 1, length(rest) - paren - 1)
            arrow = index(inner, "->")
            if (arrow == 0) next
            current = trim(substr(inner, 1, arrow - 1))
            target  = trim(substr(inner, arrow + 2))
            if (!first) print ","
            first = 0
            printf "{\"id\":\"%s\",\"name\":\"%s\",\"current_version\":\"%s\",\"target_version\":\"%s\"}", \
                   id, jsesc(name), jsesc(current), jsesc(target)
        }
        END { print "]" }
    '
}

mas_outdated_json() {
    "$MAS_BIN" outdated 2>/dev/null | mas_outdated_json_from_stdin
}

# mas_version_at_least <required-major>
# Reads version string from stdin (e.g. "4.3.0") or from `$MAS_BIN version`.
# Returns 0 if major >= required, 1 otherwise.
mas_version_at_least() {
    local required="$1"
    local version=""
    # If stdin is not a tty, try to read with a short timeout so we never
    # block waiting for input that may never arrive (phase scripts have
    # non-tty stdin but no piped data when calling this function directly).
    if [ ! -t 0 ]; then
        IFS= read -r -t 2 version || version=""
    fi
    if [ -z "$version" ]; then
        version="$("$MAS_BIN" version 2>/dev/null || printf '0.0.0')"
    fi
    local major
    major="$(printf '%s' "$version" | awk -F. '{print $1+0}')"
    [ "${major:-0}" -ge "${required:-0}" ]
}

# mas_classify_exit <code>
# Maps mas exit code to a single token usable as ascendo item status:
#   0 -> success
#   6 -> failed-not-signed-in     (mas convention; observed on signed-out hosts)
#   * -> failed
mas_classify_exit() {
    case "$1" in
        0) printf 'success\n' ;;
        6) printf 'failed-not-signed-in\n' ;;
        *) printf 'failed\n' ;;
    esac
}
