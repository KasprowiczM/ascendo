#!/usr/bin/env bash
# =============================================================================
# app/tauri/build.sh — One-shot Tauri build (deb + appimage).
#
# Auto-installs missing prerequisites (rustup, system libs, tauri-cli) after
# explicit user confirmation. Idempotent: re-runs are cheap.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAURI_DIR="${SCRIPT_DIR}/src-tauri"

YES=0
SKIP_DEPS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes) YES=1 ;;
        --skip-deps) SKIP_DEPS=1 ;;
        -h|--help)
            cat <<'EOF'
Usage: bash build.sh [-y|--yes] [--skip-deps] [extra args for cargo tauri build]

  -y, --yes      Don't prompt; install missing prerequisites automatically.
  --skip-deps    Skip the prerequisite-install step entirely.
EOF
            exit 0 ;;
        *) break ;;
    esac
    shift
done

confirm() {
    local prompt="$1"
    if [[ $YES -eq 1 ]]; then return 0; fi
    read -rp "$prompt [y/N] " ans
    [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

install_system_deps() {
    local pkgs=(
        libwebkit2gtk-4.1-dev
        libgtk-3-dev
        libayatana-appindicator3-dev
        librsvg2-dev
        libsoup-3.0-dev
        pkg-config
        build-essential
        curl
    )
    local missing=()
    for p in "${pkgs[@]}"; do
        if ! dpkg -s "$p" >/dev/null 2>&1; then
            missing+=("$p")
        fi
    done
    if [[ ${#missing[@]} -eq 0 ]]; then
        echo "✔ system dependencies already installed"
        return 0
    fi
    echo "── missing system packages: ${missing[*]}"
    if ! confirm "Install via 'sudo apt install'?"; then
        echo "abort: refusing to install"
        return 1
    fi
    sudo apt-get update -q
    sudo apt-get install -y -q "${missing[@]}"
}

install_rust() {
    if command -v cargo >/dev/null 2>&1; then
        echo "✔ cargo already installed: $(cargo --version)"
        return 0
    fi
    echo "── cargo not found"
    if ! confirm "Install Rust toolchain via rustup (https://sh.rustup.rs)?"; then
        return 1
    fi
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
    # shellcheck disable=SC1090
    source "${HOME}/.cargo/env"
    if ! command -v cargo >/dev/null 2>&1; then
        echo "rustup install ran but cargo still not in PATH" >&2
        return 1
    fi
    echo "✔ installed: $(cargo --version)"
}

install_tauri_cli() {
    if cargo tauri --version >/dev/null 2>&1; then
        echo "✔ tauri-cli already installed: $(cargo tauri --version)"
        return 0
    fi
    echo "── installing tauri-cli (~3 min compile)"
    cargo install tauri-cli --version "^2"
}

ensure_python_venv() {
    if [[ ! -x "${SCRIPT_DIR}/../.venv/bin/python" ]]; then
        echo "── bootstrapping python venv (used by .deb at runtime)"
        bash "${SCRIPT_DIR}/../install.sh"
    else
        echo "✔ python venv present"
    fi
}

ensure_icons() {
    local icons_dir="${TAURI_DIR}/icons"
    local needed=(32x32.png 128x128.png "128x128@2x.png" icon.png)
    local missing=0
    for f in "${needed[@]}"; do
        if [[ ! -s "${icons_dir}/${f}" ]]; then
            missing=1; break
        fi
    done
    if [[ $missing -eq 1 ]]; then
        echo "── generating placeholder icons (no Pillow/imagemagick required)"
        python3 "${SCRIPT_DIR}/generate-icons.py"
    else
        echo "✔ icons present in ${icons_dir}"
    fi
    # Bundler can choke on the empty .icns/.ico stubs we used to ship; remove
    # them — Linux-only targets don't reference them.
    rm -f "${icons_dir}/icon.icns" "${icons_dir}/icon.ico"
}

if [[ $SKIP_DEPS -eq 0 ]]; then
    install_system_deps
    install_rust
    # Pull cargo into PATH if rustup just installed it
    if ! command -v cargo >/dev/null 2>&1 && [[ -f "${HOME}/.cargo/env" ]]; then
        # shellcheck disable=SC1090
        source "${HOME}/.cargo/env"
    fi
    install_tauri_cli
    ensure_python_venv
    ensure_icons
fi

cd "$TAURI_DIR"
echo
echo "── cargo tauri build $*"
cargo tauri build "$@"

echo
echo "Bundles produced:"
find target -path '*/release/bundle/*' \( -name '*.deb' -o -name '*.AppImage' \) 2>/dev/null | sed 's/^/  /'

# Find the produced artifacts (case-insensitive in case Tauri's
# productName is renamed in the future; AppImage filename also varies).
DEB=$(find "${TAURI_DIR}/target" -iname '*aktualizacje*.deb' 2>/dev/null | head -1)
APPIMG=$(find "${TAURI_DIR}/target" -iname '*aktualizacje*.AppImage' 2>/dev/null | head -1)

echo
if [[ -n "$DEB" ]]; then
    # Resolve to absolute path so user can paste anywhere; apt requires
    # either an absolute path OR a relative path that starts with ./
    DEB_ABS="$(readlink -f "$DEB")"
    [[ -n "$APPIMG" ]] && chmod +x "$APPIMG" 2>/dev/null || true
    echo "Install (.deb path is absolute, paste verbatim):"
    echo "  sudo apt install \"${DEB_ABS}\""
    echo
    if [[ -n "$APPIMG" ]]; then
        APPIMG_ABS="$(readlink -f "$APPIMG")"
        echo "Or run AppImage directly (no install):"
        echo "  ${APPIMG_ABS}"
        echo
    fi
    echo "After install, launch with:"
    echo "  ascendo      # CLI binary on PATH"
    echo "  # or via Activities menu (look for 'ascendo')"
    echo
    echo "Quick install command (copy-paste, runs sudo apt install):"
    echo "  bash \"${SCRIPT_DIR}/install-deb.sh\""
else
    echo "(no .deb found — check 'cargo tauri build' output above)"
fi
