#!/usr/bin/env bash
# =============================================================================
# packaging/build-deb.sh
#
# Stage the repo into packaging/deb/opt/ascendo/ and produce a Debian
# package at dist/ascendo_<version>_all.deb.
#
# Smart features:
#   * Reads version from core/ascendo/__version__.py (single source of truth).
#     Updates DEBIAN/control's `Version:` field on the fly so a version bump
#     in __version__.py is enough — no double-edit required.
#   * Ships every helper script from bin/user-scripts/ as a /usr/local/bin
#     symlink so end users get `ascendo`, `ascendo_doctor`, `ascendo_update`,
#     `ascendo_start_web`, etc. on PATH automatically.
#   * Honors --dry-run (just stage + lint, don't dpkg-deb --build).
#   * Honors --no-symlinks (don't generate the /usr/local/bin shims).
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="$REPO_ROOT/packaging/deb"
STAGE="$PKG_DIR/opt/ascendo"
DIST="$REPO_ROOT/dist"

DRY_RUN=0
WITH_SYMLINKS=1
EDITION="${ASCENDO_EDITION:-basic}"   # basic | dev

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)      DRY_RUN=1 ;;
        --no-symlinks)  WITH_SYMLINKS=0 ;;
        --edition=*)    EDITION="${1#*=}" ;;
        --edition)      shift; EDITION="$1" ;;
        --basic)        EDITION=basic ;;
        --dev)          EDITION=dev ;;
        --help|-h)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            printf "\nEdition flags:\n  --edition=basic|dev    Bake edition marker into the .deb\n"
            printf "                         (basic ships simplified UI; dev ships full feature set)\n"
            exit 0
            ;;
        *)
            printf "build-deb.sh: ignoring unknown arg: %s\n" "$1" >&2
            ;;
    esac
    shift
done

case "$EDITION" in
    basic|dev) ;;
    *) printf "build-deb.sh: --edition must be 'basic' or 'dev' (got: %s)\n" "$EDITION" >&2; exit 2 ;;
esac

step() { printf "\n==> %s\n" "$1"; }
ok()   { printf "  [OK] %s\n"  "$1"; }
warn() { printf "  [WARN] %s\n" "$1" >&2; }
fail() { printf "  [FAIL] %s\n" "$1" >&2; exit 1; }

# ── 1. Resolve version ───────────────────────────────────────────────────
step "Resolve version + package name"

VERSION_FILE="$REPO_ROOT/core/ascendo/__version__.py"
[ -f "$VERSION_FILE" ] || fail "version file missing: $VERSION_FILE"
VERSION="$(awk -F'"' '/__version__/ {print $2}' "$VERSION_FILE")"
[ -n "$VERSION" ] || fail "could not parse __version__ from $VERSION_FILE"
ok "version: $VERSION"

# Sync DEBIAN/control's Version field to match.
CTRL="$PKG_DIR/DEBIAN/control"
[ -f "$CTRL" ] || fail "control file missing: $CTRL"
CURRENT="$(awk -F'[ :]+' '/^Version:/{print $2; exit}' "$CTRL")"
if [ "$CURRENT" != "$VERSION" ]; then
    info_msg="bumping DEBIAN/control: Version: $CURRENT → $VERSION"
    printf "  %s\n" "$info_msg"
    if [ "$DRY_RUN" -eq 0 ]; then
        # BSD sed (macOS) needs -i ''; GNU sed (Linux) needs -i without arg.
        # Use a portable approach via temp file.
        sed -e "s/^Version:.*/Version: $VERSION/" "$CTRL" > "$CTRL.tmp"
        mv "$CTRL.tmp" "$CTRL"
    fi
fi

PKG_NAME="$(awk -F'[ :]+' '/^Package:/{print $2; exit}' "$CTRL")"
[ -z "$PKG_NAME" ] && fail "cannot read Package: from $CTRL"
ok "package: $PKG_NAME"

# ── 2. Clean stage ───────────────────────────────────────────────────────
step "Clean stage"
if [ "$DRY_RUN" -eq 0 ]; then
    rm -rf "$STAGE"
    mkdir -p "$STAGE" "$DIST"
    # Drop legacy stage directory if present (left over from older builds).
    rm -rf "$PKG_DIR/opt/ascendo" 2>/dev/null || true
fi
ok "stage: $STAGE"

# ── 3. Copy tracked files into stage ─────────────────────────────────────
step "Copy tracked files (git ls-files)"
# Use a temp file so the COUNT variable survives the while-loop subshell.
FILELIST="$(mktemp -t ascendo-deb-XXXXXX)"
trap 'rm -f "$FILELIST"' EXIT
git -C "$REPO_ROOT" ls-files > "$FILELIST"
COUNT=0
while IFS= read -r f; do
    case "$f" in
        packaging/*) continue ;;       # don't ship packaging tree itself
        dist/*)      continue ;;
        .github/*|.gitignore|*.md.example) continue ;;
        ui/desktop-tauri/node_modules/*|ui/desktop-tauri/src-tauri/target/*) continue ;;
    esac
    if [ "$DRY_RUN" -eq 0 ]; then
        mkdir -p "$STAGE/$(dirname "$f")"
        cp -a "$REPO_ROOT/$f" "$STAGE/$f"
    fi
    COUNT=$((COUNT + 1))
done < "$FILELIST"
ok "$COUNT files staged"

# ── 4. Mark all .sh executable ──────────────────────────────────────────
if [ "$DRY_RUN" -eq 0 ]; then
    find "$STAGE" -name '*.sh' -exec chmod +x {} +
    ok "+x on all .sh"
fi

# ── 5. Generate /usr/local/bin shims for helper scripts ─────────────────
# We create them as POSIX dispatcher scripts (not symlinks) so they work
# even if /opt/ascendo is later moved or replaced. The shims forward to
# the real script via $ASCENDO_HOME (default /opt/ascendo).
SHIMS_DIR="$PKG_DIR/usr/local/bin"
if [ "$WITH_SYMLINKS" -eq 1 ]; then
    step "Generate user-script shims under usr/local/bin"
    if [ "$DRY_RUN" -eq 0 ]; then
        mkdir -p "$SHIMS_DIR"
        # Drop any pre-existing shims so a removed bin/user-scripts/foo
        # doesn't keep being shipped.
        rm -f "$SHIMS_DIR"/ascendo* 2>/dev/null || true
        rm -f "$SHIMS_DIR"/dev 2>/dev/null || true
        for src in "$REPO_ROOT/bin/user-scripts"/*; do
            [ -f "$src" ] || continue
            base="$(basename "$src")"
            case "$base" in
                *.ps1|README.md) continue ;;   # PowerShell mirrors irrelevant on Linux
            esac
            shim="$SHIMS_DIR/$base"
            cat > "$shim" <<EOF
#!/usr/bin/env bash
# Auto-generated by packaging/build-deb.sh — do not edit.
ASCENDO_HOME="\${ASCENDO_HOME:-/opt/ascendo}"
exec "\$ASCENDO_HOME/bin/user-scripts/$base" "\$@"
EOF
            chmod 0755 "$shim"
        done
        ok "$(ls "$SHIMS_DIR" 2>/dev/null | wc -l | tr -d ' ') shims under $SHIMS_DIR"
    fi
fi

# ── 6a. Bake edition marker into the stage ──────────────────────────────
# /opt/ascendo/.ascendo-edition is read by core/ascendo/dashboard/app.py +
# CLI at boot (priority: ASCENDO_EDITION env > marker > default basic).
if [ "$DRY_RUN" -eq 0 ]; then
    step "Bake edition marker (.ascendo-edition = $EDITION)"
    echo "$EDITION" > "$STAGE/.ascendo-edition"
    ok "wrote $STAGE/.ascendo-edition"
fi

# ── 6b. Set DEBIAN/* perms ────────────────────────────────────────────────
if [ "$DRY_RUN" -eq 0 ]; then
    chmod 0755 "$PKG_DIR/DEBIAN/postinst" \
               "$PKG_DIR/DEBIAN/prerm" \
               "$PKG_DIR/DEBIAN/postrm" 2>/dev/null || true
fi

# ── 7. Build .deb ────────────────────────────────────────────────────────
# Edition appears in filename so basic + dev artefacts coexist in dist/.
OUT="$DIST/${PKG_NAME}-${EDITION}_${VERSION}_all.deb"
if [ "$DRY_RUN" -eq 1 ]; then
    step "Dry-run: skipping dpkg-deb --build"
    printf "  Would write: %s\n" "$OUT"
    exit 0
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
    warn "dpkg-deb not on PATH — cannot build .deb on this host."
    warn "Stage prepared at $STAGE; copy to a Debian/Ubuntu host and run:"
    warn "  dpkg-deb --build --root-owner-group $PKG_DIR $OUT"
    exit 0
fi

step "Build $OUT"
( cd "$PKG_DIR/.." && dpkg-deb --build --root-owner-group deb "$OUT" )

# Clean up older same-edition + legacy artefacts. Other edition's .deb
# stays so basic + dev both live in dist/ after two consecutive builds.
find "$DIST" -maxdepth 1 -name "${PKG_NAME}-${EDITION}_*_all.deb" \
    ! -name "$(basename "$OUT")" -delete 2>/dev/null || true
find "$DIST" -maxdepth 1 -name 'ascendo_*_all.deb' -delete 2>/dev/null || true
find "$DIST" -maxdepth 1 -name 'ascendo_*_all.deb' -delete 2>/dev/null || true

# SHA256 + summary
SIZE_KB="$(du -k "$OUT" | awk '{print $1}')"
if command -v sha256sum >/dev/null 2>&1; then
    SHA="$(sha256sum "$OUT" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
    SHA="$(shasum -a 256 "$OUT" | awk '{print $1}')"
else
    SHA="(no sha256 tool available)"
fi

printf "\n========================================\n"
printf "  Ascendo .deb (v%s)\n" "$VERSION"
printf "========================================\n\n"
printf "  %s\n"             "$OUT"
printf "    size:   %s KB\n" "$SIZE_KB"
printf "    sha256: %s\n\n"  "$SHA"
printf "Install with:\n"
printf "  sudo dpkg -i %s\n" "$OUT"
printf "  sudo apt-get install -f   # if dependency resolution needed\n\n"
