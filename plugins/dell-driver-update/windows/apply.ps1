<#
.SYNOPSIS
  Dell Driver Update — apply phase. Calls dcu-cli /applyUpdates -reboot=disable
  to install all pending updates, then enumerates installed items into a
  sidecar.

.NOTES
  Requires elevation. The orchestrator (M3.14 IElevation) escalates via
  UAC before invoking this phase. -reboot=disable is mandatory: drivers
  that ask for reboot are tracked and surfaced via needs_reboot in the
  sidecar; restart is the user's call after the run completes.
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
$SidecarPath = Join-Path $RunDir 'apply__dell_driver_update.json'
$ApplyOut    = Join-Path $RunDir 'dcu-apply.log'

$dcu = Get-Command 'dcu-cli.exe' -ErrorAction SilentlyContinue
$sidecar = New-Sidecar -Run $RunId -Trigger $Trigger -Profile $Profile `
                       -DryRun:$DryRun -Phase 'apply' -Category 'dell_driver_update' `
                       -Tool @{ name = 'dcu-cli'; binary_path = if ($dcu) { $dcu.Source } else { $null } }

if (-not $dcu) {
    Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
        -Message 'dcu-cli.exe not on PATH; install Dell Command Update'
    Save-Sidecar -Sidecar $sidecar -Path $SidecarPath
    exit 0
}

if ($DryRun) {
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Message 'dry-run: would invoke dcu-cli /applyUpdates -reboot=disable'
    Save-Sidecar -Sidecar $sidecar -Path $SidecarPath
    exit 0
}

# Run the apply.
$applyArgs = @('/applyUpdates', '-silent', '-reboot=disable', "-outputLog=$ApplyOut")
try {
    & $dcu.Source @applyArgs | Out-Null
    $rc = $LASTEXITCODE
} catch {
    Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
        -Message "dcu-cli /applyUpdates threw: $($_.Exception.Message)"
    Save-Sidecar -Sidecar $sidecar -Path $SidecarPath
    exit 1
}

# DCU exit code mapping (per Dell docs):
#   0   = success, no reboot
#   1   = reboot required
#   2   = fatal error
#   500 = no updates found
#   1xx = various warnings (treated as success here)
$status = switch ($rc) {
    0   { 'success' }
    1   { 'success' }   # reboot pending; surface via needs_reboot below
    500 { 'success' }   # no updates
    default { 'failed' }
}
$rebootRequired = ($rc -eq 1)

Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
    -Message "dcu-cli /applyUpdates exit=$rc status=$status reboot=$rebootRequired"

# Best-effort item enumeration: parse the apply log if present.
if (Test-Path $ApplyOut) {
    $logLines = Get-Content -LiteralPath $ApplyOut -Encoding UTF8 -ErrorAction SilentlyContinue
    foreach ($line in $logLines) {
        if ($line -match 'Installing\s+(.+?)\s+\(version\s+([^\)]+)\)') {
            Add-SidecarItem -Sidecar $sidecar -Id $matches[1] -Name $matches[1] `
                -Status $status -TargetVersion $matches[2] -ResolvedVersion $matches[2] `
                -Source @{ type = 'plugin'; feed = 'dell_command_update' } `
                -Rollback @{ available = $false; method = 'dcu_rollback' }
        }
    }
}

if ($sidecar.items.Count -eq 0 -and $status -eq 'success') {
    # No log entries but exit said OK — emit a single roll-up item.
    Add-SidecarItem -Sidecar $sidecar -Id '<all-updates>' -Name 'Dell Command Update' `
        -Status 'success' -Source @{ type = 'plugin'; feed = 'dell_command_update' }
}

Save-Sidecar -Sidecar $sidecar -Path $SidecarPath -NeedsReboot:$rebootRequired
exit 0
