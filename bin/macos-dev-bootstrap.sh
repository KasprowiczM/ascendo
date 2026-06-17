#!/usr/bin/env bash
# =============================================================================
# bin/macos-dev-bootstrap.sh — one-liner to clone Ascendo + install the macOS
#                              build toolchain on a developer's Mac.
# =============================================================================
#
# This is the FIRST of the two developer one-liners. It gets a Mac ready to
# build the DMG locally (no Apple signing required). Pair it with
# bin/macos-build-and-install.sh (the second one-liner) which actually
# produces the .dmg and installs it into /Applications.
#
# Typical use (in a fresh, empty folder you created, e.g. ~/Ascendo):
#
#   mkdir -p ~/Ascendo && cd ~/Ascendo
#   curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/bin/macos-dev-bootstrap.sh | bash
#
# …then build + install:
#
#   cd ~/Ascendo            # (or ~/Ascendo/ascendo if it cloned into a subdir)
#   bash bin/macos-build-and-install.sh
#
# Or do everything in one shot by passing --build:
#
#   curl -fsSL .../macos-dev-bootstrap.sh | bash -s -- --build --edition=both
#
# What it installs (only what's missing — every step is idempotent):
#   1. Xcode Command Line Tools  (clang, git, make)
#   2. Homebrew                  (macOS package manager)
#   3. Rust toolchain via rustup (cargo, rustc)        — compiles the Tauri shell
#   4. Node.js >= 18 via brew    (only if absent/too old)
#   5. create-dmg via brew       (nicer DMG layout; hdiutil fallback otherwise)
#
# Flags:
#   --build                Chain straight into macos-build-and-install.sh when done.
#   --edition=basic|dev|both   Forwarded to the build step (only with --build).
#   --dir=<path>           Where to clone (default: current dir if empty, else ./ascendo).
#   --ref=<branch|tag>     Git ref to check out (default: main).
#   --help / -h            Show this banner.
#
# Safe to re-run: if the repo is already present it skips the clone and just
# tops up any missing tools.
# =============================================================================

set -u

REPO_URL="https://github.com/KasprowiczM/ascendo.git"
GIT_REF="main"
DO_BUILD=0
EDITION_ARG=""
CLONE_DIR=""

step() { printf "\n==> %s\n"    "$1"; }
ok()   { printf "  [OK] %s\n"   "$1"; }
warn() { printf "  [WARN] %s\n" "$1" >&2; }
info() { printf "  %s\n"        "$1"; }
fail() { printf "\n[FAIL] %s\n" "$1" >&2; exit 1; }

show_help() { sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
    case "$1" in
        --build)       DO_BUILD=1 ;;
        --edition=*)   EDITION_ARG="${1#*=}" ;;
        --edition)     shift; EDITION_ARG="${1:-}" ;;
        --dir=*)       CLONE_DIR="${1#*=}" ;;
        --dir)         shift; CLONE_DIR="${1:-}" ;;
        --ref=*)       GIT_REF="${1#*=}" ;;
        --ref)         shift; GIT_REF="${1:-}" ;;
        --help|-h)     show_help ;;
        *)             warn "ignoring unknown arg: $1" ;;
    esac
    shift
done

# ── Platform guard ─────────────────────────────────────────────────────────
[ "$(uname -s)" = "Darwin" ] || fail "this script is macOS-only (got: $(uname -s))"

# ── 1. Xcode Command Line Tools ────────────────────────────────────────────
step "Xcode Command Line Tools"
if xcode-select -p >/dev/null 2>&1; then
    ok "already installed: $(xcode-select -p)"
else
    warn "Command Line Tools missing — launching the installer GUI."
    info "Accept the dialog, wait for it to finish, then re-run this script."
    xcode-select --install >/dev/null 2>&1 || true
    fail "Command Line Tools install started — re-run after it completes."
fi

# ── 2. Homebrew ────────────────────────────────────────────────────────────
step "Homebrew"
if ! command -v brew >/dev/null 2>&1; then
    # Pick up an existing install that just isn't on PATH yet.
    for cand in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$cand" ] && eval "$("$cand" shellenv)" && break
    done
fi
if command -v brew >/dev/null 2>&1; then
    ok "brew: $(command -v brew)"
else
    info "Installing Homebrew (you may be prompted for your password)…"
    NONINTERACTIVE=1 /bin/bash -c \
        "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || fail "Homebrew install failed."
    for cand in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$cand" ] && eval "$("$cand" shellenv)" && break
    done
    command -v brew >/dev/null 2>&1 || fail "Homebrew installed but not on PATH — open a new shell and re-run."
    ok "brew installed: $(command -v brew)"
fi

# ── 3. Rust toolchain (rustup) ─────────────────────────────────────────────
step "Rust toolchain"
[ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
if command -v cargo >/dev/null 2>&1; then
    ok "cargo: $(cargo --version 2>/dev/null)"
else
    info "Installing Rust via rustup (non-interactive)…"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
        || fail "rustup install failed."
    [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
    command -v cargo >/dev/null 2>&1 || fail "cargo not on PATH after rustup — open a new shell and re-run."
    ok "cargo installed: $(cargo --version)"
fi

# ── 4. Node.js >= 18 ───────────────────────────────────────────────────────
step "Node.js (>= 18)"
node_ok=0
if command -v node >/dev/null 2>&1; then
    NODE_MAJ="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    if [ "${NODE_MAJ:-0}" -ge 18 ] 2>/dev/null; then
        ok "node: $(node --version) (>= 18)"
        node_ok=1
    else
        warn "node $(node --version) is too old (need >= 18)."
    fi
fi
if [ "$node_ok" -ne 1 ]; then
    info "Installing node via brew…"
    brew install node >/dev/null 2>&1 || fail "brew install node failed."
    ok "node installed: $(node --version 2>/dev/null || echo '(open a new shell)')"
fi

# ── 5. create-dmg (optional but preferred) ─────────────────────────────────
step "create-dmg"
if command -v create-dmg >/dev/null 2>&1; then
    ok "create-dmg: $(command -v create-dmg)"
else
    info "Installing create-dmg via brew (build falls back to hdiutil if this fails)…"
    brew install create-dmg >/dev/null 2>&1 && ok "create-dmg installed" \
        || warn "create-dmg install failed — the build will use the hdiutil fallback."
fi

# ── 6. Clone (or detect) the repo ──────────────────────────────────────────
step "Ascendo source"
REPO_ROOT=""
# Already inside a checkout?
if [ -f "bin/build-dmg.sh" ] && [ -d ".git" ]; then
    REPO_ROOT="$(pwd)"
    ok "already inside an Ascendo checkout: $REPO_ROOT"
else
    command -v git >/dev/null 2>&1 || fail "git not found (Command Line Tools step should have provided it)."
    if [ -z "$CLONE_DIR" ]; then
        # Empty current dir → clone in place; otherwise use ./ascendo.
        if [ -z "$(ls -A . 2>/dev/null)" ]; then
            CLONE_DIR="."
        else
            CLONE_DIR="ascendo"
        fi
    fi
    if [ -d "$CLONE_DIR/.git" ]; then
        ok "repo already present at $CLONE_DIR — pulling latest"
        git -C "$CLONE_DIR" fetch --depth 1 origin "$GIT_REF" >/dev/null 2>&1 || true
        git -C "$CLONE_DIR" checkout "$GIT_REF" >/dev/null 2>&1 || true
        git -C "$CLONE_DIR" pull --ff-only origin "$GIT_REF" >/dev/null 2>&1 || true
    else
        info "Cloning $REPO_URL (ref: $GIT_REF) into ${CLONE_DIR}…"
        git clone --branch "$GIT_REF" "$REPO_URL" "$CLONE_DIR" \
            || fail "git clone failed."
    fi
    REPO_ROOT="$(cd "$CLONE_DIR" && pwd)"
    ok "cloned to: $REPO_ROOT"
fi

# ── 7. Done / optional chain into the build ────────────────────────────────
step "Bootstrap complete"
info "Toolchain ready. Repo at: $REPO_ROOT"

if [ "$DO_BUILD" -eq 1 ]; then
    BUILD_SCRIPT="$REPO_ROOT/bin/macos-build-and-install.sh"
    [ -f "$BUILD_SCRIPT" ] || fail "build script not found: $BUILD_SCRIPT"
    info "Chaining into the build (--build was passed)…"
    if [ -n "$EDITION_ARG" ]; then
        exec bash "$BUILD_SCRIPT" "--edition=$EDITION_ARG"
    else
        exec bash "$BUILD_SCRIPT"
    fi
else
    cat <<EOF

Next step — build the DMG and install it (unsigned, local):

  cd "$REPO_ROOT"
  bash bin/macos-build-and-install.sh            # interactive: pick basic / dev / both

  # …or non-interactively:
  bash bin/macos-build-and-install.sh --edition=basic
  bash bin/macos-build-and-install.sh --edition=both --install=basic

EOF
fi
