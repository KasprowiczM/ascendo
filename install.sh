#!/usr/bin/env bash
# Ascendo — one-liner installer for macOS / Linux.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash
#
# What it does:
#   1. Detects the OS (Darwin / Ubuntu+Debian / Fedora / Arch).
#   2. Asks for language (en | pl) and persists to
#      ~/.config/ascendo/locale.txt.
#   3. Installs missing system deps via the OS package manager.
#   4. Asks for an install profile (1 = CLI, 2 = CLI + Web,
#      3 = CLI + Web + Desktop).
#   5. Clones (or pulls) the GitHub repo to ~/.local/share/ascendo.
#   6. Sets up a venv, pip-installs core/ + adapters/<os>/ editable.
#   7. Symlinks `ascendo` shim to ~/.local/bin/ascendo.
#   8. Prints profile-tailored usage instructions.
#
# Idempotent: re-running pulls instead of re-cloning.
# Bash 3.2 compatible (macOS default).

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
    C_BLUE="$(tput setaf 4 || true)"
    C_GREEN="$(tput setaf 2 || true)"
    C_YELLOW="$(tput setaf 3 || true)"
    C_RED="$(tput setaf 1 || true)"
    C_BOLD="$(tput bold || true)"
    C_RESET="$(tput sgr0 || true)"
else
    C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_RESET=""
fi

info()  { printf "%s[info]%s  %s\n"  "$C_BLUE"   "$C_RESET" "$1"; }
ok()    { printf "%s[ ok ]%s  %s\n"  "$C_GREEN"  "$C_RESET" "$1"; }
warn()  { printf "%s[warn]%s  %s\n"  "$C_YELLOW" "$C_RESET" "$1" >&2; }
fail()  { printf "%s[fail]%s  %s\n"  "$C_RED"    "$C_RESET" "$1" >&2; exit 1; }
step()  { printf "\n%s==>%s %s%s%s\n" "$C_BLUE" "$C_RESET" "$C_BOLD" "$1" "$C_RESET"; }

# ── i18n: hard-coded EN/PL strings (bash 3.2 — no associative arrays) ─────
# Keys are upper-snake. Lookup via `tr_lookup KEY`.
TR_LANG="en"

tr_lookup() {
    local key="$1"
    case "$TR_LANG-$key" in
        en-WELCOME)         echo "Welcome to the Ascendo installer." ;;
        pl-WELCOME)         echo "Witaj w instalatorze Ascendo." ;;
        en-PICK_LANG)       echo "Pick language [en/pl, default en]:" ;;
        pl-PICK_LANG)       echo "Wybierz język [en/pl, domyślnie en]:" ;;
        en-LANG_SAVED)      echo "Language saved to ~/.config/ascendo/locale.txt" ;;
        pl-LANG_SAVED)      echo "Język zapisany w ~/.config/ascendo/locale.txt" ;;
        en-DETECT_OS)       echo "Detecting operating system…" ;;
        pl-DETECT_OS)       echo "Wykrywanie systemu operacyjnego…" ;;
        en-OS_UNSUPPORTED)  echo "Unsupported OS. Ascendo currently supports macOS, Ubuntu/Debian, Fedora, Arch." ;;
        pl-OS_UNSUPPORTED)  echo "System nieobsługiwany. Ascendo wspiera macOS, Ubuntu/Debian, Fedora, Arch." ;;
        en-DEPS_CHECK)      echo "Checking system dependencies…" ;;
        pl-DEPS_CHECK)      echo "Sprawdzanie zależności systemowych…" ;;
        en-DEPS_INSTALL)    echo "Installing missing dependencies (will use sudo where required)…" ;;
        pl-DEPS_INSTALL)    echo "Instalowanie brakujących zależności (sudo gdy wymagane)…" ;;
        en-PICK_PROFILE)    echo "Pick install profile:" ;;
        pl-PICK_PROFILE)    echo "Wybierz profil instalacji:" ;;
        en-PROFILE_1)       echo "  1) CLI only         — fastest, ~30 MB" ;;
        pl-PROFILE_1)       echo "  1) Tylko CLI        — najszybciej, ~30 MB" ;;
        en-PROFILE_2)       echo "  2) CLI + Web        — adds the FastAPI dashboard" ;;
        pl-PROFILE_2)       echo "  2) CLI + Web        — dodaje dashboard FastAPI" ;;
        en-PROFILE_3)       echo "  3) CLI + Web + Desktop — full Tauri 2.x desktop app" ;;
        pl-PROFILE_3)       echo "  3) CLI + Web + Desktop — pełna aplikacja Tauri 2.x" ;;
        en-PROFILE_PROMPT)  echo "Enter 1, 2, or 3 [default 2]:" ;;
        pl-PROFILE_PROMPT)  echo "Wpisz 1, 2 lub 3 [domyślnie 2]:" ;;
        en-PROFILE_INVALID) echo "Invalid profile. Enter 1, 2, or 3." ;;
        pl-PROFILE_INVALID) echo "Niepoprawny profil. Wpisz 1, 2 lub 3." ;;
        en-CLONING)         echo "Cloning Ascendo repository…" ;;
        pl-CLONING)         echo "Klonowanie repozytorium Ascendo…" ;;
        en-PULLING)         echo "Existing checkout found — pulling latest…" ;;
        pl-PULLING)         echo "Istniejący checkout — pobieranie najnowszej wersji…" ;;
        en-VENV_CREATE)     echo "Creating Python venv at \$INSTALL_DIR/.venv…" ;;
        pl-VENV_CREATE)     echo "Tworzenie Python venv w \$INSTALL_DIR/.venv…" ;;
        en-PIP_INSTALL)     echo "Installing Ascendo Python packages (editable)…" ;;
        pl-PIP_INSTALL)     echo "Instalowanie pakietów Python Ascendo (editable)…" ;;
        en-DASHBOARD_DEPS)  echo "Installing dashboard dependencies (FastAPI + uvicorn)…" ;;
        pl-DASHBOARD_DEPS)  echo "Instalowanie zależności dashboardu (FastAPI + uvicorn)…" ;;
        en-DESKTOP_DEPS)    echo "Installing desktop dependencies (Rust + Node + npm install)…" ;;
        pl-DESKTOP_DEPS)    echo "Instalowanie zależności desktop (Rust + Node + npm install)…" ;;
        en-SHIM_INSTALL)    echo "Linking ascendo shim to ~/.local/bin/ascendo" ;;
        pl-SHIM_INSTALL)    echo "Tworzenie linku ascendo w ~/.local/bin/ascendo" ;;
        en-DONE)            echo "Installation complete." ;;
        pl-DONE)            echo "Instalacja zakończona." ;;
        en-USAGE_HEADER)    echo "Next steps:" ;;
        pl-USAGE_HEADER)    echo "Następne kroki:" ;;
        en-USAGE_PATH)      echo "Make sure ~/.local/bin is on your PATH (most modern shells already do this)." ;;
        pl-USAGE_PATH)      echo "Upewnij się że ~/.local/bin jest w PATH (większość powłok już ma)." ;;
        *)                  echo "$key" ;;
    esac
}

t() { tr_lookup "$1"; }

# ── OS detection ──────────────────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Darwin) echo "macos"; return 0 ;;
        Linux)
            if [ -f /etc/os-release ]; then
                # shellcheck disable=SC1091
                . /etc/os-release
                case "${ID:-}-${ID_LIKE:-}" in
                    ubuntu*|debian*|*-ubuntu*|*-debian*) echo "ubuntu"; return 0 ;;
                    fedora*|*-fedora*|rhel*|*-rhel*)     echo "fedora"; return 0 ;;
                    arch*|*-arch*|manjaro*|*-manjaro*)   echo "arch";   return 0 ;;
                esac
            fi
            ;;
    esac
    echo "unknown"
    return 0
}

# ── Prompt helpers (bash 3.2 safe) ────────────────────────────────────────
prompt_default() {
    # $1 = prompt, $2 = default, sets REPLY var
    local prompt="$1"
    local default="$2"
    if [ ! -t 0 ]; then
        # Non-interactive (curl|bash piped); accept default silently.
        REPLY="$default"
        return 0
    fi
    printf "%s " "$prompt"
    if ! read -r REPLY; then
        REPLY="$default"
    fi
    if [ -z "$REPLY" ]; then
        REPLY="$default"
    fi
}

# ── Step 1: language ──────────────────────────────────────────────────────
step "$(t WELCOME)"

prompt_default "$(t PICK_LANG)" "en"
case "$REPLY" in
    pl|PL|pl_*) TR_LANG="pl" ;;
    *)          TR_LANG="en" ;;
esac
mkdir -p "$HOME/.config/ascendo"
printf "%s\n" "$TR_LANG" > "$HOME/.config/ascendo/locale.txt"
ok "$(t LANG_SAVED)"

# ── Step 2: detect OS ─────────────────────────────────────────────────────
step "$(t DETECT_OS)"
OS="$(detect_os)"
case "$OS" in
    macos)   ok  "macOS (Darwin) detected" ;;
    ubuntu)  ok  "Ubuntu/Debian detected" ;;
    fedora)  ok  "Fedora/RHEL detected" ;;
    arch)    ok  "Arch/Manjaro detected" ;;
    unknown) fail "$(t OS_UNSUPPORTED)" ;;
esac

# ── Step 3: install dependencies ──────────────────────────────────────────
step "$(t DEPS_CHECK)"

need_cmd() { command -v "$1" >/dev/null 2>&1; }

deps_install_macos() {
    if ! need_cmd brew; then
        warn "Homebrew not found. Installing via the official one-liner…"
        info "sudo may prompt — Homebrew installer requests it once."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    local pkgs=""
    need_cmd python3 || pkgs="$pkgs python@3.14"
    need_cmd jq      || pkgs="$pkgs jq"
    need_cmd curl    || pkgs="$pkgs curl"
    need_cmd git     || pkgs="$pkgs git"
    if [ -n "$pkgs" ]; then
        info "$(t DEPS_INSTALL)"
        # shellcheck disable=SC2086
        brew install $pkgs
    fi
}

deps_install_ubuntu() {
    local pkgs=""
    need_cmd python3   || pkgs="$pkgs python3"
    need_cmd pip3      || pkgs="$pkgs python3-pip"
    dpkg -s python3-venv >/dev/null 2>&1 || pkgs="$pkgs python3-venv"
    need_cmd jq        || pkgs="$pkgs jq"
    need_cmd curl      || pkgs="$pkgs curl"
    need_cmd git       || pkgs="$pkgs git"
    if [ -n "$pkgs" ]; then
        info "$(t DEPS_INSTALL)"
        info "About to run: sudo apt-get update && sudo apt-get install -y$pkgs"
        sudo apt-get update
        # shellcheck disable=SC2086
        sudo apt-get install -y $pkgs
    fi
}

deps_install_fedora() {
    local pkgs=""
    need_cmd python3 || pkgs="$pkgs python3"
    need_cmd pip3    || pkgs="$pkgs python3-pip"
    need_cmd jq      || pkgs="$pkgs jq"
    need_cmd curl    || pkgs="$pkgs curl"
    need_cmd git     || pkgs="$pkgs git"
    if [ -n "$pkgs" ]; then
        info "$(t DEPS_INSTALL)"
        info "About to run: sudo dnf install -y$pkgs"
        # shellcheck disable=SC2086
        sudo dnf install -y $pkgs
    fi
}

deps_install_arch() {
    local pkgs=""
    need_cmd python  || pkgs="$pkgs python"
    need_cmd pip     || pkgs="$pkgs python-pip"
    need_cmd jq      || pkgs="$pkgs jq"
    need_cmd curl    || pkgs="$pkgs curl"
    need_cmd git     || pkgs="$pkgs git"
    if [ -n "$pkgs" ]; then
        info "$(t DEPS_INSTALL)"
        info "About to run: sudo pacman -S --needed --noconfirm$pkgs"
        # shellcheck disable=SC2086
        sudo pacman -S --needed --noconfirm $pkgs
    fi
}

case "$OS" in
    macos)   deps_install_macos ;;
    ubuntu)  deps_install_ubuntu ;;
    fedora)  deps_install_fedora ;;
    arch)    deps_install_arch ;;
esac

# Verify Python is >=3.11
PY_BIN="python3"
if ! "$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    fail "Ascendo requires Python 3.11+. Current: $($PY_BIN --version 2>&1 || echo missing)"
fi
ok "Python: $($PY_BIN --version)"

# ── Step 4: install profile ───────────────────────────────────────────────
step "$(t PICK_PROFILE)"
printf "%s\n" "$(t PROFILE_1)"
printf "%s\n" "$(t PROFILE_2)"
printf "%s\n" "$(t PROFILE_3)"

PROFILE=""
while [ -z "$PROFILE" ]; do
    prompt_default "$(t PROFILE_PROMPT)" "2"
    case "$REPLY" in
        1) PROFILE="cli" ;;
        2) PROFILE="web" ;;
        3) PROFILE="desktop" ;;
        *) warn "$(t PROFILE_INVALID)" ;;
    esac
done
ok "profile: $PROFILE"

# ── Step 5: clone or pull ─────────────────────────────────────────────────
INSTALL_DIR="$HOME/.local/share/ascendo"
REPO_URL="https://github.com/KasprowiczM/ascendo.git"

step "Repo @ $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
    info "$(t PULLING)"
    git -C "$INSTALL_DIR" pull --ff-only
else
    info "$(t CLONING)"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if [ "$PROFILE" = "cli" ]; then
        # Sparse-checkout to skip the heavy ui/desktop-tauri tree.
        git clone --filter=blob:none --no-checkout "$REPO_URL" "$INSTALL_DIR"
        git -C "$INSTALL_DIR" sparse-checkout init --cone
        git -C "$INSTALL_DIR" sparse-checkout set core adapters bin schemas docs plugins lib scripts share i18n
        git -C "$INSTALL_DIR" checkout main
    else
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
fi
ok "Repo ready"

# ── Step 6: venv + pip install ────────────────────────────────────────────
step "Python venv"
VENV_DIR="$INSTALL_DIR/.venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    info "$(t VENV_CREATE)"
    "$PY_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"
"$VENV_DIR/bin/python" -m pip install --upgrade pip --quiet

info "$(t PIP_INSTALL)"
"$VENV_DIR/bin/pip" install -e "$INSTALL_DIR/core" --quiet

ADAPTER_DIR=""
case "$OS" in
    macos)                  ADAPTER_DIR="$INSTALL_DIR/adapters/macos" ;;
    ubuntu|fedora|arch)     ADAPTER_DIR="$INSTALL_DIR/adapters/ubuntu" ;;
esac
if [ -n "$ADAPTER_DIR" ] && [ -d "$ADAPTER_DIR" ]; then
    "$VENV_DIR/bin/pip" install -e "$ADAPTER_DIR" --no-deps --quiet || \
        warn "Adapter install reported a non-fatal error; CLI core remains usable."
fi

if [ "$PROFILE" = "web" ] || [ "$PROFILE" = "desktop" ]; then
    info "$(t DASHBOARD_DEPS)"
    "$VENV_DIR/bin/pip" install fastapi 'uvicorn[standard]' httpx --quiet
fi

if [ "$PROFILE" = "desktop" ]; then
    step "Desktop toolchain"
    info "$(t DESKTOP_DEPS)"

    # Rust via rustup (the official, portable path).
    if ! need_cmd cargo; then
        info "Installing Rust via rustup (no sudo)…"
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
        # shellcheck disable=SC1091
        . "$HOME/.cargo/env"
    fi
    ok "cargo: $(cargo --version)"

    # Node 18+: macOS via brew, Linux via the OS package manager (with fnm fallback).
    if ! need_cmd node || ! node -e 'process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)' 2>/dev/null; then
        case "$OS" in
            macos)
                brew install node ;;
            ubuntu)
                info "Installing Node 20 via NodeSource (recommended for Ubuntu)…"
                curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
                sudo apt-get install -y nodejs ;;
            fedora)
                sudo dnf install -y nodejs npm ;;
            arch)
                sudo pacman -S --needed --noconfirm nodejs npm ;;
        esac
    fi
    ok "node: $(node --version)"

    if [ -d "$INSTALL_DIR/ui/desktop-tauri" ]; then
        info "Running npm install in ui/desktop-tauri/ …"
        ( cd "$INSTALL_DIR/ui/desktop-tauri" && npm install --silent ) || \
            warn "npm install reported a non-fatal error; you can re-run it manually."
    else
        warn "ui/desktop-tauri/ not present — sparse-checkout may have skipped it. Run a full clone for desktop builds."
    fi
fi

# ── Step 7: shim ──────────────────────────────────────────────────────────
step "CLI shim"
mkdir -p "$HOME/.local/bin"
SHIM="$HOME/.local/bin/ascendo"

cat > "$SHIM" <<EOF
#!/usr/bin/env bash
# Ascendo CLI shim — generated by install.sh.
exec "$VENV_DIR/bin/python" -m ascendo "\$@"
EOF
chmod +x "$SHIM"
ok "$(t SHIM_INSTALL)"

# ── Step 8: usage instructions ────────────────────────────────────────────
step "$(t DONE)"

printf "\n%s%s%s\n" "$C_BOLD" "$(t USAGE_HEADER)" "$C_RESET"
printf "  %s\n" "$(t USAGE_PATH)"
printf "\n"
printf "  %sascendo doctor%s                       # health snapshot\n" "$C_GREEN" "$C_RESET"
printf "  %sascendo run --phase check%s            # find updates (read-only)\n" "$C_GREEN" "$C_RESET"
printf "  %sascendo run --phase apply%s            # apply updates (gated)\n" "$C_GREEN" "$C_RESET"

if [ "$PROFILE" = "web" ] || [ "$PROFILE" = "desktop" ]; then
    printf "\n  %sascendo dashboard --port 8765%s        # open http://127.0.0.1:8765/\n" "$C_GREEN" "$C_RESET"
    printf "  %sascendo dashboard --background%s        # detached mode (returns immediately)\n" "$C_GREEN" "$C_RESET"
fi

if [ "$PROFILE" = "desktop" ]; then
    case "$OS" in
        macos)
            printf "\n  %sbash %s/bin/launch-desktop-macos.sh --build%s\n" "$C_GREEN" "$INSTALL_DIR" "$C_RESET"
            printf "                                          # produces .app + .dmg under target/release/bundle/\n" ;;
        ubuntu|fedora|arch)
            printf "\n  %sbash %s/bin/launch-desktop-linux.sh --build%s\n" "$C_GREEN" "$INSTALL_DIR" "$C_RESET"
            printf "                                          # produces .AppImage under target/release/bundle/\n" ;;
    esac
fi

printf "\n  Repo:    %s\n" "$INSTALL_DIR"
printf "  venv:    %s\n" "$VENV_DIR"
printf "  config:  %s\n" "$HOME/.config/ascendo/locale.txt"
printf "  Docs:    https://github.com/KasprowiczM/ascendo\n"
printf "\n"
ok "$(t DONE)"
