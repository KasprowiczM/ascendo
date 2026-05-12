#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"
source "${SCRIPT_DIR}/lib/progress.sh"

json_init check drivers
json_register_exit_trap "${JSON_OUT:-}"

print_header "Drivers & firmware — check"
detect_gpu

# NVIDIA driver presence + smi + available newer driver
if [[ "${HAS_NVIDIA:-0}" -eq 1 ]]; then
    print_step "scanning NVIDIA driver state"
    nv_pkg=$(dpkg -l 'nvidia-driver-*' 2>/dev/null | awk '/^ii/{print $2}' | head -1)
    nv_ver=$(dpkg -l 'nvidia-driver-*' 2>/dev/null | awk '/^ii/{print $3}' | head -1)
    # SPA overlay maps from→installed, to→candidate; if they differ the
    # row paints outdated. For an informational "present" item both
    # must be the version. Package name moves to details. (Sesja 56)
    json_add_item id="drivers:nvidia:installed" action="present" \
        from="${nv_ver}" to="${nv_ver}" result="ok" \
        details="package=${nv_pkg}"
    smi=""
    if has_cmd nvidia-smi && nvidia-smi >/dev/null 2>&1; then
        smi=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1)
        json_add_diag info NVIDIA-SMI "${smi}"
    else
        json_add_diag warn NVIDIA-SMI-DOWN "nvidia-smi not responsive"
        json_count_warn
    fi
    print_ok
    # Available driver upgrade?
    # Use apt-cache policy (Candidate) which respects pinning, NOT madison
    # which lists every version regardless of priority. Then dpkg-compare to
    # confirm candidate is actually NEWER than installed (the security pocket
    # often holds a newer 0ubuntu0.* than -updates' 0ubuntu1.* and vice versa).
    cand=$(apt_pkg_candidate "$nv_pkg" 2>/dev/null || echo "")
    detail="installed: ${nv_pkg} ${nv_ver}"
    [[ -n "$smi" ]] && detail+=$'\n'"runtime:   ${smi}"
    upgrade_real=0
    if [[ -n "$cand" && "$cand" != "$nv_ver" ]]; then
        if dpkg --compare-versions "$cand" gt "$nv_ver" 2>/dev/null; then
            upgrade_real=1
        fi
    fi
    if [[ $upgrade_real -eq 1 ]]; then
        detail+=$'\n'"newer:     ${cand}    [dpkg verdict: ${cand} > ${nv_ver}]"
        detail+=$'\n'"           (held by default — re-run with --nvidia to apply)"
        json_add_diag warn NVIDIA-UPGRADE-HELD "NVIDIA driver upgrade ${nv_ver} → ${cand} available but held"
        json_add_advisory "Re-run with --nvidia to apply driver upgrade ${nv_ver} → ${cand}"
        print_found nvidia 1 "$detail"
    elif [[ -n "$cand" && "$cand" != "$nv_ver" ]]; then
        # Candidate exists but is OLDER (e.g. -security has older than -updates).
        detail+=$'\n'"candidate: ${cand}  (older than installed — no upgrade needed)"
        json_add_diag info NVIDIA-CANDIDATE-OLDER "apt candidate ${cand} ≤ installed ${nv_ver}"
        print_found nvidia 0 "$detail"
    else
        print_found nvidia 0 "$detail"
    fi
fi

# Firmware
if has_cmd fwupdmgr; then
    print_step "fwupdmgr get-updates"
    chk=$(fwupdmgr get-updates 2>&1 || true)
    print_ok
    if echo "$chk" | grep -qiE "No upgrades for|No updates available"; then
        json_add_diag info FIRMWARE-CURRENT "fwupd reports no updates"
        print_found firmware 0 ""
    elif echo "$chk" | grep -qiE "upgrade available|updates available"; then
        json_add_diag warn FIRMWARE-AVAILABLE "fwupd has firmware updates available"
        json_count_warn
        # Extract device names
        fw_lines=$(echo "$chk" | grep -E '^\s*├\s|^\s*│\s+├\s|Update:' | head -10)
        print_found firmware 1 "$fw_lines"
    fi
else
    json_add_diag info FIRMWARE-NO-FWUPD "fwupdmgr not installed"
fi

if [[ -f /var/run/reboot-required ]]; then
    json_set_needs_reboot 1
    json_add_diag warn REBOOT-PENDING "kernel/driver update awaiting reboot"
fi
exit 0
