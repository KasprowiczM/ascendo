#!/usr/bin/env bash
# =============================================================================
# scripts/scheduler/install.sh — Generate & install a systemd timer for the
# update-all.sh pipeline. Runs as the invoking user.
#
# Usage:
#   scripts/scheduler/install.sh --calendar "Sun *-*-* 03:00:00" \
#                                --profile safe [--no-drivers] [--dry-run]
#   scripts/scheduler/install.sh --remove
#   scripts/scheduler/install.sh --status
#
# Default:
#   OnCalendar=Sun *-*-* 03:00:00
#   profile=safe (no driver/firmware mutations)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CURRENT_USER="${SUDO_USER:-$USER}"
SERVICE_NAME="ascendo@${CURRENT_USER}"
TIMER_NAME="ascendo@${CURRENT_USER}"

CALENDAR="Sun *-*-* 03:00:00"
PROFILE="safe"
NO_DRIVERS=0
DRY_RUN=0
MODE="install"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --calendar) shift; CALENDAR="$1" ;;
        --profile)  shift; PROFILE="$1"  ;;
        --no-drivers) NO_DRIVERS=1 ;;
        --dry-run)    DRY_RUN=1 ;;
        --remove) MODE=remove ;;
        --status) MODE=status ;;
        -h|--help) sed -n '3,15p' "$0"; exit 0 ;;
        *) echo "unknown: $1"; exit 2 ;;
    esac
    shift
done

if [[ "$MODE" == "status" ]]; then
    systemctl status "${TIMER_NAME}.timer" 2>/dev/null || echo "(timer not installed)"
    systemctl list-timers "ascendo*" 2>/dev/null || true
    exit 0
fi

if [[ "$MODE" == "remove" ]]; then
    sudo systemctl stop "${TIMER_NAME}.timer" 2>/dev/null || true
    sudo systemctl disable "${TIMER_NAME}.timer" 2>/dev/null || true
    sudo rm -f /etc/systemd/system/ascendo@.service \
               /etc/systemd/system/ascendo@.timer
    sudo systemctl daemon-reload
    echo "timer removed"
    exit 0
fi

EXTRA_ARGS=("--profile" "${PROFILE}" "--no-notify" "--run-id" "%i-scheduled-%S")
[[ $NO_DRIVERS -eq 1 ]] && EXTRA_ARGS+=("--no-drivers")
[[ $DRY_RUN    -eq 1 ]] && EXTRA_ARGS+=("--dry-run")

echo "Installing timer:"
echo "  calendar : ${CALENDAR}"
echo "  profile  : ${PROFILE}"
echo "  user     : ${CURRENT_USER}"
echo "  args     : ${EXTRA_ARGS[*]}"

sudo tee /etc/systemd/system/ascendo@.service > /dev/null <<EOF
[Unit]
Description=Ascendo — Scheduled update (${PROFILE})
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
# Optional pre-flight gate (battery, maintenance window, busy apt). Failure
# (exit 75) causes systemd to skip this run; the timer fires again next tick.
ExecStartPre=-/usr/bin/env bash ${SCRIPT_DIR}/scripts/scheduler/should-run.sh
ExecStart=/usr/bin/env bash ${SCRIPT_DIR}/update-all.sh ${EXTRA_ARGS[*]}
TimeoutStartSec=3600
StandardOutput=append:${SCRIPT_DIR}/logs/systemd_update.log
StandardError=append:${SCRIPT_DIR}/logs/systemd_update.log

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/ascendo@.timer > /dev/null <<EOF
[Unit]
Description=Ascendo — Scheduled update timer

[Timer]
OnCalendar=${CALENDAR}
Persistent=true
RandomizedDelaySec=30min
Unit=ascendo@%i.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${TIMER_NAME}.timer"
echo "timer installed and enabled"
systemctl list-timers "ascendo*" || true
