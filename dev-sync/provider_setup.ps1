#!/usr/bin/env pwsh
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $RemainingArgs)

$repoRoot = Split-Path -Parent $PSScriptRoot
$setup = Join-Path $repoRoot 'dev-sync-provider-setup.ps1'

if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) {
    Write-Error "Provider setup wrapper missing: $setup"
    exit 1
}

$cleanArgs = @($RemainingArgs | Where-Object { $null -ne $_ -and $_ -ne '' })

& $setup @cleanArgs
exit $LASTEXITCODE
