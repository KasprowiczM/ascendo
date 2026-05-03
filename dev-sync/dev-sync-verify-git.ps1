#!/usr/bin/env pwsh
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $RemainingArgs)

$helper = Join-Path $PSScriptRoot 'Invoke-DevSyncPython.ps1'
& $helper -BackendScript 'dev_sync_verify_git.py' @RemainingArgs
exit $LASTEXITCODE
