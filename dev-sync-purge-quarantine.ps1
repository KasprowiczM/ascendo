#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Delete previously-reviewed quarantined files from the remote.
.DESCRIPTION
  Windows PowerShell mirror of dev-sync-purge-quarantine.sh. Pair with
  dev-sync-prune-excluded.ps1 — plan first, review the JSON, then purge.
  Requires --apply to actually delete (Python backend enforces this).
#>
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Args)
$ErrorActionPreference = 'Stop'

# Refresh PATH from the registry so freshly-installed Python (e.g. via
# ``winget install Python.Python.3.13`` in this same shell) is found
# without restarting PowerShell.
try {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ($machine, $user | Where-Object { $_ }) -join ';'
} catch { }

$pyBackend = Join-Path $PSScriptRoot 'dev-sync\dev_sync_purge_quarantine.py'
if (-not (Test-Path $pyBackend)) { Write-Error "dev-sync backend missing: $pyBackend"; exit 1 }
$python = $null
foreach ($c in @('py','python','python3')) { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $python = $g.Source; break } }
if (-not $python) { Write-Error 'No Python found. winget install Python.Python.3.13 (then restart this shell, or call this script again so it re-reads PATH).'; exit 2 }
& $python $pyBackend @Args
exit $LASTEXITCODE
