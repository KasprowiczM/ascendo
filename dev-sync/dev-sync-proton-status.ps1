#!/usr/bin/env pwsh
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $RemainingArgs)

$helper = Join-Path $PSScriptRoot 'Invoke-DevSyncPython.ps1'
& $helper -BackendScript 'dev_sync_proton_status.py' @RemainingArgs
exit $LASTEXITCODE
