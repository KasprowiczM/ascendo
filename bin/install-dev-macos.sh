#!/usr/bin/env bash
# =============================================================================
# bin/install-dev-macos.sh -- one-shot dev install for Ascendo on macOS
# =============================================================================
# Installs:
#   1. The `ascendo` core package (editable, -e ./core)
#   2. The macOS adapter (`ascendo-macos`, editable, -e ./adapters/macos --no-deps)
#   3. Dashboard runtime deps (fastapi, uvicorn[standard], httpx)
#   4. System deps (jq via brew if missing)
#   5. Optionally runs bin/validate-macos.sh at the end
#
# Use:
#   $ bash bin/install-dev-macos.sh                # install + validate
#   $ bash bin/install-dev-macos.sh --skip-validate
#   $ bash bin/install-dev-macos.sh --reinstall
#
# Idempotent: safe to re-run after git pull.
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

SKIP_VALIDATE=0
REINSTALL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-validate) SKIP_VALIDATE=1; shift ;;
        --reinstall)     REINSTALL=1; shift ;;
        *) printf "install-dev-macos.sh: unknown arg: %s\n" "$1" >&2; exit 2 ;;
    esac
done

step() { printf "\n==> %s\n" "$1"; }
ok()   { printf "  [OK] %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1" >&2; exit 1; }

force_flag=""
if [ "$REINSTALL" -eq 1 ]; then
    force_flag="--force-reinstall"
fi

# ── 1. Detect toolchain ──────────────────────────────────────────────────────
step "Detecting toolchain"

if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not on PATH (install Python 3.11+ first)"
fi
ok "python3: $(python3 --version 2>&1)"

if ! command -v bash >/dev/null 2>&1; then
    fail "bash not on PATH"
fi
ok "bash:    $(bash --version | head -n1)"

if ! command -v brew >/dev/null 2>&1; then
    fail "brew not on PATH (install Homebrew first: https://brew.sh)"
fi
ok "brew:    $(brew --version | head -n1)"

# ── 2. Ensure jq is installed ────────────────────────────────────────────────
step "Ensuring jq is installed"
if ! command -v jq >/dev/null 2>&1; then
    printf "  jq not found -- installing via brew...\n"
    brew install jq || fail "brew install jq failed"
    ok "installed jq: $(jq --version)"
else
    ok "jq already installed: $(jq --version)"
fi

# PEP 668: Homebrew Python 3.12+ marks itself "externally-managed" and rejects
# plain `pip install` without --break-system-packages.  On macOS the user has
# already accepted this by installing Python via brew, so we always pass the
# flag.  (If the user is in a venv, the flag is ignored harmlessly.)
PIP_EXTRA_FLAGS="--break-system-packages"

# ── 3. Install ascendo core (editable) ──────────────────────────────────────
step "pip install -e ./core"
# shellcheck disable=SC2086
python3 -m pip install -e ./core $force_flag $PIP_EXTRA_FLAGS --quiet \
    || fail "core install failed"
ok "core installed"

# ── 4. Install macOS adapter (editable, --no-deps) ──────────────────────────
step "pip install -e ./adapters/macos --no-deps"
# --no-deps avoids pip's PyPI lookup of the `ascendo` core dep which is not
# yet published.
# shellcheck disable=SC2086
python3 -m pip install -e ./adapters/macos --no-deps $force_flag $PIP_EXTRA_FLAGS \
    --quiet || fail "macOS adapter install failed"
ok "macOS adapter installed"

# ── 5. Install dashboard runtime deps ───────────────────────────────────────
step "pip install fastapi uvicorn[standard] httpx (dashboard runtime)"
# shellcheck disable=SC2086
python3 -m pip install 'fastapi>=0.111' 'uvicorn[standard]>=0.30' 'httpx>=0.27' \
    $PIP_EXTRA_FLAGS --quiet || fail "dashboard runtime deps failed"
ok "dashboard runtime installed"

# ── 6. Verify installed packages ────────────────────────────────────────────
step "Verifying installed packages"
python3 -m pip show ascendo ascendo-macos 2>/dev/null \
    | grep -E '^(Name|Version|Location):' || true

# ── 7. Optional validate ─────────────────────────────────────────────────────
if [ "$SKIP_VALIDATE" -eq 0 ]; then
    step "Running bin/validate-macos.sh"
    bash "$SCRIPT_DIR/validate-macos.sh"
    exit $?
fi

printf "\nInstall OK. Run 'bash bin/validate-macos.sh' when ready to test end-to-end.\n"
