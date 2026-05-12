#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"
source "${SCRIPT_DIR}/lib/progress.sh"

json_init check flatpak
json_register_exit_trap "${JSON_OUT:-}"

print_header "Flatpak — check"

if ! has_cmd flatpak; then
    json_add_diag info FLATPAK-MISSING "flatpak not installed — category not applicable"
    exit 0
fi

CONFIG_FLATPAK="${SCRIPT_DIR}/config/flatpak-packages.list"

if [[ -f "$CONFIG_FLATPAK" ]]; then
    while IFS= read -r app_id; do
        [[ -z "$app_id" ]] && continue
        if flatpak list --app --columns=application 2>/dev/null | grep -q "^${app_id}$"; then
            ver=$(flatpak list --app --columns=application,version 2>/dev/null | awk -v id="$app_id" '$1==id{print $2}')
            json_add_item id="flatpak:installed:${app_id}" action="present" \
                from="${ver}" to="${ver}" result="ok"
            json_count_ok
        else
            json_add_item id="flatpak:installed:${app_id}" action="present" result="failed"
            json_add_diag warn FLATPAK-MISSING-CONFIG "configured flatpak not installed: ${app_id}"
            json_count_warn
        fi
    done < <(parse_config_names "$CONFIG_FLATPAK")
fi

# Updates available
print_step "flatpak remote-ls --updates"
upd_lines=$(flatpak remote-ls --updates --columns=application,version 2>/dev/null || true)
print_ok
upd=$(printf '%s' "$upd_lines" | awk 'NF{n++} END{print n+0}')
print_found flatpak "$upd" "$upd_lines"
json_add_diag info FLATPAK-OUTDATED "${upd} update(s) available across remotes"
exit 0
