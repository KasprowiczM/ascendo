#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Check Proton (or any rclone remote) coverage of the private overlay.
.DESCRIPTION
  Windows PowerShell mirror of dev-sync-proton-status.sh. Reports which
  overlay files are present on the remote, which are stale, and which are
  missing. Read-only.
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

$pyBackend = Join-Path $PSScriptRoot 'dev-sync\dev_sync_proton_status.py'
if (-not (Test-Path $pyBackend)) { Write-Error "dev-sync backend missing: $pyBackend"; exit 1 }
$python = $null
foreach ($c in @('py','python','python3')) { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $python = $g.Source; break } }
if (-not $python) { Write-Error 'No Python found. winget install Python.Python.3.13 (then restart this shell, or call this script again so it re-reads PATH).'; exit 2 }
& $python $pyBackend @Args
exit $LASTEXITCODE
