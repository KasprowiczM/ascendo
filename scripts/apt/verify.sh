#!/usr/bin/env bash
# =============================================================================
# scripts/apt/verify.sh — Post-apply APT verification (read-only)
#
# Verifies:
#   • every package from config/apt-packages.list is installed
#   • for tracked desktop apps (code, antigravity), installed == candidate
#   • no broken dpkg states remain
#   • no NVIDIA package in iF state
#   • reboot-required flag is reported (warn, not error)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

json_init verify apt
json_register_exit_trap "${JSON_OUT:-}"

print_header "APT — verify"

if ! has_cmd dpkg; then
    json_add_diag error MISSING-TOOL "dpkg not found"
    json_count_err
    exit 10
fi

CONFIG_APT="${SCRIPT_DIR}/config/apt-packages.list"
EXIT_RC=0

# ── 1. Configured packages installed? ────────────────────────────────────────
if [[ -f "$CONFIG_APT" ]]; then
    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        if apt_installed "$pkg"; then
            ver=$(apt_pkg_version "$pkg")
            json_add_item id="apt:installed:${pkg}" action="present" \
                from="${ver}" to="${ver}" result="ok"
            json_count_ok
        else
            json_add_item id="apt:installed:${pkg}" action="present" result="failed"
            json_add_diag warn APT-MISSING "configured package not installed: ${pkg}"
            json_count_warn
            EXIT_RC=1
        fi
    done < <(parse_config_names "$CONFIG_APT")
fi

# ── 2. Tracked desktop apps drift ────────────────────────────────────────────
for pkg in code antigravity; do
    apt_installed "$pkg" || continue
    inst=$(apt_pkg_version "$pkg")
    cand=$(apt_pkg_candidate "$pkg")
    if [[ -n "$cand" && "$cand" != "(none)" && "$cand" != "$inst" ]]; then
        json_add_item id="apt:drift:${pkg}" action="version-drift" \
            from="${inst}" to="${cand}" result="warn"
        json_add_diag warn APT-VERSION-DRIFT "${pkg}: installed=${inst} candidate=${cand}"
        json_count_warn
        EXIT_RC=1
    fi
done

# ── 3. Broken dpkg state ──────────────────────────────────────────────────────
broken=$(dpkg -l 2>/dev/null | awk '/^iF|^iU|^iH/{print $2}' || true)
if [[ -n "$broken" ]]; then
    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        json_add_item id="apt:broken:${pkg}" action="dpkg-state" result="failed"
        json_add_diag error DPKG-BROKEN "${pkg} in broken dpkg state"
        json_count_err
    done <<< "$broken"
    EXIT_RC=1
fi

# ── 4. NVIDIA driver health (informational) ──────────────────────────────────
if has_cmd nvidia-smi; then
    if nvidia-smi >/dev/null 2>&1; then
        json_add_diag info NVIDIA-SMI-OK "nvidia-smi responsive"
    else
        json_add_diag warn NVIDIA-SMI-DOWN "nvidia-smi not responsive (reboot or DKMS rebuild may be needed)"
        json_count_warn
        EXIT_RC=1
    fi
fi

# ── 5. Reboot flag ────────────────────────────────────────────────────────────
if [[ -f /var/run/reboot-required ]]; then
    json_set_needs_reboot 1
    json_add_diag warn REBOOT-PENDING "reboot required after apply"
    EXIT_RC=1
fi

[[ $EXIT_RC -eq 0 ]] && print_ok "apt verify clean"
exit $EXIT_RC
