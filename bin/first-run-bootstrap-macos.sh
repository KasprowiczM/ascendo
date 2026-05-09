#!/usr/bin/env bash
# =============================================================================
# bin/first-run-bootstrap-macos.sh — smart bootstrap for the macOS .app/.dmg
# =============================================================================
#
# Run by Ascendo.app on its FIRST launch (the Tauri shell looks for this
# script in its Resources/, then exec's it via `osascript -e 'tell app
# "Terminal" to do script ...'` so the user sees the progress live).
#
# Idempotent: a marker file at ~/Library/Application Support/Ascendo/.bootstrapped
# tells the Tauri shell that the bootstrap has already run; subsequent
# launches go straight to the dashboard.
#
# What it does:
#   1. Detect Python 3.11+; install via Homebrew if missing.
#   2. Detect git, curl, jq; install via Homebrew if missing.
#   3. Run install.sh --edition basic --profile full (downloads from main).
#   4. Verify with `ascendo doctor`. If non-zero, exit non-zero and
#      DON'T mark bootstrapped — the next launch will retry.
#   5. Write the marker file so future launches skip the bootstrap.
#
# Env-var overrides (mirrored from install.sh — same names so power users
# can do `ASCENDO_EDITION=dev open Ascendo.app` for the first launch):
#   ASCENDO_EDITION   basic | dev    (default: basic)
#   ASCENDO_PROFILE   cli | web | desktop | full  (default: full)
#   ASCENDO_LANG      en | pl        (default: en)
#   ASCENDO_HOME      install dir    (default: ~/.local/share/ascendo)
#   ASCENDO_NONINTERACTIVE  1 to skip prompts
#
# Used by:
#   - The .app bundle's Tauri main.rs (Resource::resolve)
#   - The .pkg postinstall script (if we ship a .pkg in addition to .dmg)
#   - bin/build-dmg.sh tests
# =============================================================================

set -u
# We do NOT `set -e` because the bootstrap should report and recover from
# expected failures (Homebrew not installed, network down, etc.) instead
# of vanishing the Terminal window mid-run.

SUPPORT_DIR="$HOME/Library/Application Support/Ascendo"
MARKER="$SUPPORT_DIR/.bootstrapped"
LOG="$SUPPORT_DIR/first-run.log"

mkdir -p "$SUPPORT_DIR" 2>/dev/null

step() { printf "\n==> %s\n"   "$1" | tee -a "$LOG"; }
ok()   { printf "  [OK] %s\n"  "$1" | tee -a "$LOG"; }
warn() { printf "  [WARN] %s\n" "$1" | tee -a "$LOG" >&2; }
info() { printf "  %s\n"        "$1" | tee -a "$LOG"; }
die()  { printf "\n[FAIL] %s\n" "$1" | tee -a "$LOG" >&2; exit 1; }

# Reset log on every full run (failures will leave the previous log).
: > "$LOG"

step "Ascendo first-run bootstrap (macOS)"
info "Log: $LOG"
info "Marker: $MARKER"

# ── Already bootstrapped? Skip. ──────────────────────────────────────────
if [ -f "$MARKER" ]; then
    ok "already bootstrapped — nothing to do"
    exit 0
fi

# ── 1. Homebrew (mac's de-facto pkg manager) ────────────────────────────
step "Detect Homebrew"
if command -v brew >/dev/null 2>&1; then
    ok "brew: $(command -v brew)"
else
    warn "Homebrew not installed."
    info "Ascendo needs Homebrew to install Python 3.11+ / git / curl on macOS."
    info "Install Homebrew now? This is an interactive step; follow the prompts."
    info ""
    info "  Run this once, then re-launch Ascendo:"
    info '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    info ""
    die "Homebrew missing — see the install line above and re-launch Ascendo afterwards."
fi

brew_install_if_missing() {
    local pkg="$1"
    local probe="${2:-$1}"     # binary to look for on PATH
    if command -v "$probe" >/dev/null 2>&1; then
        ok "$probe: $(command -v "$probe")"
    else
        info "Installing $pkg via brew (this may take a minute)…"
        if brew install "$pkg" >>"$LOG" 2>&1; then
            ok "$pkg installed"
        else
            die "brew install $pkg failed — see $LOG"
        fi
    fi
}

# ── 2. System dependencies ───────────────────────────────────────────────
step "Check + install system dependencies"

# Python: must be 3.11 or newer. Test the version explicitly because macOS
# has a system /usr/bin/python3 that's typically 3.9 (too old).
PYTHON_OK=0
for p in python3.13 python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1; then
        VER="$($p -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)"
        MAJ="${VER%.*}"; MIN="${VER#*.}"
        if [ "$MAJ" = "3" ] && [ "$MIN" -ge 11 ] 2>/dev/null; then
            ok "$p: $($p --version)"
            PYTHON_OK=1
            break
        fi
    fi
done
if [ "$PYTHON_OK" -ne 1 ]; then
    info "No Python 3.11+ found. Installing python@3.12 via brew…"
    if brew install python@3.12 >>"$LOG" 2>&1; then
        ok "python@3.12 installed"
    else
        die "brew install python@3.12 failed — see $LOG"
    fi
fi

brew_install_if_missing git    git
brew_install_if_missing curl   curl
brew_install_if_missing jq     jq

# ── 3. Run install.sh from the canonical repo ────────────────────────────
step "Run Ascendo installer (install.sh)"

EDITION="${ASCENDO_EDITION:-basic}"
PROFILE="${ASCENDO_PROFILE:-full}"
LANG_PICK="${ASCENDO_LANG:-en}"
NONINTERACTIVE="${ASCENDO_NONINTERACTIVE:-1}"   # default to non-interactive for first-run

info "Edition:  $EDITION"
info "Profile:  $PROFILE"
info "Language: $LANG_PICK"

INSTALLER_URL="https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh"

# Honour proxy settings already in env (HTTPS_PROXY / http_proxy).
if ! curl -fsSL -m 30 "$INSTALLER_URL" -o "$SUPPORT_DIR/install.sh" >>"$LOG" 2>&1; then
    die "Could not download installer from $INSTALLER_URL — check your network connection."
fi

env \
    ASCENDO_EDITION="$EDITION" \
    ASCENDO_PROFILE="$PROFILE" \
    ASCENDO_LANG="$LANG_PICK" \
    ASCENDO_NONINTERACTIVE="$NONINTERACTIVE" \
    bash "$SUPPORT_DIR/install.sh" --edition="$EDITION" --profile="$PROFILE" 2>&1 \
    | tee -a "$LOG"
RC="${PIPESTATUS[0]}"

if [ "$RC" -ne 0 ]; then
    die "install.sh exited with $RC — see $LOG. Re-launch Ascendo to retry."
fi
ok "install.sh completed successfully"

# ── 4. Verify with `ascendo doctor` ──────────────────────────────────────
step "Verify install (ascendo doctor)"

# install.sh symlinks `ascendo` into ~/.local/bin/. PATH may not include
# it yet for this shell, so resolve directly.
ASCENDO_BIN="$HOME/.local/bin/ascendo"
if [ ! -x "$ASCENDO_BIN" ]; then
    # Fallback to PATH lookup in case the user has a custom $ASCENDO_HOME.
    ASCENDO_BIN="$(command -v ascendo 2>/dev/null || true)"
fi
if [ -z "$ASCENDO_BIN" ] || [ ! -x "$ASCENDO_BIN" ]; then
    die "ascendo binary not found after install — see $LOG"
fi

if "$ASCENDO_BIN" doctor 2>&1 | tee -a "$LOG"; then
    ok "ascendo doctor passed"
else
    die "ascendo doctor reported issues — see $LOG. Re-launch Ascendo to retry."
fi

# ── 5. Mark as bootstrapped ──────────────────────────────────────────────
{
    printf 'edition=%s\n' "$EDITION"
    printf 'profile=%s\n' "$PROFILE"
    printf 'lang=%s\n'    "$LANG_PICK"
    printf 'completed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'ascendo_bin=%s\n' "$ASCENDO_BIN"
} > "$MARKER"
ok "wrote marker: $MARKER"

step "Bootstrap complete"
info "You can close this Terminal window. Ascendo is ready."
info "Subsequent launches go straight to the dashboard."
exit 0
