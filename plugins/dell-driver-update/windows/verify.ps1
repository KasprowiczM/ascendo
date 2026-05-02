<#
.SYNOPSIS
  Dell Driver Update — verify phase. Re-runs /scan; any update still
  pending is a verify failure.
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
$SidecarPath = Join-Path $RunDir 'verify__dell_driver_update.json'
$ScanOut     = Join-Path $RunDir 'dcu-verify.xml'

$dcu = Get-Command 'dcu-cli.exe' -ErrorAction SilentlyContinue
$sidecar = New-Sidecar -Run $RunId -Trigger $Trigger -Profile $Profile `
                       -DryRun:$DryRun -Phase 'verify' -Category 'dell_driver_update' `
                       -Tool @{ name = 'dcu-cli' }

if (-not $dcu) {
    Add-SidecarMessage -Sidecar $sidecar -Level 'warning' -Message 'dcu-cli.exe missing'
    Save-Sidecar -Sidecar $sidecar -Path $SidecarPath
    exit 0
}

try { & $dcu.Source @('/scan', '-silent', "-report=$ScanOut") | Out-Null } catch {}

if (Test-Path $ScanOut) {
    try {
        [xml] $report = Get-Content -LiteralPath $ScanOut -Raw -Encoding UTF8
        foreach ($u in $report.SelectNodes('//Update')) {
            $id = if ($u.releaseID) { [string] $u.releaseID } else { [string] $u.id }
            Add-SidecarItem -Sidecar $sidecar -Id $id -Name ([string] $u.name) `
                -Status 'failed' -TargetVersion ([string] $u.version) `
                -Source @{ type = 'plugin'; feed = 'dell_command_update' }
        }
    } catch {}
}

Save-Sidecar -Sidecar $sidecar -Path $SidecarPath
exit 0
