#!/usr/bin/env pwsh
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $RemainingArgs)

$helper = Join-Path $PSScriptRoot 'Invoke-DevSyncPython.ps1'
& $helper -BackendScript 'dev_sync_purge_quarantine.py' @RemainingArgs
exit $LASTEXITCODE
