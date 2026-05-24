#!/usr/bin/env bash
# =============================================================================
# scripts/fresh-machine.sh — One-liner provisioning on a freshly cloned repo
#
# Goal: after `git clone` on a new Ubuntu box, a single command brings the
# whole stack up — preflight, dev-sync overlay restore (if Proton/rclone is
# available), package reconciliation, dashboard venv, dashboard service, and
# a final state verification.
#
#   git clone https://github.com/KasprowiczM/Ascendo
#   cd Ascendo
#   bash scripts/fresh-machine.sh                 # interactive, full
#   bash scripts/fresh-machine.sh --no-dashboard  # CLI only
#   bash scripts/fresh-machine.sh --dry-run       # plan, no mutations
#   bash scripts/fresh-machine.sh --check-only    # read-only audit
#
# Layered on top of:
#   - scripts/preflight.sh       (read-only host audit)
#   - scripts/bootstrap.sh       (overlay restore + setup.sh + verify-state)
#   - app/install.sh             (dashboard venv)
#   - systemd/user/install-dashboard.sh (autostart user-service)
#
# Each step is idempotent — re-running on an already-bootstrapped host is a
# safe no-op that re-verifies state.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/i18n.sh
source "${SCRIPT_DIR}/lib/i18n.sh"

DRY_RUN=0
CHECK_ONLY=0
NO_DASHBOARD=0
NO_SERVICE=0
NO_SYNC=0
LANG_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)       DRY_RUN=1 ;;
        --check-only)    CHECK_ONLY=1 ;;
        --no-dashboard)  NO_DASHBOARD=1 ;;
        --no-service)    NO_SERVICE=1 ;;
        --no-sync)       NO_SYNC=1 ;;
        --lang)          shift; LANG_OVERRIDE="${1:-}" ;;
        -h|--help)
            sed -n '4,18p' "$0"; exit 0 ;;
        *) print_error "Unknown argument: $1"; exit 2 ;;
    esac
    shift
done

# ── Step 0: language selection (interactive unless --lang or already set) ────
if [[ -n "$LANG_OVERRIDE" ]]; then
    ascendo_set_lang "$LANG_OVERRIDE" || true
elif [[ ! -r "${XDG_CONFIG_HOME:-$HOME/.config}/ascendo/lang" && $CHECK_ONLY -eq 0 && -t 0 && -t 1 ]]; then
    [[ -f "${SCRIPT_DIR}/branding/banner.txt" ]] && cat "${SCRIPT_DIR}/branding/banner.txt"
    echo
    echo "  $(t setup.welcome 'Welcome to Ascendo - Unified Updates.')"
    echo
    echo "  $(t setup.lang_pick 'Pick the interface language for CLI and dashboard:')"
    echo "    1) English  (en)"
    echo "    2) Polski   (pl)"
    read -rp "  $(t setup.lang_choose 'Choose [%s]:' | sed 's/%s/1/'): " ans
    case "$ans" in
        2|pl|PL) ascendo_set_lang pl ;;
        *)       ascendo_set_lang en ;;
    esac
    printf "  $(t setup.lang_set 'Language set to: %s')\n" "$ASCENDO_LANG_RESOLVED"
fi

[[ -f "${SCRIPT_DIR}/branding/banner.txt" ]] && cat "${SCRIPT_DIR}/branding/banner.txt"
print_header "Ascendo — fresh-machine onboarding"
print_info "Repo : ${SCRIPT_DIR}"
print_info "Mode : $([[ $CHECK_ONLY -eq 1 ]] && echo 'check-only' || ([[ $DRY_RUN -eq 1 ]] && echo 'dry-run' || echo 'apply'))"
echo

# ── 1. Preflight (always) ─────────────────────────────────────────────────────
print_section "$(t setup.preflight 'Step 1/5 — preflight host audit')"
bash "${SCRIPT_DIR}/scripts/preflight.sh" || {
    print_warn "preflight reported issues — continuing, address before mutating runs"
}

# ── 2. Bootstrap: overlay restore + setup.sh ──────────────────────────────────
print_section "$(t setup.bootstrap 'Step 2/5 — overlay restore + setup')"
bootstrap_args=()
[[ $DRY_RUN     -eq 1 ]] && bootstrap_args+=(--dry-run)
[[ $CHECK_ONLY  -eq 1 ]] && bootstrap_args+=(--check-only)
[[ $NO_SYNC     -eq 1 ]] && bootstrap_args+=(--skip-sync)
if ! bash "${SCRIPT_DIR}/scripts/bootstrap.sh" "${bootstrap_args[@]}"; then
    if [[ $CHECK_ONLY -eq 1 || $DRY_RUN -eq 1 ]]; then
        print_warn "bootstrap reported issues (non-fatal in check/dry-run)"
    else
        print_error "bootstrap failed — fix the issues above and re-run"
        exit 1
    fi
fi

# ── 2b. Apps detect (read-only report; never auto-installs) ──────────────────
print_section "Apps detect (read-only)"
print_info "$(t setup.ask_apps 'Run \`ascendo apps detect\` to compare your installed apps with this configuration.')"
bash "${SCRIPT_DIR}/scripts/apps/detect.sh" 2>&1 | tail -n +1 | sed -n '1,40p'
echo
print_info "Use 'ascendo apps add <pkg> --category <cat>' to track a detected package."
print_info "Use 'ascendo apps install-missing' to install configured-but-missing entries."

# ── 3. Dashboard venv ─────────────────────────────────────────────────────────
if [[ $NO_DASHBOARD -eq 0 ]]; then
    print_section "$(t setup.dashboard 'Step 3/5 — dashboard Python venv')"
    if [[ $CHECK_ONLY -eq 1 ]]; then
        if [[ -x "${SCRIPT_DIR}/app/.venv/bin/python" ]]; then
            print_ok "venv present at app/.venv/"
        else
            print_warn "venv missing — run: bash app/install.sh"
        fi
    else
        bash "${SCRIPT_DIR}/app/install.sh"
    fi
else
    print_info "skipping dashboard venv (--no-dashboard)"
fi

# ── 4. Dashboard user-service ─────────────────────────────────────────────────
if [[ $NO_DASHBOARD -eq 0 && $NO_SERVICE -eq 0 ]]; then
    print_section "$(t setup.service 'Step 4/5 — dashboard user-service (systemd --user)')"
    if [[ $CHECK_ONLY -eq 1 || $DRY_RUN -eq 1 ]]; then
        if systemctl --user is-enabled ascendo-dashboard.service >/dev/null 2>&1; then
            print_ok "service enabled"
        else
            print_info "service not yet enabled — run: bash systemd/user/install-dashboard.sh"
        fi
    else
        bash "${SCRIPT_DIR}/systemd/user/install-dashboard.sh" || {
            print_warn "dashboard install-dashboard.sh failed — service can be installed later"
        }
    fi
else
    print_info "skipping dashboard service (--no-service or --no-dashboard)"
fi

# ── 5. State verification ─────────────────────────────────────────────────────
print_section "$(t setup.verify 'Step 5/5 — state verification')"
bash "${SCRIPT_DIR}/scripts/verify-state.sh" || {
    print_warn "verify-state reported issues"
}

echo
print_section "$(t setup.next_steps 'Next steps')"
echo
echo -e "  ${BOLD}CLI run:${RESET}     ./update-all.sh --profile quick --no-notify"
echo -e "  ${BOLD}Dashboard:${RESET}   xdg-open http://127.0.0.1:8765"
[[ $NO_DASHBOARD -eq 0 && $NO_SERVICE -eq 0 ]] && \
    echo -e "  ${BOLD}Service:${RESET}     systemctl --user status ascendo-dashboard"
echo -e "  ${BOLD}Schedule:${RESET}    bash scripts/scheduler/install.sh --calendar 'Sun *-*-* 03:00:00' --profile safe"
echo -e "  ${BOLD}Sudo policy:${RESET} update-all.sh prompts ONCE — askpass keeps the run going"
echo
