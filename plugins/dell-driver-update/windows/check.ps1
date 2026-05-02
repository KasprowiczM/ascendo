<#
.SYNOPSIS
  Dell Driver Update — check phase.

.DESCRIPTION
  Runs ``dcu-cli.exe /scan`` (read-only, non-elevated) and parses the
  resulting XML report to surface each pending driver / BIOS update as
  an ascendo/v1 item.

  The scan output goes to ``<OutputDir>\<RunId>\dcu-scan.xml`` for
  reproducibility; the parsed sidecar lands at
  ``<OutputDir>\<RunId>\check__dell_driver_update.json`` per the
  standard phase layout.

.NOTES
  Dell Command Update CLI documentation:
  https://www.dell.com/support/manuals/en-us/command-update
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

# Resolve sibling lib (under the WINDOWS adapter, since the plugin reuses
# the standard Ascendo sidecar emitter).
$AdapterLib = Join-Path $env:ProgramFiles 'Ascendo\adapters\windows\lib'
if (-not (Test-Path (Join-Path $AdapterLib 'AscendoJson.psm1'))) {
    # Dev mode: walk up to the repo root.
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $repoRoot = $here
    for ($i = 0; $i -lt 6; $i++) { $repoRoot = Split-Path -Parent $repoRoot }
    $AdapterLib = Join-Path $repoRoot 'adapters\windows\lib'
}
Import-Module (Join-Path $AdapterLib 'AscendoJson.psm1') -Force

$RunDir      = Join-Path $OutputDir $RunId
$null        = New-Item -ItemType Directory -Force -Path $RunDir
$SidecarPath = Join-Path $RunDir 'check__dell_driver_update.json'
$ScanOut     = Join-Path $RunDir 'dcu-scan.xml'

$dcu = Get-Command 'dcu-cli.exe' -ErrorAction SilentlyContinue
$sidecar = New-Sidecar -Run $RunId -Trigger $Trigger -Profile $Profile `
                       -DryRun:$DryRun -Phase 'check' -Category 'dell_driver_update' `
                       -Tool @{ name = 'dcu-cli'; binary_path = if ($dcu) { $dcu.Source } else { $null } }

if (-not $dcu) {
    Add-SidecarMessage -Sidecar $sidecar -Level 'warning' `
        -Message 'dcu-cli.exe not on PATH; install Dell Command Update'
    Save-Sidecar -Sidecar $sidecar -Path $SidecarPath
    exit 0
}

try {
    $argv = @('/scan', '-silent', "-report=$ScanOut")
    & $dcu.Source @argv | Out-Null
    $rc = $LASTEXITCODE
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Message "dcu-cli /scan exit=$rc"
} catch {
    Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
        -Message "dcu-cli /scan threw: $($_.Exception.Message)"
}

$filterIds = @()
if ($ItemFilter) {
    $filterIds = $ItemFilter -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

if (Test-Path $ScanOut) {
    try {
        [xml] $report = Get-Content -LiteralPath $ScanOut -Raw -Encoding UTF8
        $updates = $report.SelectNodes('//Update')
        foreach ($u in $updates) {
            $id      = if ($u.releaseID)   { [string] $u.releaseID }   else { [string] $u.id }
            $name    = if ($u.name)        { [string] $u.name }         else { $id }
            $version = if ($u.version)     { [string] $u.version }      else { $null }
            if ($filterIds.Count -gt 0 -and -not ($filterIds -contains $id)) { continue }
            Add-SidecarItem -Sidecar $sidecar -Id $id -Name $name `
                -Status 'planned' -TargetVersion $version `
                -Source @{ type = 'plugin'; feed = 'dell_command_update' }
        }
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warning' `
            -Message "scan report parse failed: $($_.Exception.Message)"
    }
}

Save-Sidecar -Sidecar $sidecar -Path $SidecarPath
exit 0
