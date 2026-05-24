#!/usr/bin/env bash
# =============================================================================
# scripts/verify-state.sh — Non-mutating repository and recovery verification
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

print_header "Verify State — Ascendo"

failures=0
warnings=0

ok_check() { print_ok "$1"; }
warn_check() { print_warn "$1"; warnings=$((warnings + 1)); }
fail_check() { print_error "$1"; failures=$((failures + 1)); }

print_section "Shell syntax"
syntax_failed=0
for script in "${SCRIPT_DIR}/update-all.sh" "${SCRIPT_DIR}/setup.sh" "${SCRIPT_DIR}"/scripts/*.sh "${SCRIPT_DIR}"/lib/*.sh "${SCRIPT_DIR}"/dev-sync/*.sh "${SCRIPT_DIR}"/dev-sync-*.sh; do
    bash -n "$script" || syntax_failed=1
done
if [[ $syntax_failed -eq 0 ]]; then
    ok_check "bash -n passed"
else
    fail_check "bash syntax failed"
fi

print_section "Python tests"
if PYTHONDONTWRITEBYTECODE=1 python3 "${SCRIPT_DIR}/tests/test_dev_sync_safety.py" -v; then
    ok_check "dev-sync safety tests passed"
else
    fail_check "dev-sync safety tests failed"
fi

print_section "Git coverage"
if bash "${SCRIPT_DIR}/dev-sync-verify-git.sh"; then
    ok_check "tracked files clean and comparable with upstream"
else
    warn_check "git verification reported issues"
fi

print_section "Private overlay"
if [[ -f "${SCRIPT_DIR}/.dev_sync_config.json" ]]; then
    if bash "${SCRIPT_DIR}/dev-sync-restore-preflight.sh"; then
        ok_check "restore preflight passed"
    else
        fail_check "restore preflight failed"
    fi
    if bash "${SCRIPT_DIR}/dev-sync-verify-full.sh"; then
        ok_check "full dev-sync verification passed"
    else
        fail_check "full dev-sync verification failed"
    fi
else
    warn_check ".dev_sync_config.json missing; provider overlay verification skipped"
fi

print_section "Systemd templates"
if has_cmd systemd-analyze; then
    if systemd-analyze verify "${SCRIPT_DIR}/systemd/ascendo@.service" "${SCRIPT_DIR}/systemd/ascendo@.timer" >/tmp/ascendo-systemd-verify.log 2>&1; then
        ok_check "systemd templates verify"
    else
        cat /tmp/ascendo-systemd-verify.log >&2 || true
        fail_check "systemd template verification failed"
    fi
else
    warn_check "systemd-analyze unavailable"
fi

print_summary "Verify State Summary"
echo "Failures: ${failures}"
echo "Warnings: ${warnings}"

[[ $failures -eq 0 ]] || exit 1
