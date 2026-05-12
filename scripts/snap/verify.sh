#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/json.sh"

json_init verify snap
json_register_exit_trap "${JSON_OUT:-}"

print_header "Snap — verify"

if ! has_cmd snap; then
    json_add_diag info SNAP-MISSING "snapd not installed"
    exit 0
fi

CONFIG_SNAP="${SCRIPT_DIR}/config/snap-packages.list"
EXIT_RC=0

# Configured snaps installed?
if [[ -f "$CONFIG_SNAP" ]]; then
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        pkg=$(echo "$line" | awk '{print $1}')
        if snap_installed "$pkg"; then
            ver=$(snap_version "$pkg")
            json_add_item id="snap:installed:${pkg}" action="present" \
                from="${ver}" to="${ver}" result="ok"
            json_count_ok
        else
            json_add_item id="snap:installed:${pkg}" action="present" result="failed"
            json_add_diag warn SNAP-MISSING-CONFIG "configured snap not installed: ${pkg}"
            json_count_warn
            EXIT_RC=1
        fi
    done < <(parse_config_lines "$CONFIG_SNAP")
fi

# Outdated after apply?
# Canonical's edge can publish new revisions between our apply and verify
# phases (minutes apart). Treat near-time-window deltas as INFO, not WARN —
# the user will pick them up on the next run.
if outdated_count=$(_snap_cmd refresh --list 2>/dev/null | tail -n +2 | grep -cE '\S' || true); then
    if [[ "${outdated_count:-0}" -gt 0 ]]; then
        outdated_names=$(_snap_cmd refresh --list 2>/dev/null | tail -n +2 | awk '{print $1}' | paste -sd', ' || true)
        json_add_diag info SNAP-NEW-REVISION "${outdated_count} snaps got new revisions after apply (${outdated_names:-?}); will pick up on next run"
        # Do NOT bump warn counter — this is normal churn, not a failure.
    fi
fi

exit $EXIT_RC
