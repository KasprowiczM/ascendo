#!/usr/bin/env bash
# =============================================================================
# plugins/_template/linux/check.sh — example check phase
#
# Usage (called by core orchestrator):
#   ./check.sh --run-id <id> --json-out <path> --log <path> --profile <name>
#              --config <dir> [--dry-run]
#
# Read-only phase: snapshot current state, list outdated, but DO NOT mutate.
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
# In real plugins, this comes from the Ubuntu adapter's lib:
#   source "${ASCENDO_LIB_BASH:-/opt/ascendo/lib}/json_emit.sh"
# For template testing, skip emit and just exit 0.

# ── Plugin logic goes here ───────────────────────────────────────────────────
# Example: check what's outdated
echo "[check] template plugin — nothing to check (replace this stub)"

# ── Exit code ────────────────────────────────────────────────────────────────
# 0 = success (nothing to do, or readiness confirmed)
exit 0
