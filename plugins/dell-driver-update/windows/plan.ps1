<#
.SYNOPSIS
  Dell Driver Update — plan phase. Same enumeration as check, but emits
  only items that WOULD be installed by apply (excludes optional /
  recommended-but-skipped categories that the policy leaves out).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $RunId,
    [Parameter(Mandatory = $true)] [string] $Trigger,
    [Parameter(Mandatory = $true)] [string] $Profile,
    [Parameter(Mandatory = $true)] [string] $OutputDir,
    [switch] $DryRun,
    [string] $ItemFilter
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$AdapterLib = Join-Path $env:ProgramFiles 'Ascendo\adapters\windows\lib'
if (-not (Test-Path (Join-Path $AdapterLib 'AscendoJson.psm1'))) {
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $repoRoot = $here
    for ($i = 0; $i -lt 6; $i++) { $repoRoot = Split-Path -Parent $repoRoot }
    $AdapterLib = Join-Path $repoRoot 'adapters\windows\lib'
}
Import-Module (Join-Path $AdapterLib 'AscendoJson.psm1') -Force

$RunDir      = Join-Path $OutputDir $RunId
$null        = New-Item -ItemType Directory -Force -Path $RunDir
$SidecarPath = Join-Path $RunDir 'plan__dell_driver_update.json'

$sidecar = New-Sidecar -Run $RunId -Trigger $Trigger -Profile $Profile `
                       -DryRun:$DryRun -Phase 'plan' -Category 'dell_driver_update' `
                       -Tool @{ name = 'dcu-cli' }

# Plan == check (DCU exposes no separate "plan" surface — the scan IS the plan).
& (Join-Path $PSScriptRoot 'check.ps1') -RunId $RunId -Trigger $Trigger -Profile $Profile `
                                       -OutputDir $OutputDir -DryRun:$DryRun -ItemFilter $ItemFilter
# Re-write under the plan name; cheapest is to copy if check produced one.
$checkOut = Join-Path $RunDir 'check__dell_driver_update.json'
if (Test-Path $checkOut) {
    $obj = Get-Content -LiteralPath $checkOut -Raw -Encoding UTF8 | ConvertFrom-Json
    $obj.phase = 'plan'
    $json = $obj | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($SidecarPath, $json, [System.Text.UTF8Encoding]::new($false))
} else {
    Save-Sidecar -Sidecar $sidecar -Path $SidecarPath
}
exit 0
