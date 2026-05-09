#!/usr/bin/env bash
# =============================================================================
# bin/first-run-bootstrap-linux.sh — smart bootstrap for the Ascendo .deb
# =============================================================================
#
# When a user installs ascendo_*.deb and runs `ascendo` for the first
# time, we want to ensure their per-user editable Python install exists,
# their venv is healthy, and `ascendo doctor` reports green. The .deb
# postinst already handled system-wide bits (file perms, /etc/ascendo,
# desktop database refresh); this script handles the per-user bring-up
# that postinst can't do (because postinst runs as root and venvs are
# per-user).
#
# Idempotent: a marker file at ~/.ascendo/.bootstrapped is checked first.
# Re-run with `--force` to re-bootstrap (e.g. after switching editions).
#
# Triggered by:
#   - The ascendo CLI shim (when invoked without args, before the first
#     update-all is dispatched) — see bin/user-scripts/ascendo_doctor for
#     the integration point.
#   - The .desktop file's Exec= line (so launching from the GNOME/KDE menu
#     also triggers bootstrap on first run).
# =============================================================================

set -u

ASCENDO_HOME_DEFAULT="${ASCENDO_HOME:-$HOME/.local/share/ascendo}"
SUPPORT_DIR="$HOME/.ascendo"
MARKER="$SUPPORT_DIR/.bootstrapped"
LOG="$SUPPORT_DIR/first-run.log"
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1 ;;
        --help|-h)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
    esac
    shift
done

mkdir -p "$SUPPORT_DIR" 2>/dev/null

step() { printf "\n==> %s\n"   "$1" | tee -a "$LOG"; }
ok()   { printf "  [OK] %s\n"  "$1" | tee -a "$LOG"; }
warn() { printf "  [WARN] %s\n" "$1" | tee -a "$LOG" >&2; }
info() { printf "  %s\n"        "$1" | tee -a "$LOG"; }
die()  { printf "\n[FAIL] %s\n" "$1" | tee -a "$LOG" >&2; exit 1; }

if [ -f "$MARKER" ] && [ "$FORCE" -ne 1 ]; then
    # Already bootstrapped — silent exit so menu launches don't slow down.
    exit 0
fi

: > "$LOG"
step "Ascendo first-run bootstrap (Linux)"
info "Log: $LOG"
info "Marker: $MARKER"

# ── 1. Read pre-seed from /etc/ascendo/preseed.conf (planted by postinst) ─
EDITION="${ASCENDO_EDITION:-basic}"
PROFILE="${ASCENDO_PROFILE:-full}"
LANG_PICK="${ASCENDO_LANG:-en}"
if [ -r /etc/ascendo/preseed.conf ]; then
    # shellcheck disable=SC1091
    set -a; . /etc/ascendo/preseed.conf; set +a
    EDITION="${ASCENDO_EDITION:-$EDITION}"
    PROFILE="${ASCENDO_PROFILE:-$PROFILE}"
    LANG_PICK="${ASCENDO_LANG:-$LANG_PICK}"
fi

info "Edition:  $EDITION"
info "Profile:  $PROFILE"
info "Language: $LANG_PICK"

# ── 2. Verify Python + git + curl ────────────────────────────────────────
step "Verify dependencies"

for cmd in python3 git curl jq; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        die "$cmd not on PATH — run 'sudo apt install $cmd' (.deb should have pulled it in; please report)"
    fi
    ok "$cmd: $(command -v "$cmd")"
done

# Python ≥ 3.11 (the .deb depends on python3 ≥ 3.11 but be explicit).
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_MAJ="${PY_VER%.*}"
PY_MIN="${PY_VER#*.}"
if [ "$PY_MAJ" != "3" ] || [ "$PY_MIN" -lt 11 ] 2>/dev/null; then
    die "Python 3.11+ required, found $PY_VER. Install python3.12: sudo apt install python3.12"
fi
ok "python: $PY_VER"

# ── 3. Run install.sh in --update mode against /opt/ascendo as the source ─
step "Set up per-user editable install"

ASCENDO_SRC="${ASCENDO_SRC:-/opt/ascendo}"
if [ ! -d "$ASCENDO_SRC" ]; then
    die "/opt/ascendo not found — is the .deb installed?"
fi

# install.sh is also shipped under /opt/ascendo/install.sh. Run it with
# --reinstall the first time so we get a clean venv.
INSTALLER="$ASCENDO_SRC/install.sh"
if [ ! -x "$INSTALLER" ]; then
    die "Installer missing or not executable: $INSTALLER"
fi

INSTALL_ARGS=(--non-interactive --edition="$EDITION" --profile="$PROFILE")
[ "$FORCE" -eq 1 ] && INSTALL_ARGS+=(--reinstall)

env \
    ASCENDO_LANG="$LANG_PICK" \
    ASCENDO_HOME="$ASCENDO_HOME_DEFAULT" \
    ASCENDO_NONINTERACTIVE=1 \
    bash "$INSTALLER" "${INSTALL_ARGS[@]}" 2>&1 | tee -a "$LOG"
RC="${PIPESTATUS[0]}"
if [ "$RC" -ne 0 ]; then
    die "install.sh exited with $RC — see $LOG"
fi
ok "install.sh completed"

# ── 4. Verify with ascendo doctor ────────────────────────────────────────
step "Verify install (ascendo doctor)"

ASCENDO_BIN="$HOME/.local/bin/ascendo"
[ -x "$ASCENDO_BIN" ] || ASCENDO_BIN="$(command -v ascendo 2>/dev/null || true)"
if [ -z "$ASCENDO_BIN" ] || [ ! -x "$ASCENDO_BIN" ]; then
    die "ascendo binary not found after install — see $LOG"
fi

if "$ASCENDO_BIN" doctor 2>&1 | tee -a "$LOG"; then
    ok "ascendo doctor passed"
else
    die "ascendo doctor reported issues — see $LOG"
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

step "Bootstrap complete — Ascendo ready"
exit 0
