#!/usr/bin/env bash
# =============================================================================
# systemd/install-timer.sh — Install systemd timer for weekly auto-updates
#
# Creates a systemd service + timer that runs update-all.sh every Sunday at 3am.
# The explicit driver/firmware module is skipped; apt may still update kernels,
# microcode, Mesa, and other normal OS packages.
#
# Usage:
#   ./systemd/install-timer.sh           # Install for current user
#   ./systemd/install-timer.sh --remove  # Remove the timer
#   ./systemd/install-timer.sh --status  # Show timer status
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_USER="${SUDO_USER:-$USER}"
SERVICE_NAME="ascendo@${CURRENT_USER}"
TIMER_NAME="ascendo@${CURRENT_USER}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

step()  { echo -ne "  ${BOLD}▶${RESET}  $* ... "; }
ok()    { echo -e "${GREEN}✔${RESET}"; }
warn()  { echo -e "${YELLOW}⚠  $*${RESET}"; }
info()  { echo -e "     $*"; }

MODE="install"
[[ "${1:-}" == "--remove" ]] && MODE="remove"
[[ "${1:-}" == "--status" ]] && MODE="status"

echo -e "\n${BOLD}${BLUE}── Ascendo systemd timer ──${RESET}\n"

if [[ "$MODE" == "status" ]]; then
    echo "── Timer status:"
    systemctl status "${TIMER_NAME}.timer" 2>/dev/null || echo "  (not installed)"
    echo ""
    echo "── Service status:"
    systemctl status "${SERVICE_NAME}.service" 2>/dev/null || echo "  (not installed)"
    echo ""
    echo "── Next runs:"
    systemctl list-timers "ascendo*" 2>/dev/null || echo "  (no timers)"
    exit 0
fi

if [[ "$MODE" == "remove" ]]; then
    step "Stop and disable timer"
    sudo systemctl stop "${TIMER_NAME}.timer" 2>/dev/null || true
    sudo systemctl disable "${TIMER_NAME}.timer" 2>/dev/null || true
    sudo rm -f /etc/systemd/system/ascendo@.service
    sudo rm -f /etc/systemd/system/ascendo@.timer
    sudo rm -f /etc/systemd/system/ascendo.timer
    sudo systemctl daemon-reload
    ok
    echo ""
    info "Timer removed."
    exit 0
fi

# ── Install mode ──────────────────────────────────────────────────────────────

# Generate service file with correct paths
step "Install service file"
sudo tee /etc/systemd/system/ascendo@.service > /dev/null << EOF
[Unit]
Description=Ascendo — Full System Update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=%i
WorkingDirectory=${SCRIPT_DIR}
Environment=HOME=/home/%i
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u "${CURRENT_USER}")
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u "${CURRENT_USER}")/bus
ExecStart=/usr/bin/env bash ${SCRIPT_DIR}/update-all.sh --no-drivers
TimeoutStartSec=3600
StandardOutput=append:${SCRIPT_DIR}/logs/systemd_update.log
StandardError=append:${SCRIPT_DIR}/logs/systemd_update.log

[Install]
WantedBy=multi-user.target
EOF
ok

# Install timer file
step "Install timer file"
sudo tee /etc/systemd/system/ascendo@.timer > /dev/null << 'EOF'
[Unit]
Description=Ascendo — Weekly update timer

[Timer]
OnCalendar=Sun *-*-* 03:00:00
Persistent=true
RandomizedDelaySec=30min
Unit=ascendo@%i.service

[Install]
WantedBy=timers.target
EOF
ok

step "Reload systemd daemon"
sudo systemctl daemon-reload && ok

step "Enable and start timer"
sudo systemctl enable --now "${TIMER_NAME}.timer" && ok

echo ""
echo -e "${GREEN}✔  Timer installed and active${RESET}"
echo ""
info "Schedule: Every Sunday at 03:00 (±30min)"
info "Skips   : explicit driver/firmware module (APT may still update OS packages)"
info "Log     : ${SCRIPT_DIR}/logs/systemd_update.log"
echo ""
echo "── Next scheduled run:"
systemctl list-timers "ascendo*" 2>/dev/null | grep -E "(NEXT|ubuntu)" || true
echo ""
echo "── Commands:"
info "Status  :  systemctl status ${TIMER_NAME}.timer"
info "Run now :  sudo systemctl start ${SERVICE_NAME}.service"
info "Logs    :  journalctl -u ${SERVICE_NAME}.service"
info "Remove  :  ./systemd/install-timer.sh --remove"
