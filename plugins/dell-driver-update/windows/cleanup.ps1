<#
.SYNOPSIS
  Dell Driver Update — cleanup phase. DCU stages drivers under
  %LOCALAPPDATA%\Dell\CommandUpdate; we don't prune them (Dell manages
  its own retention) but emit a success sidecar so the orchestrator's
  phase inventory stays consistent across categories.
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
$SidecarPath = Join-Path $RunDir 'cleanup__dell_driver_update.json'

$sidecar = New-Sidecar -Run $RunId -Trigger $Trigger -Profile $Profile `
                       -DryRun:$DryRun -Phase 'cleanup' -Category 'dell_driver_update' `
                       -Tool @{ name = 'dcu-cli' }
Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
    -Message 'dell_driver_update cleanup: no-op (Dell Command Update manages its own staging)'
Save-Sidecar -Sidecar $sidecar -Path $SidecarPath
exit 0
