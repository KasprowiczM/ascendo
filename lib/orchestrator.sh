#!/usr/bin/env bash
# =============================================================================
# lib/orchestrator.sh — Run phase scripts with sidecar JSON & per-run logs
#
# Usage from update-all.sh / setup.sh:
#   source lib/orchestrator.sh
#   orch_init "<run-id>"
#   orch_run_phase apt   check
#   orch_run_phase apt   apply
#   ...
#   orch_summary
#
# Layout per run:
#   logs/runs/<run-id>/
#     run.json                    — orchestrator-level summary
#     <category>/<phase>.json     — phase sidecar (schema v1)
#     <category>/<phase>.log      — phase plaintext log
# =============================================================================

# shellcheck disable=SC2155
[[ -z "${LIB_ORCH_DIR:-}" ]] && LIB_ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -z "${SCRIPT_DIR:-}" ]] && SCRIPT_DIR="$(cd "${LIB_ORCH_DIR}/.." && pwd)"

# Reuse common helpers if already sourced; otherwise source defensively.
if ! declare -f print_info >/dev/null 2>&1; then
    # shellcheck source=lib/common.sh
    source "${LIB_ORCH_DIR}/common.sh"
fi

ORCH_RUN_ID=""
ORCH_RUN_DIR=""
ORCH_DRY_RUN=0
ORCH_PROFILE=""

declare -A ORCH_STATUS=()
declare -A ORCH_DURATION=()
declare -A ORCH_NEEDS_REBOOT=()
ORCH_FAILED=0
ORCH_WARNED=0

orch_init() {
    local run_id="${1:-${ORCH_RUN_ID:-}}"
    if [[ -z "$run_id" ]]; then
        run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
    fi
    ORCH_RUN_ID="$run_id"
    ORCH_RUN_DIR="${SCRIPT_DIR}/logs/runs/${run_id}"
    mkdir -p "${ORCH_RUN_DIR}"
    export ORCH_RUN_ID ORCH_RUN_DIR
    _log_raw "INFO" "orch_init run_id=${run_id} dir=${ORCH_RUN_DIR}"
}

orch_set_dry_run()  { ORCH_DRY_RUN="${1:-0}"; export ORCH_DRY_RUN; }
orch_set_profile()  { ORCH_PROFILE="${1:-}";  export ORCH_PROFILE; }

# orch_run_phase <category> <phase> [extra-args...]
orch_run_phase() {
    local category="$1" phase="$2"; shift 2 || true
    local script="${SCRIPT_DIR}/scripts/${category}/${phase}.sh"
    local cat_dir="${ORCH_RUN_DIR}/${category}"
    mkdir -p "$cat_dir"
    local json_out="${cat_dir}/${phase}.json"
    local log_out="${cat_dir}/${phase}.log"
    local key="${category}/${phase}"

    if [[ ! -x "$script" && ! -f "$script" ]]; then
        # Allow phase to be missing: emit a "skipped" sidecar synthetically.
        _orch_emit_skipped "$category" "$phase" "$json_out" "no script: ${script}"
        ORCH_STATUS[$key]="missing"
        return 0
    fi

    echo -e "\n${BOLD}${BLUE}── ${category}:${phase} ──${RESET}"
    _log_raw "INFO" "orch run ${category}:${phase} -> ${json_out}"

    local start_ts; start_ts=$(date +%s)
    local rc=0

    if [[ "$ORCH_DRY_RUN" == "1" && "$phase" != "check" && "$phase" != "plan" ]]; then
        _orch_emit_skipped "$category" "$phase" "$json_out" "dry-run"
        ORCH_STATUS[$key]="dry-run"
        ORCH_DURATION[$key]=0
        return 0
    fi

    # Live-tee phase script output to both terminal and per-phase log so the
    # operator sees real-time progress. Each line is also indented for
    # readability inside the master pipeline view. Set ORCH_QUIET=1 to fall
    # back to log-file-only (used by dashboard runner where SSE captures
    # the master stream).
    if [[ "${ORCH_QUIET:-0}" == "1" ]]; then
        (
            export JSON_OUT="${json_out}"
            export LOG_FILE="${log_out}"
            export ORCH_RUN_ID ORCH_RUN_DIR ORCH_DRY_RUN ORCH_PROFILE
            bash "$script" "$@"
        ) >>"$log_out" 2>&1 || rc=$?
    else
        set +e
        (
            export JSON_OUT="${json_out}"
            export LOG_FILE="${log_out}"
            export ORCH_RUN_ID ORCH_RUN_DIR ORCH_DRY_RUN ORCH_PROFILE
            bash "$script" "$@"
        ) 2>&1 | tee -a "$log_out"
        rc=${PIPESTATUS[0]}
        set -e
    fi

    local end_ts; end_ts=$(date +%s)
    ORCH_DURATION[$key]=$(( end_ts - start_ts ))

    # Defensive: a phase script that exits 0 but produces no sidecar means a
    # silent failure (typically: a downstream `trap EXIT` clobbered the JSON
    # finalize trap). Synthesize an error sidecar and treat as failed so the
    # operator sees it instead of "all green" when the work didn't happen.
    if [[ ! -f "$json_out" ]]; then
        _orch_emit_skipped "$category" "$phase" "$json_out" \
            "phase produced no JSON sidecar (exit ${rc}); see ${log_out}"
        # Mark sidecar as error so summary status reflects reality.
        python3 - "$json_out" <<'PY' || true
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except Exception:
    sys.exit(0)
d["exit_code"] = 30
d["summary"] = {"ok": 0, "warn": 0, "err": 1}
d.setdefault("diagnostics", []).append({
    "level": "error",
    "code": "PHASE-NO-SIDECAR",
    "msg": "phase script exited without writing a JSON sidecar — likely a trap-override bug or early die",
})
with open(p + ".partial", "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
import os
os.replace(p + ".partial", p)
PY
        # Force fail status so the run is not reported as ok.
        rc=30
    fi

    # Classify exit code
    local status="ok"
    case "$rc" in
        0)   status="ok" ;;
        1)   status="warn" ;;
        2|10|11|12) status="failed" ;;
        20|30)      status="critical" ;;
        75)  status="locked" ;;
        *)   status="failed" ;;
    esac
    ORCH_STATUS[$key]="$status"

    # Read needs_reboot from sidecar if present
    if [[ -f "$json_out" ]]; then
        local nr
        nr=$(python3 -c "import json,sys
try:
    d=json.load(open('$json_out'))
    print('1' if d.get('needs_reboot') else '0')
except Exception:
    print('0')" 2>/dev/null || echo 0)
        ORCH_NEEDS_REBOOT[$key]="$nr"
    else
        ORCH_NEEDS_REBOOT[$key]=0
    fi

    case "$status" in
        ok)        echo -e "  ${GREEN}✔${RESET}  ${category}:${phase} (${ORCH_DURATION[$key]}s)" ;;
        warn)      echo -e "  ${YELLOW}⚠${RESET}  ${category}:${phase} (${ORCH_DURATION[$key]}s)"; ORCH_WARNED=1 ;;
        failed|critical|locked)
                   echo -e "  ${RED}✘${RESET}  ${category}:${phase} (${ORCH_DURATION[$key]}s, exit ${rc})"
                   ORCH_FAILED=1 ;;
    esac

    return "$rc"
}

_orch_emit_skipped() {
    local category="$1" phase="$2" out="$3" reason="$4"
    mkdir -p "$(dirname "$out")"
    python3 - "$out" "$category" "$phase" "$reason" <<'PY'
import json, os, sys
from datetime import datetime, timezone
out, category, phase, reason = sys.argv[1:5]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
obj = {
    "schema": "ascendo/v1",
    "kind": phase,
    "category": category,
    "host": os.uname().nodename,
    "started_at": now,
    "ended_at": now,
    "exit_code": 0,
    "summary": {"ok": 0, "warn": 0, "err": 0},
    "items": [],
    "diagnostics": [{"level": "info", "code": "PHASE-SKIPPED", "msg": reason}],
    "log_path": None,
    "needs_reboot": False,
}
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
with open(out + ".partial", "w", encoding="utf-8") as f:
    json.dump(obj, f, ensure_ascii=False, indent=2)
os.replace(out + ".partial", out)
PY
}

orch_summary() {
    local run_json="${ORCH_RUN_DIR}/run.json"
    local needs_reboot=0
    local key
    for key in "${!ORCH_NEEDS_REBOOT[@]}"; do
        [[ "${ORCH_NEEDS_REBOOT[$key]}" == "1" ]] && needs_reboot=1
    done
    [[ -f /var/run/reboot-required ]] && needs_reboot=1

    python3 - "$run_json" "$ORCH_RUN_ID" "$ORCH_FAILED" "$ORCH_WARNED" "$needs_reboot" <<'PY' || true
import glob, json, os, sys
from datetime import datetime, timezone

run_json, run_id, failed, warned, needs_reboot = sys.argv[1:6]
run_dir = os.path.dirname(run_json)
phases = []
for path in sorted(glob.glob(os.path.join(run_dir, "*", "*.json"))):
    if os.path.basename(path) == "run.json":
        continue
    try:
        with open(path) as f:
            d = json.load(f)
        phases.append({
            "category": d.get("category"),
            "kind": d.get("kind"),
            "exit_code": d.get("exit_code"),
            "summary": d.get("summary"),
            "needs_reboot": d.get("needs_reboot", False),
            "json": os.path.relpath(path, run_dir),
        })
    except Exception as exc:
        phases.append({"json": os.path.relpath(path, run_dir), "error": str(exc)})

status = "failed" if int(failed) else ("warn" if int(warned) else "ok")
out = {
    "schema": "ascendo/run/v1",
    "run_id": run_id,
    "ended_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "status": status,
    "needs_reboot": bool(int(needs_reboot)),
    "phases": phases,
}
with open(run_json + ".partial", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
os.replace(run_json + ".partial", run_json)
PY

    echo
    echo -e "${BOLD}${BLUE}── Run summary (${ORCH_RUN_ID}) ──${RESET}"
    local k
    for k in "${!ORCH_STATUS[@]}"; do
        printf '  %-22s %s (%ss)\n' "$k" "${ORCH_STATUS[$k]}" "${ORCH_DURATION[$k]:-0}"
    done | sort
    echo -e "${DIM}Run dir: ${ORCH_RUN_DIR}${RESET}"
    if [[ "$needs_reboot" == "1" ]]; then
        echo -e "${YELLOW}⚠  Reboot required${RESET}"
    fi
    if [[ "$ORCH_FAILED" == "1" ]]; then
        return 1
    fi
    return 0
}
