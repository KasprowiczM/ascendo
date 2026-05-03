#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Full verification: every tracked file is on GitHub AND every overlay
  file is on the cloud provider.
.DESCRIPTION
  Windows PowerShell mirror of dev-sync-verify-full.sh. Combines
  verify-git (GitHub coverage) + cloud-provider coverage so you have one
  command to confirm "yes, every byte is reproducible from GitHub +
  Proton on a fresh machine".
#>
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Args)
$ErrorActionPreference = 'Stop'
$pyBackend = Join-Path $PSScriptRoot 'dev-sync\dev_sync_verify_full.py'
if (-not (Test-Path $pyBackend)) { Write-Error "dev-sync backend missing: $pyBackend"; exit 1 }
$python = $null
foreach ($c in @('py','python','python3')) { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $python = $g.Source; break } }
if (-not $python) { Write-Error 'No Python found. winget install Python.Python.3.13'; exit 2 }
& $python $pyBackend @Args
exit $LASTEXITCODE
