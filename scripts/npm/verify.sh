#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

json_init verify npm
json_register_exit_trap "${JSON_OUT:-}"

detect_package_managers
if [[ -z "${NPM_BIN:-}" ]]; then
    json_add_diag info NPM-MISSING "npm not found"
    exit 0
fi

CONFIG_NPM="${SCRIPT_DIR}/config/npm-globals.list"
EXIT_RC=0

if [[ -f "$CONFIG_NPM" ]]; then
    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        if npm_pkg_installed "$pkg"; then
            ver=$(npm_pkg_version "$pkg")
            json_add_item id="npm:installed:${pkg}" action="present" from="${ver}" to="${ver}" result="ok"
            json_count_ok
        else
            json_add_item id="npm:installed:${pkg}" action="present" result="failed"
            json_add_diag warn NPM-MISSING "configured npm global not installed: ${pkg}"
            json_count_warn
            EXIT_RC=1
        fi
    done < <(parse_config_names "$CONFIG_NPM")
fi
exit $EXIT_RC
