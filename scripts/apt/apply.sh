#!/usr/bin/env bash
# =============================================================================
# scripts/apt/apply.sh — Mutating APT upgrade (sudo required)
#
# Reuses logic from scripts/update-apt.sh but emits structured JSON sidecar.
# Honours UPGRADE_NVIDIA, INVENTORY_SILENT (always 1 here — orchestrator runs
# inventory as its own category).
#
# Exit codes:
#   0  ok
#   1  warn  (some non-critical step returned non-zero)
#   10 missing prerequisite
#   20 apply failed, system in known state
#   30 apply failed, system in unknown state (post-apply verify failed)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/detect.sh
source "${SCRIPT_DIR}/lib/detect.sh"
# shellcheck source=lib/repos.sh
source "${SCRIPT_DIR}/lib/repos.sh"
# shellcheck source=lib/json.sh
source "${SCRIPT_DIR}/lib/json.sh"

# Inventory is owned by the inventory category; never re-run from here.
export INVENTORY_SILENT=1

json_init apply apt
json_register_exit_trap "${JSON_OUT:-}"

print_header "APT — apply"

if ! has_cmd apt-get; then
    json_add_diag error MISSING-TOOL "apt-get not found"
    json_count_err
    exit 10
fi

require_sudo

CONFIG_REPOS="${SCRIPT_DIR}/config/apt-repos.list"
CONFIG_APT="${SCRIPT_DIR}/config/apt-packages.list"
UPGRADE_NVIDIA="${UPGRADE_NVIDIA:-0}"

declare -a NVIDIA_TEMP_HELD=()

_installed_nvidia_packages() {
    dpkg -l 'nvidia-*' 'libnvidia-*' 2>/dev/null | awk '/^[iuh][iUFHWt]/{print $2}'
}

_temporarily_hold_nvidia() {
    local current_holds
    mapfile -t current_holds < <(apt-mark showhold 2>/dev/null || true)
    local nvidia_pkgs
    mapfile -t nvidia_pkgs < <(_installed_nvidia_packages)
    [[ ${#nvidia_pkgs[@]} -eq 0 ]] && return 0
    local pkg held already
    for pkg in "${nvidia_pkgs[@]}"; do
        already=0
        for held in "${current_holds[@]}"; do
            [[ "$pkg" == "$held" ]] && already=1 && break
        done
        [[ $already -eq 1 ]] && continue
        if sudo apt-mark hold "$pkg" >> "${LOG_FILE}" 2>&1; then
            NVIDIA_TEMP_HELD+=("$pkg")
        fi
    done
    [[ ${#NVIDIA_TEMP_HELD[@]} -gt 0 ]] && \
        json_add_diag info APT-NVIDIA-HELD "temporarily held ${#NVIDIA_TEMP_HELD[@]} NVIDIA packages"
}

_restore_nvidia_holds() {
    [[ ${#NVIDIA_TEMP_HELD[@]} -eq 0 ]] && return 0
    sudo apt-mark unhold "${NVIDIA_TEMP_HELD[@]}" >> "${LOG_FILE}" 2>&1 || true
    NVIDIA_TEMP_HELD=()
}

declare -a EXCL_TEMP_HELD=()

# Apply per-user exclusions via apt-mark hold for the duration of this phase.
# Exclusions live in config/exclusions.list as "apt:<package>" lines.
APT_CATEGORY_EXCLUDED=0
_temporarily_hold_excluded_apt() {
    # shellcheck disable=SC1091
    [[ -f "${SCRIPT_DIR}/lib/exclusions.sh" ]] && source "${SCRIPT_DIR}/lib/exclusions.sh"
    declare -F excl_load >/dev/null 2>&1 || return 0
    excl_load
    if declare -F excl_category_skipped >/dev/null && excl_category_skipped apt; then
        # Whole apt category disabled — record and let main flow exit cleanly
        # with a sidecar (don't `exit 0` here, that would lose the JSON output).
        APT_CATEGORY_EXCLUDED=1
        json_add_diag info APT-EXCLUDED-ALL "apt category disabled in config/exclusions.list"
        return 0
    fi
    local current_holds key pkg
    mapfile -t current_holds < <(apt-mark showhold 2>/dev/null || true)
    for key in "${!EXCL_SET[@]}"; do
        [[ "$key" == apt:* ]] || continue
        pkg="${key#apt:}"
        if dpkg -l "$pkg" >/dev/null 2>&1; then
            local already=0
            for h in "${current_holds[@]:-}"; do [[ "$h" == "$pkg" ]] && already=1 && break; done
            [[ $already -eq 1 ]] && continue
            if sudo apt-mark hold "$pkg" >> "${LOG_FILE}" 2>&1; then
                EXCL_TEMP_HELD+=("$pkg")
            fi
        fi
    done
    [[ ${#EXCL_TEMP_HELD[@]} -gt 0 ]] && \
        json_add_diag info APT-USER-HELD "user-excluded packages held: ${#EXCL_TEMP_HELD[@]}"
    return 0
}
_restore_excluded_apt_holds() {
    [[ ${#EXCL_TEMP_HELD[@]} -eq 0 ]] && return 0
    sudo apt-mark unhold "${EXCL_TEMP_HELD[@]}" >> "${LOG_FILE}" 2>&1 || true
    EXCL_TEMP_HELD=()
}

# Compose EXIT handler so that hold-restoration AND JSON sidecar finalize both
# fire on exit (regardless of whether we exit normally or via set -e). The
# previous version overrode the json trap and the apply.json sidecar was never
# written — orchestrator then silently dropped apt:apply from run.json.
#
# Sesja 68: the kill-keepalive step MUST run too. require_sudo (called above)
# installs a trap chain that includes `kill ${SUDO_KEEP_ALIVE_PID}` to stop
# the background sudo-refresher subshell. If we overwrite the trap without
# preserving that kill, the subshell holds the parent's stdio pipes open and
# Python's subprocess.run(capture_output=True) blocks forever on EOF —
# producing the operator-visible "safe update hanged on apt" symptom. The
# kill is wrapped in `|| true` because the keepalive PID may have already
# exited by then; under set -e a failing kill aborts the trap chain.
_apt_apply_on_exit() {
    local rc=$?
    if [[ -n "${SUDO_KEEP_ALIVE_PID:-}" ]]; then
        kill "${SUDO_KEEP_ALIVE_PID}" 2>/dev/null || true
    fi
    _restore_nvidia_holds 2>/dev/null || true
    _restore_excluded_apt_holds 2>/dev/null || true
    _json_finalize_on_exit "$rc"
}
trap _apt_apply_on_exit EXIT

_temporarily_hold_excluded_apt
if [[ "$APT_CATEGORY_EXCLUDED" -eq 1 ]]; then
    print_warn "apt category excluded via config/exclusions.list — skipping upgrade"
    json_count_ok
    exit 0
fi

# ── 1. Detect broken NVIDIA dpkg state up front ──────────────────────────────
broken_nvidia=$(dpkg -l 'nvidia-*' 'libnvidia-*' 2>/dev/null | awk '/^iF/{print $2}' | tr '\n' ' ')
if [[ -n "${broken_nvidia// }" ]]; then
    json_add_diag warn DPKG-NVIDIA-BROKEN "broken NVIDIA dpkg state: ${broken_nvidia}"
    json_add_advisory "Run scripts/rebuild-dkms.sh or sudo dpkg --configure -a"
    print_warn "broken NVIDIA dpkg state — may cause repeated DKMS rebuild attempts"
    json_count_warn
fi

# ── 2. Hold NVIDIA unless explicitly upgrading ───────────────────────────────
if [[ "${UPGRADE_NVIDIA}" -eq 0 ]]; then
    _temporarily_hold_nvidia
fi

# ── 3. Ensure third-party repos exist (fail-closed) ──────────────────────────
print_section "Ensuring configured APT repositories"
if [[ -f "$CONFIG_REPOS" ]]; then
    repo_failed=0
    while IFS= read -r repo_id; do
        [[ -z "$repo_id" ]] && continue
        if setup_repo "$repo_id"; then
            json_count_ok
        else
            json_add_diag error APT-REPO-FAIL "repository setup failed: ${repo_id}"
            json_count_err
            repo_failed=1
        fi
    done < <(parse_config_names "$CONFIG_REPOS")
    if [[ $repo_failed -ne 0 ]]; then
        print_error "one or more repos failed — aborting fail-closed"
        exit 20
    fi
fi

# ── 4. apt-get update ─────────────────────────────────────────────────────────
print_section "Refreshing package lists"
print_step "apt-get update"
update_out=$(sudo apt-get -o DPkg::Lock::Timeout=600 update -q 2>&1) && update_rc=0 || update_rc=$?
echo "${update_out}" >> "${LOG_FILE}"
if [[ $update_rc -eq 0 ]]; then
    print_ok
    json_add_item id="apt:update" action="refresh" result="ok"
    json_count_ok
    if echo "${update_out}" | grep -q "configured multiple times"; then
        dup=$(echo "${update_out}" | grep -oP '/etc/apt/sources\.list\.d/\S+' | sort -u | paste -sd' ')
        json_add_diag warn APT-DUP-SOURCES "duplicate APT source files: ${dup}"
        json_count_warn
    fi
else
    print_error "apt-get update failed"
    json_add_item id="apt:update" action="refresh" result="failed"
    json_add_diag error APT-UPDATE-FAIL "apt-get update returned ${update_rc}"
    json_count_err
    exit 20
fi

# ── 5. apt-get upgrade + dist-upgrade ─────────────────────────────────────────
APT_OPTS=(
    -y -q
    -o DPkg::Lock::Timeout=600
    -o Dpkg::Options::="--force-confdef"
    -o Dpkg::Options::="--force-confold"
    -o APT::Get::Show-Versions=true
)

# Show what's about to be upgraded (live progress feedback) before the
# silent batch run. Helps the operator understand WHY apt-get takes long.
print_section "Upgrading packages"
print_step "scanning upgradable list"
_upgradable=()
mapfile -t _upgradable < <(apt list --upgradable 2>/dev/null | awk -F'/' 'NR>1 && NF>1 {print $1}' | sort -u || true)
print_ok "${#_upgradable[@]} package(s) outdated"
if (( ${#_upgradable[@]} > 0 )); then
    echo
    printf '     %s\n' "${_upgradable[@]:0:30}"
    (( ${#_upgradable[@]} > 30 )) && echo "     … and $(( ${#_upgradable[@]} - 30 )) more"
    echo
fi

upgrade_rc=0
print_step "apt-get upgrade (streaming)"
echo
# Emit start marker for the dashboard progress bar before apt streams.
printf 'PROGRESS|start|apt-upgrade|%d|apt upgrade\n' "${#_upgradable[@]}"
_stream_emit ">>> apt-get upgrade (${#_upgradable[@]} pkgs)"
# Stream apt output through tee to BOTH the per-phase log and a parser that
# emits one human-friendly line per package, plus a JSON sidecar item.
# Dpkg::Progress-Fancy=0 keeps lines plain (no terminal-resize escape codes).
# When ASCENDO_STREAM_LOG is set, we ALSO append a copy via _stream_tee so
# the dashboard's SSE Run Center sees apt's live output (parity with macOS).
set +e
sudo apt-get upgrade "${APT_OPTS[@]}" \
    -o Dpkg::Progress-Fancy=0 \
    -o Dpkg::Use-Pty=0 2>&1 \
| tee -a "${LOG_FILE}" \
| _stream_tee \
| awk -v total=${#_upgradable[@]} '
    BEGIN { i = 0 }
    /^Setting up / {
        i++
        # capture: "Setting up firefox (132.0+build1-0ubuntu1) ..."
        match($0, /^Setting up ([^ ]+) \(([^)]+)\)/, m)
        if (m[1] != "") {
            # Human-friendly line for the console.
            printf "  \033[0;32m✔\033[0m  [%d/%d] %s → %s\n", i, total, m[1], m[2]
            # Machine marker for the dashboard SSE consumer (matches lib/progress.sh).
            printf "PROGRESS|step|apt-upgrade|%d|%d|ok|%s → %s\n", i, total, m[1], m[2]
            fflush()
        }
    }
    /^Unpacking / {
        match($0, /^Unpacking ([^ ]+) \(([^)]+)\)/, m)
        if (m[1] != "") {
            printf "  \033[2m·  unpacking %s\033[0m\n", m[1]
            fflush()
        }
    }
    /^Removing / {
        match($0, /^Removing ([^ ]+) /, m)
        if (m[1] != "") {
            printf "  \033[2m·  removing %s\033[0m\n", m[1]
            fflush()
        }
    }
'
upgrade_rc=${PIPESTATUS[0]}
printf 'PROGRESS|done|apt-upgrade|%d|0|0|0\n' "${#_upgradable[@]}"
set -e
if [[ $upgrade_rc -eq 0 ]]; then
    print_ok "apt-get upgrade complete"
    json_add_item id="apt:upgrade" action="upgrade" result="ok"
    json_count_ok
    # Per-package items (best-effort: parse the log we just wrote).
    while IFS= read -r line; do
        pkg=$(echo "$line" | sed -nE 's/^Setting up ([^ ]+) \(([^)]+)\).*/\1|\2/p')
        [[ -z "$pkg" ]] && continue
        name="${pkg%%|*}"; ver="${pkg##*|}"
        json_add_item id="apt:upgrade:${name}" action="upgrade" to="${ver}" result="ok" || true
    done < <(grep '^Setting up ' "${LOG_FILE}" 2>/dev/null || true)
else
    print_warn "apt-get upgrade non-zero (${upgrade_rc})"
    if grep -qiE "nvidia-dkms|dkms.*nvidia" "${LOG_FILE}" 2>/dev/null; then
        json_add_diag warn APT-DKMS-NVIDIA "upgrade hit NVIDIA DKMS build error"
    fi
    json_add_item id="apt:upgrade" action="upgrade" result="warn"
    json_count_warn
fi

print_step "apt-get dist-upgrade"
_stream_emit ">>> apt-get dist-upgrade"
dist_rc=0
# Capture combined output to a temp file so we can tail it on failure.
_du_log="$(mktemp 2>/dev/null || echo /tmp/ascendo-apt-dist-upgrade.$$)"
if sudo apt-get dist-upgrade "${APT_OPTS[@]}" >"${_du_log}" 2>&1; then
    [[ -s "${_du_log}" ]] && cat "${_du_log}" >> "${LOG_FILE}"
    [[ -s "${_du_log}" && -n "${ASCENDO_STREAM_LOG:-}" ]] \
        && cat "${_du_log}" >> "${ASCENDO_STREAM_LOG}" 2>/dev/null
    print_ok
    json_add_item id="apt:dist-upgrade" action="dist-upgrade" result="ok"
    json_count_ok
else
    dist_rc=$?
    [[ -s "${_du_log}" ]] && cat "${_du_log}" >> "${LOG_FILE}"
    [[ -s "${_du_log}" && -n "${ASCENDO_STREAM_LOG:-}" ]] \
        && cat "${_du_log}" >> "${ASCENDO_STREAM_LOG}" 2>/dev/null
    print_warn "apt-get dist-upgrade non-zero (${dist_rc})"
    if grep -qiE "nvidia-dkms|dkms.*nvidia" "${LOG_FILE}" 2>/dev/null; then
        json_add_diag warn APT-DKMS-NVIDIA "dist-upgrade hit NVIDIA DKMS build error"
    fi
    _du_tail="$(_stderr_tail "${_du_log}")"
    if [[ -n "$_du_tail" ]]; then
        json_add_diag warn APT-DIST-UPGRADE-FAIL \
            "apt-get dist-upgrade exited ${dist_rc} — last output: ${_du_tail}"
    fi
    json_add_item id="apt:dist-upgrade" action="dist-upgrade" result="warn"
    json_count_warn
fi
rm -f "${_du_log}" 2>/dev/null

if [[ $upgrade_rc -ne 0 && $dist_rc -ne 0 ]]; then
    json_add_diag error APT-UPGRADE-BOTH-FAILED "both upgrade and dist-upgrade returned non-zero"
    json_count_err
fi

# ── 6. Targeted desktop-app feed retry (code, antigravity) ──────────────────
print_section "Targeted desktop-app verification"
for pkg in code antigravity; do
    apt_installed "$pkg" || continue
    inst=$(apt_pkg_version "$pkg")
    cand=$(apt_pkg_candidate "$pkg")
    if [[ -z "$cand" || "$cand" == "(none)" || "$cand" == "$inst" ]]; then
        continue
    fi
    print_step "apt-get install --only-upgrade ${pkg} (${inst} -> ${cand})"
    if sudo apt-get install -y -q --only-upgrade "$pkg" >> "${LOG_FILE}" 2>&1; then
        new=$(apt_pkg_version "$pkg")
        json_add_item id="apt:upgrade:${pkg}" action="upgrade" \
            from="${inst}" to="${new}" result="ok"
        print_ok
        json_count_ok
    else
        json_add_item id="apt:upgrade:${pkg}" action="upgrade" \
            from="${inst}" to="${cand}" result="failed"
        json_add_diag warn APT-PKG-RETRY-FAIL "targeted upgrade for ${pkg} failed"
        json_count_warn
    fi
done

# ── 7. Reboot indicator ───────────────────────────────────────────────────────
if [[ -f /var/run/reboot-required ]]; then
    json_set_needs_reboot 1
    pkgs=""
    [[ -f /var/run/reboot-required.pkgs ]] && pkgs=$(paste -sd', ' /var/run/reboot-required.pkgs)
    json_add_diag warn REBOOT-PENDING "reboot required (${pkgs:-no pkg list})"
fi

# Final exit decision: if both upgrade & dist-upgrade failed → 20, else if any
# err counted → 1.
if [[ $upgrade_rc -ne 0 && $dist_rc -ne 0 ]]; then
    exit 20
fi

# Read summary back via JSON helper isn't trivial in bash; rely on the count_*.
# If at least one warn/err recorded, return 1; orchestrator still records ok if
# critical phases passed.
if grep -q '"level": "error"' "${JSON_BUFDIR}/diags.jsonl" 2>/dev/null; then
    exit 1
fi
exit 0
