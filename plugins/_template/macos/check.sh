#!/usr/bin/env bash
# =============================================================================
# plugins/_template/macos/check.sh — example check phase (macOS)
#
# Bash 3.2 compatible (macOS system shell).
#
# Usage (called by core orchestrator):
#   ./check.sh --run-id <id> --json-out <path> --log <path> --profile <name>
#              --config <dir> [--dry-run]
# =============================================================================
set -euo pipefail

# ── Parse standard flags ─────────────────────────────────────────────────────
RUN_ID=""
JSON_OUT=""
LOG_PATH=""
PROFILE="safe"
CONFIG_DIR=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --json-out) JSON_OUT="$2"; shift 2 ;;
        --log) LOG_PATH="$2"; shift 2 ;;
        --profile) PROFILE="$2"; shift 2 ;;
        --config) CONFIG_DIR="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

# ── Source JSON sidecar emitter ──────────────────────────────────────────────
# In real plugins:
#   source "${ASCENDO_LIB_BASH:-/usr/local/ascendo/lib}/json_emit.sh"

# ── Plugin logic goes here ───────────────────────────────────────────────────
echo "[check] template plugin — nothing to check (replace this stub)"

exit 0
