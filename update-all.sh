#!/usr/bin/env bash
# =============================================================================
# update-all.sh — Master update script (thin orchestrator)
#
# Drives the 5-phase per-category pipeline via lib/orchestrator.sh and emits
# JSON sidecars under logs/runs/<run-id>/<category>/<phase>.json (schema v1).
#
# Backward-compat flags (preserved):
#   --no-drivers      Skip driver/firmware category
#   --nvidia          Allow NVIDIA APT upgrade (sets UPGRADE_NVIDIA=1)
#   --dry-run         Run only check+plan, no mutating phases
#   --no-notify       Suppress desktop notification
#   --only <group>    Run only one category (apt|snap|brew|npm|pip|flatpak|drivers|inventory)
#
# New flags:
#   --profile <name>  Profile from config/profiles.toml (quick|safe|full). Default: full
#   --phase  <name>   Run only one phase (check|plan|apply|verify|cleanup)
#   --run-id <id>     Override generated run id
#   --snapshot        Take a pre-apply snapshot (timeshift/etckeeper) before apt:apply
#
# Groups for --only (categories): apt | snap | brew | npm | pip | flatpak | drivers | inventory
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/detect.sh
source "${SCRIPT_DIR}/lib/detect.sh"
# shellcheck source=lib/orchestrator.sh
source "${SCRIPT_DIR}/lib/orchestrator.sh"
# shellcheck source=lib/i18n.sh
source "${SCRIPT_DIR}/lib/i18n.sh"

# Print Ascendo ASCII banner once per run (skip when ORCH_QUIET=1).
if [[ "${ORCH_QUIET:-0}" != "1" && -f "${SCRIPT_DIR}/branding/banner.txt" ]]; then
    cat "${SCRIPT_DIR}/branding/banner.txt"
fi

# Suppress per-script inventory calls — orchestrator runs inventory once.
export INVENTORY_SILENT=1

# ── Parse arguments ───────────────────────────────────────────────────────────
NO_DRIVERS=0
DRY_RUN=0
NO_NOTIFY=0
ONLY=""
UPGRADE_NVIDIA=0
PROFILE="full"
PHASE=""
RUN_ID=""
TAKE_SNAPSHOT=0
BUDGET_SECONDS=0     # 0 = no limit; set with --budget Nm or --budget Ns
HEALTH_CHECK=1       # post-run health audit (0 to skip)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-drivers)   NO_DRIVERS=1 ;;
        --nvidia)       UPGRADE_NVIDIA=1 ;;
        --dry-run)      DRY_RUN=1 ;;
        --no-notify)    NO_NOTIFY=1 ;;
        --only)
            shift
            [[ $# -gt 0 ]] || { echo "--only requires a group"; exit 2; }
            ONLY="$1"
            case "$ONLY" in
                apt|snap|brew|npm|pip|flatpak|drivers|inventory) ;;
                *) echo "Invalid --only group: ${ONLY}"; exit 2 ;;
            esac ;;
        --profile)
            shift
            [[ $# -gt 0 ]] || { echo "--profile requires a name"; exit 2; }
            PROFILE="$1"
            case "$PROFILE" in
                quick|safe|full) ;;
                *) echo "Invalid --profile: ${PROFILE} (use quick|safe|full)"; exit 2 ;;
            esac ;;
        --phase)
            shift
            [[ $# -gt 0 ]] || { echo "--phase requires a name"; exit 2; }
            PHASE="$1"
            case "$PHASE" in
                check|plan|apply|verify|cleanup) ;;
                *) echo "Invalid --phase: ${PHASE}"; exit 2 ;;
            esac ;;
        --run-id)
            shift
            [[ $# -gt 0 ]] || { echo "--run-id requires a value"; exit 2; }
            RUN_ID="$1" ;;
        --snapshot)
            TAKE_SNAPSHOT=1 ;;
        --budget)
            shift
            [[ $# -gt 0 ]] || { echo "--budget requires Ns | Nm | Nh"; exit 2; }
            case "$1" in
                *s) BUDGET_SECONDS="${1%s}" ;;
                *m) BUDGET_SECONDS=$(( ${1%m} * 60 )) ;;
                *h) BUDGET_SECONDS=$(( ${1%h} * 3600 )) ;;
                *)  BUDGET_SECONDS="$1" ;;
            esac
            [[ "$BUDGET_SECONDS" =~ ^[0-9]+$ ]] || { echo "--budget invalid"; exit 2; } ;;
        --no-health)
            HEALTH_CHECK=0 ;;
        -h|--help)
            cat <<'EOF'
Usage: update-all.sh [options]

Backward-compat:
  --no-drivers      Skip driver/firmware category
  --nvidia          Allow NVIDIA APT upgrade (default: held)
  --dry-run         Run only check+plan (no mutations)
  --no-notify       Suppress desktop notification
  --only <group>    Run only one category

New:
  --profile <name>  quick | safe | full   (default: full)
  --phase  <name>   check | plan | apply | verify | cleanup
                    (omit to run the profile's phase set)
  --run-id <id>     Override the generated run id
  --snapshot        Pre-apply snapshot (timeshift/etckeeper)

Groups: apt | snap | brew | npm | pip | flatpak | drivers | inventory
EOF
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

export UPGRADE_NVIDIA

# ── Detect optional package managers ──────────────────────────────────────────
detect_package_managers
NO_BREW=$([[ $HAS_BREW    -eq 0 ]] && echo 1 || echo 0)
NO_SNAP=$([[ $HAS_SNAP    -eq 0 ]] && echo 1 || echo 0)
NO_FLATPAK=$([[ $HAS_FLATPAK -eq 0 ]] && echo 1 || echo 0)

# ── Compose category list per profile + flags ─────────────────────────────────
case "$PROFILE" in
    quick) DEFAULT_PHASES=(check) ;;
    safe)  DEFAULT_PHASES=(check plan apply verify cleanup) ;;
    full)  DEFAULT_PHASES=(check plan apply verify cleanup) ;;
esac

# Honor --phase override
if [[ -n "$PHASE" ]]; then
    PHASES=("$PHASE")
else
    PHASES=("${DEFAULT_PHASES[@]}")
fi

ALL_CATEGORIES=(apt snap brew npm pip flatpak drivers inventory)

active_categories() {
    local cat
    for cat in "${ALL_CATEGORIES[@]}"; do
        # --only filter
        if [[ -n "$ONLY" && "$cat" != "$ONLY" ]]; then continue; fi
        # availability gates
        case "$cat" in
            snap)    [[ "$NO_SNAP"    -eq 1 ]] && continue ;;
            brew)    [[ "$NO_BREW"    -eq 1 ]] && continue ;;
            flatpak) [[ "$NO_FLATPAK" -eq 1 ]] && continue ;;
            drivers) [[ "$NO_DRIVERS" -eq 1 ]] && continue ;;
        esac
        # quick profile = read-only sweep — exclude drivers + inventory
        if [[ "$PROFILE" == "quick" ]]; then
            [[ "$cat" == "drivers"   ]] && continue
            [[ "$cat" == "inventory" ]] && continue
        fi
        echo "$cat"
    done
}

# Skip categories that don't support a given phase per config/categories.toml.
# We don't parse TOML in bash; orchestrator handles missing phase scripts by
# emitting a 'skipped' sidecar. See lib/orchestrator.sh::orch_run_phase.
phase_supported_for_category() {
    local phase="$1" cat="$2"
    # Inventory only supports 'apply' (single regen pass)
    if [[ "$cat" == "inventory" && "$phase" != "apply" ]]; then return 1; fi
    # Drivers does not have a cleanup phase (high-risk; manual ops only)
    if [[ "$cat" == "drivers" && "$phase" == "cleanup" ]]; then return 1; fi
    return 0
}

# ── Init orchestrator + lock ──────────────────────────────────────────────────
[[ -n "$RUN_ID" ]] && orch_init "$RUN_ID" || orch_init ""
orch_set_dry_run "$DRY_RUN"
orch_set_profile "$PROFILE"

print_header "Ubuntu System Update — $(date '+%Y-%m-%d %H:%M:%S')"
print_info "Host    : $(hostname)"
print_info "OS      : $(lsb_release -ds 2>/dev/null)"
print_info "Kernel  : $(uname -r)"
print_info "Run id  : ${ORCH_RUN_ID}"
print_info "Profile : ${PROFILE}"
print_info "Phases  : ${PHASES[*]}"
[[ -n "$ONLY" ]]    && print_info "Only    : ${ONLY}"
[[ "$DRY_RUN" -eq 1 ]] && print_warn "DRY RUN — apply/verify/cleanup phases skipped"

# Loud banner for CHECK-ONLY runs so user knows nothing was applied.
_is_apply=0
for p in "${PHASES[@]}"; do
    case "$p" in apply|cleanup) _is_apply=1 ;; esac
done
if [[ $_is_apply -eq 0 ]]; then
    echo
    echo -e "${BOLD}${YELLOW}╔══════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}${YELLOW}║  MODE: CHECK-ONLY — nothing will be installed or changed ║${RESET}"
    echo -e "${BOLD}${YELLOW}╚══════════════════════════════════════════════════════════╝${RESET}"
    echo -e "${DIM}  To actually apply updates: ./update-all.sh --profile safe${RESET}"
    echo -e "${DIM}  To include NVIDIA driver:   ./update-all.sh --nvidia${RESET}"
fi
echo

acquire_project_lock "update-all"

# ── Sudo upfront (only if any mutating phase will run) ───────────────────────
needs_sudo=0
for p in "${PHASES[@]}"; do
    case "$p" in
        apply|cleanup) needs_sudo=1 ;;
    esac
done
UA_ASKPASS_HELPER=""
_ua_cleanup_askpass() {
    [[ -n "${UA_ASKPASS_HELPER}" && -e "${UA_ASKPASS_HELPER}" ]] && rm -f "${UA_ASKPASS_HELPER}"
    UA_ASKPASS_HELPER=""
}

if [[ "$DRY_RUN" -eq 0 && "$needs_sudo" -eq 1 ]]; then
    if [[ -n "${SUDO_ASKPASS:-}" ]] && sudo -A -n -v 2>/dev/null; then
        # Inherit askpass from caller (dashboard / systemd) and trust it.
        :
    elif sudo -n -v 2>/dev/null && [[ -n "${SUDO_ASKPASS:-}" ]]; then
        :
    else
        # Build an in-memory askpass helper so every sudo invocation in every
        # phase script can authenticate without re-prompting. Prompt password
        # ONCE here and never again for the whole run.
        if [[ -n "${SUDO_ASKPASS:-}" ]]; then
            sudo -A -v || { echo -e "${RED}  sudo askpass failed — aborting${RESET}"; exit 1; }
        elif [[ -t 0 && -t 1 ]]; then
            echo -e "${YELLOW}  Sudo password (asked ONCE for the whole run):${RESET}"
            UA_PW=""
            while [[ -z "$UA_PW" ]]; do
                read -r -s -p "  [sudo] password for ${USER}: " UA_PW; echo
                if ! printf '%s\n' "$UA_PW" | sudo -S -p '' -v 2>/dev/null; then
                    echo -e "${RED}  Wrong password, try again.${RESET}"; UA_PW=""
                fi
            done
            ASKPASS_DIR="${XDG_RUNTIME_DIR:-/tmp}/ascendo"
            mkdir -p "$ASKPASS_DIR"; chmod 0700 "$ASKPASS_DIR"
            UA_ASKPASS_HELPER=$(mktemp "${ASKPASS_DIR}/askpass-XXXXXX.sh")
            chmod 0700 "$UA_ASKPASS_HELPER"
            # Embed password as single-quoted shell literal (escape ' as '"'"').
            UA_ESC=${UA_PW//\'/\'\"\'\"\'}
            printf '#!/usr/bin/env bash\nprintf %%s '"'"'%s'"'"'\n' "$UA_ESC" > "$UA_ASKPASS_HELPER"
            unset UA_PW UA_ESC
            export SUDO_ASKPASS="$UA_ASKPASS_HELPER"
            sudo -A -n -v >/dev/null 2>&1 || true
        else
            cat >&2 <<'EOF'
  sudo cache empty and no terminal/askpass available.
  Options:
    • CLI:        sudo -v   (then re-run this script)
    • Dashboard:  click "Authenticate sudo" / POST /sudo/auth
    • Headless:   export SUDO_ASKPASS=/path/to/askpass.sh
EOF
            exit 1
        fi
    fi
    export UPDATE_ALL_SUDO_READY=1
    (while true; do sudo -A -n -v >/dev/null 2>&1 || sudo -n -v >/dev/null 2>&1 || break; sleep 50; done) &
    SUDO_KEEP_PID=$!
    trap 'kill "${SUDO_KEEP_PID}" 2>/dev/null; _ua_cleanup_askpass; true' INT TERM EXIT
fi

# ── Pre-apply snapshot (opt-in) ──────────────────────────────────────────────
# Hardened: timeshift can hang without a TTY (dashboard reproduced it twice).
# We bound the wall time, log the full output to a per-run file, and we never
# block the run — a snapshot failure becomes a warning, not a hang.
SNAPSHOT_ID=""
if [[ "$TAKE_SNAPSHOT" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    if printf '%s\n' "${PHASES[@]}" | grep -qx apply; then
        echo
        echo -e "${BOLD}${BLUE}══ pre-apply snapshot ══${RESET}"
        SNAP_LOG="${ORCH_RUN_DIR}/snapshot.log"
        : >"${SNAP_LOG}"
        # Bounded run: scripts/snapshot/create.sh has its own internal timeout
        # but we wrap once more with a hard outer cap to be safe.
        SNAP_OUT=$(UA_SNAPSHOT_TIMEOUT="${UA_SNAPSHOT_TIMEOUT:-300}" \
            bash "${SCRIPT_DIR}/scripts/snapshot/create.sh" \
            "Ascendo run ${ORCH_RUN_ID}" 2>&1 | tee -a "${SNAP_LOG}" | tail -1 || true)
        if [[ -n "$SNAP_OUT" && "$SNAP_OUT" != *"no snapshot provider"* ]]; then
            SNAPSHOT_ID="$SNAP_OUT"
            print_info "snapshot id: ${SNAPSHOT_ID}"
            echo "$SNAPSHOT_ID" > "${ORCH_RUN_DIR}/snapshot.id"
        else
            print_warn "snapshot skipped or failed — see ${SNAP_LOG} (continuing the run)"
        fi
    fi
fi

# ── Run pipeline: outer = phase, inner = category (DAG-friendly) ─────────────
START_TIME=$(date +%s)
BUDGET_HIT=0

for phase in "${PHASES[@]}"; do
    echo
    echo -e "${BOLD}${BLUE}══ phase: ${phase} ══${RESET}"
    while IFS= read -r cat; do
        [[ -z "$cat" ]] && continue
        phase_supported_for_category "$phase" "$cat" || continue
        # Update budget guard — bail out cleanly if we exceeded the wall-clock
        # cap. Stops new categories from starting; in-flight one finishes.
        if [[ "$BUDGET_SECONDS" -gt 0 ]]; then
            ELAPSED=$(( $(date +%s) - START_TIME ))
            if [[ $ELAPSED -ge $BUDGET_SECONDS ]]; then
                BUDGET_HIT=1
                print_warn "update budget reached (${ELAPSED}s ≥ ${BUDGET_SECONDS}s) — stopping early"
                break 2
            fi
        fi
        orch_run_phase "$cat" "$phase" || true
    done < <(active_categories)
done

END_TIME=$(date +%s)
TOTAL_TIME=$(( END_TIME - START_TIME ))
ELAPSED_STR="$((TOTAL_TIME/60))m $((TOTAL_TIME%60))s"

echo
echo -e "${BOLD}${BLUE}══════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${BLUE}  COMPLETE — ${ELAPSED_STR}${RESET}"
echo -e "${BOLD}${BLUE}══════════════════════════════════════════════════════════${RESET}"

orch_summary_rc=0
orch_summary || orch_summary_rc=$?

# ── Detailed package-level summary (read every sidecar's items array) ────────
# This is the part that was missing — operator now sees what was actually
# changed (or, in CHECK-ONLY mode, what's available).
echo
echo -e "${BOLD}${BLUE}── Detailed package activity ──${RESET}"
python3 - "${ORCH_RUN_DIR}" <<'PY' || true
import json, os, sys
from pathlib import Path
run_dir = Path(sys.argv[1])
counts = {"upgrade":0, "install":0, "refresh":0, "remove":0, "present":0, "noop":0}
result_counts = {"ok":0, "warn":0, "failed":0, "noop":0}
samples = {"upgrade":[], "install":[], "refresh":[], "noop":[]}
for sc in sorted(run_dir.glob("*/*.json")):
    try:
        d = json.loads(sc.read_text(encoding="utf-8"))
    except Exception:
        continue
    if d.get("kind") not in ("apply", "check"):
        continue
    cat = d.get("category", "?")
    for it in d.get("items", []) or []:
        action = it.get("action", "?")
        result = it.get("result", "?")
        counts[action] = counts.get(action, 0) + 1
        result_counts[result] = result_counts.get(result, 0) + 1
        # Keep representative samples with from→to.
        bucket = action if action in samples else None
        if bucket and len(samples[bucket]) < 8:
            tag = f"{cat}:{(it.get('id') or '').split(':')[-1]}"
            frm = it.get("from"); to = it.get("to")
            if frm and to and frm != to:
                samples[bucket].append(f"{tag}: {frm} → {to}")
            elif to:
                samples[bucket].append(f"{tag}: {to}")
            else:
                samples[bucket].append(tag)

# Emit a compact, useful summary.
def num(n, c="0;32"):
    return f"\033[{c}m{n}\033[0m"

print(f"  upgraded : {num(counts.get('upgrade', 0))}")
print(f"  installed: {num(counts.get('install', 0))}")
print(f"  refreshed: {num(counts.get('refresh', 0))}")
print(f"  noop     : {num(counts.get('noop', 0), '0;33')}  (already present / pending)")
for kind in ("upgrade", "install", "refresh", "noop"):
    if samples.get(kind):
        more = counts[kind] - len(samples[kind])
        suffix = f"  …and {more} more" if more > 0 else ""
        print(f"\n  \033[1m{kind}\033[0m:")
        for s in samples[kind]:
            print(f"    · {s}")
        if suffix:
            print(f"    \033[2m{suffix}\033[0m")
PY
echo

# ── Post-run health check (lightweight) ──────────────────────────────────────
# Confidence boost after an apply run: failed services, recent dmesg errors,
# disk pressure. Writes JSON to <run-dir>/health.json so the dashboard can
# show a Health card.
if [[ $HEALTH_CHECK -eq 1 && -f "${SCRIPT_DIR}/scripts/health-check.sh" ]]; then
    HEALTH_OUT="${ORCH_RUN_DIR}/health.json"
    bash "${SCRIPT_DIR}/scripts/health-check.sh" --json > "${HEALTH_OUT}" 2>/dev/null || true
    if [[ -s "${HEALTH_OUT}" ]]; then
        SCORE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('score','?'))" "${HEALTH_OUT}" 2>/dev/null || echo "?")
        ISSUES=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('issue_count',0))" "${HEALTH_OUT}" 2>/dev/null || echo 0)
        echo
        echo -e "${BOLD}${BLUE}── post-run health ──${RESET}"
        echo -e "  ${DIM}score: ${SCORE}/100, issues: ${ISSUES}${RESET}"
        echo -e "  ${DIM}details: ${HEALTH_OUT}${RESET}"
    fi
fi
[[ "$BUDGET_HIT" -eq 1 ]] && print_warn "Run stopped early due to --budget. Re-run to finish remaining phases."

# ── Reboot notice ─────────────────────────────────────────────────────────────
REBOOT_FLAG=0
if [[ -f /var/run/reboot-required ]]; then
    REBOOT_FLAG=1
    REBOOT_PKGS=""
    [[ -f /var/run/reboot-required.pkgs ]] && \
        REBOOT_PKGS=$(paste -sd', ' /var/run/reboot-required.pkgs 2>/dev/null || true)
    echo
    echo -e "${YELLOW}╔══════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${YELLOW}║  ⚠  RESTART REQUIRED                                     ║${RESET}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════════╝${RESET}"
    [[ -n "$REBOOT_PKGS" ]] && echo -e "${DIM}  Pending packages: ${REBOOT_PKGS}${RESET}"
    echo -e "${BOLD}  Run now:${RESET}    sudo systemctl reboot"
    echo -e "${BOLD}  Or later:${RESET}   sudo shutdown -r +5"
    echo -e "${DIM}  Dashboard: open http://127.0.0.1:8765 — banner shows a one-click 'Restart now' button.${RESET}"
    echo
fi

# ── Desktop notification ──────────────────────────────────────────────────────
if [[ $NO_NOTIFY -eq 0 && $DRY_RUN -eq 0 && -f "${SCRIPT_DIR}/scripts/notify.sh" ]]; then
    bash "${SCRIPT_DIR}/scripts/notify.sh" \
        --title "Ubuntu Updates Complete" \
        --time "${ELAPSED_STR}" \
        --errors "${ORCH_FAILED}" \
        $([[ $REBOOT_FLAG -eq 1 ]] && echo "--reboot") \
        2>/dev/null || true
fi

exit "$orch_summary_rc"
