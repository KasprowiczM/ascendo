#!/usr/bin/env bash
# =============================================================================
# bin/launch-desktop-macos.sh -- run/build the Ascendo Tauri desktop on macOS
# =============================================================================
# Tauri 2.x shell. Spawns `python3 -m ascendo dashboard` as a sidecar process
# and opens it in a native WKWebView window.
#
# Modes:
#   $ bash bin/launch-desktop-macos.sh             # dev mode (npm run tauri dev)
#   $ bash bin/launch-desktop-macos.sh --build     # produces .app + .dmg
#                                                  # in ui/desktop-tauri/src-tauri/
#                                                  # target/release/bundle/{macos,dmg}/
#
# Build prerequisites (one-time):
#   xcode-select --install                         # Apple command-line tools
#   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # Rust
#   brew install node                              # Node 18+
#
# Dev runs against the live worktree's Python adapter (no install required
# to test). Build produces a code-unsigned .app/.dmg — running it on another
# Mac will trigger Gatekeeper. Code signing is M6 work.
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
TAURI_DIR="$REPO_ROOT/ui/desktop-tauri"

MODE="dev"
# Argument parser. We tolerate two shapes of accidental input that
# shell users hit when copy-pasting our doc snippets:
#   1. zsh interactive mode ignores `#` comments by default unless
#      `interactive_comments` is set, so `bash bin/launch-…sh # foo`
#      passes "#", "foo" as positional args. Tokens starting with `#`
#      are silently dropped here.
#   2. comment tail words after the `#` (e.g. "dev,", "instant",
#      "reload") are not flag tokens. Anything that isn't one of our
#      known flags is now a soft WARN — print and continue, instead of
#      hard exit 2 — so a stray paste doesn't cancel the build/run.
while [ $# -gt 0 ]; do
    case "$1" in
        --build|-b) MODE="build"; shift ;;
        --help|-h)
            sed -n '2,21p' "$0"; exit 0 ;;
        \#*) shift ;;
        *)
            printf "launch-desktop-macos.sh: ignoring unknown arg: %s\n" "$1" >&2
            shift
            ;;
    esac
done

step() { printf "\n==> %s\n" "$1"; }
ok()   { printf "  [OK] %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1" >&2; exit 1; }

# ── Toolchain check ─────────────────────────────────────────────────────────
step "Toolchain check"

if ! command -v node >/dev/null 2>&1; then
    fail "node not on PATH (install: brew install node)"
fi
ok "node:  $(node --version)"

if ! command -v npm >/dev/null 2>&1; then
    fail "npm not on PATH"
fi
ok "npm:   $(npm --version)"

if ! command -v cargo >/dev/null 2>&1; then
    fail "cargo not on PATH (install Rust: https://rustup.rs)"
fi
ok "cargo: $(cargo --version)"

if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not on PATH"
fi
if ! python3 -m ascendo --help >/dev/null 2>&1; then
    fail "python3 -m ascendo failed -- run 'bash bin/install-dev-macos.sh' first"
fi
ok "python: $(python3 --version)  ascendo CLI: ok"

# ── Build / dev ─────────────────────────────────────────────────────────────
cd "$TAURI_DIR"

if [ ! -d node_modules ]; then
    step "npm install (one-time, ~30s)"
    npm install --silent || fail "npm install failed"
    ok "node_modules ready"
fi

case "$MODE" in
    dev)
        step "npm run tauri dev (Ctrl-C to stop)"
        npm run tauri dev
        ;;
    build)
        step "npm run tauri build (~5-10 min on first run)"
        # The DMG bundler (bundle_dmg.sh) is a fragile post-step that often
        # fails on macOS over network mounts, in CI sandboxes, or when the
        # `hdiutil` create_dmg dependency tree drifts. We DON'T treat its
        # failure as fatal: the .app bundle is what users actually launch
        # via Cmd+Tab / Finder, and rebuilding the DMG on demand later is
        # one helper invocation. Tauri build exit code is captured and
        # reported separately from the .app existence check.
        BUNDLE_DIR="$TAURI_DIR/src-tauri/target/release/bundle"
        APP_PATH="$BUNDLE_DIR/macos/Ascendo.app"
        TAURI_RC=0
        npm run tauri build || TAURI_RC=$?
        if [ -d "$APP_PATH" ]; then
            ok ".app bundle ready: $APP_PATH"
            if [ "$TAURI_RC" -ne 0 ]; then
                printf "  [WARN] tauri build returned %d but the .app exists.\n" "$TAURI_RC"
                printf "         Most likely cause: DMG bundler post-step failed.\n"
                printf "         The .app is launchable; DMG packaging is optional.\n"
            fi
        else
            fail "tauri build failed and no .app produced"
        fi
        printf "\nBundle artefacts:\n"
        find "$BUNDLE_DIR" -name 'Ascendo*' 2>/dev/null | sed 's/^/  /'
        printf "\nTo launch:\n"
        printf "  open '%s'\n" "$APP_PATH"
        ;;
esac
