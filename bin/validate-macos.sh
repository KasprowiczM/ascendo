#!/usr/bin/env bash
# =============================================================================
# bin/validate-macos.sh -- Ascendo macOS-side validation harness
# =============================================================================
# Run AFTER `bash bin/install-dev-macos.sh`.
#
# Verifies (in order):
#   1. python3 -m ascendo --help / version / doctor all exit 0
#   2. python3 -m ascendo run --category brew --phase {check, plan,
#         apply --dry-run, verify, cleanup --dry-run} each produces a
#         sidecar at the right path with schema=ascendo/v1, phase=<expected>,
#         category=brew
#   3. Dashboard launches in background, /version + /health respond,
#      POST /runs/async + status poll, stopped cleanly.
#
# Exits 0 on full success, 1 with [FAIL] count otherwise.
# Final line on success: ALL CHECKS PASSED.
#
# Flags:
#   --port <N>         dashboard port (default: 8765)
#   --skip-dashboard   skip dashboard tests
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

DASHBOARD_PORT="${DASHBOARD_PORT:-8765}"
SKIP_DASHBOARD=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port)           DASHBOARD_PORT="$2"; shift 2 ;;
        --skip-dashboard) SKIP_DASHBOARD=1; shift ;;
        *) printf "validate-macos.sh: unknown arg: %s\n" "$1" >&2; exit 2 ;;
    esac
done

FAIL_COUNT=0
PASS_COUNT=0

step() { printf "\n==> %s\n" "$1"; }

result() {
    local name="$1"
    local ok="$2"
    local detail="${3:-}"
    if [ "$ok" = "1" ]; then
        printf "  [PASS] %s\n" "$name"
        if [ -n "$detail" ]; then
            printf "         %s\n" "$detail"
        fi
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        printf "  [FAIL] %s\n" "$name" >&2
        if [ -n "$detail" ]; then
            printf "         %s\n" "$detail" >&2
        fi
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

# ── 1. CLI ────────────────────────────────────────────────────────────────────
step "1. CLI checks"

if python3 -m ascendo --help >/dev/null 2>&1; then
    result "ascendo --help" 1
else
    result "ascendo --help" 0 "exit $?"
fi

if out=$(python3 -m ascendo version 2>&1); then
    result "ascendo version" 1 "$out"
else
    result "ascendo version" 0 "$out"
fi

if out=$(python3 -m ascendo doctor 2>&1); then
    result "ascendo doctor" 1 "$(printf '%s' "$out" | head -3)"
else
    result "ascendo doctor" 0 "$(printf '%s' "$out" | head -5)"
fi

# ── 2. Five-phase brew contract ───────────────────────────────────────────────
step "2. Five-phase brew contract"
RUNS_DIR=$(mktemp -d -t ascendo_validate_XXXXXX)
export PYTHONPATH="$REPO_ROOT/core"

phase_check() {
    local phase="$1"
    shift
    local extra_args="$*"
    local out rc sidecar sc_phase sc_cat sc_schema

    # Build the command; --dry-run must be passed as a positional arg here
    if [ -n "$extra_args" ]; then
        out=$(python3 -m ascendo run --category brew --phase "$phase" \
              --runs-dir "$RUNS_DIR" $extra_args 2>&1)
        rc=$?
    else
        out=$(python3 -m ascendo run --category brew --phase "$phase" \
              --runs-dir "$RUNS_DIR" 2>&1)
        rc=$?
    fi

    if [ $rc -ne 0 ]; then
        result "brew/$phase" 0 "$(printf '%s' "$out" | tail -10)"
        return
    fi

    # Find the sidecar for this phase
    sidecar=$(find "$RUNS_DIR" -name "${phase}__brew.json" -type f 2>/dev/null | head -1)
    if [ -z "$sidecar" ] || [ ! -f "$sidecar" ]; then
        result "brew/$phase" 0 "no sidecar found in $RUNS_DIR"
        return
    fi

    sc_phase=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['phase'])" 2>/dev/null)
    sc_cat=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['category'])" 2>/dev/null)
    sc_schema=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['schema'])" 2>/dev/null)

    if [ "$sc_phase" = "$phase" ] && [ "$sc_cat" = "brew" ] && [ "$sc_schema" = "ascendo/v1" ]; then
        result "brew/$phase" 1 "sidecar=$(basename "$sidecar")"
    else
        result "brew/$phase" 0 \
            "sidecar shape wrong: phase=$sc_phase category=$sc_cat schema=$sc_schema"
    fi
}

phase_check check
phase_check plan
phase_check apply --dry-run
phase_check verify
phase_check cleanup --dry-run

# ── 3. Dashboard ──────────────────────────────────────────────────────────────
if [ "$SKIP_DASHBOARD" -eq 0 ]; then
    step "3. Dashboard"
    LOG=$(mktemp -t ascendo_dash_XXXXXX)

    # Start dashboard in background
    python3 -m ascendo dashboard --port "$DASHBOARD_PORT" >"$LOG" 2>&1 &
    DASH_PID=$!

    # Wait up to 10s for dashboard to bind
    _i=0
    while [ $_i -lt 10 ]; do
        if curl -sf "http://127.0.0.1:$DASHBOARD_PORT/version" >/dev/null 2>&1; then
            break
        fi
        sleep 1
        _i=$((_i + 1))
    done

    if curl -sf "http://127.0.0.1:$DASHBOARD_PORT/version" >/dev/null 2>&1; then
        result "GET /version" 1
    else
        result "GET /version" 0 "$(tail -10 "$LOG")"
    fi

    if curl -sf "http://127.0.0.1:$DASHBOARD_PORT/health" >/dev/null 2>&1; then
        result "GET /health" 1
    else
        result "GET /health" 0 "$(tail -5 "$LOG")"
    fi

    # POST /runs/async -- kick a check phase run, poll status until completed
    ASYNC_BODY='{"phases":["check"],"categories":["brew"]}'
    RUN_ID=$(curl -sf -X POST "http://127.0.0.1:$DASHBOARD_PORT/runs/async" \
                 -H "Content-Type: application/json" -d "$ASYNC_BODY" 2>/dev/null \
             | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])" 2>/dev/null)

    if [ -z "$RUN_ID" ]; then
        result "POST /runs/async" 0 "could not parse run_id from response"
    else
        # Poll status up to 60s
        ASYNC_STATUS=""
        _j=0
        while [ $_j -lt 60 ]; do
            ASYNC_STATUS=$(curl -sf \
                "http://127.0.0.1:$DASHBOARD_PORT/runs/$RUN_ID/status" 2>/dev/null \
                | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" 2>/dev/null)
            if [ "$ASYNC_STATUS" = "completed" ] || [ "$ASYNC_STATUS" = "failed" ]; then
                break
            fi
            sleep 1
            _j=$((_j + 1))
        done

        if [ "$ASYNC_STATUS" = "completed" ]; then
            result "POST /runs/async + poll status" 1 "run_id=$RUN_ID"
        else
            result "POST /runs/async + poll status" 0 "final status=$ASYNC_STATUS"
        fi
    fi

    # Tear down dashboard
    kill "$DASH_PID" 2>/dev/null || true
    wait "$DASH_PID" 2>/dev/null || true
    rm -f "$LOG"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
printf "\n"
if [ "$FAIL_COUNT" -eq 0 ]; then
    printf "ALL CHECKS PASSED. (%d/%d)\n" "$PASS_COUNT" "$PASS_COUNT"
    exit 0
else
    printf "FAILED %d / %d checks.\n" "$FAIL_COUNT" "$((PASS_COUNT + FAIL_COUNT))" >&2
    exit 1
fi
