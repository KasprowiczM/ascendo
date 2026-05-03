#!/usr/bin/env bash
# =============================================================================
# bin/run-tag-release-macos.sh -- interactive real-apply + tag harness
# =============================================================================
# 7-stage flow:
#   1. Preflight       -- ensure repo root, PYTHONPATH wired, tools present
#   2. Snapshot        -- N/A in M5.1; warning printed (Time Machine = M5.4)
#   3. Plan            -- `ascendo run --category brew --phase plan`
#   4. Confirm gate    -- type literal `apply` to proceed
#   5. Apply           -- `ascendo run --category brew --phase apply`
#   6. Verify + cleanup -- both phases run unconditionally
#   7. Doctor + tag    -- `git tag -a v0.0.8-alpha`. Does NOT push.
#
# Flags:
#   --what-if                show plan only, no mutation
#   --no-tag                 apply but skip the git tag step
#   --no-snapshot            skip snapshot step (no-op in M5.1)
#   --i-accept-upgrade-risk  bypass the interactive confirm gate (for CI)
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

WHAT_IF=0
NO_TAG=0
NO_SNAPSHOT=0
ACCEPT_RISK=0

while [ $# -gt 0 ]; do
    case "$1" in
        --what-if)               WHAT_IF=1; shift ;;
        --no-tag)                NO_TAG=1; shift ;;
        --no-snapshot)           NO_SNAPSHOT=1; shift ;;
        --i-accept-upgrade-risk) ACCEPT_RISK=1; shift ;;
        *) printf "run-tag-release-macos.sh: unknown arg: %s\n" "$1" >&2; exit 2 ;;
    esac
done

step() { printf "\n==> [Stage %s] %s\n" "$1" "$2"; }
note() { printf "    %s\n" "$1"; }
warn() { printf "    [WARN] %s\n" "$1" >&2; }
fail() { printf "    [FAIL] %s\n" "$1" >&2; exit 1; }

export PYTHONPATH="$REPO_ROOT/core"

# ── Stage 1: Preflight ────────────────────────────────────────────────────────
step 1 "Preflight"

if [ ! -f "$REPO_ROOT/PLAN.md" ]; then
    fail "not in repo root (PLAN.md missing at $REPO_ROOT)"
fi
note "repo root:   $REPO_ROOT"
note "PYTHONPATH:  $PYTHONPATH"

if ! command -v brew >/dev/null 2>&1; then
    fail "brew not on PATH (install Homebrew first: https://brew.sh)"
fi
note "brew: $(brew --version | head -n1)"

if ! command -v jq >/dev/null 2>&1; then
    fail "jq not on PATH (install: brew install jq)"
fi
note "jq:   $(jq --version)"

if ! command -v git >/dev/null 2>&1; then
    fail "git not on PATH"
fi
note "git:  $(git --version)"

if ! python3 -m ascendo --help >/dev/null 2>&1; then
    fail "python3 -m ascendo failed -- run 'bash bin/install-dev-macos.sh' first"
fi
note "ascendo CLI: ok"

# ── Stage 2: Snapshot ─────────────────────────────────────────────────────────
step 2 "Snapshot"

if [ "$NO_SNAPSHOT" -eq 1 ]; then
    note "skipped (--no-snapshot)"
else
    warn "Time Machine integration not yet implemented (M5.4). Continuing without snapshot."
    note "To manually create a backup before applying: open System Preferences > Time Machine."
fi

# ── Stage 3: Plan ─────────────────────────────────────────────────────────────
step 3 "Plan"

python3 -m ascendo run --category brew --phase plan
PLAN_RC=$?

if [ $PLAN_RC -ne 0 ]; then
    fail "plan phase exited $PLAN_RC -- aborting before any mutations"
fi

if [ "$WHAT_IF" -eq 1 ]; then
    note "--what-if: stopping after plan. No changes made."
    exit 0
fi

# ── Stage 4: Confirm gate ─────────────────────────────────────────────────────
step 4 "Confirm gate"

if [ "$ACCEPT_RISK" -eq 1 ]; then
    note "skipped (--i-accept-upgrade-risk)"
else
    printf "    About to upgrade Homebrew packages on this Mac.\n"
    printf "    This is a REAL upgrade -- not a dry run.\n"
    printf "\n"
    printf "    Type 'apply' to proceed, anything else to abort: "
    read -r ANSWER
    if [ "$ANSWER" != "apply" ]; then
        note "aborted. No changes made."
        exit 0
    fi
fi

# ── Stage 5: Apply ────────────────────────────────────────────────────────────
step 5 "Apply"

python3 -m ascendo run --category brew --phase apply
APPLY_RC=$?

case "$APPLY_RC" in
    0)  note "apply succeeded (exit 0)" ;;
    75) note "apply succeeded -- reboot required (exit 75)" ;;
    *)  warn "apply exited $APPLY_RC; continuing to verify to capture diagnostics"
        ;;
esac

# ── Stage 6: Verify + cleanup ─────────────────────────────────────────────────
step 6 "Verify + cleanup"

python3 -m ascendo run --category brew --phase verify
VERIFY_RC=$?
note "verify exit: $VERIFY_RC"

python3 -m ascendo run --category brew --phase cleanup
CLEANUP_RC=$?
note "cleanup exit: $CLEANUP_RC"

# ── Stage 7: Doctor + tag ─────────────────────────────────────────────────────
step 7 "Doctor + tag"

python3 -m ascendo doctor
DOCTOR_RC=$?

if [ "$NO_TAG" -eq 1 ]; then
    note "skipped tag (--no-tag)"
elif [ "$APPLY_RC" -ne 0 ] && [ "$APPLY_RC" -ne 75 ]; then
    warn "apply did not succeed cleanly (exit $APPLY_RC) -- refusing to tag v0.0.8-alpha."
    exit 1
elif [ "$VERIFY_RC" -gt 1 ]; then
    warn "verify exited $VERIFY_RC (>1) -- refusing to tag v0.0.8-alpha."
    exit 1
elif git rev-parse v0.0.8-alpha >/dev/null 2>&1; then
    note "tag v0.0.8-alpha already exists -- skipping."
else
    git tag -a v0.0.8-alpha \
        -m "macOS adapter M5.1: brew end-to-end on real hardware (apply RC=$APPLY_RC)"
    note "tagged v0.0.8-alpha. Run 'git push --tags' when ready."
fi

printf "\nDone.\n"
