#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Import private repo overlay files from the configured cloud provider.

.DESCRIPTION
  Windows PowerShell mirror of dev-sync-import.sh. Used on a fresh clone
  to pull back the .env.local + agent settings + OAuth tokens that GitHub
  doesn't carry (because they're git-ignored). Delegates to the Python
  backend at dev-sync\dev_sync_import.py.

.EXAMPLE
  .\dev-sync-import.ps1 --dry-run --verbose
  .\dev-sync-import.ps1
#>
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Args)

$ErrorActionPreference = 'Stop'
$pyBackend = Join-Path $PSScriptRoot 'dev-sync\dev_sync_import.py'
if (-not (Test-Path $pyBackend)) { Write-Error "dev-sync backend missing: $pyBackend"; exit 1 }

$python = $null
foreach ($c in @('py','python','python3')) { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $python = $g.Source; break } }
if (-not $python) { Write-Error 'No Python found. winget install Python.Python.3.13'; exit 2 }

& $python $pyBackend @Args
exit $LASTEXITCODE
