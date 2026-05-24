#!/usr/bin/env bash
# =============================================================================
# scripts/health-check.sh — Post-run system health snapshot.
#
# Lightweight checks (no sudo required for the read-only ones):
#   • systemctl --failed             → failed unit count
#   • dmesg                          → recent error/warn lines (last 5 minutes)
#   • disk free                      → /, /home, /var thresholds
#   • reboot-required                → /var/run/reboot-required flag
#   • outdated count                 → from latest run.json (if available)
#
# Output:
#   default → human text on stdout
#   --json  → machine-readable {score, issue_count, issues[]}
#
# Score:
#   Start at 100. Each issue subtracts a weight. Final clamped to [0, 100].
# =============================================================================
set -euo pipefail

JSON_MODE=0
[[ "${1:-}" == "--json" ]] && JSON_MODE=1

SCORE=100
ISSUES=()

_add() {
    local sev="$1" weight="$2" msg="$3"
    SCORE=$(( SCORE - weight ))
    ISSUES+=("${sev}|${weight}|${msg}")
}

# 1. Failed systemd units (read-only, no sudo)
if command -v systemctl >/dev/null 2>&1; then
    failed=$(systemctl --failed --no-legend --plain 2>/dev/null | wc -l || echo 0)
    if [[ "$failed" -gt 0 ]]; then
        units=$(systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}' | tr '\n' ',' | sed 's/,$//')
        _add err 15 "$failed failed systemd unit(s): ${units}"
    fi
fi

# 2. Recent dmesg severity (kernel ring buffer, may need sudo on hardened systems)
if dmesg -T --level=err,crit,alert,emerg 2>/dev/null | tail -20 | grep -q .; then
    cnt=$(dmesg -T --level=err,crit,alert,emerg 2>/dev/null | tail -200 | wc -l || echo 0)
    if [[ "$cnt" -gt 0 ]]; then
        _add warn 5 "${cnt} kernel error(s) in recent dmesg"
    fi
fi

# 3. Disk free (root + home, var)
for mp in / /home /var; do
    [[ -d "$mp" ]] || continue
    pct=$(df -P "$mp" 2>/dev/null | awk 'NR==2{print $5}' | tr -d '%')
    [[ "$pct" =~ ^[0-9]+$ ]] || continue
    if   [[ "$pct" -ge 95 ]]; then _add err  20 "${mp} is ${pct}% full"
    elif [[ "$pct" -ge 85 ]]; then _add warn  8 "${mp} is ${pct}% full"
    fi
done

# 4. Reboot pending
if [[ -f /var/run/reboot-required ]]; then
    pkgs=""
    [[ -f /var/run/reboot-required.pkgs ]] && \
        pkgs=$(paste -sd', ' /var/run/reboot-required.pkgs 2>/dev/null || true)
    _add warn 3 "Reboot required${pkgs:+ (${pkgs})}"
fi

# 5. Last run status (if recently completed)
RUNS_DIR="${UA_RUNS_DIR:-${HOME}/Dev_Env/Ascendo/logs/runs}"
if [[ -d "$RUNS_DIR" ]]; then
    last=$(ls -1dt "${RUNS_DIR}"/*/ 2>/dev/null | head -1 | sed 's:/$::' || true)
    if [[ -n "$last" && -f "${last}/run.json" ]]; then
        st=$(awk -F\" '/"status"/{print $4; exit}' "${last}/run.json" 2>/dev/null || true)
        case "$st" in
            failed) _add err 25 "last run status: failed (${last##*/})" ;;
            warn)   _add warn 4 "last run status: warn (${last##*/})" ;;
        esac
    fi
fi

[[ $SCORE -lt 0 ]]   && SCORE=0
[[ $SCORE -gt 100 ]] && SCORE=100

if [[ $JSON_MODE -eq 1 ]]; then
    issues_json=""
    for it in "${ISSUES[@]:-}"; do
        [[ -z "$it" ]] && continue
        sev="${it%%|*}"; rest="${it#*|}"; weight="${rest%%|*}"; msg="${rest#*|}"
        msg_esc=${msg//\\/\\\\}; msg_esc=${msg_esc//\"/\\\"}
        [[ -n "$issues_json" ]] && issues_json+=","
        issues_json+="{\"severity\":\"${sev}\",\"weight\":${weight},\"msg\":\"${msg_esc}\"}"
    done
    printf '{"score":%d,"issue_count":%d,"issues":[%s],"checked_at":"%s"}\n' \
        "$SCORE" "${#ISSUES[@]}" "$issues_json" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
else
    echo "Health score: ${SCORE}/100  (issues: ${#ISSUES[@]})"
    for it in "${ISSUES[@]:-}"; do
        [[ -z "$it" ]] && continue
        sev="${it%%|*}"; rest="${it#*|}"; weight="${rest%%|*}"; msg="${rest#*|}"
        printf '  [%s -%d] %s\n' "$sev" "$weight" "$msg"
    done
fi
