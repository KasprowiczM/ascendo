<#
.SYNOPSIS
    npm check phase. Read-only enumeration of installed vs latest versions
    for every package in the manifest. Emits ascendo/v1 sidecar.

.NOTES
    Mirrors scripts/winget/check.ps1 shape - same [Alias('Profile')],
    same [switch] $DryRun, same -OutputDir-based Save-Sidecar.
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

# Locate sibling lib/.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $AdapterDir 'lib'
Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoNpm.psm1')  -Force -DisableNameChecking

$ManifestPath = Join-Path (Join-Path $AdapterDir 'config') 'npm_global_clis.txt'

function _Compare-Version {
    param([string]$Installed, [string]$Latest)
    if (-not $Installed -or -not $Latest) { return 0 }
    try {
        # Normalise common version prefixes (e.g. v1.2.3 -> 1.2.3).
        $a = $Installed.TrimStart('v','V')
        $b = $Latest.TrimStart('v','V')
        # Stop at first non-numeric/dot suffix (e.g. 1.2.3-rc.1 -> 1.2.3)
        $aClean = ($a -split '[^0-9.]')[0]
        $bClean = ($b -split '[^0-9.]')[0]
        if (-not $aClean -or -not $bClean) { return 0 }
        $av = [version]$aClean
        $bv = [version]$bClean
        return $av.CompareTo($bv)
    } catch {
        return 0
    }
}

$sidecar = $null
try {
    # Tool version probe.
    $npmBin = Get-AscendoNpmBin
    $toolVersion = 'unknown'
    $toolBinaryPath = $null
    if ($npmBin) {
        $toolBinaryPath = $npmBin
        try {
            $v = & $npmBin --version 2>$null | Out-String
            if ($v) { $toolVersion = $v.Trim() }
        } catch {}
    }

    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'check'
        Category    = 'npm'
        ToolName    = 'npm'
        ToolVersion = $toolVersion
    }
    if ($toolBinaryPath) { $newSidecarArgs['ToolBinaryPath'] = $toolBinaryPath }
    $sidecar = New-Sidecar @newSidecarArgs

    # Item filter parsing.
    $itemFilterArray = $null
    if ($ItemFilter -and $ItemFilter.Trim()) {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    # Load manifest.
    $manifest = Read-AscendoNpmManifest -Path $ManifestPath
    if ($manifest.Count -eq 0) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text "Manifest is empty or missing: $ManifestPath"
    }

    if (-not $npmBin) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text "npm not found on PATH; all manifest items will be missing."
    }

    foreach ($pkg in $manifest) {
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg)) {
            continue
        }
        $installed = Get-AscendoNpmInstalledVersion -PackageName $pkg
        $latest    = Get-AscendoNpmLatestVersion    -PackageName $pkg

        if (-not $installed) {
            $status = 'missing'
        } elseif ($latest -and (_Compare-Version $installed $latest) -lt 0) {
            $status = 'planned'
        } else {
            $status = 'up_to_date'
        }

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = $pkg
            Name       = $pkg
            Category   = 'npm'
            SourceType = 'npm'
            Status     = $status
        }
        # Always emit both Current + Target so the SPA inventory overlay
        # paints "installed -> candidate" correctly (Sesja 57 fix on Linux).
        if ($installed) { $itemArgs['CurrentVersion'] = $installed }
        if ($latest)    { $itemArgs['TargetVersion']  = $latest }
        # When up_to_date and only one of the two is known, mirror it so
        # both columns are populated.
        if ($status -eq 'up_to_date') {
            if ($installed -and -not $latest)    { $itemArgs['TargetVersion']  = $installed }
            if ($latest    -and -not $installed) { $itemArgs['CurrentVersion'] = $latest }
        }
        [void](Add-SidecarItem @itemArgs)
    }

    $plannedCount = @(
        $sidecar['items'] | Where-Object { $_['status'] -eq 'planned' }
    ).Count
    $missingCount = @(
        $sidecar['items'] | Where-Object { $_['status'] -eq 'missing' }
    ).Count
    if ($plannedCount -gt 0 -or $missingCount -gt 0) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("Found {0} planned + {1} missing npm package(s)." `
                -f $plannedCount, $missingCount)
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("Phase failed: {0}" -f $errMsg)
        } catch {}
        try {
            Add-SidecarItem -Sidecar $sidecar `
                -Id '__phase_error__' -Name 'check phase error' `
                -Category 'npm' -SourceType 'npm' -Status 'failed' | Out-Null
        } catch {}
        try {
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {}
    }
    [Console]::Error.WriteLine("npm/check.ps1 FAILED: $errMsg")
    exit 1
}
