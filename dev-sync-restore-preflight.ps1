#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Check fresh-clone restore readiness BEFORE running dev-sync-import.
.DESCRIPTION
  Windows PowerShell mirror of dev-sync-restore-preflight.sh. Verifies
  that the rclone remote is reachable, the .dev_sync_config.json points
  at the right project, and the local repo state is clean enough to
  receive the overlay safely.
#>
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Args)
$ErrorActionPreference = 'Stop'
$pyBackend = Join-Path $PSScriptRoot 'dev-sync\dev_sync_restore_preflight.py'
if (-not (Test-Path $pyBackend)) { Write-Error "dev-sync backend missing: $pyBackend"; exit 1 }
$python = $null
foreach ($c in @('py','python','python3')) { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $python = $g.Source; break } }
if (-not $python) { Write-Error 'No Python found. winget install Python.Python.3.13'; exit 2 }
& $python $pyBackend @Args
exit $LASTEXITCODE
