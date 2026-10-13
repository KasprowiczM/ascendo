#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_proc.sh -- bounded process runner (bash side)
# =============================================================================
# Sourced by ascendo_json.sh (so every phase script has it), and directly by
# ascendo_npm.sh / ascendo_web.sh (so their unit tests can source them alone).
# Idempotent. Bash 3.2 compatible.
#
#   ascendo_run_with_timeout SECONDS CMD [ARGS...]
#
#     Runs CMD, killing it when SECONDS elapse. SECONDS must be a positive
#     integer. Returns the child's exit code, or 124 on timeout (same as GNU
#     timeout). Never call it on a shell function -- only on executables.
#
#     * stdin of the child is /dev/null unless the caller exports
#       ASCENDO_RUN_KEEP_STDIN=1 (a TTY-less dashboard run must never block on
#       a prompt). In the process-group fallback the child inherits whatever
#       stdin this function received.
#     * GNU `timeout` / `gtimeout` are used when present, with
#       --kill-after=$ASCENDO_KILL_AFTER (default 5 s).
#     * Otherwise (the common case on stock macOS) the child is started in its
#       OWN process group (python3 os.setpgrp wrapper) and on the deadline the
#       whole GROUP gets SIGTERM, then SIGKILL after the kill-after grace, so a
#       TERM-ignoring child and its grandchildren cannot outlive the deadline.
#     * A 137 (SIGKILL) exit is reported as 124 only when the deadline had
#       really been reached; an unrelated `kill -9` stays 137.
#
#   ascendo_app_running_state BUNDLE_ID
#
#     Tri-state "is this app running?" probe -- prints exactly one of
#     true | false | unknown (always exit 0). Built on `lsappinfo list`.
#     `unknown` = anything short of an explicit answer: lsappinfo missing,
#     non-zero exit, timeout, empty listing (a real session always lists at
#     least the Finder/Dock), or an invalid bundle id. Every gate that
#     copies, swaps or upgrades an app bundle must proceed ONLY on `false`
#     (fail closed): `unknown` is treated as "running" and the action is
#     deferred. Never quit or kill an app on the strength of this probe.
#     Test seam: ASCENDO_LSAPPINFO_BIN (path to an lsappinfo stand-in).
#
#   Env knobs: ASCENDO_KILL_AFTER (seconds, default 5),
#              ASCENDO_RUN_KEEP_STDIN (1 = do not detach stdin),
#              ASCENDO_FORCE_TIMEOUT_FALLBACK (1 = skip timeout/gtimeout;
#              used by tests to exercise the process-group path).
# =============================================================================
# shellcheck shell=bash

if [ -n "${_ASCENDO_PROC_LOADED:-}" ]; then
    return 0 2>/dev/null || true
fi
_ASCENDO_PROC_LOADED=1

# Public entry: decides the child's stdin, then delegates.
ascendo_run_with_timeout() {
    if [ "${ASCENDO_RUN_KEEP_STDIN:-0}" = "1" ]; then
        _ascendo_run_with_timeout_impl "$@"
    else
        _ascendo_run_with_timeout_impl "$@" </dev/null
    fi
}

# Map a GNU timeout exit code: 124 stays; 137 -> 124 only if deadline reached.
_ascendo_proc_map_rc() {
    local rc="$1" elapsed="$2" seconds="$3"
    if [ "$rc" -eq 124 ]; then
        return 124
    fi
    if [ "$rc" -eq 137 ] && [ "$elapsed" -ge "$seconds" ]; then
        return 124
    fi
    return "$rc"
}

_ascendo_run_with_timeout_impl() {
    local seconds="$1" grace="${ASCENDO_KILL_AFTER:-5}"
    local started_at command_exit elapsed bin pid ticks max_ticks grace_ticks
    if [ "$#" -lt 2 ]; then
        echo "ascendo_run_with_timeout: usage: SECONDS CMD [ARGS...]" >&2
        return 2
    fi
    case "$seconds" in
        ''|*[!0-9]*) echo "ascendo_run_with_timeout: SECONDS must be an integer (got '$seconds')" >&2
                     return 2 ;;
    esac
    case "$grace" in
        ''|*[!0-9]*) grace=5 ;;
    esac
    shift

    if [ "${ASCENDO_FORCE_TIMEOUT_FALLBACK:-0}" != "1" ]; then
        bin=""
        if command -v timeout >/dev/null 2>&1; then
            bin=timeout
        elif command -v gtimeout >/dev/null 2>&1; then
            bin=gtimeout
        fi
        if [ -n "$bin" ]; then
            started_at=$SECONDS
            command_exit=0
            "$bin" --kill-after="$grace" "$seconds" "$@" || command_exit=$?
            elapsed=$(( SECONDS - started_at ))
            _ascendo_proc_map_rc "$command_exit" "$elapsed" "$seconds"
            return $?
        fi
    fi

    # ---- Fallback: own process group + group kill --------------------------
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import os, sys
os.setpgrp()
os.execvp(sys.argv[1], sys.argv[1:])
' "$@" <&0 &
    else
        "$@" <&0 &
    fi
    pid=$!

    # 0.2 s ticks: deadline is measured in ticks so it can never fire early.
    max_ticks=$(( seconds * 5 ))
    grace_ticks=$(( grace * 5 ))
    ticks=0
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$ticks" -ge "$max_ticks" ]; then
            # SIGTERM the whole group (leader + grandchildren); fall back to the
            # bare pid when no group exists (python3 missing).
            kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
            ticks=0
            while kill -0 "$pid" 2>/dev/null && [ "$ticks" -lt "$grace_ticks" ]; do
                sleep 0.2
                ticks=$(( ticks + 1 ))
            done
            # Unconditional group KILL: reaps TERM-ignoring children and any
            # grandchild that outlived a leader that exited on TERM.
            kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            return 124
        fi
        sleep 0.2
        ticks=$(( ticks + 1 ))
    done
    command_exit=0
    wait "$pid" || command_exit=$?
    return "$command_exit"
}

# Tri-state running probe (see header). Bundle ids are [A-Za-z0-9._-] only,
# which also keeps them safe as a grep -F needle.
ascendo_app_running_state() {
    local id="${1:-}" bin out
    case "$id" in
        ''|*[!A-Za-z0-9._-]*) printf 'unknown\n'; return 0 ;;
    esac
    bin="${ASCENDO_LSAPPINFO_BIN:-}"
    if [ -z "$bin" ]; then
        bin="$(command -v lsappinfo 2>/dev/null)" || bin=""
    fi
    if [ -z "$bin" ] || [ ! -x "$bin" ]; then
        printf 'unknown\n'
        return 0
    fi
    if ! out="$(ascendo_run_with_timeout 10 "$bin" list 2>/dev/null)"; then
        printf 'unknown\n'
        return 0
    fi
    if [ -z "$out" ]; then
        printf 'unknown\n'
        return 0
    fi
    # Here-string, not a pipe: under `set -o pipefail` an early-exiting
    # `grep -q` would SIGPIPE the writer of a large listing and read as false.
    if /usr/bin/grep -Fq "bundleID=\"$id\"" <<<"$out"; then
        printf 'true\n'
    else
        printf 'false\n'
    fi
    return 0
}
