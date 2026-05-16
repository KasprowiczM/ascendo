#!/usr/bin/env bash
# ============================================================
# Backward-compatibility wrapper — calls dev-sync/provider_setup.sh
# (root-level twin of dev-sync-provider-setup.ps1)
# ============================================================
set -eu
exec bash "$(cd "$(dirname "$0")" && pwd)/dev-sync/provider_setup.sh" "$@"
