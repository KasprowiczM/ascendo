<#
.SYNOPSIS
  Dell Driver Update - plan phase. Same scan as check, but every item is
  emitted as 'planned' (read-only preview of what apply would install).

.DESCRIPTION
  StrictMode-safe rewrite (per design spec A4). DCU has no separate "plan"
  surface - the scan IS the plan. We re-run /scan and label results as
  planned regardless of exit code. Items: action=planned, status=planned.

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
        Phase       = 'plan'
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
    $ScanOut = Join-Path $RunDir 'dcu-plan.xml'

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

    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    $rc = -1
    try {
        & $dcuPath @('/scan', '-silent', "-report=$ScanOut") 2>&1 | Out-Null
        $rc = $LASTEXITCODE
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("dcu-cli /scan threw: {0}" -f $_.Exception.Message)
    }
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("dcu-cli /scan (plan) exit={0}" -f $rc)

    $updates = Get-DcuScanItems -ReportPath $ScanOut
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
            -Text 'dell_driver_update plan: no items to plan'
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("dell_driver_update plan infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Add-SidecarItem -Sidecar $sidecar `
                -Id 'dell:<infrastructure>' -Name 'dell_driver_update plan' `
                -Category 'plugin' -SourceType 'plugin' -SourceFeed 'dell_command_update' `
                -Status 'failed')
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch { }
    }
    [Console]::Error.WriteLine("plan.ps1 (dell_driver_update) FAILED: $($_.Exception.Message)")
    exit 1
}
