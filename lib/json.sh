#!/usr/bin/env bash
# =============================================================================
# lib/json.sh — Phase-result JSON sidecar emitter (schema v1)
#
# Usage:
#   source lib/json.sh
#   json_init <kind> <category>          # kind: check|plan|apply|verify|cleanup
#   json_add_item id=foo action=upgrade result=ok [from=1 to=2 duration_ms=80]
#   json_add_diag warn CODE-NAME "human readable message"
#   json_count_ok / json_count_warn / json_count_err [N]
#   json_set_needs_reboot 1
#   json_finalize <exit_code> <out_path>
#
# JSON_OUT env (when set, json_finalize-on-trap auto-writes to it). See
# `_json_finalize_on_exit` below.
# =============================================================================

# shellcheck disable=SC2155
[[ -z "${LIB_JSON_DIR:-}" ]] && LIB_JSON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_JSON_EMIT="${LIB_JSON_DIR}/_json_emit.py"

if ! command -v python3 >/dev/null 2>&1; then
    echo "lib/json.sh: python3 is required for JSON sidecar emission" >&2
    return 1 2>/dev/null || exit 1
fi

if [[ ! -f "${_JSON_EMIT}" ]]; then
    echo "lib/json.sh: missing helper ${_JSON_EMIT}" >&2
    return 1 2>/dev/null || exit 1
fi

JSON_BUFDIR=""
JSON_KIND=""
JSON_CATEGORY=""
JSON_OUT_PATH=""
JSON_FINALIZED=0

_json_now_utc() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

json_init() {
    local kind="$1" category="$2"
    if [[ -z "$kind" || -z "$category" ]]; then
        echo "json_init: usage: json_init <kind> <category>" >&2
        return 2
    fi
    JSON_KIND="$kind"
    JSON_CATEGORY="$category"
    # If the orchestrator pre-created a bufdir and exported it (passing
    # `JSON_BUFDIR=/path` in the child env), honor it. This lets Python
    # salvage partial state if the bash script dies mid-run without
    # firing its EXIT trap — finalize manually from the surviving
    # bufdir contents. Otherwise allocate a fresh temp dir as before.
    if [[ -z "${JSON_BUFDIR:-}" ]]; then
        JSON_BUFDIR="$(mktemp -d -t ua-json-XXXXXX)"
    else
        mkdir -p "${JSON_BUFDIR}"
    fi
    JSON_FINALIZED=0
    local started_at; started_at="$(_json_now_utc)"
    local host; host="$(hostname 2>/dev/null || echo unknown)"
    python3 "${_JSON_EMIT}" init \
        --bufdir "${JSON_BUFDIR}" \
        --kind "${kind}" \
        --category "${category}" \
        --host "${host}" \
        --started-at "${started_at}" \
        ${LOG_FILE:+--log-path "${LOG_FILE}"}
    export JSON_BUFDIR JSON_KIND JSON_CATEGORY
}

# json_add_item id=foo action=upgrade result=ok [from=...] [to=...]
#               [duration_ms=N] [details="..."]
json_add_item() {
    [[ -z "${JSON_BUFDIR}" ]] && return 0
    local args=(--bufdir "${JSON_BUFDIR}")
    local kv key val
    for kv in "$@"; do
        key="${kv%%=*}"
        val="${kv#*=}"
        case "$key" in
            id)          args+=(--id "$val") ;;
            action)      args+=(--action "$val") ;;
            result)      args+=(--result "$val") ;;
            from)        args+=(--from "$val") ;;
            to)          args+=(--to "$val") ;;
            duration_ms) args+=(--duration-ms "$val") ;;
            details)     args+=(--details "$val") ;;
            *) echo "json_add_item: unknown key: $key" >&2; return 2 ;;
        esac
    done
    python3 "${_JSON_EMIT}" add-item "${args[@]}"
}

# json_add_diag <level> <code> <msg...>
json_add_diag() {
    [[ -z "${JSON_BUFDIR}" ]] && return 0
    local level="$1" code="$2"; shift 2 || true
    local msg="$*"
    python3 "${_JSON_EMIT}" add-diag \
        --bufdir "${JSON_BUFDIR}" \
        --level "${level}" \
        --code "${code}" \
        --msg "${msg}"
}

json_add_advisory() {
    [[ -z "${JSON_BUFDIR}" ]] && return 0
    local msg="$*"
    python3 "${_JSON_EMIT}" add-advisory \
        --bufdir "${JSON_BUFDIR}" \
        --msg "${msg}"
}

json_count_ok()   { [[ -z "${JSON_BUFDIR}" ]] && return 0; python3 "${_JSON_EMIT}" count --bufdir "${JSON_BUFDIR}" --bucket ok   --n "${1:-1}"; }
json_count_warn() { [[ -z "${JSON_BUFDIR}" ]] && return 0; python3 "${_JSON_EMIT}" count --bufdir "${JSON_BUFDIR}" --bucket warn --n "${1:-1}"; }
json_count_err()  { [[ -z "${JSON_BUFDIR}" ]] && return 0; python3 "${_JSON_EMIT}" count --bufdir "${JSON_BUFDIR}" --bucket err  --n "${1:-1}"; }

json_set_needs_reboot() {
    [[ -z "${JSON_BUFDIR}" ]] && return 0
    python3 "${_JSON_EMIT}" set-flag \
        --bufdir "${JSON_BUFDIR}" \
        --key needs_reboot \
        --value "${1:-0}"
}

json_finalize() {
    [[ -z "${JSON_BUFDIR}" ]] && return 0
    [[ "${JSON_FINALIZED}" == "1" ]] && return 0
    local exit_code="${1:-0}" out_path="${2:-${JSON_OUT_PATH:-}}"
    if [[ -z "${out_path}" ]]; then
        rm -rf "${JSON_BUFDIR}"
        JSON_BUFDIR=""
        JSON_FINALIZED=1
        return 0
    fi
    local ended_at; ended_at="$(_json_now_utc)"
    python3 "${_JSON_EMIT}" finalize \
        --bufdir "${JSON_BUFDIR}" \
        --out "${out_path}" \
        --exit-code "${exit_code}" \
        --ended-at "${ended_at}"
    rm -rf "${JSON_BUFDIR}"
    JSON_BUFDIR=""
    JSON_FINALIZED=1
}

# Convenience: register an EXIT trap that finalizes with the script's exit code
# when JSON_OUT is set (used by phase scripts).
json_register_exit_trap() {
    JSON_OUT_PATH="${1:-${JSON_OUT:-}}"
    [[ -z "${JSON_OUT_PATH}" ]] && return 0
    trap '_json_finalize_on_exit $?' EXIT
    # Also catch INT (Ctrl+C) and TERM (kill from orchestrator on shutdown)
    # so an interrupted apply still produces a sidecar with status=failed
    # and a diagnostic message — orchestrator-side write_sidecar then has
    # something to read instead of "missing sidecar" errors.
    trap '_json_interrupt_handler INT'  INT
    trap '_json_interrupt_handler TERM' TERM
}

# Handle SIGINT/SIGTERM by adding a diagnostic + finalizing with the
# canonical exit code for "interrupted" (130 for INT, 143 for TERM per
# POSIX), then re-raising the signal so the parent's wait sees the
# real exit status. The EXIT trap registered above will NOT fire after
# this because we exit explicitly with the chosen code.
_json_interrupt_handler() {
    local sig="$1"
    local rc=130
    [[ "$sig" = "TERM" ]] && rc=143
    if [[ -n "${JSON_BUFDIR:-}" && -d "${JSON_BUFDIR}" ]]; then
        json_add_diag warn ASCENDO-INTERRUPTED \
            "phase script received SIG${sig}; partial sidecar saved" \
            2>/dev/null || true
    fi
    json_finalize "$rc" "${JSON_OUT_PATH}" 2>/dev/null || true
    JSON_FINALIZED=1
    # Reset trap and re-raise so the parent process sees the right
    # exit status (130/143).
    trap - "$sig" EXIT
    kill -"$sig" $$
}

_json_finalize_on_exit() {
    local rc="${1:-0}"
    json_finalize "${rc}" "${JSON_OUT_PATH}"
}

# =============================================================================
# Live-stream helpers (Linux parity with macOS adapters/macos/lib/ascendo_json.sh)
# =============================================================================
# When the orchestrator (Python or bash) sets ASCENDO_STREAM_LOG, every apply
# script can pipe mutating commands through `_stream_tee` so the dashboard's
# SSE endpoint streams every line live to Run Center. If the env var is unset
# (CLI-only runs), these helpers degrade to no-ops or pass-through.
#
# Usage in apply scripts:
#     sudo apt-get -y upgrade 2>&1 | _stream_tee >/dev/null
#     _stream_emit ">>> upgrading firefox"
#     _stream_progress 42 "apt: 4 of 9 pkgs"

# _stream_tee — append every stdin line to $ASCENDO_STREAM_LOG and pass through.
# When ASCENDO_STREAM_LOG is unset/empty, behaves like ``cat`` (no-op pipe).
_stream_tee() {
    if [[ -n "${ASCENDO_STREAM_LOG:-}" ]]; then
        # tee can fail (disk full, permission); fall back to cat so the
        # caller's pipeline doesn't break.
        tee -a "${ASCENDO_STREAM_LOG}" 2>/dev/null || cat
    else
        cat
    fi
}

# _stream_emit <line> — write a single sentinel/marker into the stream log.
# Useful for ">>> running brew upgrade foo" headers in the SSE feed. No-op
# when ASCENDO_STREAM_LOG is unset.
_stream_emit() {
    if [[ -n "${ASCENDO_STREAM_LOG:-}" ]]; then
        printf '%s\n' "$*" >> "${ASCENDO_STREAM_LOG}" 2>/dev/null || true
    fi
}

# _stream_progress <pct> <label> — emit a >>> PROGRESS pct label sentinel
# that the SPA's SSE consumer renders as a percentage bar + label line.
_stream_progress() {
    local _pct="${1:-0}"
    shift || true
    local _label="$*"
    _stream_emit ">>> PROGRESS ${_pct} ${_label}"
}

# =============================================================================
# Output-tail capture helpers — surface failure context to the sidecar
# =============================================================================
# Linux apply scripts historically pipe combined output to LOG_FILE via
# `run_silent`. When a command fails the operator only sees the LOG_FILE
# path in the SPA — not the actual error. These helpers capture combined
# stdout+stderr to a tempfile, route the body to LOG_FILE, and (on failure)
# emit the last 12 non-empty lines as a sidecar diagnostic so the operator
# sees apt's "E: Could not get lock" / npm's "EACCES" / brew's "Error:"
# message inline in Run Center.
#
# Usage:
#     run_capture sudo apt-get -y upgrade
#     local rc=$?
#     if [[ $rc -ne 0 ]]; then
#         json_add_diag error APT-UPGRADE-FAIL "$(_stderr_tail "$_LAST_RUN_OUT_FILE")"
#     fi
#
# Or higher-level: run_silent_with_tail emits the diagnostic for you.

# _stderr_tail <path> [maxlines] [maxchars]
# Echoes the last MAXLINES non-empty lines from PATH, capped at MAXCHARS.
# Empty when path missing / unreadable. Mirrors the macOS Sesja 34 12-line /
# 1500-char convention.
_stderr_tail() {
    local _path="$1"
    local _maxlines="${2:-12}"
    local _maxchars="${3:-1500}"
    [[ -z "$_path" || ! -f "$_path" ]] && return 0
    local _t
    _t=$(awk 'NF{print}' "$_path" 2>/dev/null | tail -n "$_maxlines" \
         | tr '\t' ' ' | head -c "$_maxchars" || true)
    printf '%s' "${_t}"
}

# run_capture <cmd...>  — like run_silent, but exposes the captured combined
# output via the global $_LAST_RUN_OUT_FILE so the caller can read its tail
# on failure. Returns the command's exit code. Output is also appended to
# LOG_FILE (preserving the run_silent contract) and tee'd through
# _stream_tee so the SSE feed shows it live.
_LAST_RUN_OUT_FILE=""
run_capture() {
    local cmd=("$@")
    if declare -F _log_raw >/dev/null 2>&1; then
        _log_raw "RUN " "${cmd[*]}"
    fi
    # Clean up any previous tempfile (best-effort).
    [[ -n "${_LAST_RUN_OUT_FILE:-}" && -f "${_LAST_RUN_OUT_FILE}" ]] \
        && rm -f "${_LAST_RUN_OUT_FILE}" 2>/dev/null
    _LAST_RUN_OUT_FILE="$(mktemp 2>/dev/null \
        || mktemp -t ascendo-run-capture 2>/dev/null \
        || echo "/tmp/ascendo-run-capture.$$.$RANDOM")"
    local rc=0
    "${cmd[@]}" 2>&1 | tee "${_LAST_RUN_OUT_FILE}" | _stream_tee >/dev/null
    rc="${PIPESTATUS[0]}"
    if [[ -s "${_LAST_RUN_OUT_FILE}" && -n "${LOG_FILE:-}" ]]; then
        cat "${_LAST_RUN_OUT_FILE}" >> "${LOG_FILE}" 2>/dev/null || true
    fi
    return "${rc}"
}

# run_silent_with_tail <code> <cmd...>
# Wraps run_capture, and on non-zero exit emits a sidecar diagnostic
# carrying the last 12 lines of combined output. <code> is the diagnostic
# code (e.g. NPM-INSTALL-FAIL). Returns the command's exit code. Caller can
# still inspect $_LAST_RUN_OUT_FILE for further details.
run_silent_with_tail() {
    local _code="$1"; shift || true
    run_capture "$@"
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        local _tail; _tail="$(_stderr_tail "${_LAST_RUN_OUT_FILE}")"
        if [[ -n "$_tail" ]]; then
            json_add_diag error "${_code}" \
                "exited ${rc} — last output: ${_tail}"
        else
            json_add_diag error "${_code}" \
                "exited ${rc} (no output captured)"
        fi
    fi
    return "${rc}"
}
