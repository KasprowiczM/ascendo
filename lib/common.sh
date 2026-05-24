#!/usr/bin/env bash
# =============================================================================
# lib/common.sh — Shared library: colors, logging, status helpers
# =============================================================================

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# ── Log file setup ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/update_${TIMESTAMP}.log}"

mkdir -p "${LOG_DIR}"

# ── Internal log (both file and screen) ───────────────────────────────────────
_log_raw() {
    local level="$1"; shift
    local msg="$*"
    local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "${ts}  [${level}]  ${msg}" >> "${LOG_FILE}"
}

# ── Public print helpers ───────────────────────────────────────────────────────
print_header() {
    local title="$1"
    local line; line="$(printf '═%.0s' $(seq 1 60))"
    echo
    echo -e "${BOLD}${BLUE}${line}${RESET}"
    echo -e "${BOLD}${BLUE}  ${title}${RESET}"
    echo -e "${BOLD}${BLUE}${line}${RESET}"
    echo
    _log_raw "INFO" "======== ${title} ========"
}

print_section() {
    local title="$1"
    echo
    echo -e "${BOLD}${CYAN}── ${title} ──────────────────────────────${RESET}"
    _log_raw "INFO" "--- ${title} ---"
}

print_step() {
    # Usage: print_step "Updating APT package list"
    echo -ne "  ${DIM}▶${RESET}  $* ... "
    _log_raw "STEP" "$*"
}

print_ok() {
    echo -e "${GREEN}✔${RESET}"
    _log_raw "OK  " "${1:-done}"
}

print_warn() {
    echo -e "${YELLOW}⚠  $*${RESET}"
    _log_raw "WARN" "$*"
}

print_error() {
    echo -e "${RED}✘  $*${RESET}"
    _log_raw "ERR " "$*"
}

print_info() {
    echo -e "     ${DIM}$*${RESET}"
    _log_raw "INFO" "$*"
}

print_skipped() {
    echo -e "${DIM}⊘  (skipped)${RESET}"
    _log_raw "SKIP" "${1:-skipped}"
}

print_result() {
    # Usage: print_result $? "optional context"
    if [[ "$1" -eq 0 ]]; then
        print_ok "${2:-}"
    else
        print_error "failed (exit $1) ${2:-}"
    fi
}

# ── Sudo wrapper: route every `sudo` through SUDO_ASKPASS when present ────────
# Phase scripts call plain `sudo apt-get …` etc.; without this wrapper sudo
# would fall back to TTY prompts after the cached timestamp expires (default
# 5–15 min). With SUDO_ASKPASS exported by the master, all sudos auto-fill the
# password from the in-memory askpass helper. No effect when SUDO_ASKPASS is
# unset — plain `sudo` keeps its standard semantics.
sudo() {
    if [[ -n "${SUDO_ASKPASS:-}" ]]; then
        command sudo -A "$@"
    else
        command sudo "$@"
    fi
}
export -f sudo 2>/dev/null || true

# ── Run a command silently, capture output to log ─────────────────────────────
# Returns the command's exit code.
run_silent() {
    local cmd=("$@")
    _log_raw "RUN " "${cmd[*]}"
    local output
    output=$("${cmd[@]}" 2>&1)
    local rc=$?
    if [[ -n "${output}" ]]; then
        echo "${output}" >> "${LOG_FILE}"
    fi
    return $rc
}

# ── Run with sudo silently ─────────────────────────────────────────────────────
sudo_silent() {
    run_silent sudo "$@"
}

# ── Run a command as the invoking (non-root) user ─────────────────────────────
# When the master script is run via "sudo ./update-all.sh", sub-scripts inherit
# EUID=0.  Tools like brew and npm refuse to run as root, so we drop back to
# SUDO_USER for those commands.  Falls through to a normal run when not root.
REAL_USER="${SUDO_USER:-${USER}}"
_real_user_home() { getent passwd "${REAL_USER}" | cut -d: -f6; }

run_as_user() {
    if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
        sudo -u "${SUDO_USER}" HOME="$(_real_user_home)" "$@"
    else
        "$@"
    fi
}

run_silent_as_user() {
    if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
        run_silent sudo -u "${SUDO_USER}" HOME="$(_real_user_home)" "$@"
    else
        run_silent "$@"
    fi
}

# ── Run a command silently as the invoking user, capturing tail on failure ──
# Same dispatch rule as run_silent_as_user but routes through run_capture
# (defined in lib/json.sh) so the captured output lives at
# $_LAST_RUN_OUT_FILE and the SSE stream sees every line live. Returns the
# command's exit code; the caller decides how to surface the failure.
#
# Pair with run_silent_with_tail when you also want an automatic
# json_add_diag emit. This thin wrapper exists to preserve the
# user-context dispatch (brew/npm refuse root) while sharing the capture
# infrastructure.
run_capture_as_user() {
    if ! declare -F run_capture >/dev/null 2>&1; then
        # Caller forgot to source lib/json.sh — degrade to run_silent
        # so the script still works, just without SSE / tail capture.
        run_silent_as_user "$@"
        return $?
    fi
    if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
        run_capture sudo -u "${SUDO_USER}" HOME="$(_real_user_home)" "$@"
    else
        run_capture "$@"
    fi
}

# ── Require root or sudo ───────────────────────────────────────────────────────
require_sudo() {
    if [[ $EUID -eq 0 ]]; then return 0; fi
    # Master script already prompted once and either warmed the cache or set
    # SUDO_ASKPASS — phase scripts must never re-prompt.
    if [[ "${UPDATE_ALL_SUDO_READY:-0}" == "1" ]]; then
        return 0
    fi
    # Standalone phase script: ensure auth.
    if sudo -n true 2>/dev/null; then return 0; fi
    if [[ -n "${SUDO_ASKPASS:-}" ]]; then
        sudo -A -v || { print_error "sudo askpass failed"; exit 1; }
    else
        echo -e "${YELLOW}  Sudo password required for privileged operations:${RESET}"
        sudo -v || { print_error "sudo authentication failed"; exit 1; }
    fi
    # Refresher: if SUDO_ASKPASS is wired (dashboard path), use it to
    # re-validate the cache periodically — `sudo -A -v` re-runs the
    # askpass helper and refreshes credentials cleanly. Without askpass
    # (interactive run with a TTY), fall back to `sudo -n true` which
    # works as long as the sudo cache stays valid.
    #
    # CRITICAL: when the dashboard spawns this script with stdin=DEVNULL +
    # start_new_session=True (no controlling TTY), `sudo -n true` from
    # the keepalive subshell can fail on the FIRST iteration even when
    # `sudo -A -v` just succeeded, because sudo's credential cache is
    # tty-scoped. The subshell then `break`s, the keepalive PID dies,
    # and 50s later the script's EXIT trap tries to `kill <dead-PID>`,
    # which returns 1 under set -e — aborting the trap chain BEFORE
    # `_json_finalize_on_exit` runs. Result: no sidecar produced, the
    # bridge raises "snap apply script produced no sidecar". See
    # HANDOFF.md Sesja 68 for the full trace + Ubuntu 24.04 docs on
    # sudo / no-tty / askpass interaction.
    # CRITICAL: redirect the keepalive subshell's stdin/stdout/stderr to
    # /dev/null. When the parent bash is spawned by Python with
    # `subprocess.run(capture_output=True)`, the dashboard reads stdout
    # and stderr via pipes — and Popen.communicate() blocks until ALL
    # writers close those pipes (EOF). Without the redirection here, the
    # keepalive subshell INHERITS the parent's pipe FDs, holds them open,
    # and Python never sees EOF after the main bash exits. Result: the
    # apt apply Python sees the script as "still running" for tens of
    # minutes even though the bash trap finished and the sidecar landed.
    # Detaching the subshell's stdio here also makes the entire chain
    # safer if the script's trap is overwritten (apt apply.sh does this
    # — see _apt_apply_on_exit) since the keepalive can no longer keep
    # the parent pipes alive.
    if [[ -n "${SUDO_ASKPASS:-}" ]]; then
        (while true; do sudo -A -v 2>/dev/null || break; sleep 50; done) </dev/null >/dev/null 2>&1 &
    else
        (while true; do sudo -n true 2>/dev/null || break; sleep 50; done) </dev/null >/dev/null 2>&1 &
    fi
    SUDO_KEEP_ALIVE_PID=$!
    # CRITICAL: chain — do NOT replace. lib/json.sh registers a json_finalize
    # EXIT trap so the sidecar gets written; if we clobber it with a single
    # keepalive-killer trap, the script's exit produces NO sidecar and the
    # bridge raises "no sidecar produced", marking the apply as failed even
    # though every command succeeded. Chain by reading the current EXIT trap
    # body and prepending our killer.
    #
    # The trailing `|| true` is LOAD-BEARING: when set -e is in effect
    # inside the trap and `kill` returns non-zero (PID already gone —
    # the keepalive subshell may have died if its `sudo -n true` failed
    # on a TTY-less spawn), the chain aborts before the existing json
    # finalize trap runs. `|| true` swallows the kill failure locally so
    # the finalize hook always executes. This is the documented bash
    # idiom for cleanup chains under errexit (see Ubuntu / GNU bash
    # docs on EXIT trap + set -e interaction).
    _existing_exit_trap=$(trap -p EXIT 2>/dev/null | sed -E "s/^trap -- '(.*)' EXIT$/\1/" || true)
    if [[ -n "${_existing_exit_trap}" ]]; then
        # shellcheck disable=SC2064  # we WANT eager expansion of $SUDO_KEEP_ALIVE_PID
        trap "kill ${SUDO_KEEP_ALIVE_PID} 2>/dev/null || true; ${_existing_exit_trap}" EXIT
    else
        # shellcheck disable=SC2064
        trap "kill ${SUDO_KEEP_ALIVE_PID} 2>/dev/null || true" EXIT
    fi
    unset _existing_exit_trap
}

# ── Check if a command exists ─────────────────────────────────────────────────
has_cmd() { command -v "$1" &>/dev/null; }

# ── Cross-script project lock ────────────────────────────────────────────────
# Keeps manual runs, systemd timer runs, and bootstrap flows from colliding on
# dpkg/snap/flatpak/brew state or generated inventory files.
acquire_project_lock() {
    local name="${1:-project}"
    local lock_dir="${XDG_RUNTIME_DIR:-/tmp}"
    if [[ ! -d "$lock_dir" || ! -w "$lock_dir" ]]; then
        lock_dir="/tmp"
    fi
    local lock_file="${UPDATE_ALL_LOCK_FILE:-${lock_dir}/ascendo.lock}"

    if [[ "${UPDATE_ALL_LOCK_HELD:-0}" == "1" ]]; then
        _log_raw "INFO" "reusing parent ${name} lock"
        return 0
    fi
    if ! has_cmd flock; then
        print_warn "flock not available — concurrency guard disabled"
        return 0
    fi

    if ! eval "exec {PROJECT_LOCK_FD}>\"\${lock_file}\""; then
        lock_file="/tmp/ascendo.lock"
        eval "exec {PROJECT_LOCK_FD}>\"\${lock_file}\""
    fi
    if ! flock -n "${PROJECT_LOCK_FD}"; then
        print_error "Another Ascendo workflow is already running (${lock_file})"
        print_info "If this is stale, verify no update/bootstrap process is active before removing it."
        exit 75
    fi
    export UPDATE_ALL_LOCK_HELD=1
    _log_raw "INFO" "acquired ${name} lock: ${lock_file}"
}

# ── Summary counters ──────────────────────────────────────────────────────────
SUMMARY_OK=0
SUMMARY_WARN=0
SUMMARY_ERR=0

record_ok()   { SUMMARY_OK=$((SUMMARY_OK + 1));     }
record_warn() { SUMMARY_WARN=$((SUMMARY_WARN + 1)); }
record_err()  { SUMMARY_ERR=$((SUMMARY_ERR + 1));   }

print_summary() {
    local title="${1:-Update Summary}"
    echo
    echo -e "${BOLD}${BLUE}── ${title} ───────────────────────────────${RESET}"
    echo -e "  ${GREEN}✔  OK      : ${SUMMARY_OK}${RESET}"
    [[ $SUMMARY_WARN -gt 0 ]] && echo -e "  ${YELLOW}⚠  Warnings: ${SUMMARY_WARN}${RESET}"
    [[ $SUMMARY_ERR  -gt 0 ]] && echo -e "  ${RED}✘  Errors  : ${SUMMARY_ERR}${RESET}"
    echo -e "  ${DIM}Log       : ${LOG_FILE}${RESET}"
    echo
    _log_raw "INFO" "Summary: OK=${SUMMARY_OK} WARN=${SUMMARY_WARN} ERR=${SUMMARY_ERR}"
}
