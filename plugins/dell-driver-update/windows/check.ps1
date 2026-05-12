<#
.SYNOPSIS
  Dell Driver Update - check phase. Read-only scan via dcu-cli.exe /scan.
  Emits ascendo/v1 sidecar listing each pending driver / BIOS update as
  status='outdated' (planned) item.

.DESCRIPTION
  StrictMode-safe rewrite following adapters/windows/scripts/winget/check.ps1
  as canonical template (per design spec A4). The plugin reuses the
  standard Ascendo sidecar emitter from adapters/windows/lib/AscendoJson.psm1.

  Resolution rules:
    - dcu-cli.exe absent  -> emit single 'skipped' item, status=skipped, exit 0
    - dcu-cli.exe present -> /scan, parse XML/log, emit one item per pending update
                              with status='planned' (outdated)

  The schema enum doesn't include a 'dell_driver_update' source type, so
  Category and SourceType are both 'plugin' (the umbrella enum value); the
  dell-specific feed is recorded via SourceFeed='dell_command_update'.

.NOTES
  Dell Command Update CLI exit codes:
    0   = success, no reboot
    1   = reboot required
    5   = reboot pending
    500 = no updates available
  Documentation: https://www.dell.com/support/manuals/en-us/command-update
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RunId,

    [Parameter(Mandatory)]
        [ValidateSet('cli','scheduler','dashboard','plugin','unknown')]
        [string] $Trigger,

    # Profile is a PowerShell automatic variable; alias so callers passing
    # -Profile <slug> bind to $ProfileName.
    [Parameter(Mandatory)]
        [Alias('Profile')]
        [string] $ProfileName,

    [Parameter(Mandatory)] [string] $OutputDir,

    [Parameter()] [switch] $DryRun,
    [Parameter()] [string] $ItemFilter = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# Module imports
# -----------------------------------------------------------------------------

# plugins/dell-driver-update/windows/check.ps1
#   -> plugins/dell-driver-update/windows
#   -> plugins/dell-driver-update
#   -> plugins
#   -> <repo-root>
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $ScriptDir))
$LibDir    = Join-Path $RepoRoot 'adapters\windows\lib'

# Production install fallback: $env:ProgramFiles\Ascendo\adapters\windows\lib
if (-not (Test-Path (Join-Path $LibDir 'AscendoJson.psm1'))) {
    $prodLib = Join-Path $env:ProgramFiles 'Ascendo\adapters\windows\lib'
    if (Test-Path (Join-Path $prodLib 'AscendoJson.psm1')) {
        $LibDir = $prodLib
    }
}

Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking

# -----------------------------------------------------------------------------
# Inline helpers
# -----------------------------------------------------------------------------

function Get-DcuPath {
    <#
    .SYNOPSIS
        Resolve absolute path to dcu-cli.exe, or $null if missing.
    .DESCRIPTION
        Tries Get-Command first (PATH lookup), then well-known install
        locations on x64 + x86 Program Files.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    try {
        $cmd = Get-Command -Name 'dcu-cli.exe' -ErrorAction SilentlyContinue
        if ($null -ne $cmd -and $cmd.Source) {
            return [string]$cmd.Source
        }
    } catch {
        Write-Verbose "Get-DcuPath: Get-Command failed: $_"
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles 'Dell\CommandUpdate\dcu-cli.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Dell\CommandUpdate\dcu-cli.exe')
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path -LiteralPath $p)) {
            return $p
        }
    }
    return $null
}

function Get-DcuVersion {
    <#
    .SYNOPSIS
        Best-effort extraction of dcu-cli.exe version, or 'unknown'.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory)] [string] $DcuPath)

    try {
        $info = (Get-Item -LiteralPath $DcuPath -ErrorAction Stop).VersionInfo
        if ($null -ne $info -and $info.FileVersion) {
            return [string]$info.FileVersion
        }
    } catch {
        Write-Verbose "Get-DcuVersion: failed: $_"
    }
    return 'unknown'
}

function Get-DcuScanItems {
    <#
    .SYNOPSIS
        Parse dcu-cli /scan XML report into a flat array of update objects.
    .OUTPUTS
        Array of psobject with: Id, Name, Version
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $ReportPath)

    $out = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $ReportPath)) { return ,$out }

    try {
        [xml] $report = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8
    } catch {
        Write-Verbose "Get-DcuScanItems: parse failed: $_"
        return ,$out
    }

    $nodes = $null
    try { $nodes = $report.SelectNodes('//Update') } catch { $nodes = $null }
    if ($null -eq $nodes) { return ,$out }

    foreach ($u in $nodes) {
        # Strict-mode-safe property reads via PSObject.Properties.
        $releaseId = $null
        if ($u.PSObject.Properties['releaseID']) { $releaseId = [string]$u.releaseID }
        $idAttr = $null
        if ($u.PSObject.Properties['id']) { $idAttr = [string]$u.id }
        $name = $null
        if ($u.PSObject.Properties['name']) { $name = [string]$u.name }
        $version = $null
        if ($u.PSObject.Properties['version']) { $version = [string]$u.version }

        $id = if ($releaseId) { $releaseId } elseif ($idAttr) { $idAttr } else { $null }
        if (-not $id) { continue }
        if (-not $name) { $name = $id }

        $out.Add([pscustomobject]@{
            Id      = "dell:$id"
            Name    = $name
            Version = if ($version) { $version } else { $null }
        })
    }
    return ,$out
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

$sidecar = $null
try {
    # Initialise sidecar (use 'plugin' as Category since 'dell_driver_update'
    # is not in the schema enum; SourceFeed records the dell-specific origin).
    $dcuPath    = Get-DcuPath
    $dcuVersion = if ($dcuPath) { Get-DcuVersion -DcuPath $dcuPath } else { 'unknown' }

    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'check'
        Category    = 'plugin'
        ToolName    = 'dcu-cli'
        ToolVersion = $dcuVersion
    }
    if ($null -ne $dcuPath -and $dcuPath -ne '') {
        $newSidecarArgs['ToolBinaryPath'] = $dcuPath
    }
    $sidecar = New-Sidecar @newSidecarArgs

    # Resolve sidecar run dir; XML report goes alongside the sidecar.
    $RunDir = Join-Path $OutputDir $RunId
    if (-not (Test-Path -LiteralPath $RunDir)) {
        New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    }
    $ScanOut = Join-Path $RunDir 'dcu-scan.xml'

    if (-not $dcuPath) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text 'dcu-cli.exe not found - install Dell Command Update'
        [void](Add-SidecarItem -Sidecar $sidecar `
            -Id 'dell:tool-missing' -Name 'Dell Command Update CLI' `
            -Category 'plugin' -SourceType 'plugin' -SourceFeed 'dell_command_update' `
            -Status 'skipped')
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    # Parse the item filter (comma-separated IDs).
    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    # Run the scan. dcu-cli writes its XML report to -report=<path>.
    $rc = -1
    try {
        $scanArgs = @('/scan', '-silent', "-report=$ScanOut")
        & $dcuPath @scanArgs 2>&1 | Out-Null
        $rc = $LASTEXITCODE
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("dcu-cli /scan threw: {0}" -f $_.Exception.Message)
    }
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("dcu-cli /scan exit={0}" -f $rc)

    # Surface a clear "needs Administrator" warn when dcu-cli refused
    # to run for elevation reasons. Affects both:
    #   exit 6  = "Application requires elevated privileges"
    #   exit -1 = process-spawn refused with "operation requires elevation"
    # The user re-runs from an elevated PowerShell to get a real scan.
    if ($rc -eq 6 -or $rc -eq -1) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text 'dcu-cli requires Administrator elevation -- re-run from an elevated PowerShell to scan for pending Dell driver / BIOS updates'
    } elseif ($rc -eq 7) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text 'another Dell Command Update process is already running'
    }

    # Parse + emit items.
    $updates = Get-DcuScanItems -ReportPath $ScanOut
    Write-Verbose ("Get-DcuScanItems returned {0} row(s)" -f $updates.Count)

    foreach ($u in $updates) {
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $u.Id)) {
            continue
        }

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = [string]$u.Id
            Name       = [string]$u.Name
            Category   = 'plugin'
            SourceType = 'plugin'
            SourceFeed = 'dell_command_update'
            Status     = 'planned'
        }
        if ($u.Version) {
            $itemArgs['TargetVersion'] = [string]$u.Version
        }
        [void](Add-SidecarItem @itemArgs)
    }

    if (@($sidecar['items']).Count -eq 0) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text 'dcu-cli: no pending driver / BIOS updates'
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("dell_driver_update check infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Add-SidecarItem -Sidecar $sidecar `
                -Id 'dell:<infrastructure>' -Name 'dell_driver_update check' `
                -Category 'plugin' -SourceType 'plugin' -SourceFeed 'dell_command_update' `
                -Status 'failed')
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch { }
    }
    [Console]::Error.WriteLine("check.ps1 (dell_driver_update) FAILED: $($_.Exception.Message)")
    exit 1
}
