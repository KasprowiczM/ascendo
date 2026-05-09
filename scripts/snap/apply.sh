#!/usr/bin/env bash
# =============================================================================
# scripts/snap/apply.sh — Native snap refresh + missing-from-config install.
# Replaces previous delegation to legacy scripts/update-snap.sh.
#
# Exit codes: 0 ok, 1 warn, 10 missing prereq, 20 apply failed.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"
source "${SCRIPT_DIR}/lib/progress.sh"

export INVENTORY_SILENT=1
json_init apply snap
json_register_exit_trap "${JSON_OUT:-}"

print_header "Snap — apply"

if ! has_cmd snap; then
    json_add_diag info SNAP-MISSING "snapd not installed — nothing to do"
    exit 0
fi

require_sudo

CONFIG_SNAP="${SCRIPT_DIR}/config/snap-packages.list"
EXIT_RC=0

# ── 1. snap refresh (with running-apps fallback) ─────────────────────────────
print_section "Refreshing snaps"
# Pre-scan: how many will be refreshed?
mapfile -t _to_refresh < <(_snap_cmd refresh --list 2>/dev/null | tail -n +2 | awk 'NF{print $1}')
n_pre=${#_to_refresh[@]}
if (( n_pre > 0 )); then
    print_info "Will refresh ${n_pre} snap(s): ${_to_refresh[*]}"
    progress_start "snap-refresh" "$n_pre" "snap refresh"
fi
print_step "snap refresh"
_stream_emit ">>> snap refresh"
refresh_out=$(sudo snap refresh 2>&1) || refresh_rc=$? || true
echo "${refresh_out}" >> "${LOG_FILE}"
# Mirror to SSE stream log (best-effort) so Run Center sees live progress.
if [[ -n "${ASCENDO_STREAM_LOG:-}" && -n "${refresh_out}" ]]; then
    printf '%s\n' "${refresh_out}" >> "${ASCENDO_STREAM_LOG}" 2>/dev/null || true
fi

if echo "${refresh_out}" | grep -q "All snaps up to date"; then
    if (( n_pre > 0 )); then
        # snapd auto-refresh happened between our check and apply, OR a snap
        # was held back without telling us. Either way, surface it clearly.
        print_warn "snap reported 'all up to date' but check found ${n_pre} pending: ${_to_refresh[*]}"
        print_info "Most likely snapd's automatic refresh already applied them in the background."
        print_info "Verify with: snap changes  |  snap list ${_to_refresh[*]}"
        json_add_diag info SNAP-AUTO-REFRESHED "snapd appears to have auto-refreshed ${n_pre} snap(s): ${_to_refresh[*]} (snap reported up-to-date in apply)"
    else
        print_ok "all up to date"
    fi
    json_add_item id="snap:refresh" action="refresh" result="noop"
    json_count_ok
elif echo "${refresh_out}" | grep -qi "running apps"; then
    blocked=$(echo "${refresh_out}" | grep -oE 'running apps \([^)]*\)' | head -1)
    blocked_snap=$(echo "${refresh_out}" | grep -oE '"[^"]*" has running apps' | head -1 | tr -d '"' | awk '{print $1}')
    print_warn "Snap refresh blocked: ${blocked:-running apps}"
    if [[ -n "$blocked_snap" ]]; then
        print_warn "→ '${blocked_snap}' is running. Close it for a clean refresh, or wait for --ignore-running fallback."
        json_add_diag warn SNAP-APP-RUNNING "Close '${blocked_snap}' to allow a clean snap refresh; falling back to --ignore-running"
    else
        print_info "If you're using a snap right now (e.g. Firefox), close it for cleanest results."
        json_add_diag warn SNAP-RUNNING "initial refresh blocked by running apps: ${blocked}"
    fi
    print_step "snap refresh --ignore-running (fallback)"
    refresh_out2=$(sudo snap refresh --ignore-running 2>&1) || true
    echo "${refresh_out2}" >> "${LOG_FILE}"
    if echo "${refresh_out2}" | grep -qi "error:"; then
        print_error "snap refresh failed (after --ignore-running)"
        json_add_item id="snap:refresh" action="refresh" result="failed"
        json_count_err
        EXIT_RC=20
    else
        print_ok "completed with --ignore-running"
        json_add_item id="snap:refresh" action="refresh" result="ok"
        json_count_ok
        # Parse refreshed lines
        while IFS= read -r line; do
            if echo "$line" | grep -qE 'refreshed$'; then
                pkg=$(echo "$line" | awk '{print $1}')
                ver=$(echo "$line" | grep -oP '\(\K[^)]+' | head -1 || true)
                json_add_item id="snap:upgrade:${pkg}" action="refresh" \
                    to="${ver}" result="ok"
            fi
        done <<< "${refresh_out2}"
    fi
elif echo "${refresh_out}" | grep -qi "error:"; then
    print_error "snap refresh failed"
    json_add_item id="snap:refresh" action="refresh" result="failed"
    # Surface the last 12 non-empty lines of refresh output (capped at
    # 1500 chars) so the operator sees the actual failure context, not
    # just the first "error:" line.
    _snap_tail="$(printf '%s\n' "${refresh_out}" | awk 'NF{print}' \
                  | tail -n 12 | tr '\t' ' ' | head -c 1500 || true)"
    if [[ -n "$_snap_tail" ]]; then
        json_add_diag error SNAP-REFRESH-FAIL \
            "snap refresh failed — last output: ${_snap_tail}"
    else
        json_add_diag error SNAP-REFRESH-FAIL \
            "$(echo "${refresh_out}" | grep -i 'error:' | head -1)"
    fi
    json_count_err
    EXIT_RC=20
else
    print_ok "refresh completed"
    json_add_item id="snap:refresh" action="refresh" result="ok"
    json_count_ok
    while IFS= read -r line; do
        if echo "$line" | grep -qE 'refreshed$'; then
            pkg=$(echo "$line" | awk '{print $1}')
            ver=$(echo "$line" | grep -oP '\(\K[^)]+' | head -1 || true)
            json_add_item id="snap:upgrade:${pkg}" action="refresh" \
                to="${ver}" result="ok"
            (( n_pre > 0 )) && progress_step "${pkg} → ${ver:-?}" ok
        fi
    done <<< "${refresh_out}"
fi
(( n_pre > 0 )) && progress_done

# ── 2. Install missing snaps from config ─────────────────────────────────────
if [[ -f "$CONFIG_SNAP" ]]; then
    print_section "Configured snaps"
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        pkg=$(echo "$line" | awk '{print $1}')
        flags=$(echo "$line" | awk '{$1=""; print $0}' | xargs)
        if snap_installed "$pkg"; then
            ver=$(snap_version "$pkg")
            json_add_item id="snap:configured:${pkg}" action="present" \
                to="${ver}" result="ok"
            json_count_ok
        else
            print_step "snap install ${pkg} ${flags}"
            install_args=("$pkg")
            [[ -n "$flags" ]] && install_args+=($flags)
            if sudo snap install "${install_args[@]}" >> "${LOG_FILE}" 2>&1; then
                print_ok
                json_add_item id="snap:install:${pkg}" action="install" result="ok"
                json_count_ok
            else
                print_warn "install failed"
                json_add_item id="snap:install:${pkg}" action="install" result="failed"
                json_add_diag warn SNAP-INSTALL-FAIL "snap install ${pkg} failed"
                json_count_warn
                [[ $EXIT_RC -eq 0 ]] && EXIT_RC=1
            fi
        fi
    done < <(parse_config_lines_filtered snap "$CONFIG_SNAP")
fi

# ── 3. Reboot signal? ─────────────────────────────────────────────────────────
[[ -f /var/run/reboot-required ]] && json_set_needs_reboot 1

exit $EXIT_RC
