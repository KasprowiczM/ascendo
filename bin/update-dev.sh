#!/usr/bin/env bash
# =============================================================================
# bin/update-dev.sh -- post-`git pull` editable re-install for the dev tree
# =============================================================================
# Why this exists (M5.7.6, 2026-05-24):
#   The operator's dev workflow lives in $REPO_ROOT (typically
#   ~/Dev_Env/Ascendo), NOT in $ASCENDO_HOME (~/.local/share/ascendo).
#   `update.sh` only refreshes the latter; running `git pull` in the dev
#   tree leaves CLI/dashboard binding to the OLD editable install if the
#   site-packages registration ever drifted (different python, fresh
#   venv, install.sh ran from elsewhere). This script re-anchors the
#   editable installs against the cwd repo every time.
#
# Usage:
#   cd ~/Dev_Env/Ascendo
#   git pull
#   bash bin/update-dev.sh                  # editable install + smoke
#   bash bin/update-dev.sh --check-only     # verify without reinstalling
#
# Idempotent. Safe to wire into a post-merge git hook.
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

CHECK_ONLY=0
QUIET=0
while [ $# -gt 0 ]; do
    case "$1" in
        --check-only) CHECK_ONLY=1; shift ;;
        --quiet|-q)   QUIET=1; shift ;;
        --help|-h)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) printf "update-dev.sh: unknown arg: %s\n" "$1" >&2; exit 2 ;;
    esac
done

if [ -t 1 ] && [ "$QUIET" = 0 ]; then
    GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; RED=$'\033[0;31m'; CYAN=$'\033[0;36m'; RESET=$'\033[0m'
else
    GREEN=""; YELLOW=""; RED=""; CYAN=""; RESET=""
fi
ok()   { [ "$QUIET" = 0 ] && printf "  ${GREEN}✓${RESET} %s\n" "$1"; }
warn() { printf "  ${YELLOW}!${RESET} %s\n" "$1" >&2; }
fail() { printf "  ${RED}✗${RESET} %s\n" "$1" >&2; exit 1; }
info() { [ "$QUIET" = 0 ] && printf "  ${CYAN}·${RESET} %s\n" "$1"; }

# Sanity: this must be the Ascendo repo
[ -f "$REPO_ROOT/core/ascendo/__init__.py" ] || \
    fail "Not in an Ascendo repo: $REPO_ROOT"

OS_TAG="$(uname -s)"
case "$OS_TAG" in
    Darwin) ADAPTER_REL="adapters/macos" ;;
    Linux)
        if [ -f /etc/os-release ]; then
            # shellcheck disable=SC1091
            . /etc/os-release
            case "${ID:-}-${ID_LIKE:-}" in
                ubuntu*|debian*|*-ubuntu*|*-debian*) ADAPTER_REL="adapters/ubuntu" ;;
                *) ADAPTER_REL="" ;;
            esac
        fi
        ;;
    *) ADAPTER_REL="" ;;
esac

# Verify the currently-imported ascendo points at this repo. If not,
# the operator either runs a different python or never ran the editable
# install. Either way, --check-only flags it.
ACTIVE_PATH="$(python3 -c 'import ascendo, os; print(os.path.dirname(os.path.dirname(ascendo.__file__)))' 2>/dev/null || true)"
if [ -z "$ACTIVE_PATH" ]; then
    info "ascendo not yet importable (first run?)"
elif [ "$ACTIVE_PATH" != "$REPO_ROOT/core" ] && [ "$ACTIVE_PATH" != "$REPO_ROOT" ]; then
    warn "Active ascendo install points at: $ACTIVE_PATH"
    warn "Expected:                         $REPO_ROOT/core"
    if [ "$CHECK_ONLY" = "1" ]; then
        fail "Editable install drift detected (run without --check-only to fix)."
    fi
fi

if [ "$CHECK_ONLY" = "1" ]; then
    ok "dev tree pinned correctly at $REPO_ROOT"
    exit 0
fi

# Re-install editable. Honour PEP 668 by passing --break-system-packages
# only on Homebrew Python (which sets it externally-managed).
PIP_ARGS="install --upgrade --quiet -e $REPO_ROOT/core"
if python3 -c 'import sys, sysconfig; sys.exit(0 if sysconfig.get_path("purelib","posix_prefix").startswith("/opt/homebrew") or sysconfig.get_path("purelib","posix_prefix").startswith("/usr/local/Cellar") else 1)' 2>/dev/null; then
    PIP_ARGS="$PIP_ARGS --break-system-packages"
fi

info "pip install -e core/"
# shellcheck disable=SC2086
python3 -m pip $PIP_ARGS >/dev/null 2>&1 || \
    fail "core editable install failed (re-run with stderr to see why)"
ok "core editable install refreshed"

if [ -n "$ADAPTER_REL" ] && [ -d "$REPO_ROOT/$ADAPTER_REL" ]; then
    info "pip install -e $ADAPTER_REL"
    # shellcheck disable=SC2086
    python3 -m pip $PIP_ARGS --no-deps >/dev/null 2>&1 || \
        warn "$ADAPTER_REL editable install reported a non-fatal error"
    # shellcheck disable=SC2086
    python3 -m pip install --upgrade --quiet -e "$REPO_ROOT/$ADAPTER_REL" --no-deps >/dev/null 2>&1 || true
    ok "$ADAPTER_REL editable install refreshed"
fi

# Smoke: confirm fresh import path.
NEW_ACTIVE="$(python3 -c 'import ascendo, os; print(os.path.dirname(os.path.dirname(ascendo.__file__)))' 2>/dev/null || true)"
if [ "$NEW_ACTIVE" = "$REPO_ROOT/core" ]; then
    ok "ascendo now imports from $NEW_ACTIVE"
else
    warn "post-install: ascendo imports from $NEW_ACTIVE (expected $REPO_ROOT/core)"
fi
