#!/usr/bin/env bash
# Install the user-level dashboard service. Runs without root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${HOME}/.config/systemd/user"

# 1. Bootstrap venv if missing (PEP-668 safe)
if [[ ! -x "${SCRIPT_DIR}/app/.venv/bin/python" ]]; then
    echo "── bootstrapping venv (PEP-668 safe)"
    bash "${SCRIPT_DIR}/app/install.sh"
fi

# 2. Install user unit
mkdir -p "$DEST"
install -m 0644 "${SCRIPT_DIR}/systemd/user/ascendo-dashboard.service" \
    "${DEST}/ascendo-dashboard.service"
systemctl --user daemon-reload
systemctl --user enable --now ascendo-dashboard.service
sleep 1
systemctl --user status ascendo-dashboard.service --no-pager || true

# 3. Install Ascendo icon + desktop entries (user-level, no root)
#    Two .desktop entries:
#      ascendo.desktop          → "Ascendo - Unified Updates"           opens default browser
#      ascendo-desktop.desktop  → "Ascendo - Unified Updates (Desktop)"  standalone window
#    Both call the ascendo-launch shim (installed below).
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
APPS_DIR="${HOME}/.local/share/applications"
BIN_DIR="${HOME}/.local/bin"
mkdir -p "$ICON_DIR" "$APPS_DIR" "$BIN_DIR"
install -m 0644 "${SCRIPT_DIR}/share/icons/hicolor/scalable/apps/ascendo.svg" \
    "${ICON_DIR}/ascendo.svg"
install -m 0644 "${SCRIPT_DIR}/share/applications/ascendo.desktop" \
    "${APPS_DIR}/ascendo.desktop"
install -m 0644 "${SCRIPT_DIR}/share/applications/ascendo-desktop.desktop" \
    "${APPS_DIR}/ascendo-desktop.desktop"
install -m 0755 "${SCRIPT_DIR}/share/bin/ascendo-launch" \
    "${BIN_DIR}/ascendo-launch"
# Drop the old (pre-rebrand) desktop file if it lingers
rm -f "${APPS_DIR}/ascendo.desktop"
# Warn the user if ~/.local/bin isn't on $PATH (the .desktop entries call
# `ascendo-launch` by name; if $PATH is missing it, GNOME spawns nothing).
case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) echo "ℹ ${BIN_DIR} is not on \$PATH — add it (e.g. in ~/.profile) so the launcher resolves." ;;
esac
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "${APPS_DIR}" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 \
    && gtk-update-icon-cache -t "${HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || true

# 4. Verify port
if ss -lntp 2>/dev/null | grep -q ":8765"; then
    echo
    echo "✔ Dashboard listening at http://127.0.0.1:8765"
    echo "✔ Ascendo icons installed in app menu:"
    echo "    • Ascendo - Unified Updates           — opens dashboard in your default browser"
    echo "    • Ascendo - Unified Updates (Desktop) — opens dashboard in a standalone window"
else
    echo
    echo "⚠ Dashboard not listening on :8765 — check journalctl --user -u ascendo-dashboard.service"
fi
