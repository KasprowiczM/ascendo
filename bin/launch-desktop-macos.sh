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
WITH_DMG=0
# Argument parser. Tolerates two shapes of accidental input that shell
# users hit when copy-pasting our doc snippets:
#   1. zsh interactive mode ignores `#` comments by default unless
#      `interactive_comments` is set, so `bash bin/launch-…sh # foo`
#      passes "#", "foo" as positional args. Tokens starting with `#`
#      are silently dropped here.
#   2. comment tail words after the `#` (e.g. "dev,", "instant",
#      "reload") are not flag tokens. Anything that isn't one of our
#      known flags is a soft WARN — print and continue, instead of
#      hard exit 2 — so a stray paste doesn't cancel the build/run.
#
# Flags:
#   --build / -b      build the .app bundle (release)
#   --with-dmg        also try to package a .dmg (off by default; Tauri's
#                     bundle_dmg.sh is fragile on macOS 14+ and brew's
#                     create-dmg isn't a hard dependency)
#   --help / -h       help
while [ $# -gt 0 ]; do
    case "$1" in
        --build|-b)  MODE="build"; shift ;;
        --with-dmg)  WITH_DMG=1;   shift ;;
        --help|-h)
            sed -n '2,25p' "$0"; exit 0 ;;
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
        # Pre-clean the bundle output. Tauri's bundle_dmg.sh fails when a
        # previous DMG is already on disk because hdiutil refuses to
        # overwrite an attached image. Clearing both the macos/ and dmg/
        # subdirs before each build is the simplest reliable cure — we
        # rebuild from scratch every time, which is also the right
        # behaviour for an `--build` mode.
        BUNDLE_DIR="$TAURI_DIR/src-tauri/target/release/bundle"
        APP_PATH="$BUNDLE_DIR/macos/Ascendo.app"
        if [ -d "$BUNDLE_DIR/macos" ] || [ -d "$BUNDLE_DIR/dmg" ]; then
            step "Cleaning previous bundle output"
            rm -rf "$BUNDLE_DIR/macos" "$BUNDLE_DIR/dmg" 2>/dev/null || true
            ok "removed prior .app + .dmg artefacts"
        fi
        # Build the .app first (always succeeds when Rust compiles). The
        # DMG bundler (bundle_dmg.sh) is fragile — it depends on
        # hdiutil/rsync state and is the post-step that flakes most. We
        # run it as a SECOND, optional pass so a DMG failure can't
        # invalidate the .app. The standard workflow is: build .app →
        # ship .app via brew tap or direct download. DMG is mostly for
        # GitHub-Releases drag-to-Applications UX.
        step "npm run tauri build --bundles app   (~5-10 min on first run)"
        TAURI_RC=0
        npm run tauri build -- --bundles app || TAURI_RC=$?
        if [ ! -d "$APP_PATH" ]; then
            fail "tauri build failed and no .app produced (rc=$TAURI_RC)"
        fi
        ok ".app bundle ready: $APP_PATH"

        # DMG packaging is OFF by default. Tauri's bundle_dmg.sh fails
        # on macOS 14+ for reasons that are out of our control (hdiutil
        # drift, mountpoint cleanup races, etc.) and the previous
        # behaviour produced confusing [WARN] noise on every successful
        # .app build. The user already has a launchable .app — the .dmg
        # is only needed for distribution-style drag-to-Applications,
        # which is a separate concern.
        #
        # Opt in with `--with-dmg`. If `create-dmg` (brew) is on PATH,
        # we use it directly (skip Tauri's bundle_dmg.sh entirely);
        # otherwise we try Tauri's bundler and surface the error.
        if [ "$WITH_DMG" -eq 1 ]; then
            step "DMG packaging (--with-dmg requested)"
            DMG_RC=0
            DMG_OUT_DIR="$BUNDLE_DIR/dmg"
            DMG_OUT="$DMG_OUT_DIR/Ascendo_0.2.0_aarch64.dmg"
            mkdir -p "$DMG_OUT_DIR" 2>/dev/null

            if command -v create-dmg >/dev/null 2>&1; then
                # create-dmg from brew is the upstream that Tauri's
                # bundle_dmg.sh forked from. It's actively maintained
                # and works on current macOS — prefer it directly.
                if create-dmg \
                    --volname "Ascendo" \
                    --window-size 600 380 \
                    --icon-size 96 \
                    --icon "Ascendo.app" 150 180 \
                    --app-drop-link 450 180 \
                    --hide-extension "Ascendo.app" \
                    "$DMG_OUT" \
                    "$APP_PATH"; then
                    ok "create-dmg produced: $DMG_OUT"
                else
                    DMG_RC=$?
                fi
            else
                # Fall back to Tauri's bundle_dmg.sh — flaky but it's
                # what we have without an extra dep.
                npm run tauri build -- --bundles dmg --verbose || DMG_RC=$?
                # Tauri sometimes leaves a half-baked .dmg shell in
                # bundle/macos/ when bundle_dmg.sh aborts mid-stream.
                # Sweep it so the user doesn't see misleading artefacts.
                rm -f "$BUNDLE_DIR/macos/Ascendo"*.dmg 2>/dev/null
            fi

            if [ ! -f "$DMG_OUT" ] && [ "$DMG_RC" -ne 0 ]; then
                printf "  [WARN] DMG bundler failed (rc=%d). The .app is fine.\n" "$DMG_RC"
                printf "         For a reliable DMG: brew install create-dmg, then re-run with --with-dmg.\n"
            fi
        fi

        printf "\nBundle artefacts:\n"
        find "$BUNDLE_DIR" -name 'Ascendo*' 2>/dev/null | sed 's/^/  /'
        printf "\nTo launch:\n"
        printf "  open '%s'\n" "$APP_PATH"
        if [ "$WITH_DMG" -eq 0 ]; then
            printf "\n(DMG packaging skipped. Pass --with-dmg next time if you want a .dmg.)\n"
        fi
        ;;
esac
