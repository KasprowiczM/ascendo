#!/usr/bin/env bash
# =============================================================================
# scripts/brew/apply.sh — Native Homebrew (Linuxbrew) update.
# Replaces previous delegation. Per-package items emitted from `brew outdated`.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

export INVENTORY_SILENT=1
export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_ANALYTICS=1
export HOMEBREW_NO_ENV_HINTS=1

json_init apply brew
json_register_exit_trap "${JSON_OUT:-}"

print_header "Homebrew — apply"

detect_package_managers
if [[ "${HAS_BREW:-0}" -ne 1 ]]; then
    json_add_diag info BREW-MISSING "homebrew not installed"
    exit 0
fi

CONFIG_F="${SCRIPT_DIR}/config/brew-formulas.list"
CONFIG_C="${SCRIPT_DIR}/config/brew-casks.list"
EXIT_RC=0

# ── 1. brew update ────────────────────────────────────────────────────────────
print_step "brew update"
_stream_emit ">>> brew update"
if run_capture_as_user "${BREW_BIN}" update; then
    print_ok
    json_add_item id="brew:update" action="refresh" result="ok"
    json_count_ok
else
    print_warn "brew update non-zero"
    json_add_item id="brew:update" action="refresh" result="warn"
    _tail="$(_stderr_tail "${_LAST_RUN_OUT_FILE}")"
    if [[ -n "$_tail" ]]; then
        json_add_diag warn BREW-UPDATE-WARN \
            "brew update returned non-zero — last output: ${_tail}"
    else
        json_add_diag warn BREW-UPDATE-WARN "brew update returned non-zero (network?)"
    fi
    json_count_warn
fi

# ── 2. Pre-upgrade outdated snapshot for items ────────────────────────────────
outdated_json=$(run_as_user "${BREW_BIN}" outdated --json=v2 2>/dev/null || echo '{}')

# ── 3. brew upgrade --formula ─────────────────────────────────────────────────
print_step "brew upgrade --formula"
_stream_emit ">>> brew upgrade --formula"
if run_capture_as_user "${BREW_BIN}" upgrade --formula; then
    print_ok
    json_add_item id="brew:upgrade:formula" action="upgrade" result="ok"
    json_count_ok
else
    print_warn "formula upgrade non-zero"
    json_add_item id="brew:upgrade:formula" action="upgrade" result="warn"
    _tail="$(_stderr_tail "${_LAST_RUN_OUT_FILE}")"
    if [[ -n "$_tail" ]]; then
        json_add_diag warn BREW-FORMULA-WARN \
            "some formulas failed to upgrade — last output: ${_tail}"
    else
        json_add_diag warn BREW-FORMULA-WARN "some formulas failed to upgrade"
    fi
    json_count_warn
    EXIT_RC=1
fi

# ── 4. brew upgrade --cask ────────────────────────────────────────────────────
# NB: previously --greedy unconditionally. That re-downloads every cask
# whose version is "latest" or that has auto_updates=true on every apply,
# easily 10+ minutes per run on Linuxbrew with no SPA progress visible —
# the run looks frozen. Default upgrade is enough for normal flow; opt-in
# greedy via ASCENDO_BREW_GREEDY=1 when you actually want it.
GREEDY_ARGS=()
if [[ "${ASCENDO_BREW_GREEDY:-0}" = "1" ]]; then
    GREEDY_ARGS=(--greedy)
    print_step "brew upgrade --cask --greedy (ASCENDO_BREW_GREEDY=1)"
    _stream_emit ">>> brew upgrade --cask --greedy"
else
    print_step "brew upgrade --cask"
    _stream_emit ">>> brew upgrade --cask  (ASCENDO_BREW_GREEDY=1 to force --greedy)"
fi
if run_capture_as_user "${BREW_BIN}" upgrade --cask "${GREEDY_ARGS[@]}"; then
    print_ok
    json_add_item id="brew:upgrade:cask" action="upgrade" result="ok"
    json_count_ok
else
    print_warn "cask upgrade non-zero"
    json_add_item id="brew:upgrade:cask" action="upgrade" result="warn"
    _tail="$(_stderr_tail "${_LAST_RUN_OUT_FILE}")"
    if [[ -n "$_tail" ]]; then
        json_add_diag warn BREW-CASK-WARN \
            "some casks failed to upgrade — last output: ${_tail}"
    else
        json_add_diag warn BREW-CASK-WARN "some casks failed to upgrade"
    fi
    json_count_warn
    EXIT_RC=1
fi

# ── 5. Per-package items (from pre-upgrade outdated) ─────────────────────────
echo "$outdated_json" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read() or '{}')
    for f in d.get('formulae', []):
        ivs = f.get('installed_versions') or ['']
        print(f\"formula|{f.get('name','')}|{ivs[0]}|{f.get('current_version','')}\")
    for c in d.get('casks', []):
        ivs = c.get('installed_versions') or ['']
        print(f\"cask|{c.get('name','')}|{ivs[0]}|{c.get('current_version','')}\")
except Exception:
    pass
" 2>/dev/null | while IFS='|' read -r kind name frm to; do
    [[ -z "$name" ]] && continue
    json_add_item id="brew:${kind}:${name}" action="upgrade" \
        from="${frm}" to="${to}" result="ok"
done

# ── 6. Install missing formulas/casks from config ────────────────────────────
if [[ -f "$CONFIG_F" ]]; then
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        if ! brew_formula_installed "$f"; then
            print_step "brew install ${f}"
            if run_silent_as_user "${BREW_BIN}" install "$f"; then
                print_ok
                json_add_item id="brew:install:formula:${f}" action="install" result="ok"
                json_count_ok
            else
                print_warn "install failed"
                json_add_item id="brew:install:formula:${f}" action="install" result="failed"
                json_count_warn
                [[ $EXIT_RC -eq 0 ]] && EXIT_RC=1
            fi
        fi
    done < <(parse_config_names_filtered brew "$CONFIG_F")
fi
if [[ -f "$CONFIG_C" ]]; then
    while IFS= read -r c; do
        [[ -z "$c" ]] && continue
        if ! brew_cask_installed "$c"; then
            print_step "brew install --cask ${c}"
            if run_silent_as_user "${BREW_BIN}" install --cask "$c"; then
                print_ok
                json_add_item id="brew:install:cask:${c}" action="install" result="ok"
                json_count_ok
            else
                print_warn "install failed"
                json_add_item id="brew:install:cask:${c}" action="install" result="failed"
                json_count_warn
                [[ $EXIT_RC -eq 0 ]] && EXIT_RC=1
            fi
        fi
    done < <(parse_config_names_filtered brew "$CONFIG_C")
fi

# ── 7. brew doctor (informational) ────────────────────────────────────────────
doc=$(run_as_user "${BREW_BIN}" doctor 2>&1 || true)
echo "$doc" >> "${LOG_FILE}"
if echo "$doc" | grep -q "Your system is ready to brew"; then
    json_add_diag info BREW-DOCTOR-OK "brew doctor: ready"
else
    n=$(echo "$doc" | grep -c '^Warning:' || true)
    json_add_diag warn BREW-DOCTOR "brew doctor found ${n} warning(s)"
fi

exit $EXIT_RC
