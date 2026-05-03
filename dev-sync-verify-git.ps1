#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Verify that every tracked file is reproducible from GitHub origin/main.
.DESCRIPTION
  Windows PowerShell mirror of dev-sync-verify-git.sh. Read-only sanity
  check — confirms local working tree matches origin/main and there are
  no uncommitted changes that would be lost on a fresh clone.
#>
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Args)
$ErrorActionPreference = 'Stop'
$pyBackend = Join-Path $PSScriptRoot 'dev-sync\dev_sync_verify_git.py'
if (-not (Test-Path $pyBackend)) { Write-Error "dev-sync backend missing: $pyBackend"; exit 1 }
$python = $null
foreach ($c in @('py','python','python3')) { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $python = $g.Source; break } }
if (-not $python) { Write-Error 'No Python found. winget install Python.Python.3.13'; exit 2 }
& $python $pyBackend @Args
exit $LASTEXITCODE
