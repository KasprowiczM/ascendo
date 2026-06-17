#!/usr/bin/env bash
# =============================================================================
# bin/macos-build-and-install.sh — build the Ascendo DMG locally (unsigned)
#                                  and install it into /Applications.
# =============================================================================
#
# This is the SECOND of the two developer one-liners. Run it from inside the
# repo (the first one-liner, bin/macos-dev-bootstrap.sh, clones the repo and
# installs the build toolchain).
#
# It does three things:
#   1. Builds the DMG for the edition(s) you choose via bin/build-dmg.sh.
#   2. Mounts the resulting DMG.
#   3. Copies Ascendo.app into /Applications and strips the macOS
#      quarantine flag so the UNSIGNED app launches without a Gatekeeper
#      "damaged / cannot be verified" prompt.
#
# Because the build is unsigned, this is meant for the person building on
# their OWN machine. Removing the quarantine flag is safe here: you compiled
# the app yourself from source you just cloned.
#
# Usage:
#   bash bin/macos-build-and-install.sh                 # interactive picker
#   bash bin/macos-build-and-install.sh --edition=basic
#   bash bin/macos-build-and-install.sh --edition=dev
#   bash bin/macos-build-and-install.sh --edition=both --install=basic
#
# Flags:
#   --edition=basic|dev|both   Which edition(s) to BUILD. Default: interactive
#                              prompt (falls back to "basic" with no TTY).
#   --install=basic|dev|none   Which built edition to COPY into /Applications.
#                              Both editions ship the same "Ascendo.app", so
#                              only one can be installed at a time. Default:
#                              the edition built (when "both", defaults to basic).
#   --profile=cli|web|desktop|full   Forwarded to build-dmg.sh. Default: full.
#   --apps-dir=<path>          Install target. Default: /Applications.
#   --skip-deps                Forwarded to build-dmg.sh (skip `npm install`).
#   --no-launch                Do not `open -a Ascendo` after installing.
#   --no-install               Build only; do not copy into /Applications.
#   --help / -h                Show this banner.
# =============================================================================

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$REPO_ROOT/dist"

EDITION_SEL=""
INSTALL_SEL=""
PROFILE="full"
APPS_DIR="/Applications"
SKIP_DEPS=0
NO_LAUNCH=0
NO_INSTALL=0

step() { printf "\n==> %s\n"    "$1"; }
ok()   { printf "  [OK] %s\n"   "$1"; }
warn() { printf "  [WARN] %s\n" "$1" >&2; }
info() { printf "  %s\n"        "$1"; }
fail() { printf "\n[FAIL] %s\n" "$1" >&2; exit 1; }

show_help() { sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
    case "$1" in
        --edition=*)   EDITION_SEL="${1#*=}" ;;
        --edition)     shift; EDITION_SEL="${1:-}" ;;
        --install=*)   INSTALL_SEL="${1#*=}" ;;
        --install)     shift; INSTALL_SEL="${1:-}" ;;
        --profile=*)   PROFILE="${1#*=}" ;;
        --profile)     shift; PROFILE="${1:-}" ;;
        --apps-dir=*)  APPS_DIR="${1#*=}" ;;
        --apps-dir)    shift; APPS_DIR="${1:-}" ;;
        --skip-deps)   SKIP_DEPS=1 ;;
        --no-launch)   NO_LAUNCH=1 ;;
        --no-install)  NO_INSTALL=1 ;;
        --help|-h)     show_help ;;
        *)             warn "ignoring unknown arg: $1" ;;
    esac
    shift
done

# ── Platform + repo guards ─────────────────────────────────────────────────
[ "$(uname -s)" = "Darwin" ] || fail "macOS-only (got: $(uname -s))"
[ -f "$REPO_ROOT/bin/build-dmg.sh" ] || fail "bin/build-dmg.sh not found — run from inside the repo."

# Make sure cargo / brew are reachable even in a fresh, non-login shell
# (e.g. piped from curl right after the bootstrap installed them).
[ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
if ! command -v brew >/dev/null 2>&1; then
    for cand in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$cand" ] && eval "$("$cand" shellenv)" && break
    done
fi

# ── Resolve which edition(s) to build ──────────────────────────────────────
if [ -z "$EDITION_SEL" ]; then
    if [ -t 0 ]; then
        printf "\nWhich edition would you like to build?\n"
        printf "  1) basic   (default)\n"
        printf "  2) dev\n"
        printf "  3) both\n"
        printf "Choice [1]: "
        read -r choice
        case "${choice:-1}" in
            1|"") EDITION_SEL="basic" ;;
            2)    EDITION_SEL="dev" ;;
            3)    EDITION_SEL="both" ;;
            *)    fail "invalid choice: $choice" ;;
        esac
    else
        EDITION_SEL="basic"
        info "no TTY — defaulting to --edition=basic"
    fi
fi

case "$EDITION_SEL" in
    basic) EDITIONS=(basic) ;;
    dev)   EDITIONS=(dev) ;;
    both)  EDITIONS=(basic dev) ;;
    *)     fail "--edition must be basic|dev|both (got: $EDITION_SEL)" ;;
esac

# Which one to install into /Applications.
if [ -z "$INSTALL_SEL" ]; then
    INSTALL_SEL="${EDITIONS[0]}"           # basic for "both"; otherwise the built one
fi
case "$INSTALL_SEL" in
    basic|dev|none) ;;
    *) fail "--install must be basic|dev|none (got: $INSTALL_SEL)" ;;
esac

ARCH="$(uname -m)"
case "$ARCH" in
    arm64|aarch64) ARCH_LABEL="arm64" ;;
    x86_64)        ARCH_LABEL="x64" ;;
    *)             fail "unsupported arch: $ARCH" ;;
esac

edition_label() { case "$1" in basic) echo "Basic" ;; dev) echo "Dev" ;; esac; }

# ── 1. Build each requested edition ────────────────────────────────────────
for ed in "${EDITIONS[@]}"; do
    step "Build DMG — edition: $ed"
    BUILD_ARGS=(--edition="$ed" --profile="$PROFILE")
    [ "$SKIP_DEPS" -eq 1 ] && BUILD_ARGS+=(--skip-deps)
    bash "$REPO_ROOT/bin/build-dmg.sh" "${BUILD_ARGS[@]}" \
        || fail "build-dmg.sh failed for edition '$ed'"
done

# ── 2. Locate the DMG to install ───────────────────────────────────────────
if [ "$NO_INSTALL" -eq 1 ] || [ "$INSTALL_SEL" = "none" ]; then
    step "Skipping install (--no-install / --install=none)"
    ok "DMG(s) are in: $DIST_DIR"
    ls -1 "$DIST_DIR"/Ascendo-*-"$ARCH_LABEL".dmg 2>/dev/null || true
    exit 0
fi

LABEL="$(edition_label "$INSTALL_SEL")"
# Newest matching DMG for the chosen edition + arch.
DMG_PATH="$(ls -t "$DIST_DIR"/Ascendo-"$LABEL"-*-"$ARCH_LABEL".dmg 2>/dev/null | head -n1 || true)"
[ -n "$DMG_PATH" ] && [ -f "$DMG_PATH" ] \
    || fail "no built DMG found for edition '$INSTALL_SEL' (looked for $DIST_DIR/Ascendo-$LABEL-*-$ARCH_LABEL.dmg)"

# ── 3. Mount, copy into /Applications, strip quarantine ────────────────────
step "Install $DMG_PATH → $APPS_DIR"
MOUNT_POINT="$(mktemp -d -t ascendo-install)"
cleanup() { hdiutil detach "$MOUNT_POINT" >/dev/null 2>&1 || true; rmdir "$MOUNT_POINT" 2>/dev/null || true; }
trap cleanup EXIT

hdiutil attach "$DMG_PATH" -nobrowse -noautoopen -mountpoint "$MOUNT_POINT" >/dev/null \
    || fail "could not mount $DMG_PATH"
ok "mounted at $MOUNT_POINT"

SRC_APP="$MOUNT_POINT/Ascendo.app"
[ -d "$SRC_APP" ] || fail "Ascendo.app not found inside the DMG"

DEST_APP="$APPS_DIR/Ascendo.app"
if [ -d "$DEST_APP" ]; then
    info "removing existing $DEST_APP"
    rm -rf "$DEST_APP" || fail "could not remove old $DEST_APP (try: sudo rm -rf '$DEST_APP')"
fi

info "copying Ascendo.app → $APPS_DIR …"
cp -R "$SRC_APP" "$APPS_DIR/" || fail "copy failed (is $APPS_DIR writable? try a sudo copy)"
ok "installed: $DEST_APP"

# Strip the quarantine xattr so the unsigned app opens without a Gatekeeper
# prompt. Safe here — you just built this app yourself from source.
if xattr -dr com.apple.quarantine "$DEST_APP" 2>/dev/null; then
    ok "removed com.apple.quarantine (unsigned app will launch cleanly)"
else
    info "no quarantine flag to remove (fine)"
fi

cleanup
trap - EXIT
ok "ejected DMG"

# ── 4. Summary + launch ────────────────────────────────────────────────────
step "Done"
info "Installed edition: $INSTALL_SEL"
info "App:               $DEST_APP"
[ "$EDITION_SEL" = "both" ] && info "Both DMGs are in $DIST_DIR; only '$INSTALL_SEL' was installed (same app name)."

if [ "$NO_LAUNCH" -eq 0 ]; then
    info "Launching Ascendo…"
    open -a "$DEST_APP" 2>/dev/null || open "$DEST_APP" 2>/dev/null || warn "could not auto-launch — open it from Launchpad."
else
    info "Launch later with:  open -a Ascendo"
fi
