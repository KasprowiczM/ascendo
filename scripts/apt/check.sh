#!/usr/bin/env bash
# =============================================================================
# scripts/apt/check.sh — Read-only APT health check
#
# Inspects:
#   • required tools (apt-get, dpkg, apt-cache, apt-mark)
#   • freshness of cached source lists
#   • broken dpkg states (iF/iU)
#   • NVIDIA hold state (informational)
#   • current reboot-required flag
#
# Emits JSON sidecar at $JSON_OUT (schema ascendo/v1).
# Exit codes: 0 ok, 1 warn, 10 missing prerequisite.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/detect.sh
source "${SCRIPT_DIR}/lib/detect.sh"
# shellcheck source=lib/json.sh
source "${SCRIPT_DIR}/lib/json.sh"
source "${SCRIPT_DIR}/lib/progress.sh"

json_init check apt
json_register_exit_trap "${JSON_OUT:-}"

EXIT_RC=0

print_header "APT — check"

# ── 1. Required tools ─────────────────────────────────────────────────────────
for tool in apt-get apt-cache dpkg apt-mark apt-config; do
    if ! has_cmd "$tool"; then
        json_add_diag error MISSING-TOOL "required command not found: ${tool}"
        json_count_err
        print_error "missing tool: ${tool}"
        EXIT_RC=10
    fi
done
[[ $EXIT_RC -eq 10 ]] && exit 10

# ── 2. Source list freshness (informational) ─────────────────────────────────
# /var/lib/apt/lists last update — if older than 24h emit advisory
LISTS_DIR=/var/lib/apt/lists
if [[ -d "$LISTS_DIR" ]]; then
    newest=$(stat -c %Y "$LISTS_DIR" 2>/dev/null || echo 0)
    now=$(date +%s)
    age_h=$(( (now - newest) / 3600 ))
    json_add_diag info APT-LISTS-AGE "apt source lists last refreshed ${age_h}h ago"
    if [[ $age_h -gt 24 ]]; then
        json_add_advisory "Run 'sudo apt-get update' — source lists are ${age_h}h old"
        EXIT_RC=1
    fi
fi

# ── 3. Outdated packages (no sudo) ────────────────────────────────────────────
print_section "Scanning APT package universe"
print_step "apt list --upgradable"
mapfile -t upgradable < <(apt list --upgradable 2>/dev/null | tail -n +2 || true)
print_ok
out_count=0
detail=""
for line in "${upgradable[@]}"; do
    [[ -z "$line" ]] && continue
    # Format: pkg/release version arch [upgradable from: oldver]
    pkg=${line%%/*}
    new=$(echo "$line" | awk '{print $2}')
    old=$(echo "$line" | grep -oP 'upgradable from: \K\S+' | tr -d ']' || true)
    json_add_item id="apt:upgrade:${pkg}" action="upgrade" \
        from="${old}" to="${new}" result="noop"
    detail+="${pkg}: ${old} → ${new}"$'\n'
    out_count=$((out_count + 1))
done
print_found apt "$out_count" "$detail"
json_add_diag info APT-OUTDATED "${out_count} packages have updates"

# ── 4. Broken dpkg state ──────────────────────────────────────────────────────
broken=$(dpkg -l 2>/dev/null | awk '/^iF|^iU|^iH/{print $2}' || true)
if [[ -n "$broken" ]]; then
    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        json_add_item id="apt:broken:${pkg}" action="repair" result="failed"
        json_add_diag warn DPKG-BROKEN "${pkg} is in a broken dpkg state (iF/iU)"
        json_count_warn
    done <<< "$broken"
    json_add_advisory "Run: sudo dpkg --configure -a"
    print_warn "broken dpkg packages detected"
    EXIT_RC=1
fi

# ── 5. NVIDIA hold state (informational) ──────────────────────────────────────
held=$(apt-mark showhold 2>/dev/null | grep -E '^(nvidia|libnvidia)' || true)
if [[ -n "$held" ]]; then
    n=$(echo "$held" | wc -l | awk '{print $1}')
    json_add_diag info APT-NVIDIA-HOLD "${n} NVIDIA package(s) currently held"
fi

# ── 6. Reboot flag ────────────────────────────────────────────────────────────
if [[ -f /var/run/reboot-required ]]; then
    json_set_needs_reboot 1
    json_add_diag warn REBOOT-PENDING "kernel or driver update awaiting reboot"
    EXIT_RC=1
fi

if [[ $EXIT_RC -eq 0 ]]; then
    json_count_ok
    print_ok "apt check clean"
fi

exit $EXIT_RC
