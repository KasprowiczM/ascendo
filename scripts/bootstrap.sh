#!/usr/bin/env bash
# =============================================================================
# scripts/bootstrap.sh — Fresh-clone onboarding orchestrator
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

DRY_RUN=0
SKIP_SYNC=0
SKIP_SETUP=0
CHECK_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --skip-sync) SKIP_SYNC=1 ;;
        --skip-setup) SKIP_SETUP=1 ;;
        --check-only) CHECK_ONLY=1 ;;
        -h|--help)
            cat <<'EOF'
Usage: bash scripts/bootstrap.sh [--dry-run] [--skip-sync] [--skip-setup] [--check-only]

Fresh-clone flow:
  1. Run read-only preflight checks.
  2. Restore private overlay from dev-sync/Proton when configured.
  3. Run setup.sh --check or setup.sh to reconcile package state.
  4. Run verify-state checks.
EOF
            exit 0 ;;
        *) print_error "Unknown argument: $1"; exit 2 ;;
    esac
    shift
done

print_header "Bootstrap — Ascendo"
acquire_project_lock "bootstrap"

bash "${SCRIPT_DIR}/scripts/preflight.sh"

if [[ $SKIP_SYNC -eq 0 ]]; then
    if [[ -f "${SCRIPT_DIR}/.dev_sync_config.json" ]]; then
        restore_args=(--verbose)
        [[ $DRY_RUN -eq 1 ]] && restore_args+=(--dry-run)
        bash "${SCRIPT_DIR}/scripts/restore-from-proton.sh" "${restore_args[@]}"
    else
        print_warn "Skipping dev-sync restore: .dev_sync_config.json missing"
        print_info "Run: bash dev-sync/provider_setup.sh"
    fi
fi

if [[ $SKIP_SETUP -eq 0 ]]; then
    if [[ $DRY_RUN -eq 1 || $CHECK_ONLY -eq 1 ]]; then
        bash "${SCRIPT_DIR}/setup.sh" --check --non-interactive
    else
        bash "${SCRIPT_DIR}/setup.sh" --non-interactive
    fi
fi

bash "${SCRIPT_DIR}/scripts/verify-state.sh"
