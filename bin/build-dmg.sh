#!/usr/bin/env bash
# =============================================================================
# bin/build-dmg.sh — Build the macOS Ascendo.app + wrap it in a DMG installer.
# =============================================================================
#
# Output: dist/Ascendo-<version>-<arch>.dmg  (sha256 printed at end)
#
# This is the canonical macOS installer build. It composes the same building
# blocks as bin/build-installer.ps1 (the Windows side), but produces a .dmg
# rather than .msi/.exe. End users then drag Ascendo.app to /Applications;
# on first launch the bundled `first-run-bootstrap-macos.sh` ensures Python
# 3.11+, git, and curl are present (installing via Homebrew if needed),
# then runs install.sh --edition basic --profile full.
#
# Modes / flags:
#   --skip-tauri          Re-use existing .app under target/release/bundle/macos/
#                         (useful while iterating on DMG cosmetics).
#   --skip-deps           Forwarded to launch-desktop-macos.sh (no `npm install`).
#   --no-sign             Skip codesign even if APPLE_CERT_NAME is set.
#   --no-notarize         Skip notarization even if APPLE_NOTARY_USER is set.
#   --output=<path>       Override the .dmg output path.
#   --dry-run             Print what would happen, do nothing.
#   --help / -h           Show this banner.
#
# Environment variables (all optional):
#   APPLE_CERT_NAME       "Developer ID Application: <Team> (XXXXXXXXXX)"
#                         If set, codesigns the .app + .dmg before notarize.
#   APPLE_NOTARY_USER     Apple ID for notarytool (App-specific password).
#   APPLE_NOTARY_PASSWORD App-specific password for the above.
#   APPLE_NOTARY_TEAM     Team ID (10-char) for notarytool.
#   ASCENDO_DMG_VOLNAME   Volume name shown in Finder (default: Ascendo).
#
# Build prerequisites (one-time):
#   xcode-select --install                  # Apple command-line tools
#   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
#   brew install node create-dmg            # node >=18, create-dmg optional
#
# Validation flow on a clean Mac:
#   1. bash bin/build-dmg.sh
#   2. open dist/Ascendo-0.0.7-arm64.dmg
#   3. Drag Ascendo.app to /Applications (Finder UI from create-dmg).
#   4. Launch Ascendo from Launchpad. First-run bootstrap ensures deps,
#      runs install.sh --edition basic --profile full, then opens the
#      dashboard. Subsequent launches skip the bootstrap.
# =============================================================================

set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
TAURI_DIR="$REPO_ROOT/ui/desktop-tauri"
DIST_DIR="$REPO_ROOT/dist"
BUNDLE_DIR="$TAURI_DIR/src-tauri/target/release/bundle"

# ── Defaults ─────────────────────────────────────────────────────────────
SKIP_TAURI=0
SKIP_DEPS=0
NO_SIGN=0
NO_NOTARIZE=0
DRY_RUN=0
OUTPUT_OVERRIDE=""
VOLNAME="${ASCENDO_DMG_VOLNAME:-Ascendo}"

show_help() {
    sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-tauri)   SKIP_TAURI=1 ;;
        --skip-deps)    SKIP_DEPS=1 ;;
        --no-sign)      NO_SIGN=1 ;;
        --no-notarize)  NO_NOTARIZE=1 ;;
        --dry-run)      DRY_RUN=1 ;;
        --output=*)     OUTPUT_OVERRIDE="${1#*=}" ;;
        --help|-h)      show_help ;;
        \#*)            ;;  # tolerate accidental comment fragments
        *)
            printf "build-dmg.sh: ignoring unknown arg: %s\n" "$1" >&2
            ;;
    esac
    shift
done

step() { printf "\n==> %s\n" "$1"; }
ok()   { printf "  [OK] %s\n" "$1"; }
warn() { printf "  [WARN] %s\n" "$1" >&2; }
fail() { printf "  [FAIL] %s\n" "$1" >&2; exit 1; }
run()  {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf "  (dry-run) %s\n" "$*"
    else
        "$@"
    fi
}

# ── 1. Resolve version + arch ────────────────────────────────────────────
step "Resolve version + architecture"

VERSION_FILE="$REPO_ROOT/core/ascendo/__version__.py"
[ -f "$VERSION_FILE" ] || fail "version file missing: $VERSION_FILE"
VERSION="$(awk -F'"' '/__version__/ {print $2}' "$VERSION_FILE")"
[ -n "$VERSION" ] || fail "could not parse __version__ from $VERSION_FILE"
ok "version: $VERSION"

ARCH="$(uname -m)"        # arm64 on Apple Silicon, x86_64 on Intel
case "$ARCH" in
    arm64|aarch64) ARCH_LABEL="arm64" ;;
    x86_64)        ARCH_LABEL="x64" ;;
    *)             fail "unsupported macOS arch: $ARCH" ;;
esac
ok "arch:    $ARCH_LABEL"

DMG_FILENAME="Ascendo-${VERSION}-${ARCH_LABEL}.dmg"
if [ -n "$OUTPUT_OVERRIDE" ]; then
    DMG_OUT="$OUTPUT_OVERRIDE"
else
    DMG_OUT="$DIST_DIR/$DMG_FILENAME"
fi
ok "output:  $DMG_OUT"

# ── 2. Toolchain check ───────────────────────────────────────────────────
step "Toolchain check"

# These are required to BUILD the .app + .dmg. End users do NOT need any
# of this — the first-run bootstrap inside Ascendo.app handles their side.
need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        fail "$1 not on PATH ($2)"
    fi
    ok "$1: $(command -v "$1")"
}

if [ "$SKIP_TAURI" -eq 0 ]; then
    need node  "install: brew install node"
    need npm   "install: brew install node"
    need cargo "install: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    need xcrun "install: xcode-select --install"
fi
need hdiutil   "ships with macOS — your install is broken"
if command -v create-dmg >/dev/null 2>&1; then
    ok "create-dmg: $(command -v create-dmg)  (preferred wrapper)"
    HAVE_CREATE_DMG=1
else
    warn "create-dmg not on PATH (install: brew install create-dmg). Will fall back to plain hdiutil."
    HAVE_CREATE_DMG=0
fi

# ── 3. Mirror the first-run bootstrap into src-tauri/ ───────────────────
# Tauri's path resolver chokes on `..` segments crossing the workspace
# root, so anything listed under bundle.resources in tauri.conf.json must
# live INSIDE src-tauri/. We mirror bin/first-run-bootstrap-macos.sh into
# src-tauri/ at build time. The mirror is .gitignored.
step "Mirror first-run-bootstrap into src-tauri/"
BOOTSTRAP_SRC="$SCRIPT_DIR/first-run-bootstrap-macos.sh"
BOOTSTRAP_DST="$TAURI_DIR/src-tauri/first-run-bootstrap-macos.sh"
if [ -f "$BOOTSTRAP_SRC" ]; then
    if [ "$DRY_RUN" -eq 0 ]; then
        cp -f "$BOOTSTRAP_SRC" "$BOOTSTRAP_DST"
        chmod +x "$BOOTSTRAP_DST"
    fi
    ok "mirrored: $BOOTSTRAP_DST"
else
    warn "first-run-bootstrap-macos.sh not found — bundle will be missing the post-install smart-deps step"
fi

# ── 4. Build the .app via Tauri (delegate to launch-desktop-macos.sh) ────
APP_PATH="$BUNDLE_DIR/macos/Ascendo.app"

if [ "$SKIP_TAURI" -eq 1 ]; then
    [ -d "$APP_PATH" ] || fail "no existing .app at $APP_PATH — drop --skip-tauri"
    ok "re-using $APP_PATH (--skip-tauri)"
else
    step "Build Ascendo.app via Tauri"
    LAUNCH_ARGS=(--build --no-open)
    [ "$SKIP_DEPS" -eq 1 ] && LAUNCH_ARGS+=(--skip-deps)
    if [ "$DRY_RUN" -eq 1 ]; then
        printf "  (dry-run) bash %s/launch-desktop-macos.sh %s\n" "$SCRIPT_DIR" "${LAUNCH_ARGS[*]}"
        ok "(dry-run) Tauri build skipped"
    else
        bash "$SCRIPT_DIR/launch-desktop-macos.sh" "${LAUNCH_ARGS[@]}"
        [ -d "$APP_PATH" ] || fail "Tauri build did not produce $APP_PATH"
        ok ".app built: $APP_PATH"
    fi
fi

# ── 5. Optional: codesign the .app ───────────────────────────────────────
if [ "$NO_SIGN" -eq 0 ] && [ -n "${APPLE_CERT_NAME:-}" ]; then
    step "Codesign Ascendo.app with: $APPLE_CERT_NAME"
    # --options runtime: enable hardened runtime (required for notarization).
    # --deep: sign all nested Mach-O binaries (Tauri ships several).
    # --timestamp: include a secure RFC3161 timestamp (required for notary).
    run codesign --force --deep --timestamp --options runtime \
        --sign "$APPLE_CERT_NAME" \
        "$APP_PATH"
    run codesign --verify --deep --strict --verbose=2 "$APP_PATH"
    ok ".app signed"
else
    [ "$NO_SIGN" -eq 1 ] && ok "codesign skipped (--no-sign)"
    [ -z "${APPLE_CERT_NAME:-}" ] && warn "codesign skipped (no \$APPLE_CERT_NAME). Gatekeeper will warn end users."
fi

# ── 6. Build the DMG ─────────────────────────────────────────────────────
step "Build DMG"

mkdir -p "$DIST_DIR"
[ -f "$DMG_OUT" ] && rm -f "$DMG_OUT"

# Helper to build a plain DMG via hdiutil — always available on macOS,
# never depends on AppleScript. Used as the primary builder when
# create-dmg isn't installed AND as the fallback when create-dmg's
# Finder-cosmetics step times out (a known macOS 14+ flake).
_build_dmg_hdiutil() {
    local stage
    stage="$(mktemp -d -t ascendo-dmg-stage)"
    cp -R "$APP_PATH" "$stage/"
    ln -s /Applications "$stage/Applications"
    rm -f "$DMG_OUT"
    hdiutil create \
        -volname "$VOLNAME" \
        -srcfolder "$stage" \
        -ov -format UDZO \
        "$DMG_OUT" >/dev/null
    local rc=$?
    rm -rf "$stage"
    return $rc
}

DMG_BUILT=0

if [ "$HAVE_CREATE_DMG" -eq 1 ]; then
    # create-dmg from Homebrew (which Tauri's bundle_dmg.sh forks from) —
    # actively maintained, but its AppleScript-driven "Finder pretty"
    # step is fragile on macOS 14+ (osascript -1712 timeouts). When it
    # fails we fall through to the hdiutil path, which always works.
    if [ "$DRY_RUN" -eq 1 ]; then
        printf "  (dry-run) create-dmg --volname '%s' ... %s %s\n" "$VOLNAME" "$DMG_OUT" "$APP_PATH"
        DMG_BUILT=1
    else
        CDMG_RC=0
        create-dmg \
            --volname "$VOLNAME" \
            --window-pos 200 120 \
            --window-size 600 380 \
            --icon-size 96 \
            --icon "Ascendo.app" 150 180 \
            --app-drop-link 450 180 \
            --hide-extension "Ascendo.app" \
            --no-internet-enable \
            "$DMG_OUT" \
            "$APP_PATH" || CDMG_RC=$?
        # Be paranoid: also probe the artefact. create-dmg has been
        # observed to exit 0 while leaving an unverifiable DMG behind
        # when the Finder AppleScript step times out.
        if [ "$CDMG_RC" -eq 0 ] && [ -f "$DMG_OUT" ] && hdiutil verify "$DMG_OUT" >/dev/null 2>&1; then
            ok "DMG built (create-dmg)"
            DMG_BUILT=1
        else
            warn "create-dmg failed or produced an unverifiable DMG (rc=$CDMG_RC). Falling back to hdiutil."
        fi
    fi
fi

if [ "$DMG_BUILT" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    # Plain hdiutil fallback — minimal Finder layout (no custom icons,
    # no /Applications alias positioning) but always works. The DMG
    # contains Ascendo.app and a Symlink to /Applications; user drags
    # the app onto the symlink to install. Same UX as create-dmg's
    # output, just without the bespoke icon arrangement.
    if _build_dmg_hdiutil; then
        ok "DMG built (hdiutil fallback)"
        DMG_BUILT=1
    else
        fail "Both create-dmg and hdiutil failed to produce $DMG_OUT"
    fi
fi

# Sweep create-dmg's leftover read-write image if it bailed mid-stream.
if [ "$DRY_RUN" -eq 0 ]; then
    find "$DIST_DIR" -maxdepth 1 -name 'rw.*.dmg' -delete 2>/dev/null || true
fi

# ── 7. Verify DMG ────────────────────────────────────────────────────────
if [ "$DRY_RUN" -eq 0 ]; then
    step "Verify DMG"
    if hdiutil verify "$DMG_OUT" >/dev/null 2>&1; then
        ok "hdiutil verify passed"
    else
        fail "hdiutil verify FAILED for $DMG_OUT"
    fi
fi

# ── 8. Optional: codesign the DMG itself ─────────────────────────────────
if [ "$NO_SIGN" -eq 0 ] && [ -n "${APPLE_CERT_NAME:-}" ] && [ "$DRY_RUN" -eq 0 ]; then
    step "Codesign DMG"
    run codesign --sign "$APPLE_CERT_NAME" --timestamp "$DMG_OUT"
    ok "DMG signed"
fi

# ── 9. Optional: notarize ────────────────────────────────────────────────
if [ "$NO_NOTARIZE" -eq 0 ] && [ -n "${APPLE_NOTARY_USER:-}" ] && \
   [ -n "${APPLE_NOTARY_PASSWORD:-}" ] && [ -n "${APPLE_NOTARY_TEAM:-}" ] && \
   [ "$DRY_RUN" -eq 0 ]; then
    step "Submit DMG for notarization (this can take 5-30 min)"
    if xcrun notarytool submit "$DMG_OUT" \
        --apple-id "$APPLE_NOTARY_USER" \
        --password "$APPLE_NOTARY_PASSWORD" \
        --team-id "$APPLE_NOTARY_TEAM" \
        --wait; then
        ok "notarization succeeded"
        run xcrun stapler staple "$DMG_OUT"
        ok "stapled notarization ticket to DMG"
    else
        warn "notarization failed — DMG is still usable but Gatekeeper will quarantine it"
    fi
else
    if [ -z "${APPLE_NOTARY_USER:-}" ]; then
        warn "notarization skipped (no \$APPLE_NOTARY_USER). End users will see a Gatekeeper warning."
    fi
fi

# ── 10. SHA256 + summary ─────────────────────────────────────────────────
if [ "$DRY_RUN" -eq 0 ]; then
    step "Summary"
    SIZE_MB="$(du -m "$DMG_OUT" | awk '{print $1}')"
    SHA="$(shasum -a 256 "$DMG_OUT" | awk '{print $1}')"
    printf "\n========================================\n"
    printf "  Ascendo DMG (v%s, %s)\n"  "$VERSION" "$ARCH_LABEL"
    printf "========================================\n\n"
    printf "  %s\n"             "$DMG_OUT"
    printf "    size:   %s MB\n" "$SIZE_MB"
    printf "    sha256: %s\n\n"  "$SHA"
    printf "Next steps:\n"
    printf "  - test:   open '%s'\n" "$DMG_OUT"
    printf "  - notarize (if not already): set APPLE_NOTARY_{USER,PASSWORD,TEAM} and re-run.\n"
    printf "  - distribute: upload to GitHub Releases, brew tap, etc.\n\n"
fi
