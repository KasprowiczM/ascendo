#!/usr/bin/env bash
# =============================================================================
# bin/validate-macos.sh -- Ascendo macOS-side validation harness
# =============================================================================
# Run AFTER `bash bin/install-dev-macos.sh`.
#
# Verifies (in order):
#   1. python3 -m ascendo --help / version / doctor all exit 0
#   2. Five-phase brew contract: check / plan / apply --dry-run / verify /
#      cleanup --dry-run each produces a sidecar with schema=ascendo/v1,
#      phase=<expected>, category=brew
#   3. Dashboard smoke: /version + /health, POST /runs/async + status poll,
#      clean teardown
#   4-7. (reserved for future stages)
#   8. mas + dashboard askpass round-trip (M5.2): doctor reports mas,
#      five-phase mas contract, /elevation/auth + /elevation/status cycle
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
export PYTHONPATH="$REPO_ROOT/adapters/macos:$REPO_ROOT/core"

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

# ── 8. mas + dashboard askpass round-trip ─────────────────────────────────────
step "8. mas + dashboard askpass (M5.2)"

# 8.1 doctor reports mas component
step "8.1 doctor reports mas component"
DOCTOR_OUT=$(python3 -m ascendo doctor 2>&1)
if printf '%s' "$DOCTOR_OUT" | grep -qE '^\s+mas\s+(ok|degraded|unavailable|error)'; then
    MAS_LINE=$(printf '%s' "$DOCTOR_OUT" | grep -E '^\s+mas\s+(ok|degraded|unavailable|error)' | head -1)
    result "8.1 doctor: mas component" 1 "$MAS_LINE"
    MAS_AVAILABLE=1
else
    result "8.1 doctor: mas component" 0 "mas not listed in doctor output"
    MAS_AVAILABLE=0
fi

# 8.2-8.6 five-phase mas contract (skip if mas not available)
mas_phase_check() {
    local label="$1"
    local phase="$2"
    shift 2
    local extra_args="$*"
    local out rc sidecar sc_phase sc_cat sc_schema
    local MAS_RUNS_DIR
    MAS_RUNS_DIR=$(mktemp -d -t ascendo_mas_validate_XXXXXX)

    if [ -n "$extra_args" ]; then
        out=$(python3 -m ascendo run --category mas --phase "$phase" \
              --runs-dir "$MAS_RUNS_DIR" $extra_args 2>&1)
        rc=$?
    else
        out=$(python3 -m ascendo run --category mas --phase "$phase" \
              --runs-dir "$MAS_RUNS_DIR" 2>&1)
        rc=$?
    fi

    if [ $rc -ne 0 ]; then
        result "$label" 0 "exit $rc: $(printf '%s' "$out" | tail -5)"
        rm -rf "$MAS_RUNS_DIR"
        return
    fi

    sidecar=$(find "$MAS_RUNS_DIR" -name "${phase}__mas.json" -type f 2>/dev/null | head -1)
    if [ -z "$sidecar" ] || [ ! -f "$sidecar" ]; then
        result "$label" 0 "no sidecar found in $MAS_RUNS_DIR"
        rm -rf "$MAS_RUNS_DIR"
        return
    fi

    sc_phase=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['phase'])" 2>/dev/null)
    sc_cat=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['category'])" 2>/dev/null)
    sc_schema=$(python3 -c "import json; d=json.load(open('$sidecar')); print(d['schema'])" 2>/dev/null)

    if [ "$sc_phase" = "$phase" ] && [ "$sc_cat" = "mas" ] && [ "$sc_schema" = "ascendo/v1" ]; then
        result "$label" 1 "sidecar=$(basename "$sidecar")"
    else
        result "$label" 0 "sidecar shape wrong: phase=$sc_phase category=$sc_cat schema=$sc_schema"
    fi
    rm -rf "$MAS_RUNS_DIR"
}

if [ "$MAS_AVAILABLE" -eq 1 ]; then
    mas_phase_check "8.2 mas check"          check
    mas_phase_check "8.3 mas plan"           plan
    mas_phase_check "8.4 mas apply --dry-run" apply --dry-run
    mas_phase_check "8.5 mas verify"         verify
    mas_phase_check "8.6 mas cleanup"        cleanup
else
    printf "  [skip] 8.2 mas check (mas not available)\n"
    printf "  [skip] 8.3 mas plan (mas not available)\n"
    printf "  [skip] 8.4 mas apply --dry-run (mas not available)\n"
    printf "  [skip] 8.5 mas verify (mas not available)\n"
    printf "  [skip] 8.6 mas cleanup (mas not available)\n"
fi

# 8.7 dashboard askpass round-trip (requires $SUDO_PW; skipped in CI)
step "8.7 dashboard askpass round-trip"
if [ -z "${SUDO_PW:-}" ]; then
    printf "  [skip] 8.7 dashboard askpass round-trip (SUDO_PW not set; export SUDO_PW=<password> to enable)\n"
else
    # Use env override or +100 offset (not +10) to avoid collision with stale
    # dashboards when --port is shifted near defaults.
    _ASKPASS_PORT="${ASCENDO_VALIDATE_ASKPASS_PORT:-$((DASHBOARD_PORT + 100))}"
    _ASKPASS_LOG=$(mktemp -t ascendo_dash8_XXXXXX)
    _ASKPASS_PID=""

    # Ensure dashboard is torn down on exit from this block
    # NOTE: this overrides any pre-existing EXIT trap; future stages must coordinate.
    _cleanup_askpass_dash() {
        if [ -n "$_ASKPASS_PID" ]; then
            kill "$_ASKPASS_PID" 2>/dev/null || true
            wait "$_ASKPASS_PID" 2>/dev/null || true
        fi
        rm -f "$_ASKPASS_LOG"
    }
    trap '_cleanup_askpass_dash' EXIT INT TERM

    # Start dashboard in background on a dedicated port
    python3 -m ascendo dashboard --port "$_ASKPASS_PORT" >"$_ASKPASS_LOG" 2>&1 &
    _ASKPASS_PID=$!

    # Wait up to 10s for dashboard to bind
    _ai=0
    while [ $_ai -lt 10 ]; do
        if curl -sf "http://127.0.0.1:$_ASKPASS_PORT/version" >/dev/null 2>&1; then
            break
        fi
        sleep 1
        _ai=$((_ai + 1))
    done

    if ! curl -sf "http://127.0.0.1:$_ASKPASS_PORT/version" >/dev/null 2>&1; then
        result "8.7 dashboard for askpass" 0 "dashboard did not bind on port $_ASKPASS_PORT"
    else
        # 8.7a GET /elevation/status -> registered: false
        STATUS_A=$(curl -sf "http://127.0.0.1:$_ASKPASS_PORT/elevation/status" 2>/dev/null \
            | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('registered','?'))" 2>/dev/null)
        if [ "$STATUS_A" = "False" ] || [ "$STATUS_A" = "false" ]; then
            result "8.7a GET /elevation/status (registered=false)" 1
        else
            result "8.7a GET /elevation/status (registered=false)" 0 "got registered=$STATUS_A"
        fi

        # 8.7b POST /elevation/auth -> 200
        # Use jq (already a hard dep) to build the JSON body safely — prevents
        # shell injection if SUDO_PW contains ", \, $, or backticks.
        AUTH_CODE=""
        if ! command -v jq >/dev/null 2>&1; then
            result "8.7b POST /elevation/auth" 0 "jq missing — needed for safe password JSON encoding"
        else
            _auth_body="$(jq -n --arg pw "$SUDO_PW" '{password: $pw}')"
            AUTH_CODE=$(curl -fsS -o /dev/null -w "%{http_code}" -X POST \
                "http://127.0.0.1:$_ASKPASS_PORT/elevation/auth" \
                -H "Content-Type: application/json" \
                -d "$_auth_body" 2>/dev/null)
            if [ "$AUTH_CODE" = "200" ]; then
                result "8.7b POST /elevation/auth" 1
            else
                result "8.7b POST /elevation/auth" 0 "HTTP $AUTH_CODE (wrong password or no elevation support)"
            fi
        fi

        # 8.7c GET /elevation/status -> registered: true
        STATUS_B=$(curl -sf "http://127.0.0.1:$_ASKPASS_PORT/elevation/status" 2>/dev/null \
            | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('registered','?'))" 2>/dev/null)
        if [ "$STATUS_B" = "True" ] || [ "$STATUS_B" = "true" ]; then
            result "8.7c GET /elevation/status (registered=true)" 1
        else
            result "8.7c GET /elevation/status (registered=true)" 0 "got registered=$STATUS_B"
        fi

        # 8.7d POST /runs/async (mas apply, dry_run=true) + poll status
        ASYNC_MAS_BODY='{"phases":["apply"],"categories":["mas"],"dry_run":true}'
        MAS_RUN_ID=$(curl -sf -X POST "http://127.0.0.1:$_ASKPASS_PORT/runs/async" \
            -H "Content-Type: application/json" -d "$ASYNC_MAS_BODY" 2>/dev/null \
            | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])" 2>/dev/null)

        if [ -z "$MAS_RUN_ID" ]; then
            result "8.7d POST /runs/async (mas apply dry-run)" 0 "could not parse run_id"
        else
            MAS_ASYNC_STATUS=""
            _mj=0
            while [ $_mj -lt 60 ]; do
                MAS_ASYNC_STATUS=$(curl -sf \
                    "http://127.0.0.1:$_ASKPASS_PORT/runs/$MAS_RUN_ID/status" 2>/dev/null \
                    | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" 2>/dev/null)
                if [ "$MAS_ASYNC_STATUS" = "completed" ] || [ "$MAS_ASYNC_STATUS" = "failed" ]; then
                    break
                fi
                sleep 1
                _mj=$((_mj + 1))
            done
            if [ "$MAS_ASYNC_STATUS" = "completed" ]; then
                result "8.7d POST /runs/async + poll status" 1 "run_id=$MAS_RUN_ID"
            else
                result "8.7d POST /runs/async + poll status" 0 "final status=$MAS_ASYNC_STATUS"
            fi
        fi

        # 8.7e POST /elevation/invalidate
        INV_CODE=$(curl -sf -o /dev/null -w "%{http_code}" -X POST \
            "http://127.0.0.1:$_ASKPASS_PORT/elevation/invalidate" 2>/dev/null)
        if [ "$INV_CODE" = "200" ]; then
            result "8.7e POST /elevation/invalidate" 1
        else
            result "8.7e POST /elevation/invalidate" 0 "HTTP $INV_CODE"
        fi

        # 8.7f GET /elevation/status -> registered: false
        STATUS_C=$(curl -sf "http://127.0.0.1:$_ASKPASS_PORT/elevation/status" 2>/dev/null \
            | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('registered','?'))" 2>/dev/null)
        if [ "$STATUS_C" = "False" ] || [ "$STATUS_C" = "false" ]; then
            result "8.7f GET /elevation/status (registered=false after invalidate)" 1
        else
            result "8.7f GET /elevation/status (registered=false after invalidate)" 0 "got registered=$STATUS_C"
        fi
    fi

    _cleanup_askpass_dash
    trap - EXIT INT TERM
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
