<#
.SYNOPSIS
  Dell Driver Update - verify phase. Re-scans via dcu-cli /scan and reports
  whether previously-applied items have disappeared from the pending list.

.DESCRIPTION
  StrictMode-safe rewrite (per design spec A4). DCU has no per-item state
  query; verify is a delta against the post-apply scan.

  When -ItemFilter is supplied, each filter id is checked: if it appears
  in the new scan it is reported with status='failed' (still pending);
  otherwise status='success' (no longer pending). When -ItemFilter is
  empty, every entry returned by the scan is emitted with status='failed'
  (these are updates still pending and should not be).

  If dcu-cli.exe is missing, emits a single 'skipped' item and exits 0.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RunId,

    [Parameter(Mandatory)]
        [ValidateSet('cli','scheduler','dashboard','plugin','unknown')]
        [string] $Trigger,

    [Parameter(Mandatory)]
        [Alias('Profile')]
        [string] $ProfileName,

    [Parameter(Mandatory)] [string] $OutputDir,

    [Parameter()] [switch] $DryRun,
    [Parameter()] [string] $ItemFilter = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $ScriptDir))
$LibDir    = Join-Path $RepoRoot 'adapters\windows\lib'
if (-not (Test-Path (Join-Path $LibDir 'AscendoJson.psm1'))) {
    $prodLib = Join-Path $env:ProgramFiles 'Ascendo\adapters\windows\lib'
    if (Test-Path (Join-Path $prodLib 'AscendoJson.psm1')) {
        $LibDir = $prodLib
    }
}
Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking

function Get-DcuPath {
    [CmdletBinding()] [OutputType([string])] param()
    try {
        $cmd = Get-Command -Name 'dcu-cli.exe' -ErrorAction SilentlyContinue
        if ($null -ne $cmd -and $cmd.Source) { return [string]$cmd.Source }
    } catch { }
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Dell\CommandUpdate\dcu-cli.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Dell\CommandUpdate\dcu-cli.exe')
    )
    foreach ($p in $candidates) { if ($p -and (Test-Path -LiteralPath $p)) { return $p } }
    return $null
}

function Get-DcuVersion {
    [CmdletBinding()] [OutputType([string])]
    param([Parameter(Mandatory)] [string] $DcuPath)
    try {
        $info = (Get-Item -LiteralPath $DcuPath -ErrorAction Stop).VersionInfo
        if ($null -ne $info -and $info.FileVersion) { return [string]$info.FileVersion }
    } catch { }
    return 'unknown'
}

function Get-DcuScanItems {
    [CmdletBinding()] param([Parameter(Mandatory)] [string] $ReportPath)
    $out = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $ReportPath)) { return ,$out }
    try {
        [xml] $report = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8
    } catch { return ,$out }
    $nodes = $null
    try { $nodes = $report.SelectNodes('//Update') } catch { $nodes = $null }
    if ($null -eq $nodes) { return ,$out }
    foreach ($u in $nodes) {
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

$sidecar = $null
try {
    $dcuPath    = Get-DcuPath
    $dcuVersion = if ($dcuPath) { Get-DcuVersion -DcuPath $dcuPath } else { 'unknown' }

    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'verify'
        Category    = 'plugin'
        ToolName    = 'dcu-cli'
        ToolVersion = $dcuVersion
    }
    if ($null -ne $dcuPath -and $dcuPath -ne '') {
        $newSidecarArgs['ToolBinaryPath'] = $dcuPath
    }
    $sidecar = New-Sidecar @newSidecarArgs

    $RunDir = Join-Path $OutputDir $RunId
    if (-not (Test-Path -LiteralPath $RunDir)) {
        New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    }
    $ScanOut = Join-Path $RunDir 'dcu-verify.xml'

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

    $rc = -1
    try {
        & $dcuPath @('/scan', '-silent', "-report=$ScanOut") 2>&1 | Out-Null
        $rc = $LASTEXITCODE
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("dcu-cli /scan (verify) threw: {0}" -f $_.Exception.Message)
    }
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("dcu-cli /scan (verify) exit={0}" -f $rc)

    $stillPending = Get-DcuScanItems -ReportPath $ScanOut

    # Index ids for filter intersection.
    $pendingIds = @{}
    foreach ($u in $stillPending) {
        if ($u.Id) { $pendingIds[[string]$u.Id] = $true }
    }

    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    if ($null -ne $itemFilterArray) {
        # For each filter id: success if no longer in scan, failed if still there.
        foreach ($fid in $itemFilterArray) {
            $stillThere = $pendingIds.ContainsKey($fid)
            $status = if ($stillThere) { 'failed' } else { 'success' }
            $itemArgs = @{
                Sidecar    = $sidecar
                Id         = $fid
                Name       = $fid
                Category   = 'plugin'
                SourceType = 'plugin'
                SourceFeed = 'dell_command_update'
                Status     = $status
            }
            [void](Add-SidecarItem @itemArgs)
        }
    } else {
        # No filter: emit each currently-pending update as 'failed' (i.e.
        # an update is still outstanding, so verify did NOT confirm clean).
        foreach ($u in $stillPending) {
            $itemArgs = @{
                Sidecar    = $sidecar
                Id         = [string]$u.Id
                Name       = [string]$u.Name
                Category   = 'plugin'
                SourceType = 'plugin'
                SourceFeed = 'dell_command_update'
                Status     = 'failed'
            }
            if ($u.Version) { $itemArgs['TargetVersion'] = [string]$u.Version }
            [void](Add-SidecarItem @itemArgs)
        }

        if (@($sidecar['items']).Count -eq 0) {
            Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
                -Text 'dell_driver_update verify: no updates pending - system is up to date'
        }
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("dell_driver_update verify infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Add-SidecarItem -Sidecar $sidecar `
                -Id 'dell:<infrastructure>' -Name 'dell_driver_update verify' `
                -Category 'plugin' -SourceType 'plugin' -SourceFeed 'dell_command_update' `
                -Status 'failed')
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch { }
    }
    [Console]::Error.WriteLine("verify.ps1 (dell_driver_update) FAILED: $($_.Exception.Message)")
    exit 1
}
