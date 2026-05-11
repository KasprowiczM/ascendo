#!/usr/bin/env bash
# Ascendo — one-liner updater for macOS / Linux.
#
# Canonical usage:
#   curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.sh | bash
#
# Flags:
#   --verbose / -v        Trace every command
#   --help / -h           Show this banner and exit
#
# Env-var overrides:
#   ASCENDO_HOME=<path>   Install directory (default: ~/.local/share/ascendo)
#   ASCENDO_BRANCH=main   Branch to pull (default: main)
#   ASCENDO_VERBOSE=1     Same as --verbose
#
# What it does:
#   1. Detects existing installation at $ASCENDO_HOME.
#   2. Reads current version from core/ascendo/__version__.py.
#   3. git fetch + pull --ff-only origin <branch>. Refuses to merge.
#   4. Re-runs `pip install --upgrade -e core/ -e adapters/<os>/`.
#   5. Refreshes dashboard deps if FastAPI is installed.
#   6. Restarts any running `ascendo dashboard` background process.
#   7. Self-tests via `ascendo doctor`.
#   8. Prints version delta.
#
# If the install is missing, redirects to install.sh.

set -euo pipefail

INSTALL_DIR="${ASCENDO_HOME:-$HOME/.local/share/ascendo}"
REPO_BRANCH="${ASCENDO_BRANCH:-main}"
VERBOSE="${ASCENDO_VERBOSE:-0}"

while [ $# -gt 0 ]; do
    case "$1" in
        --verbose|-v) VERBOSE=1 ;;
        --help|-h)
            sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        --home=*)     INSTALL_DIR="${1#*=}" ;;
        *)
            printf "Unknown flag: %s. Try --help.\n" "$1" >&2
            exit 2
            ;;
    esac
    shift
done

[ "$VERBOSE" = "1" ] && set -x

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
info() { printf "%s[info]%s  %s\n" "$C_BLUE" "$C_RESET" "$1"; }
ok()   { printf "%s[ ok ]%s  %s\n" "$C_GREEN" "$C_RESET" "$1"; }
warn() { printf "%s[warn]%s  %s\n" "$C_YELLOW" "$C_RESET" "$1" >&2; }
fail() { printf "%s[fail]%s  %s\n" "$C_RED" "$C_RESET" "$1" >&2; exit 1; }
step() { printf "\n%s==>%s %s%s%s\n" "$C_BLUE" "$C_RESET" "$C_BOLD" "$1" "$C_RESET"; }

if [ -z "${HOME:-}" ]; then
    HOME="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6 || true)"
    [ -z "$HOME" ] && fail "\$HOME is not set."
    export HOME
fi

# Existence check
if [ ! -d "$INSTALL_DIR/.git" ]; then
    warn "No Ascendo installation found at $INSTALL_DIR."
    info "Run the installer first:"
    info "  curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash"
    exit 1
fi

# OS detection
detect_os() {
    case "$(uname -s)" in
        Darwin) echo "macos"; return ;;
        Linux)
            if [ -f /etc/os-release ]; then
                # shellcheck disable=SC1091
                . /etc/os-release
                case "${ID:-}-${ID_LIKE:-}" in
                    ubuntu*|debian*|*-ubuntu*|*-debian*) echo "ubuntu"; return ;;
                    fedora*|*-fedora*|rhel*|*-rhel*)     echo "fedora"; return ;;
                    arch*|*-arch*|manjaro*|*-manjaro*)   echo "arch"; return ;;
                esac
            fi
            ;;
    esac
    echo "unknown"
}
OS="$(detect_os)"
[ "$OS" = "unknown" ] && fail "Unsupported OS."

step "Updating Ascendo"
info "Install dir: $INSTALL_DIR"
info "Branch:      $REPO_BRANCH"
info "OS:          $OS"

# Network preflight
if ! curl -fsSL --max-time 8 -o /dev/null -I https://github.com 2>/dev/null; then
    fail "github.com unreachable. Check connection / proxy."
fi

VENV_DIR="$INSTALL_DIR/.venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    warn "venv missing — recreating from python3."
    command -v python3 >/dev/null 2>&1 || fail "python3 not found."
    python3 -m venv "$VENV_DIR"
fi

# Read pre-update version
OLD_VERSION="$( "$VENV_DIR/bin/python" -c 'from ascendo import __version__; print(__version__)' 2>/dev/null || echo "unknown" )"
OLD_COMMIT="$( git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown )"
info "Current: $OLD_VERSION ($OLD_COMMIT)"

# Refuse to merge if local changes exist
if ! git -C "$INSTALL_DIR" diff --quiet 2>/dev/null || \
   ! git -C "$INSTALL_DIR" diff --cached --quiet 2>/dev/null; then
    fail "Local changes in $INSTALL_DIR. Stash/commit them, or run install.sh --reinstall."
fi

step "git pull --ff-only origin $REPO_BRANCH"
git -C "$INSTALL_DIR" fetch --quiet origin "$REPO_BRANCH"
git -C "$INSTALL_DIR" pull --ff-only origin "$REPO_BRANCH" || \
    fail "Cannot fast-forward (diverged from origin/$REPO_BRANCH). Re-run install.sh --reinstall."

step "Refreshing Python packages"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install --upgrade -e "$INSTALL_DIR/core" --quiet

ADAPTER_DIR=""
case "$OS" in
    macos)               ADAPTER_DIR="$INSTALL_DIR/adapters/macos" ;;
    ubuntu|fedora|arch)  ADAPTER_DIR="$INSTALL_DIR/adapters/ubuntu" ;;
esac
if [ -n "$ADAPTER_DIR" ] && [ -d "$ADAPTER_DIR" ]; then
    "$VENV_DIR/bin/pip" install --upgrade -e "$ADAPTER_DIR" --no-deps --quiet || \
        warn "Adapter upgrade reported a non-fatal error."
fi

if "$VENV_DIR/bin/pip" show fastapi >/dev/null 2>&1; then
    info "Refreshing dashboard deps (FastAPI/uvicorn/httpx)…"
    "$VENV_DIR/bin/pip" install --upgrade fastapi 'uvicorn[standard]' httpx --quiet
fi

# Re-link helper scripts (in case new ones were added upstream).
EDITION="basic"
if [ -f "$INSTALL_DIR/.ascendo-edition" ]; then
    EDITION="$(head -n1 "$INSTALL_DIR/.ascendo-edition" | tr -d '[:space:]')"
    [ "$EDITION" = "dev" ] || EDITION="basic"
fi
USER_SCRIPTS_DIR="$INSTALL_DIR/bin/user-scripts"
LOCAL_BIN="$HOME/.local/bin"
if [ -d "$USER_SCRIPTS_DIR" ]; then
    mkdir -p "$LOCAL_BIN"
    LINKED_COUNT=0
    for src in "$USER_SCRIPTS_DIR"/ascendo_*; do
        case "$src" in *.ps1) continue ;; esac
        [ -f "$src" ] || continue
        ln -sf "$src" "$LOCAL_BIN/$(basename "$src")"
        LINKED_COUNT=$((LINKED_COUNT + 1))
    done
    if [ "$EDITION" = "dev" ] && [ -d "$USER_SCRIPTS_DIR/dev" ]; then
        for src in "$USER_SCRIPTS_DIR/dev"/ascendo_*; do
            case "$src" in *.ps1) continue ;; esac
            [ -f "$src" ] || continue
            ln -sf "$src" "$LOCAL_BIN/$(basename "$src")"
            LINKED_COUNT=$((LINKED_COUNT + 1))
        done
    fi
    [ "$LINKED_COUNT" -gt 0 ] && ok "Helper scripts: $LINKED_COUNT symlink(s) refreshed (edition=$EDITION)"
fi

# Restart running dashboard, if any.
#
# Be selective about which process we kill: a Tauri-spawned sidecar runs as
# "<sidecar-path>/ascendo dashboard --host 127.0.0.1 --port <ephemeral>" and
# its lifecycle is owned by the desktop app — killing it would orphan the
# Ascendo.app window with a dead WebView. The standalone CLI dashboard
# (what we WANT to restart so it picks up the new venv) runs as
# "python -m ascendo dashboard" via the `ascendo` shim.
#
# We target argv prefix "python.*-m ascendo dashboard" specifically so the
# Tauri sidecar (which runs a packaged executable, not "python -m") is
# left alone.
if pgrep -f "python.*-m ascendo dashboard" >/dev/null 2>&1; then
    step "Restarting standalone dashboard (CLI shim only — Tauri sidecar untouched)"
    pkill -f "python.*-m ascendo dashboard" 2>/dev/null || true
    sleep 1
    if [ -x "$HOME/.local/bin/ascendo" ]; then
        nohup "$HOME/.local/bin/ascendo" dashboard --background >/dev/null 2>&1 &
        ok "Dashboard restarted in background."
    else
        warn "ascendo shim missing at ~/.local/bin/ascendo — start dashboard manually."
    fi
elif pgrep -f "ascendo dashboard" >/dev/null 2>&1; then
    # Likely a Tauri sidecar; leave it alone (it owns its own lifecycle and
    # killing it would break the open desktop app). The user can quit
    # Ascendo.app and reopen it to pick up the new code.
    ok "Dashboard process detected but appears Tauri-managed — restart the desktop app to refresh."
fi

# Self-test
step "Self-test"
NEW_VERSION="$( "$VENV_DIR/bin/python" -c 'from ascendo import __version__; print(__version__)' 2>/dev/null || echo "unknown" )"
NEW_COMMIT="$( git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown )"

if "$VENV_DIR/bin/python" -m ascendo doctor >/dev/null 2>&1; then
    ok "ascendo doctor: green"
else
    warn "ascendo doctor returned non-zero. Re-run with --verbose to inspect."
    [ "$VERBOSE" = "1" ] && "$VENV_DIR/bin/python" -m ascendo doctor || true
fi

step "Update complete"
printf "  %sBefore:%s %s (%s)\n" "$C_BOLD" "$C_RESET" "$OLD_VERSION" "$OLD_COMMIT"
printf "  %sAfter: %s %s (%s)\n" "$C_BOLD" "$C_RESET" "$NEW_VERSION" "$NEW_COMMIT"
if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
    info "Already up to date."
else
    ok "Upgraded $OLD_COMMIT → $NEW_COMMIT"
fi
