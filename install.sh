#!/usr/bin/env bash
# Ascendo — one-liner installer for macOS / Linux.
#
# Canonical usage:
#   curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash
#
# Flags:
#   --update / -u            Update an existing installation and exit (no clone)
#   --reinstall / --force    Wipe ~/.local/share/ascendo and rebuild from scratch
#   --verbose / -v           Trace every command
#   --non-interactive        Refuse to prompt; use defaults / env-var overrides
#   --help / -h              Show this banner and exit
#
# Env-var overrides (useful for unattended / CI installs):
#   ASCENDO_LANG=en|pl              Language (default: en)
#   ASCENDO_PROFILE=cli|web|desktop Install profile (default: web)
#   ASCENDO_HOME=<path>             Install directory (default: ~/.local/share/ascendo)
#   ASCENDO_NONINTERACTIVE=1        Same as --non-interactive
#   ASCENDO_VERBOSE=1               Same as --verbose
#   ASCENDO_REPO_URL=<url>          Override clone URL (useful for forks)
#   ASCENDO_BRANCH=main             Branch to clone (default: main)
#   HTTPS_PROXY / http_proxy        Honoured by curl + git (no special handling needed)
#
# What it does (install path):
#   1. Detects OS (Darwin / Ubuntu+Debian / Fedora / Arch).
#   2. Asks for language (or reads $ASCENDO_LANG).
#   3. Verifies prerequisites (Python ≥3.11, git, curl, network, disk).
#   4. Installs missing system deps via the OS package manager.
#   5. Asks for an install profile (or reads $ASCENDO_PROFILE).
#   6. Clones (or pulls) the repo to $ASCENDO_HOME.
#   7. Sets up venv, pip-installs core/ + adapters/<os>/ editable.
#   8. Symlinks `ascendo` shim to ~/.local/bin/.
#   9. Self-tests via `ascendo doctor` and bails on failure.
#
# Idempotent: re-running upgrades in-place. Bash 3.2 compatible.

set -euo pipefail

# ── Defaults from env ─────────────────────────────────────────────────────
TR_LANG="${ASCENDO_LANG:-en}"
PROFILE="${ASCENDO_PROFILE:-}"
INSTALL_DIR="${ASCENDO_HOME:-$HOME/.local/share/ascendo}"
REPO_URL="${ASCENDO_REPO_URL:-https://github.com/KasprowiczM/ascendo.git}"
REPO_BRANCH="${ASCENDO_BRANCH:-main}"
NONINTERACTIVE="${ASCENDO_NONINTERACTIVE:-0}"
VERBOSE="${ASCENDO_VERBOSE:-0}"
MODE="install"          # install | update
FORCE_REINSTALL=0
MIN_DISK_MB=1024        # 1 GB

# ── Arg parser ────────────────────────────────────────────────────────────
show_help() {
    sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --update|-u)            MODE="update" ;;
        --reinstall|--force)    FORCE_REINSTALL=1 ;;
        --verbose|-v)           VERBOSE=1 ;;
        --non-interactive)      NONINTERACTIVE=1 ;;
        --help|-h)              show_help ;;
        --lang=*)               TR_LANG="${1#*=}" ;;
        --profile=*)            PROFILE="${1#*=}" ;;
        --home=*)               INSTALL_DIR="${1#*=}" ;;
        *)
            printf "Unknown flag: %s. Try --help.\n" "$1" >&2
            exit 2
            ;;
    esac
    shift
done

[ "$VERBOSE" = "1" ] && set -x

# ── Colours ────────────────────────────────────────────────────────────────
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
    C_BLUE="$(tput setaf 4 2>/dev/null || true)"
    C_GREEN="$(tput setaf 2 2>/dev/null || true)"
    C_YELLOW="$(tput setaf 3 2>/dev/null || true)"
    C_RED="$(tput setaf 1 2>/dev/null || true)"
    C_BOLD="$(tput bold 2>/dev/null || true)"
    C_RESET="$(tput sgr0 2>/dev/null || true)"
else
    C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_RESET=""
fi

info()  { printf "%s[info]%s  %s\n"  "$C_BLUE"   "$C_RESET" "$1"; }
ok()    { printf "%s[ ok ]%s  %s\n"  "$C_GREEN"  "$C_RESET" "$1"; }
warn()  { printf "%s[warn]%s  %s\n"  "$C_YELLOW" "$C_RESET" "$1" >&2; }
fail()  { printf "%s[fail]%s  %s\n"  "$C_RED"    "$C_RESET" "$1" >&2; exit 1; }
step()  { printf "\n%s==>%s %s%s%s\n" "$C_BLUE" "$C_RESET" "$C_BOLD" "$1" "$C_RESET"; }

# ── i18n: hard-coded EN/PL strings (bash 3.2 — no associative arrays) ─────
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
        en-VENV_CREATE)     echo "Creating Python venv…" ;;
        pl-VENV_CREATE)     echo "Tworzenie Python venv…" ;;
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
        en-NO_NETWORK)      echo "No network reachable (https://github.com unreachable). Check connection / proxy and retry." ;;
        pl-NO_NETWORK)      echo "Brak sieci (https://github.com nieosiągalne). Sprawdź połączenie / proxy i spróbuj ponownie." ;;
        en-DISK_LOW)        echo "Less than 1 GB free in install dir. Free up space and retry." ;;
        pl-DISK_LOW)        echo "Mniej niż 1 GB wolnego miejsca. Zwolnij miejsce i spróbuj ponownie." ;;
        en-PYTHON_OLD)      echo "Python 3.11+ required." ;;
        pl-PYTHON_OLD)      echo "Wymagany Python 3.11+." ;;
        en-LOCKED_PM)       echo "Package manager appears locked (another install in progress?). Wait and retry." ;;
        pl-LOCKED_PM)       echo "Menedżer pakietów zablokowany (inna instalacja?). Poczekaj i spróbuj ponownie." ;;
        en-DOCTOR_OK)       echo "Self-test passed — ascendo doctor reports green." ;;
        pl-DOCTOR_OK)       echo "Test pomyślny — ascendo doctor zgłasza zielone." ;;
        en-DOCTOR_FAIL)     echo "Self-test FAILED — ascendo doctor returned non-zero. Run with --verbose to debug." ;;
        pl-DOCTOR_FAIL)     echo "Test NIEUDANY — ascendo doctor zwrócił błąd. Uruchom z --verbose." ;;
        en-UPDATE_NOTFOUND) echo "No installation found at \$ASCENDO_HOME. Run install.sh (without --update) to install." ;;
        pl-UPDATE_NOTFOUND) echo "Nie znaleziono instalacji w \$ASCENDO_HOME. Uruchom install.sh (bez --update)." ;;
        *)                  echo "$key" ;;
    esac
}
t() { tr_lookup "$1"; }

# ── HOME safety net ───────────────────────────────────────────────────────
if [ -z "${HOME:-}" ]; then
    if HOME="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6)" && [ -n "$HOME" ]; then
        export HOME
        warn "\$HOME was unset — derived $HOME from passwd."
    else
        fail "\$HOME is not set and could not be derived. Set HOME and retry."
    fi
fi

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

# ── Prompt helpers ────────────────────────────────────────────────────────
prompt_default() {
    local prompt="$1" default="$2"
    if [ "$NONINTERACTIVE" = "1" ] || [ ! -t 0 ]; then
        REPLY="$default"
        return 0
    fi
    printf "%s " "$prompt"
    if ! read -r REPLY; then REPLY="$default"; fi
    [ -z "$REPLY" ] && REPLY="$default"
}

need_cmd() { command -v "$1" >/dev/null 2>&1; }

# ── Network + disk preflight ──────────────────────────────────────────────
check_network() {
    if ! curl -fsSL --max-time 8 -o /dev/null -I https://github.com; then
        fail "$(t NO_NETWORK)"
    fi
    ok "Network: github.com reachable"
}

check_disk() {
    local dir="$1" avail_kb avail_mb
    mkdir -p "$dir" 2>/dev/null || true
    if avail_kb="$(df -Pk "$dir" 2>/dev/null | awk 'NR==2 {print $4}')" && [ -n "$avail_kb" ]; then
        avail_mb=$((avail_kb / 1024))
        if [ "$avail_mb" -lt "$MIN_DISK_MB" ]; then
            fail "$(t DISK_LOW) (have ${avail_mb} MB, need ${MIN_DISK_MB} MB)"
        fi
        ok "Disk: ${avail_mb} MB free in $dir"
    else
        warn "Could not determine free disk space in $dir; continuing anyway."
    fi
}

check_pm_unlocked() {
    case "$OS" in
        ubuntu)
            for f in /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock; do
                if [ -e "$f" ] && command -v fuser >/dev/null 2>&1 && \
                   sudo -n fuser "$f" >/dev/null 2>&1; then
                    fail "$(t LOCKED_PM) (lock: $f)"
                fi
            done
            ;;
    esac
}

# ── Banner ────────────────────────────────────────────────────────────────
step "$(t WELCOME)"
info "Mode: $MODE   Verbose: $VERBOSE   Non-interactive: $NONINTERACTIVE"

# ── Update path: short-circuit ────────────────────────────────────────────
if [ "$MODE" = "update" ]; then
    if [ ! -d "$INSTALL_DIR/.git" ]; then
        fail "$(t UPDATE_NOTFOUND) (looked at: $INSTALL_DIR)"
    fi
    step "Updating $INSTALL_DIR"
    OS="$(detect_os)"
    [ "$OS" = "unknown" ] && fail "$(t OS_UNSUPPORTED)"
    check_network

    OLD_VERSION="$( "$INSTALL_DIR/.venv/bin/python" -c 'from ascendo import __version__; print(__version__)' 2>/dev/null || echo unknown )"
    info "Current version: $OLD_VERSION"

    info "git pull --ff-only origin $REPO_BRANCH"
    git -C "$INSTALL_DIR" fetch --quiet origin "$REPO_BRANCH" || fail "git fetch failed; try git -C $INSTALL_DIR pull manually"
    git -C "$INSTALL_DIR" pull --ff-only origin "$REPO_BRANCH" || \
        fail "Local changes prevent fast-forward. Stash or commit them, then re-run."

    VENV_DIR="$INSTALL_DIR/.venv"
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        warn "venv missing — recreating."
        python3 -m venv "$VENV_DIR"
    fi

    info "Upgrading editable installs…"
    "$VENV_DIR/bin/pip" install --upgrade -e "$INSTALL_DIR/core" --quiet
    case "$OS" in
        macos)               ADAPTER_DIR="$INSTALL_DIR/adapters/macos" ;;
        ubuntu|fedora|arch)  ADAPTER_DIR="$INSTALL_DIR/adapters/ubuntu" ;;
    esac
    if [ -n "${ADAPTER_DIR:-}" ] && [ -d "$ADAPTER_DIR" ]; then
        "$VENV_DIR/bin/pip" install --upgrade -e "$ADAPTER_DIR" --no-deps --quiet || true
    fi
    if "$VENV_DIR/bin/pip" show fastapi >/dev/null 2>&1; then
        "$VENV_DIR/bin/pip" install --upgrade fastapi 'uvicorn[standard]' httpx --quiet
    fi

    NEW_VERSION="$( "$VENV_DIR/bin/python" -c 'from ascendo import __version__; print(__version__)' 2>/dev/null || echo unknown )"
    info "New version:     $NEW_VERSION"

    # Restart any running dashboard so users see the new code.
    if pgrep -f "ascendo dashboard" >/dev/null 2>&1; then
        info "Restarting running dashboard process(es)…"
        pkill -f "ascendo dashboard" || true
        sleep 1
        nohup "$HOME/.local/bin/ascendo" dashboard --background >/dev/null 2>&1 &
        ok "Dashboard restarted in background."
    fi

    # Self-test.
    if "$VENV_DIR/bin/python" -m ascendo doctor >/dev/null 2>&1; then
        ok "$(t DOCTOR_OK)"
    else
        warn "$(t DOCTOR_FAIL)"
    fi
    ok "Update complete: $OLD_VERSION → $NEW_VERSION"
    exit 0
fi

# ── Install path ──────────────────────────────────────────────────────────
prompt_default "$(t PICK_LANG)" "${TR_LANG:-en}"
case "$REPLY" in
    pl|PL|pl_*) TR_LANG="pl" ;;
    *)          TR_LANG="en" ;;
esac
mkdir -p "$HOME/.config/ascendo"
printf "%s\n" "$TR_LANG" > "$HOME/.config/ascendo/locale.txt"
ok "$(t LANG_SAVED)"

step "$(t DETECT_OS)"
OS="$(detect_os)"
case "$OS" in
    macos)   ok  "macOS (Darwin) detected" ;;
    ubuntu)  ok  "Ubuntu/Debian detected" ;;
    fedora)  ok  "Fedora/RHEL detected" ;;
    arch)    ok  "Arch/Manjaro detected" ;;
    unknown) fail "$(t OS_UNSUPPORTED)" ;;
esac

step "Preflight checks"
check_network
check_disk "$(dirname "$INSTALL_DIR")"
check_pm_unlocked

# ── Reinstall: nuke prior install ─────────────────────────────────────────
if [ "$FORCE_REINSTALL" = "1" ] && [ -e "$INSTALL_DIR" ]; then
    warn "--reinstall: removing $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
fi

# ── System deps ───────────────────────────────────────────────────────────
step "$(t DEPS_CHECK)"

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

PY_BIN="python3"
if ! "$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    fail "$(t PYTHON_OLD) Current: $($PY_BIN --version 2>&1 || echo missing)"
fi
ok "Python: $($PY_BIN --version)"
need_cmd git || fail "git not on PATH after dep install — please install git manually."

# ── Profile ───────────────────────────────────────────────────────────────
if [ -z "$PROFILE" ]; then
    step "$(t PICK_PROFILE)"
    printf "%s\n" "$(t PROFILE_1)"
    printf "%s\n" "$(t PROFILE_2)"
    printf "%s\n" "$(t PROFILE_3)"
    while [ -z "$PROFILE" ]; do
        prompt_default "$(t PROFILE_PROMPT)" "2"
        case "$REPLY" in
            1|cli)     PROFILE="cli" ;;
            2|web)     PROFILE="web" ;;
            3|desktop) PROFILE="desktop" ;;
            *)         warn "$(t PROFILE_INVALID)" ;;
        esac
    done
fi
case "$PROFILE" in
    cli|web|desktop) ;;
    *) fail "Invalid \$ASCENDO_PROFILE: '$PROFILE'. Use cli|web|desktop." ;;
esac
ok "profile: $PROFILE"

# ── Clone or pull ─────────────────────────────────────────────────────────
step "Repo @ $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
    info "$(t PULLING)"
    git -C "$INSTALL_DIR" fetch --quiet origin "$REPO_BRANCH" || true
    git -C "$INSTALL_DIR" pull --ff-only origin "$REPO_BRANCH" || \
        fail "Existing checkout has local changes — stash or rerun with --reinstall."
else
    info "$(t CLONING)"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if [ "$PROFILE" = "cli" ]; then
        git clone --filter=blob:none --no-checkout -b "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
        git -C "$INSTALL_DIR" sparse-checkout init --cone
        git -C "$INSTALL_DIR" sparse-checkout set core adapters bin schemas docs plugins lib scripts share i18n
        git -C "$INSTALL_DIR" checkout "$REPO_BRANCH"
    else
        git clone -b "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
    fi
fi
ok "Repo ready"

# ── venv + pip ────────────────────────────────────────────────────────────
step "Python venv"
VENV_DIR="$INSTALL_DIR/.venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    info "$(t VENV_CREATE)"
    "$PY_BIN" -m venv "$VENV_DIR"
fi
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
    if ! need_cmd cargo; then
        info "Installing Rust via rustup (no sudo)…"
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
        # shellcheck disable=SC1091
        . "$HOME/.cargo/env"
    fi
    ok "cargo: $(cargo --version)"
    if ! need_cmd node || ! node -e 'process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)' 2>/dev/null; then
        case "$OS" in
            macos)
                brew install node ;;
            ubuntu)
                info "Installing Node 20 via NodeSource…"
                curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
                sudo apt-get install -y nodejs ;;
            fedora) sudo dnf install -y nodejs npm ;;
            arch)   sudo pacman -S --needed --noconfirm nodejs npm ;;
        esac
    fi
    ok "node: $(node --version)"
    if [ -d "$INSTALL_DIR/ui/desktop-tauri" ]; then
        info "Running npm install in ui/desktop-tauri/ …"
        ( cd "$INSTALL_DIR/ui/desktop-tauri" && npm install --silent ) || \
            warn "npm install reported a non-fatal error; you can re-run it manually."
    else
        warn "ui/desktop-tauri/ not present — sparse-checkout may have skipped it."
    fi
fi

# ── Shim ──────────────────────────────────────────────────────────────────
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

# PATH check
case ":$PATH:" in
    *:"$HOME/.local/bin":*) ok "~/.local/bin already on PATH" ;;
    *)
        warn "~/.local/bin is NOT on PATH. Add this line to your shell rc:"
        case "${SHELL:-}" in
            */zsh)   info "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc" ;;
            */fish)  info "  fish_add_path \$HOME/.local/bin" ;;
            *)       info "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc" ;;
        esac
        ;;
esac

# ── Self-test ─────────────────────────────────────────────────────────────
step "Self-test"
if "$VENV_DIR/bin/python" -m ascendo doctor >/dev/null 2>&1; then
    ok "$(t DOCTOR_OK)"
else
    warn "$(t DOCTOR_FAIL)"
    if [ "$VERBOSE" = "1" ]; then
        "$VENV_DIR/bin/python" -m ascendo doctor || true
    fi
fi

# ── Usage ─────────────────────────────────────────────────────────────────
step "$(t DONE)"
printf "\n%s%s%s\n" "$C_BOLD" "$(t USAGE_HEADER)" "$C_RESET"
printf "  %s\n" "$(t USAGE_PATH)"
printf "\n"
printf "  %sascendo doctor%s                       # health snapshot\n" "$C_GREEN" "$C_RESET"
printf "  %sascendo run --phase check%s            # find updates (read-only)\n" "$C_GREEN" "$C_RESET"
printf "  %sascendo run --phase apply%s            # apply updates (gated)\n" "$C_GREEN" "$C_RESET"

if [ "$PROFILE" = "web" ] || [ "$PROFILE" = "desktop" ]; then
    printf "\n  %sascendo dashboard --port 8765%s        # open http://127.0.0.1:8765/\n" "$C_GREEN" "$C_RESET"
    printf "  %sascendo dashboard --background%s        # detached mode\n" "$C_GREEN" "$C_RESET"
fi

printf "\n  Repo:    %s\n" "$INSTALL_DIR"
printf "  venv:    %s\n" "$VENV_DIR"
printf "  config:  %s\n" "$HOME/.config/ascendo/locale.txt"
printf "  Update:  curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.sh | bash\n"
printf "\n"
ok "$(t DONE)"
