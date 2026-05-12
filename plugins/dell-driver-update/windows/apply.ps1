<#
.SYNOPSIS
  Dell Driver Update - apply phase. Calls dcu-cli.exe /applyUpdates with
  -reboot=disable to install all pending updates, parses the apply log,
  and emits one ascendo/v1 sidecar item per installed driver / BIOS update.

.DESCRIPTION
  StrictMode-safe rewrite (per design spec A4). Requires elevation; the
  Python orchestrator escalates via UAC before invoking this phase.

  Reboot semantics: -reboot=disable is mandatory. DCU exit codes 1 (reboot
  required) and 5 (reboot pending) are mapped to status='success' on the
  affected items, and the phase records this on its info-level messages.
  Operators decide when to reboot.

  In -DryRun mode, no mutation is performed: emits 'planned' items based
  on a /scan only.

.NOTES
  DCU exit codes:
    0   = success, no reboot
    1   = reboot required
    5   = reboot pending
    500 = no updates available
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

function Get-DcuApplyItems {
    <#
    .SYNOPSIS
        Parse dcu-cli /applyUpdates -outputLog text into per-driver records.
    .OUTPUTS
        Array of psobject with: Id, Name, Version
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $LogPath)

    $out = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $LogPath)) { return ,$out }

    $lines = $null
    try {
        $lines = Get-Content -LiteralPath $LogPath -Encoding UTF8 -ErrorAction Stop
    } catch {
        return ,$out
    }
    if ($null -eq $lines) { return ,$out }

    foreach ($line in $lines) {
        if ($line -match 'Installing\s+(.+?)\s+\(version\s+([^\)]+)\)') {
            $out.Add([pscustomobject]@{
                Id      = "dell:$($matches[1])"
                Name    = [string]$matches[1]
                Version = [string]$matches[2]
            })
        }
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
        Phase       = 'apply'
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

    # -- Dry-run path: scan only, emit 'planned' items, no mutation -----
    if ($DryRun) {
        $ScanOut = Join-Path $RunDir 'dcu-apply-dryrun.xml'
        $rcDry = -1
        try {
            & $dcuPath @('/scan', '-silent', "-report=$ScanOut") 2>&1 | Out-Null
            $rcDry = $LASTEXITCODE
        } catch {
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("dcu-cli /scan (dry-run) threw: {0}" -f $_.Exception.Message)
        }
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("dry-run: would invoke dcu-cli /applyUpdates -reboot=disable (scan exit={0})" -f $rcDry)

        $dryUpdates = Get-DcuScanItems -ReportPath $ScanOut
        foreach ($u in $dryUpdates) {
            if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $u.Id)) { continue }
            $dryArgs = @{
                Sidecar    = $sidecar
                Id         = [string]$u.Id
                Name       = [string]$u.Name
                Category   = 'plugin'
                SourceType = 'plugin'
                SourceFeed = 'dell_command_update'
                Status     = 'planned'
            }
            if ($u.Version) { $dryArgs['TargetVersion'] = [string]$u.Version }
            [void](Add-SidecarItem @dryArgs)
        }
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    # -- Real apply path ------------------------------------------------
    $ApplyOut = Join-Path $RunDir 'dcu-apply.log'
    $rc = -1
    try {
        $applyArgs = @('/applyUpdates', '-silent', '-reboot=disable', "-outputLog=$ApplyOut")
        & $dcuPath @applyArgs 2>&1 | Out-Null
        $rc = $LASTEXITCODE
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
            -Text ("dcu-cli /applyUpdates threw: {0}" -f $_.Exception.Message)
        [void](Add-SidecarItem -Sidecar $sidecar `
            -Id 'dell:<apply-error>' -Name 'dcu-cli /applyUpdates' `
            -Category 'plugin' -SourceType 'plugin' -SourceFeed 'dell_command_update' `
            -Status 'failed')
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 1
    }

    # Per-item result mapping (full DCU CLI exit code table from Dell docs):
    #   0   -> success (no reboot)
    #   1   -> success + reboot required
    #   5   -> success + reboot pending
    #   500 -> success (no updates available)
    #   6   -> SKIPPED (needs Administrator elevation)  <-- non-elevated invocation
    #   7   -> SKIPPED (process locked - another DCU instance is running)
    #   3   -> SKIPPED (user-interrupted; SIGINT-equivalent)
    #   *   -> failed
    # The orchestrator's stop_on_failure heuristic aborts the run only when
    # ALL managers report 'failed' for a phase. Treating elevation issues
    # as `skipped` (informational) rather than `failed` keeps the run
    # going through subsequent phases / categories.
    $perItemStatus = switch ($rc) {
        0   { 'success' }
        1   { 'success' }
        5   { 'success' }
        500 { 'success' }
        6   { 'skipped' }
        7   { 'skipped' }
        3   { 'skipped' }
        default { 'failed' }
    }
    $rebootRequired = ($rc -eq 1 -or $rc -eq 5)
    $skipReason = switch ($rc) {
        6 { 'dcu-cli requires Administrator elevation -- re-run from an elevated PowerShell' }
        7 { 'another Dell Command Update process is already running' }
        3 { 'dcu-cli /applyUpdates interrupted by user (Ctrl+C / kill)' }
        default { '' }
    }

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("dcu-cli /applyUpdates exit={0} status={1} reboot_required={2}" -f $rc, $perItemStatus, $rebootRequired)
    if ($skipReason) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' -Text $skipReason
    }
    if ($rebootRequired) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text 'reboot required to complete driver installation'
    }

    # Emit one item per parsed driver from the apply log; if the log has
    # no per-driver lines (DCU sometimes only writes a roll-up), emit a
    # single roll-up item so the sidecar reflects what happened.
    $applied = Get-DcuApplyItems -LogPath $ApplyOut
    foreach ($u in $applied) {
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $u.Id)) { continue }
        $itemArgs = @{
            Sidecar         = $sidecar
            Id              = [string]$u.Id
            Name            = [string]$u.Name
            Category        = 'plugin'
            SourceType      = 'plugin'
            SourceFeed      = 'dell_command_update'
            Status          = $perItemStatus
            ExitCode        = [int]$rc
            Rollback        = @{ available = $false; method = 'reinstall_required' }
        }
        if ($u.Version) {
            $itemArgs['TargetVersion']   = [string]$u.Version
            $itemArgs['ResolvedVersion'] = [string]$u.Version
        }
        [void](Add-SidecarItem @itemArgs)
    }

    if (@($sidecar['items']).Count -eq 0) {
        # No log lines parsed; emit a single roll-up item so the sidecar
        # reflects the apply outcome.
        $rollupId = if ($rc -eq 500) { 'dell:no-updates' } else { 'dell:<all-updates>' }
        $rollupName = if ($rc -eq 500) { 'no updates available' } else { 'Dell Command Update' }
        [void](Add-SidecarItem -Sidecar $sidecar `
            -Id $rollupId -Name $rollupName `
            -Category 'plugin' -SourceType 'plugin' -SourceFeed 'dell_command_update' `
            -Status $perItemStatus -ExitCode ([int]$rc) `
            -Rollback @{ available = $false; method = 'reinstall_required' })
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)

    # Surface reboot via process exit semantics for upstream callers that
    # tail exit code (sidecar carries the canonical info).
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("dell_driver_update apply infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Add-SidecarItem -Sidecar $sidecar `
                -Id 'dell:<infrastructure>' -Name 'dell_driver_update apply' `
                -Category 'plugin' -SourceType 'plugin' -SourceFeed 'dell_command_update' `
                -Status 'failed')
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch { }
    }
    [Console]::Error.WriteLine("apply.ps1 (dell_driver_update) FAILED: $($_.Exception.Message)")
    exit 1
}
