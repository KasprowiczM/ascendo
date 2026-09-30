# adapters/macos/lib/ascendo_mas.sh
# mas helper functions for the macOS adapter. Bash 3.2-safe.
#
# Public functions:
#   mas_signed_in                 0 if `mas list` exits 0 (bounded); rc in
#                                 MAS_SIGNIN_RC (124 = timed out = unknown)
#   mas_list_json                 emits JSON array of installed apps to stdout
#   mas_outdated_json             emits JSON array of outdated apps to stdout;
#                                 non-zero rc when the probe failed/timed out
#   mas_probe_outdated            bounded `mas outdated` in the CURRENT shell:
#                                 sets MAS_OUTDATED_JSON, MAS_PROBE_RC,
#                                 MAS_PROBE_ERR; rc != 0 => state UNKNOWN
#   mas_probe_list                same for `mas list` (MAS_LIST_JSON)
#   mas_probe_reason              "timed out after Ns" / "exited N: <stderr>"
#   mas_report_signin_failure     phase-script helper (sign-in probe failed)
#   mas_report_outdated_unknown   phase-script helper (outdated probe failed)
#   mas_report_ipad_pending MODE FILTER   report-only iPad app updates
#   mas_version_at_least <major>  reads version from stdin or `mas version`
#   mas_classify_exit <code>      maps mas exit code -> ascendo status string
#   mas_filter_match <csv> <id> [name]   0 if csv empty or id/name listed
#
# Stdin variants (test seams; do not use in production scripts):
#   mas_list_json_from_stdin
#   mas_outdated_json_from_stdin
#
# Override knobs:
#   MAS_BIN                     used instead of `mas` (for testing).
#   ASCENDO_MAS_CHECK_TIMEOUT   seconds for `mas outdated`/`mas list` (120)
#   ASCENDO_MAS_UPGRADE_TIMEOUT seconds for `mas upgrade` (1800)
#   (both: positive integer; anything else falls back to the default)

# shellcheck shell=bash

: "${MAS_BIN:=mas}"

_ASCENDO_MAS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Bounded process runner + iPad helper (both idempotent; usable when this file
# is sourced on its own, e.g. by unit tests).
# shellcheck source=ascendo_proc.sh
. "$_ASCENDO_MAS_DIR/ascendo_proc.sh"
# shellcheck source=ascendo_appstore_ios.sh
. "$_ASCENDO_MAS_DIR/ascendo_appstore_ios.sh"

MAS_PROBE_RC=0
MAS_PROBE_ERR=""
MAS_PROBE_WHAT=""
MAS_OUTDATED_JSON="[]"
MAS_LIST_JSON="[]"
MAS_SIGNIN_RC=0

# _mas_secs <value> <default>: positive integer, else the default.
_mas_secs() {
    case "${1:-}" in
        ''|*[!0-9]*|0) printf '%s' "$2" ;;
        *) printf '%s' "$1" ;;
    esac
}
ascendo_mas_check_timeout()   { _mas_secs "${ASCENDO_MAS_CHECK_TIMEOUT:-}" 120; }
ascendo_mas_upgrade_timeout() { _mas_secs "${ASCENDO_MAS_UPGRADE_TIMEOUT:-}" 1800; }

# _mas_run_probe <what> <mas args...>
#   Runs `mas <args>` bounded by the check timeout. stdout -> MAS_PROBE_OUT,
#   last stderr lines -> MAS_PROBE_ERR, exit code -> MAS_PROBE_RC (124 = timed
#   out). Runs in the CURRENT shell (no subshell) so callers can read globals.
MAS_PROBE_OUT=""
_mas_run_probe() {
    local _errf _tmo
    MAS_PROBE_WHAT="$1"; shift
    MAS_PROBE_RC=0
    MAS_PROBE_ERR=""
    MAS_PROBE_OUT=""
    _tmo="$(ascendo_mas_check_timeout)"
    _errf="$(mktemp "${TMPDIR:-/tmp}/ascendo-mas-err.XXXXXX")" || _errf=/dev/null
    MAS_PROBE_OUT="$(ascendo_run_with_timeout "$_tmo" "$MAS_BIN" "$@" 2>"$_errf")" || MAS_PROBE_RC=$?
    if [ "$_errf" != /dev/null ]; then
        MAS_PROBE_ERR="$(tail -n 3 "$_errf" 2>/dev/null | tr '\n' ' ' | cut -c1-300)"
        rm -f "$_errf"
    fi
    return "$MAS_PROBE_RC"
}

# mas_probe_reason -> "timed out after 120s" | "exited 1: <stderr tail>"
mas_probe_reason() {
    if [ "$MAS_PROBE_RC" -eq 124 ]; then
        printf 'timed out after %ss' "$(ascendo_mas_check_timeout)"
    elif [ -n "$MAS_PROBE_ERR" ]; then
        printf 'exited %s: %s' "$MAS_PROBE_RC" "$MAS_PROBE_ERR"
    else
        printf 'exited %s' "$MAS_PROBE_RC"
    fi
}

# Bounded sign-in probe. rc = mas's exit code (124 on timeout, kept in
# MAS_SIGNIN_RC so callers can tell "not signed in" from "unknown").
mas_signed_in() {
    MAS_SIGNIN_RC=0
    ascendo_run_with_timeout "$(ascendo_mas_check_timeout)" "$MAS_BIN" list >/dev/null 2>&1 || MAS_SIGNIN_RC=$?
    return "$MAS_SIGNIN_RC"
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

# mas_probe_list: bounded `mas list` in the current shell -> MAS_LIST_JSON.
# rc != 0 (probe failed / timed out) leaves MAS_LIST_JSON="[]" and the reason in
# MAS_PROBE_*; "[]" from a FAILED probe must never be read as "nothing installed".
mas_probe_list() {
    MAS_LIST_JSON="[]"
    _mas_run_probe "mas list" list || return $?
    MAS_LIST_JSON="$(printf '%s\n' "$MAS_PROBE_OUT" | mas_list_json_from_stdin)"
    return 0
}

mas_list_json() {
    mas_probe_list || return $?
    printf '%s\n' "$MAS_LIST_JSON"
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

# mas_probe_outdated: bounded `mas outdated` (default mode -- never --accurate:
# it opens dialogs, wrong for a TTY-less dashboard run) in the current shell ->
# MAS_OUTDATED_JSON. rc != 0 means the update state is UNKNOWN (leaves "[]");
# callers must report that, never "nothing pending".
mas_probe_outdated() {
    MAS_OUTDATED_JSON="[]"
    _mas_run_probe "mas outdated" outdated || return $?
    MAS_OUTDATED_JSON="$(printf '%s\n' "$MAS_PROBE_OUT" | mas_outdated_json_from_stdin)"
    return 0
}

mas_outdated_json() {
    mas_probe_outdated || return $?
    printf '%s\n' "$MAS_OUTDATED_JSON"
}

# ---- phase-script reporting helpers (need ascendo_json.sh sourced) ----------

# mas_report_signin_failure: call when `mas_signed_in` returned non-zero.
mas_report_signin_failure() {
    if [ "${MAS_SIGNIN_RC:-1}" -eq 124 ]; then
        json_add_message "warn" "mas list timed out after $(ascendo_mas_check_timeout)s; Mac App Store state is unknown (not reported as up to date)."
        json_add_item "mas:probe-unknown" "" "" "skipped" "mas"
    else
        json_add_message "error" "Not signed into Mac App Store. Open App Store.app and sign in."
        json_add_item "mas:not-signed-in" "" "" "failed" "mas"
    fi
}

# mas_report_outdated_unknown: call after mas_probe_outdated failed. The probe
# failing means we do NOT know whether updates are pending: skipped + reason.
mas_report_outdated_unknown() {
    json_add_message "warn" "mas outdated $(mas_probe_reason); pending App Store updates are unknown (not reported as up to date)."
    json_add_item "mas:outdated-probe" "" "" "skipped" "mas"
}

# mas_report_ipad_pending <planned|skipped> [filter_csv]
#   Report-only iPad/iOS-on-Mac apps whose store version is newer (iTunes Lookup
#   gate). check/plan pass "planned"; apply passes "skipped" (apply NEVER
#   touches them and never drives the App Store GUI). Item id `ipad:<name>`,
#   message carries the ACTION-REQUIRED token (reason=ipad_pending). A failed
#   lookup is a soft unknown (skipped item + warning), never "all current".
mas_report_ipad_pending() {
    local _mode="${1:-planned}" _filter="${2:-}"
    local _out _rc _name _iid _inst _store _path
    _out="$(ascendo_ios_apps_pending)"; _rc=$?
    if [ "$_rc" -ne 0 ]; then
        json_add_message "warn" "iPad app check unavailable (App Store lookup failed); iPad app updates are unknown (not reported as up to date)."
        json_add_item "ipad:lookup" "" "" "skipped" "mas"
        return 0
    fi
    [ -n "$_out" ] || return 0
    while IFS="$ASCENDO_IOS_US" read -r _name _iid _inst _store _path; do
        [ -n "$_iid" ] || continue
        mas_filter_match "$_filter" "ipad:$_name" "$_name" || mas_filter_match "$_filter" "$_iid" "" || continue
        json_add_item "ipad:$_name" "$_inst" "$_store" "$_mode" "mas"
        json_add_message "info" "ipad:$_name: iPad app - update it in the App Store (mas cannot) | $(ascendo_action_required_token ipad_pending "$_inst" "$_store")"
    done <<EOF_IPAD
$_out
EOF_IPAD
    return 0
}

# mas_filter_match <csv> <id> [name]
# Returns 0 when <csv> is empty OR the numeric id OR the display name is a
# member of the comma-separated list. The dashboard's exclusion-derived
# filter carries display names (check/plan emit the name as the item id)
# while explicit callers pass numeric ids -- both must select the app.
# Bash 3.2-safe: comma sentinels + quoted (literal) case patterns, so
# "10" never matches inside "1011" and names with glob characters are safe.
mas_filter_match() {
    local _csv="$1" _id="$2" _name="${3:-}"
    [ -z "$_csv" ] && return 0
    case ",$_csv," in
        (*",$_id,"*) return 0 ;;
    esac
    if [ -n "$_name" ]; then
        case ",$_csv," in
            (*",$_name,"*) return 0 ;;
        esac
    fi
    return 1
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
# Maps mas exit code to a valid ascendo ItemStatus enum value:
#   0 -> success
#   6 -> failed  (mas convention: not signed in; rc=6 is captured in the
#                 apply.sh error message which includes the raw exit code)
#   * -> failed
mas_classify_exit() {
    case "$1" in
        0) printf 'success\n' ;;
        *) printf 'failed\n' ;;
    esac
}
