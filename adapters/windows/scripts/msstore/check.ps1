<#
.SYNOPSIS
  Microsoft Store check phase. Read-only enumeration of upgradable + installed
  Store-managed packages via `winget --source msstore`. Emits ascendo/v1 sidecar.

.NOTES
  Mirrors the working pattern from scripts/winget/check.ps1 — same hashtable
  splatting for New-Sidecar / Add-SidecarItem, same -OutputDir-based
  Save-Sidecar, same [Alias('Profile')] for the Python caller's -Profile arg.
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

# Locate sibling lib/ — script lives at adapters\windows\scripts\msstore\,
# lib lives at adapters\windows\lib\, so two parent walks.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDir    = Join-Path (Split-Path -Parent (Split-Path -Parent $ScriptDir)) 'lib'
Import-Module (Join-Path $LibDir 'AscendoJson.psm1')   -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoWinget.psm1') -Force -DisableNameChecking

# Local helpers (these aren't exported from AscendoWinget.psm1; each script
# that needs them defines its own minimal version).
function _Get-WingetVersionString {
    try {
        $v = (& winget --version 2>$null) -as [string]
        if ($v -and $v.Trim()) { return $v.Trim() }
    } catch {}
    return 'unknown'
}
function _Get-WingetBinaryPathString {
    try {
        $cmd = Get-Command winget -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) { return [string]$cmd.Source }
    } catch {}
    return $null
}


$sidecar = $null
$prevEnc = $null

try {
    # ── 1. Initialise sidecar ──────────────────────────────────────────
    $toolVersion    = _Get-WingetVersionString
    $toolBinaryPath = _Get-WingetBinaryPathString

    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'check'
        Category    = 'msstore'
        ToolName    = 'winget'
        ToolVersion = $toolVersion
    }
    if ($null -ne $toolBinaryPath -and $toolBinaryPath -ne '') {
        $newSidecarArgs['ToolBinaryPath'] = $toolBinaryPath
    }
    $sidecar = New-Sidecar @newSidecarArgs

    # ── 2. Configure winget output encoding ────────────────────────────
    $prevEnc = Initialize-WingetEnvironment

    # ── 3. Parse the item filter (comma-separated IDs) ─────────────────
    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    # ── 4. Enumerate upgradable + installed Store packages ─────────────
    # Get-WingetUpgradable / Get-WingetInstalled don't accept -Source; we
    # call them with no filter and post-filter to msstore-sourced rows.
    $upgradable = @()
    try {
        $allUpg = @(Get-WingetUpgradable -ErrorAction Stop)
        $upgradable = @($allUpg | Where-Object {
            $_.PSObject.Properties['Source'] -and $_.Source -and ($_.Source -ieq 'msstore')
        })
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-WingetUpgradable failed: {0}" -f $_.Exception.Message)
    }
    $installed = @()
    try {
        $allInst = @(Get-WingetInstalled -ErrorAction Stop)
        $installed = @($allInst | Where-Object {
            $_.PSObject.Properties['Source'] -and $_.Source -and ($_.Source -ieq 'msstore')
        })
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-WingetInstalled failed: {0}" -f $_.Exception.Message)
    }

    $installedById = @{}
    foreach ($pkg in $installed) {
        if ($pkg.Id -and -not $installedById.ContainsKey($pkg.Id)) {
            $installedById[$pkg.Id] = $pkg.Version
        }
    }
    $emittedIds = @{}

    # ── 5. Items with available upgrade ────────────────────────────────
    foreach ($pkg in $upgradable) {
        if (-not $pkg.Id) { continue }
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg.Id)) { continue }

        $hasAvailable = ($null -ne $pkg.Available) -and ([string]$pkg.Available).Trim() -ne ''
        $status = if ($hasAvailable) { 'planned' } else { 'up_to_date' }

        $current = $null
        if ($installedById.ContainsKey($pkg.Id)) { $current = [string]$installedById[$pkg.Id] }
        if ([string]::IsNullOrWhiteSpace($current) -or $current -eq 'Unknown') {
            if ($pkg.PSObject.Properties['Version']) {
                $cand = [string]$pkg.Version
                if (-not [string]::IsNullOrWhiteSpace($cand) -and $cand -ne 'Unknown') { $current = $cand }
            }
        }

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = [string]$pkg.Id
            Name       = [string]$pkg.Name
            Category   = 'msstore'
            SourceType = 'msstore'
            SourceFeed = 'msstore'
            Status     = $status
        }
        if (-not [string]::IsNullOrWhiteSpace($current)) { $itemArgs['CurrentVersion'] = $current }
        if ($hasAvailable) { $itemArgs['TargetVersion'] = [string]$pkg.Available }

        [void](Add-SidecarItem @itemArgs)
        $emittedIds[$pkg.Id] = $true
    }

    # ── 6. Installed Store packages not in the upgrade list ────────────
    foreach ($pkg in $installed) {
        if (-not $pkg.Id) { continue }
        if ($emittedIds.ContainsKey($pkg.Id)) { continue }
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg.Id)) { continue }

        $current = [string]$pkg.Version
        if ([string]::IsNullOrWhiteSpace($current) -or $current -eq 'Unknown') { $current = $null }

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = [string]$pkg.Id
            Name       = [string]$pkg.Name
            Category   = 'msstore'
            SourceType = 'msstore'
            SourceFeed = 'msstore'
            Status     = 'up_to_date'
        }
        if ($null -ne $current) { $itemArgs['CurrentVersion'] = $current }
        [void](Add-SidecarItem @itemArgs)
    }

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("msstore: {0} installed, {1} upgradable" -f $installed.Count, $upgradable.Count)

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("msstore check infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Add-SidecarItem -Sidecar $sidecar `
                -Id '<infrastructure>' -Name 'msstore enumeration' `
                -Category 'msstore' -SourceType 'msstore' -SourceFeed 'msstore' `
                -Status 'failed')
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch { }
    }
    exit 1
}
finally {
    if ($null -ne $prevEnc) {
        try { Restore-WingetEnvironment -Previous $prevEnc } catch {}
    }
}
