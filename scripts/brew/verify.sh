#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

json_init verify brew
json_register_exit_trap "${JSON_OUT:-}"

detect_package_managers
if [[ "${HAS_BREW:-0}" -ne 1 ]]; then
    json_add_diag info BREW-MISSING "homebrew not installed"
    exit 0
fi

CONFIG_F="${SCRIPT_DIR}/config/brew-formulas.list"
CONFIG_C="${SCRIPT_DIR}/config/brew-casks.list"
EXIT_RC=0

if [[ -f "$CONFIG_F" ]]; then
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        if brew_formula_installed "$f"; then
            ver=$(brew_formula_version "$f")
            json_add_item id="brew:formula:${f}" action="present" from="${ver}" to="${ver}" result="ok"
            json_count_ok
        else
            json_add_item id="brew:formula:${f}" action="present" result="failed"
            json_add_diag warn BREW-MISSING "formula not installed: ${f}"
            json_count_warn
            EXIT_RC=1
        fi
    done < <(parse_config_names "$CONFIG_F")
fi
if [[ -f "$CONFIG_C" ]]; then
    while IFS= read -r c; do
        [[ -z "$c" ]] && continue
        if brew_cask_installed "$c"; then
            ver=$(brew_cask_version "$c")
            json_add_item id="brew:cask:${c}" action="present" from="${ver}" to="${ver}" result="ok"
            json_count_ok
        else
            json_add_item id="brew:cask:${c}" action="present" result="failed"
            json_add_diag warn BREW-MISSING "cask not installed: ${c}"
            json_count_warn
            EXIT_RC=1
        fi
    done < <(parse_config_names "$CONFIG_C")
fi

# Outdated count after apply
n=$(run_as_user "${BREW_BIN}" outdated --quiet 2>/dev/null | wc -l | awk '{print $1}' || echo 0)
if [[ "${n:-0}" -gt 0 ]]; then
    json_add_diag warn BREW-STILL-OUTDATED "${n} brew package(s) still outdated"
    json_count_warn
    EXIT_RC=1
fi
exit $EXIT_RC
