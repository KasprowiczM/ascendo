#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Plan/quarantine cloud overlay files that violate the include/exclude rules.
.DESCRIPTION
  Windows PowerShell mirror of dev-sync-prune-excluded.sh. Plan-first:
  emits a JSON plan; nothing is deleted on the remote until the operator
  reviews and runs purge-quarantine separately.
#>
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Args)
$ErrorActionPreference = 'Stop'
$pyBackend = Join-Path $PSScriptRoot 'dev-sync\dev_sync_prune_excluded.py'
if (-not (Test-Path $pyBackend)) { Write-Error "dev-sync backend missing: $pyBackend"; exit 1 }
$python = $null
foreach ($c in @('py','python','python3')) { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $python = $g.Source; break } }
if (-not $python) { Write-Error 'No Python found. winget install Python.Python.3.13'; exit 2 }
& $python $pyBackend @Args
exit $LASTEXITCODE
