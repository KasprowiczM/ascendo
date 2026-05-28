#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

json_init verify drivers
json_register_exit_trap "${JSON_OUT:-}"

detect_gpu
EXIT_RC=0

if [[ "${HAS_NVIDIA:-0}" -eq 1 ]]; then
    if has_cmd nvidia-smi && nvidia-smi >/dev/null 2>&1; then
        smi=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1)
        json_add_item id="drivers:nvidia-smi" action="health" result="ok" details="${smi}"
        json_count_ok
    else
        # Match the severity check + apply use for the same condition:
        # nvidia-smi unresponsive is typically a kernel/DKMS mismatch
        # (running kernel ≠ kernel the driver was built against) that
        # pre-exists the run, so we don't escalate to error here. The
        # warn surfaces in the report and pairs with the reboot-required
        # banner that DKMS already sets via /var/run/reboot-required.
        running_kernel=$(uname -r 2>/dev/null || echo unknown)
        dkms_state=$(dkms status 2>/dev/null | grep -E "^nvidia/" | head -1 || echo "")
        details="kernel=${running_kernel}; dkms=${dkms_state:-none}"
        json_add_item id="drivers:nvidia-smi" action="health" result="warn" details="${details}"
        json_add_diag warn NVIDIA-SMI-DOWN "nvidia-smi not responsive (reboot or DKMS rebuild may be needed; ${details})"
        json_count_warn
        # Do not set EXIT_RC=1 — pre-existing kernel/DKMS mismatches
        # aren't apply failures and shouldn't poison the run status.
    fi
fi

# Broken NVIDIA dpkg state is a critical post-apply signal
broken=$(dpkg -l 'nvidia-*' 'libnvidia-*' 2>/dev/null | awk '/^iF/{print $2}' || true)
if [[ -n "$broken" ]]; then
    json_add_diag error DPKG-NVIDIA-BROKEN "broken NVIDIA dpkg state after apply: $(echo "$broken" | tr '\n' ' ')"
    json_count_err
    EXIT_RC=1
fi

if [[ -f /var/run/reboot-required ]]; then
    json_set_needs_reboot 1
fi

exit $EXIT_RC
