#!/usr/bin/env bash
# =============================================================================
# scripts/flatpak/apply.sh — Native flatpak update + missing-from-config install.
# Replaces previous delegation to legacy scripts/update-flatpak.sh.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

export INVENTORY_SILENT=1
json_init apply flatpak
json_register_exit_trap "${JSON_OUT:-}"

print_header "Flatpak — apply"

if ! has_cmd flatpak; then
    json_add_diag info FLATPAK-MISSING "flatpak not installed"
    exit 0
fi

CONFIG_FLATPAK="${SCRIPT_DIR}/config/flatpak-packages.list"
EXIT_RC=0

# ── 1. Refresh metadata ───────────────────────────────────────────────────────
print_step "flatpak update --appstream"
if flatpak update --appstream >> "${LOG_FILE}" 2>&1; then
    print_ok
    json_count_ok
else
    print_warn "appstream refresh non-zero"
    json_add_diag warn FLATPAK-METADATA "appstream metadata refresh failed"
    json_count_warn
fi

# ── 2. Update apps ────────────────────────────────────────────────────────────
print_step "flatpak update --noninteractive"
_stream_emit ">>> flatpak update --noninteractive"
update_out=$(flatpak update --noninteractive 2>&1) && update_rc=0 || update_rc=$?
echo "${update_out}" >> "${LOG_FILE}"
# Mirror to SSE stream log (best-effort) so Run Center sees the output live.
if [[ -n "${ASCENDO_STREAM_LOG:-}" && -n "${update_out}" ]]; then
    printf '%s\n' "${update_out}" >> "${ASCENDO_STREAM_LOG}" 2>/dev/null || true
fi

if [[ $update_rc -ne 0 ]]; then
    print_error "flatpak update failed"
    json_add_item id="flatpak:update" action="upgrade" result="failed"
    # Surface last 12 non-empty lines of flatpak output (Sesja-34 style).
    _fp_tail="$(printf '%s\n' "${update_out}" | awk 'NF{print}' \
                | tail -n 12 | tr '\t' ' ' | head -c 1500 || true)"
    if [[ -n "$_fp_tail" ]]; then
        json_add_diag error FLATPAK-UPDATE-FAIL \
            "flatpak update returned ${update_rc} — last output: ${_fp_tail}"
    else
        json_add_diag error FLATPAK-UPDATE-FAIL "flatpak update returned ${update_rc}"
    fi
    json_count_err
    EXIT_RC=20
elif echo "${update_out}" | grep -q "Nothing to do"; then
    print_ok "all up to date"
    json_add_item id="flatpak:update" action="upgrade" result="noop"
    json_count_ok
else
    print_ok "updates applied"
    # Parse "Updating <app>..." lines for items
    while IFS= read -r line; do
        if [[ "$line" == *"Updating "* ]]; then
            app=$(echo "$line" | grep -oP 'Updating \K\S+')
            [[ -n "$app" ]] && json_add_item id="flatpak:upgrade:${app}" \
                action="upgrade" result="ok"
        fi
    done <<< "${update_out}"
    json_add_item id="flatpak:update" action="upgrade" result="ok"
    json_count_ok
fi

# ── 3. Install missing apps from config ──────────────────────────────────────
if [[ -f "$CONFIG_FLATPAK" ]]; then
    flatpak_listing=$(flatpak list --app --columns=application,version 2>/dev/null || true)
    while IFS= read -r app_id; do
        [[ -z "$app_id" ]] && continue
        _ver=$(echo "$flatpak_listing" | awk -v id="$app_id" '$1==id{print $2; exit}')
        if [[ -n "$_ver" ]]; then
            # Sesja 57: bidirectional from=/to= so SPA inventory paints.
            json_add_item id="flatpak:configured:${app_id}" action="present" \
                from="${_ver}" to="${_ver}" result="ok"
            json_count_ok
        else
            print_step "flatpak install ${app_id}"
            if flatpak install --noninteractive flathub "$app_id" >> "${LOG_FILE}" 2>&1; then
                print_ok
                json_add_item id="flatpak:install:${app_id}" action="install" result="ok"
                json_count_ok
            else
                print_warn "install failed"
                json_add_item id="flatpak:install:${app_id}" action="install" result="failed"
                json_add_diag warn FLATPAK-INSTALL-FAIL "flatpak install ${app_id} failed"
                json_count_warn
                [[ $EXIT_RC -eq 0 ]] && EXIT_RC=1
            fi
        fi
    done < <(parse_config_names_filtered flatpak "$CONFIG_FLATPAK")
fi

exit $EXIT_RC
