#!/usr/bin/env bash
# =============================================================================
# scripts/preflight.sh — Read-only environment and recovery readiness checks
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"

MANIFEST="${SCRIPT_DIR}/config/restore-manifest.json"

print_header "Preflight — Ascendo"

failures=0
warnings=0

check_ok() { print_ok "$1"; }
check_warn() { print_warn "$1"; warnings=$((warnings + 1)); }
check_fail() { print_error "$1"; failures=$((failures + 1)); }

detect_os || true
print_info "Host   : $(hostname)"
print_info "OS     : ${OS_PRETTY:-$(lsb_release -ds 2>/dev/null || echo unknown)}"
print_info "Kernel : $(uname -r)"
print_info "Repo   : ${SCRIPT_DIR}"

print_section "Required project files"
for path in update-all.sh setup.sh lib/common.sh lib/detect.sh lib/repos.sh config/apt-packages.list config/restore-manifest.json; do
    [[ -f "${SCRIPT_DIR}/${path}" ]] && check_ok "${path}" || check_fail "missing ${path}"
done

print_section "Ubuntu release"
if [[ -r /etc/os-release ]]; then
    . /etc/os-release
    if [[ "${ID:-}" == "ubuntu" ]]; then
        case "${VERSION_ID:-}" in
            22.04|24.04|24.10|25.04|25.10|26.04) check_ok "Ubuntu ${VERSION_ID}" ;;
            *) check_warn "Ubuntu ${VERSION_ID:-unknown} not explicitly validated by this repo" ;;
        esac
    else
        check_fail "unsupported distro: ${ID:-unknown}"
    fi
else
    check_fail "/etc/os-release not readable"
fi

print_section "Command availability"
for cmd in bash git awk sed grep find sort xargs flock; do
    has_cmd "$cmd" && check_ok "$cmd" || check_fail "missing command: $cmd"
done
for cmd in curl gpg lsb_release systemctl systemd-analyze python3 rclone snap flatpak brew npm pip3 pipx fwupdmgr; do
    has_cmd "$cmd" && check_ok "$cmd" || check_warn "optional command missing: $cmd"
done

print_section "Git state"
if git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    dirty=$(git -C "$SCRIPT_DIR" status --porcelain=v1 --untracked-files=no)
    [[ -z "$dirty" ]] && check_ok "tracked working tree clean" || check_warn "tracked working tree has local modifications"
    upstream=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)
    [[ -n "$upstream" ]] && check_ok "upstream: ${upstream}" || check_warn "current branch has no upstream"
else
    check_fail "not inside a Git working tree"
fi

print_section "Private overlay manifest"
if [[ -f "$MANIFEST" ]]; then
    python3 -m json.tool "$MANIFEST" >/dev/null && check_ok "restore manifest JSON valid" || check_fail "restore manifest JSON invalid"
    python3 - "$SCRIPT_DIR" "$MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
for item in manifest.get("expected_private_overlay", []):
    path = item["path"]
    state = "present" if (root / path).exists() else "missing"
    print(f"  {state}: {path} [{item.get('class', 'unknown')}]")
PY
else
    check_fail "missing restore manifest: ${MANIFEST}"
fi

print_section "Ubuntu auto-update policy visibility"
for path in /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/50unattended-upgrades; do
    [[ -r "$path" ]] && check_ok "readable ${path}" || check_warn "not readable/missing ${path}"
done
if has_cmd systemctl; then
    systemctl is-enabled apt-daily.timer >/dev/null 2>&1 && check_ok "apt-daily.timer enabled" || check_warn "apt-daily.timer not enabled"
    systemctl is-enabled apt-daily-upgrade.timer >/dev/null 2>&1 && check_ok "apt-daily-upgrade.timer enabled" || check_warn "apt-daily-upgrade.timer not enabled"
fi

print_summary "Preflight Summary"
echo "Failures: ${failures}"
echo "Warnings: ${warnings}"

[[ $failures -eq 0 ]] || exit 1
