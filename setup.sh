#!/usr/bin/env bash
# =============================================================================
# setup.sh — Universal migration & bootstrap script
#
# MODES:
#   ./setup.sh                  Migrate: install everything from config/
#   ./setup.sh --discover       Scan this machine → write/update config files
#   ./setup.sh --check          Dry-run: show what's installed vs missing
#   ./setup.sh --update-config  Like --discover but merges into existing config
#   ./setup.sh --rollback       Restore config files from latest .bak_* backup
#
# FLAGS (combine with any mode):
#   --nvidia        Install NVIDIA driver (auto-detected by default)
#   --no-brew       Skip Homebrew installation
#   --no-snaps      Skip Snap packages
#   --no-npm        Skip npm global packages
#   --non-interactive  No prompts (for CI/automation)
#   --help          Show usage
#
# EXAMPLES:
#   # Fresh machine setup:
#   git clone https://github.com/KasprowiczM/Ascendo.git
#   cd Ascendo && ./setup.sh
#
#   # Capture current machine state into config files:
#   ./setup.sh --discover
#
#   # Check what would be installed (no changes):
#   ./setup.sh --check
#
#   # Undo last --discover (restore previous config):
#   ./setup.sh --rollback
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "${LOG_DIR}"
export LOG_FILE="${LOG_DIR}/setup_$(date +%Y%m%d_%H%M%S).log"

# ── Bootstrap: load libs ──────────────────────────────────────────────────────
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/detect.sh"
source "${SCRIPT_DIR}/lib/repos.sh"

CONFIG_DIR="${SCRIPT_DIR}/config"

# ── Parse arguments ───────────────────────────────────────────────────────────
MODE="migrate"
OPT_NVIDIA="auto"
OPT_BREW=1
OPT_SNAPS=1
OPT_NPM=1
OPT_NONINTERACTIVE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --discover)         MODE="discover" ;;
        --check)            MODE="check" ;;
        --update-config)    MODE="update-config" ;;
        --rollback)         MODE="rollback" ;;
        --nvidia)           OPT_NVIDIA="yes" ;;
        --no-nvidia)        OPT_NVIDIA="no" ;;
        --no-brew)          OPT_BREW=0 ;;
        --no-snaps)         OPT_SNAPS=0 ;;
        --no-npm)           OPT_NPM=0 ;;
        --non-interactive)  OPT_NONINTERACTIVE=1 ;;
        -h|--help)
            sed -n '/^# MODES:/,/^#.*EXAMPLES/p' "$0" | sed 's/^# //' | sed 's/^#//'
            exit 0 ;;
        *) print_error "Unknown argument: $1"; exit 1 ;;
    esac
    shift
done

# ── Banner ────────────────────────────────────────────────────────────────────
print_header "Ascendo — Setup [mode: ${MODE}]"
detect_os
detect_hardware
detect_gpu
detect_package_managers

print_info "Host    : $(hostname)"
print_info "OS      : ${OS_PRETTY}"
print_info "Kernel  : ${KERNEL_VER}"
print_info "Machine : ${HW_VENDOR} ${HW_MODEL}"
print_info "Log     : ${LOG_FILE}"
echo

acquire_project_lock "setup"

# =============================================================================
# MODE: ROLLBACK — restore config files from latest backup
# =============================================================================
if [[ "$MODE" == "rollback" ]]; then
    print_header "Rollback — Restoring config files from backup"

    RESTORED=0
    FAILED=0

    for f in "${CONFIG_DIR}"/*.list "${CONFIG_DIR}"/*.conf; do
        [[ -f "$f" ]] || continue
        base="$f"
        # Find most recent backup
        latest_bak=$(find "$(dirname "$base")" -maxdepth 1 -name "$(basename "$base").bak_*" -type f -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1{print $2}')
        if [[ -z "$latest_bak" ]]; then
            print_info "$(basename "$base"): no backup found — skipping"
            continue
        fi

        bak_date=$(echo "$latest_bak" | grep -oP '\d{8}_\d{6}$' || echo "unknown")
        print_step "Restore $(basename "$base") (from backup ${bak_date})"

        if [[ $OPT_NONINTERACTIVE -eq 0 ]]; then
            echo -ne "  ${YELLOW}Restore?${RESET} [y/N] "
            read -r ans
            [[ "${ans,,}" != "y" ]] && { print_info "skipped"; continue; }
        fi

        # Backup current state before restoring
        cp "$base" "${base}.bak_$(date +%Y%m%d_%H%M%S)_pre_rollback" 2>/dev/null || true
        cp "$latest_bak" "$base"
        print_ok "restored from $(basename "$latest_bak")"
        RESTORED=$((RESTORED + 1))
    done

    echo
    print_info "Restored: ${RESTORED} file(s)"
    [[ $FAILED -gt 0 ]] && print_warn "Failed: ${FAILED} file(s)"
    print_info "Run './setup.sh --check' to verify state."
    exit 0
fi

# =============================================================================
# MODE: DISCOVER — scan machine → write config files
# =============================================================================
if [[ "$MODE" == "discover" || "$MODE" == "update-config" ]]; then
    print_header "Discovery Mode — Scanning installed packages"

    _confirm_overwrite() {
        local file="$1"
        if [[ -f "$file" && $OPT_NONINTERACTIVE -eq 0 ]]; then
            echo -ne "  ${YELLOW}Overwrite ${file}?${RESET} [y/N] "
            read -r ans
            [[ "${ans,,}" != "y" ]] && return 1
        fi
        return 0
    }

    _backup_config() {
        local file="$1"
        [[ -f "$file" ]] && cp "$file" "${file}.bak_$(date +%Y%m%d_%H%M%S)"
    }

    # ── Discover APT packages ──────────────────────────────────────────────────
    print_section "Scanning APT packages"
    APT_OUT="${CONFIG_DIR}/apt-packages.list"
    _confirm_overwrite "$APT_OUT" && {
        _backup_config "$APT_OUT"
        {
            echo "# APT packages — discovered on $(hostname) at $(date '+%Y-%m-%d')"
            echo "# Edit this file to add/remove packages for migration."
            echo ""
            echo "# ── Manually installed (non-automatic) ──────────────────────────────────────"
            apt-mark showmanual 2>/dev/null | sort | while read -r pkg; do
                # Skip low-level base packages unlikely to be intentional
                case "$pkg" in
                    ubuntu-desktop|ubuntu-desktop-minimal|ubuntu-standard|ubuntu-minimal|\
                    ubuntu-session|gdm3|snapd|plymouth*|systemd*|grub*|shim*|linux-*|\
                    locales|adduser|login|passwd|sudo) continue ;;
                esac
                ver=$(apt_pkg_version "$pkg")
                echo "${pkg}  # ${ver}"
            done
        } > "$APT_OUT"
        print_ok "Written: ${APT_OUT}"
        record_ok
    }

    # ── Discover Snap packages ─────────────────────────────────────────────────
    print_section "Scanning Snap packages"
    SNAP_OUT="${CONFIG_DIR}/snap-packages.list"
    _confirm_overwrite "$SNAP_OUT" && {
        _backup_config "$SNAP_OUT"
        {
            echo "# Snap packages — discovered on $(hostname) at $(date '+%Y-%m-%d')"
            echo ""
            scan_snaps_user | while IFS='|' read -r name ver rev chan pub; do
                echo "${name}  # ${ver} (${chan})"
            done
        } > "$SNAP_OUT"
        print_ok "Written: ${SNAP_OUT}"
        record_ok
    }

    # ── Discover Brew formulas ─────────────────────────────────────────────────
    if [[ $HAS_BREW -eq 1 ]]; then
        print_section "Scanning Homebrew formulas"
        BREW_FORM_OUT="${CONFIG_DIR}/brew-formulas.list"
        _confirm_overwrite "$BREW_FORM_OUT" && {
            _backup_config "$BREW_FORM_OUT"
            {
                echo "# Homebrew formulas — discovered on $(hostname) at $(date '+%Y-%m-%d')"
                echo "# Note: dependency-only formulas are included — remove if not needed."
                echo ""
                scan_brew_formulas | while read -r name ver; do
                    # Skip known pure-dependency formulas
                    case "$name" in
                        berkeley-db*|gmp|isl|libmpc|mpfr|binutils) echo "# ${name}  # ${ver}  (dependency)" ;;
                        *) echo "${name}  # ${ver}" ;;
                    esac
                done
            } > "$BREW_FORM_OUT"
            print_ok "Written: ${BREW_FORM_OUT}"
            record_ok
        }

        print_section "Scanning Homebrew casks"
        BREW_CASK_OUT="${CONFIG_DIR}/brew-casks.list"
        _confirm_overwrite "$BREW_CASK_OUT" && {
            _backup_config "$BREW_CASK_OUT"
            {
                echo "# Homebrew casks — discovered on $(hostname) at $(date '+%Y-%m-%d')"
                echo ""
                scan_brew_casks | while IFS='|' read -r name ver; do
                    echo "${name}  # ${ver}"
                done
            } > "$BREW_CASK_OUT"
            print_ok "Written: ${BREW_CASK_OUT}"
            record_ok
        }
    fi

    # ── Discover npm globals ───────────────────────────────────────────────────
    if [[ -n "${NPM_BIN:-}" ]]; then
        print_section "Scanning npm global packages"
        NPM_OUT="${CONFIG_DIR}/npm-globals.list"
        _confirm_overwrite "$NPM_OUT" && {
            _backup_config "$NPM_OUT"
            {
                echo "# npm global packages — discovered on $(hostname) at $(date '+%Y-%m-%d')"
                echo "# npm itself is managed by Homebrew (node formula) — do not add it here."
                echo ""
                scan_npm_globals | grep -v '^npm|' | while IFS='|' read -r name ver; do
                    echo "${name}  # ${ver}"
                done
            } > "$NPM_OUT"
            print_ok "Written: ${NPM_OUT}"
            record_ok
        }
    fi

    # ── Discover pip user packages ────────────────────────────────────────────
    if has_cmd python3; then
        print_section "Scanning pip user packages"
        PIP_OUT="${CONFIG_DIR}/pip-packages.list"
        _confirm_overwrite "$PIP_OUT" && {
            _backup_config "$PIP_OUT"
            {
                echo "# pip user packages — discovered on $(hostname) at $(date '+%Y-%m-%d')"
                echo "# Only --user scope is captured."
                echo ""
                python3 -m pip list --user --format=json 2>/dev/null | python3 -c '
import sys, json
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
for row in sorted(rows, key=lambda x: x.get("name","").lower()):
    name = row.get("name","")
    ver = row.get("version","")
    if name:
        print(f"{name}  # {ver}")
' || true
            } > "$PIP_OUT"
            print_ok "Written: ${PIP_OUT}"
            record_ok
        }
    fi

    # ── Discover pipx apps ────────────────────────────────────────────────────
    if [[ $HAS_PIPX -eq 1 ]]; then
        print_section "Scanning pipx packages"
        PIPX_OUT="${CONFIG_DIR}/pipx-packages.list"
        _confirm_overwrite "$PIPX_OUT" && {
            _backup_config "$PIPX_OUT"
            {
                echo "# pipx packages — discovered on $(hostname) at $(date '+%Y-%m-%d')"
                echo ""
                run_as_user pipx list --json 2>/dev/null | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
venvs = d.get("venvs", {})
for name in sorted(venvs.keys()):
    info = venvs.get(name, {})
    meta = info.get("metadata", {})
    main = meta.get("main_package", {}) if isinstance(meta, dict) else {}
    ver = main.get("package_version", "") if isinstance(main, dict) else ""
    if name:
        print(f"{name}  # {ver}".rstrip())
' || true
            } > "$PIPX_OUT"
            print_ok "Written: ${PIPX_OUT}"
            record_ok
        }
    fi

    # ── Discover Flatpak apps ─────────────────────────────────────────────────
    if [[ $HAS_FLATPAK -eq 1 ]]; then
        print_section "Scanning Flatpak applications"
        FLATPAK_OUT="${CONFIG_DIR}/flatpak-packages.list"
        _confirm_overwrite "$FLATPAK_OUT" && {
            _backup_config "$FLATPAK_OUT"
            {
                echo "# Flatpak applications — discovered on $(hostname) at $(date '+%Y-%m-%d')"
                echo ""
                flatpak list --app --columns=application,name,version 2>/dev/null | \
                    while IFS=$'\t' read -r app_id name ver; do
                        [[ -z "$app_id" ]] && continue
                        echo "${app_id}  # ${name:-unknown} ${ver:-}"
                    done
            } > "$FLATPAK_OUT"
            print_ok "Written: ${FLATPAK_OUT}"
            record_ok
        }
    fi

    print_summary "Discovery complete"
    echo
    print_info "Config files updated in: ${CONFIG_DIR}/"
    print_info "Review them, then run './setup.sh' on a new machine to migrate."
    exit 0
fi

# =============================================================================
# MODE: CHECK — show what's installed vs what config expects
# =============================================================================
if [[ "$MODE" == "check" ]]; then
    print_header "Check Mode — Comparing config vs installed state"

    _check_apt() {
        print_section "APT packages"
        local missing=() present=()
        while IFS= read -r pkg; do
            [[ -z "$pkg" ]] && continue
            if apt_installed "$pkg"; then
                present+=("$pkg ($(apt_pkg_version "$pkg"))")
            else
                missing+=("$pkg")
            fi
        done < <(parse_config_names "${CONFIG_DIR}/apt-packages.list")
        for p in "${present[@]}"; do print_info "  ${GREEN}✔${RESET} ${p}"; done
        for m in "${missing[@]}"; do print_warn "  ✘ MISSING: ${m}"; done
    }

    _check_snaps() {
        print_section "Snap packages"
        if ! _snap_cmd list >/tmp/ascendo-snap-list.$$ 2>/dev/null; then
            rm -f /tmp/ascendo-snap-list.$$
            print_warn "snap list timed out or failed — skipping Snap package check"
            return 0
        fi
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            local pkg; pkg=$(echo "$line" | awk '{print $1}')
            if awk -v pkg="$pkg" 'NR>1 && $1 == pkg {found=1} END{exit found ? 0 : 1}' /tmp/ascendo-snap-list.$$; then
                local ver; ver=$(awk -v pkg="$pkg" 'NR>1 && $1 == pkg {print $2; exit}' /tmp/ascendo-snap-list.$$)
                print_info "  ${GREEN}✔${RESET} ${pkg} (${ver})"
            else
                print_warn "  ✘ MISSING: ${pkg}"
            fi
        done < <(parse_config_lines "${CONFIG_DIR}/snap-packages.list")
        rm -f /tmp/ascendo-snap-list.$$
    }

    _check_brew() {
        [[ $HAS_BREW -eq 0 ]] && { print_warn "Homebrew not installed"; return; }
        print_section "Brew formulas"
        while IFS= read -r f; do
            [[ -z "$f" ]] && continue
            if brew_formula_installed "$f"; then
                print_info "  ${GREEN}✔${RESET} ${f} ($(brew_formula_version "$f"))"
            else
                print_warn "  ✘ MISSING: ${f}"
            fi
        done < <(parse_config_names "${CONFIG_DIR}/brew-formulas.list")

        print_section "Brew casks"
        while IFS= read -r c; do
            [[ -z "$c" ]] && continue
            if brew_cask_installed "$c"; then
                print_info "  ${GREEN}✔${RESET} ${c} ($(brew_cask_version "$c"))"
            else
                print_warn "  ✘ MISSING: ${c}"
            fi
        done < <(parse_config_names "${CONFIG_DIR}/brew-casks.list")
    }

    _check_npm() {
        [[ -z "${NPM_BIN:-}" ]] && return
        print_section "npm global packages"
        while IFS= read -r pkg; do
            [[ -z "$pkg" ]] && continue
            if npm_pkg_installed "$pkg"; then
                print_info "  ${GREEN}✔${RESET} ${pkg} ($(npm_pkg_version "$pkg"))"
            else
                print_warn "  ✘ MISSING: ${pkg}"
            fi
        done < <(parse_config_names "${CONFIG_DIR}/npm-globals.list")
    }

    _check_apt
    _check_snaps
    [[ $OPT_BREW -eq 1 ]] && _check_brew
    [[ $OPT_NPM -eq 1 ]]  && _check_npm

    print_summary "Check complete — no changes made"
    exit 0
fi

# =============================================================================
# MODE: MIGRATE — install everything from config on this machine
# =============================================================================

assert_ubuntu

# ── Sudo authentication ────────────────────────────────────────────────────────
print_section "Authentication"
print_step "sudo"
sudo -v || { print_error "sudo required"; exit 1; }
(while true; do sudo -n true; sleep 50; done) &
SUDO_KEEP_PID=$!
trap 'kill "${SUDO_KEEP_PID}" 2>/dev/null; true' EXIT INT TERM
print_ok

# =============================================================================
# STEP 1 — APT prerequisites
# =============================================================================
print_header "Step 1/7 — APT Prerequisites"

PREREQS=(
    curl wget git gpg ca-certificates apt-transport-https
    software-properties-common gnupg lsb-release
    build-essential file procps python3
)

print_step "apt-get update"
sudo_silent apt-get update -q && print_ok || { print_warn "update had issues"; record_warn; }

print_step "Install base prerequisites"
if sudo_silent apt-get install -y -q "${PREREQS[@]}"; then
    print_ok; record_ok
else
    print_error "Prerequisite install failed"; exit 1
fi

# =============================================================================
# STEP 2 — APT repositories
# =============================================================================
print_header "Step 2/7 — Third-Party APT Repositories"

while IFS= read -r repo_id; do
    [[ -z "$repo_id" ]] && continue
    setup_repo "$repo_id"
done < <(parse_config_names "${CONFIG_DIR}/apt-repos.list")

print_step "apt-get update (after adding repos)"
sudo_silent apt-get update -q && print_ok || { print_warn "update had issues"; record_warn; }

# =============================================================================
# STEP 3 — APT packages
# =============================================================================
print_header "Step 3/7 — APT Application Install"

# Auto-detect NVIDIA and add driver to install list
if [[ "$OPT_NVIDIA" == "auto" ]]; then
    detect_gpu
    [[ $HAS_NVIDIA -eq 1 ]] && OPT_NVIDIA="yes"
fi

INSTALL_PKGS=()
SKIP_PKGS=()
MISSING_PKGS=()

while IFS= read -r pkg; do
    [[ -z "$pkg" ]] && continue
    if apt_installed "$pkg"; then
        SKIP_PKGS+=("$pkg")
    else
        INSTALL_PKGS+=("$pkg")
    fi
done < <(parse_config_names "${CONFIG_DIR}/apt-packages.list")

[[ "$OPT_NVIDIA" == "yes" && -n "${NVIDIA_DRIVER_VER:-}" ]] && \
    INSTALL_PKGS+=("nvidia-driver-${NVIDIA_DRIVER_VER}")
[[ "$OPT_NVIDIA" == "yes" && -z "${NVIDIA_DRIVER_VER:-}" ]] && \
    INSTALL_PKGS+=("nvidia-driver-580")

print_info "Already installed : ${#SKIP_PKGS[@]} packages"
print_info "To install        : ${#INSTALL_PKGS[@]} packages"

if [[ ${#INSTALL_PKGS[@]} -gt 0 ]]; then
    for pkg in "${INSTALL_PKGS[@]}"; do
        print_step "apt install ${pkg}"
        if sudo_silent apt-get install -y -q \
            -o Dpkg::Options::="--force-confdef" \
            -o Dpkg::Options::="--force-confold" \
            "$pkg"; then
            print_ok; record_ok
        else
            print_warn "Failed: ${pkg} (may need manual install or different source)"
            MISSING_PKGS+=("$pkg")
            record_warn
        fi
    done
fi

# ── Docker post-install ────────────────────────────────────────────────────────
if apt_installed docker-ce; then
    print_step "Add ${USER} to docker group"
    if groups "${USER}" 2>/dev/null | grep -q docker; then
        print_info "(already in docker group)"
    else
        sudo_silent usermod -aG docker "${USER}" && print_ok || { print_warn "Failed"; record_warn; }
        print_warn "Logout/in required for docker without sudo (or: newgrp docker)"
    fi
fi

# =============================================================================
# STEP 4 — Homebrew
# =============================================================================
if [[ $OPT_BREW -eq 1 ]]; then
    print_header "Step 4/7 — Homebrew (Linuxbrew)"

    if [[ $HAS_BREW -eq 0 ]]; then
        print_step "Install Homebrew"
        NONINTERACTIVE=1 /bin/bash -c \
            "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
            >> "${LOG_FILE}" 2>&1 && print_ok || { print_error "Homebrew install failed"; exit 1; }
        detect_package_managers  # re-detect to pick up new brew
    else
        print_info "Homebrew already installed at ${BREW_PREFIX}"
    fi

    # ── Add brew to shell PATH ─────────────────────────────────────────────────
    BREW_SHELLENV='
# Homebrew (Linuxbrew)
if [[ -x "/home/linuxbrew/.linuxbrew/bin/brew" ]]; then
    eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
fi'
    for rcfile in "${HOME}/.bashrc" "${HOME}/.profile" "${HOME}/.zshrc"; do
        [[ ! -f "$rcfile" ]] && continue
        if ! grep -q "linuxbrew" "$rcfile" 2>/dev/null; then
            print_step "Add brew PATH to $(basename "$rcfile")"
            echo "${BREW_SHELLENV}" >> "$rcfile" && print_ok || print_warn "Could not update ${rcfile}"
        fi
    done

    # ── Install formulas ───────────────────────────────────────────────────────
    print_section "Homebrew formulas"
    while IFS= read -r formula; do
        [[ -z "$formula" ]] && continue
        print_step "brew install ${formula}"
        if brew_formula_installed "$formula"; then
            print_info "(already installed — $(brew_formula_version "$formula"))"
        else
            if run_silent "${BREW_BIN}" install "$formula"; then
                print_ok; record_ok
            else
                print_warn "Failed: ${formula}"; record_warn
            fi
        fi
    done < <(parse_config_names "${CONFIG_DIR}/brew-formulas.list")

    # ── Install casks ──────────────────────────────────────────────────────────
    print_section "Homebrew casks"
    while IFS= read -r cask; do
        [[ -z "$cask" ]] && continue
        print_step "brew install --cask ${cask}"
        if brew_cask_installed "$cask"; then
            print_info "(already installed — $(brew_cask_version "$cask"))"
        else
            if run_silent "${BREW_BIN}" install --cask "$cask"; then
                print_ok; record_ok
            else
                print_warn "Failed cask: ${cask}"; record_warn
            fi
        fi
    done < <(parse_config_names "${CONFIG_DIR}/brew-casks.list")
fi

# =============================================================================
# STEP 5 — Snap packages
# =============================================================================
if [[ $OPT_SNAPS -eq 1 && $HAS_SNAP -eq 1 ]]; then
    print_header "Step 5/7 — Snap Packages"

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        pkg=$(echo "$line" | awk '{print $1}')
        flags=$(echo "$line" | awk '{$1=""; print $0}' | xargs)

        print_step "snap install ${pkg} ${flags}"
        if snap_installed "$pkg"; then
            print_info "(already installed — $(snap_version "$pkg"))"
        else
            # shellcheck disable=SC2086
            if sudo_silent snap install "$pkg" ${flags}; then
                print_ok; record_ok
            else
                print_warn "Failed snap: ${pkg}"; record_warn
            fi
        fi
    done < <(parse_config_lines "${CONFIG_DIR}/snap-packages.list")
fi

# =============================================================================
# STEP 6 — npm global packages
# =============================================================================
if [[ $OPT_NPM -eq 1 && -n "${NPM_BIN:-}" ]]; then
    print_header "Step 6/7 — npm Global Packages"

    NPM_PKGS=()
    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        NPM_PKGS+=("$pkg")
    done < <(parse_config_names "${CONFIG_DIR}/npm-globals.list")

    if [[ ${#NPM_PKGS[@]} -eq 0 ]]; then
        print_info "No npm global packages configured in config/npm-globals.list"
    else
        for pkg in "${NPM_PKGS[@]}"; do
            print_step "npm install -g ${pkg}"
            if npm_pkg_installed "$pkg"; then
                print_info "(already installed — $(npm_pkg_version "$pkg"))"
            else
                if run_silent "${NPM_BIN}" install -g "$pkg"; then
                    print_ok; record_ok
                else
                    print_warn "Failed npm: ${pkg}"; record_warn
                fi
            fi
        done
    fi
fi

# =============================================================================
# STEP 7 — Shell PATH & permissions
# =============================================================================
print_header "Step 7/7 — Shell Configuration"

SCRIPTS_PATH='
# Ascendo — update scripts
if [[ -d "'"${SCRIPT_DIR}"'" ]]; then
    export PATH="'"${SCRIPT_DIR}"':$PATH"
fi'

for rcfile in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
    [[ ! -f "$rcfile" ]] && continue
    if ! grep -q "Ascendo" "$rcfile" 2>/dev/null; then
        print_step "Add project PATH to $(basename "$rcfile")"
        echo "${SCRIPTS_PATH}" >> "$rcfile" && print_ok || print_warn "failed"
    fi
done

print_step "Make scripts executable"
find "${SCRIPT_DIR}" -name "*.sh" -exec chmod +x {} \; && print_ok || print_warn "chmod failed"

# =============================================================================
# GENERATE INVENTORY
# =============================================================================
print_header "Generating APPS.md inventory"
print_step "Running update-inventory.sh"
if bash "${SCRIPT_DIR}/scripts/update-inventory.sh"; then
    print_ok; record_ok
else
    print_warn "Inventory generation had issues"; record_warn
fi

# =============================================================================
# FINAL REPORT
# =============================================================================
print_summary "Setup complete"
echo
echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   Ascendo — Setup complete!               ║${RESET}"
echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════════╝${RESET}"
echo

[[ ${#MISSING_PKGS[@]} -gt 0 ]] && {
    print_warn "Packages that need manual attention:"
    for p in "${MISSING_PKGS[@]}"; do print_info "  • ${p}"; done
    echo
}

print_info "Apply PATH now         :  source ~/.bashrc"
print_info "Run full update        :  ./update-all.sh"
print_info "Update APT only        :  ./scripts/update-apt.sh"
print_info "Check installed state  :  ./setup.sh --check"
print_info "Log                    :  ${LOG_FILE}"
echo

if [[ $OPT_NONINTERACTIVE -eq 0 ]] && [[ -f /var/run/reboot-required ]]; then
    print_warn "REBOOT REQUIRED — kernel or driver update pending"
fi
