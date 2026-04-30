# =============================================================================
# plugins\_template\windows\check.ps1 — example check phase (Windows)
#
# Usage (called by core orchestrator):
#   .\check.ps1 -RunId <id> -JsonOut <path> -LogPath <path> -Profile <name>
#               -ConfigDir <dir> [-DryRun]
#
# Read-only phase: snapshot current state, list outdated, but DO NOT mutate.
# =============================================================================

param(
    [Parameter(Mandatory=$true)] [string]$RunId,
    [Parameter(Mandatory=$true)] [string]$JsonOut,
    [Parameter(Mandatory=$true)] [string]$LogPath,
    [string]$Profile = "safe",
    [string]$ConfigDir = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# ── UTF-8 console (prevents ellipsis 3-byte mis-decode) ──────────────────────
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ── Source JSON sidecar emitter ──────────────────────────────────────────────
# In real plugins, this comes from the Windows adapter's lib:
#   . "$env:ASCENDO_LIB_PS\Json-Emit.ps1"
# For template testing, skip emit and just exit 0.

# ── Plugin logic goes here ───────────────────────────────────────────────────
# Example: check what's outdated
Write-Host "[check] template plugin — nothing to check (replace this stub)"

# ── Exit code ────────────────────────────────────────────────────────────────
# 0 = success (nothing to do, or readiness confirmed)
exit 0
