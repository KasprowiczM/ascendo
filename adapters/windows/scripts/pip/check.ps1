<#
.SYNOPSIS
    pip check phase. Read-only enumeration of installed vs latest versions
    for every package in the manifest. Emits ascendo/v1 sidecar.
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
$AdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $AdapterDir 'lib'
Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoPip.psm1')  -Force -DisableNameChecking

$ManifestPath = Join-Path (Join-Path $AdapterDir 'config') 'pip_global_clis.txt'

function _Compare-Version {
    param([string]$Installed, [string]$Latest)
    if (-not $Installed -or -not $Latest) { return 0 }
    try {
        $a = $Installed.TrimStart('v','V'); $b = $Latest.TrimStart('v','V')
        $aClean = ($a -split '[^0-9.]')[0]; $bClean = ($b -split '[^0-9.]')[0]
        if (-not $aClean -or -not $bClean) { return 0 }
        return ([version]$aClean).CompareTo([version]$bClean)
    } catch { return 0 }
}

$sidecar = $null
try {
    $pipBin = Get-AscendoPipBin
    $toolVersion = 'unknown'
    $toolBinaryPath = $null
    if ($pipBin) {
        $toolBinaryPath = $pipBin
        try {
            # Probe version (works for both pip3.exe direct and py launcher).
            $cmd = Get-AscendoPipCommand
            if ($cmd -and $cmd.Count -ge 1) {
                $exe = [string]$cmd[0]
                $args = @($cmd[1])
                $argList = @()
                foreach ($a in $args) { if ($null -ne $a) { $argList += [string]$a } }
                $argList += '--version'
                $v = & $exe @argList 2>$null | Out-String
                if ($v) { $toolVersion = ($v -split "`r?`n")[0].Trim() }
            }
        } catch {}
    }

    $newSidecarArgs = @{
        RunId = $RunId; Trigger = $Trigger; ProfileName = $ProfileName
        DryRun = [bool]$DryRun; Phase = 'check'; Category = 'pip'
        ToolName = 'pip'; ToolVersion = $toolVersion
    }
    if ($toolBinaryPath) { $newSidecarArgs['ToolBinaryPath'] = $toolBinaryPath }
    $sidecar = New-Sidecar @newSidecarArgs

    $itemFilterArray = $null
    if ($ItemFilter -and $ItemFilter.Trim()) {
        $itemFilterArray = @(
            $ItemFilter -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    $manifest = Read-AscendoPipManifest -Path $ManifestPath
    if ($manifest.Count -eq 0) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text "Manifest is empty or missing: $ManifestPath"
    }
    if (-not $pipBin) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text "pip not found on PATH; all manifest items will be missing."
    }

    foreach ($pkg in $manifest) {
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg)) { continue }
        $installed = Get-AscendoPipInstalledVersion -PackageName $pkg
        $latest    = Get-AscendoPipLatestVersion    -PackageName $pkg

        if (-not $installed) {
            $status = 'missing'
        } elseif ($latest -and (_Compare-Version $installed $latest) -lt 0) {
            $status = 'planned'
        } else {
            $status = 'up_to_date'
        }

        $itemArgs = @{
            Sidecar = $sidecar; Id = $pkg; Name = $pkg
            Category = 'pip'; SourceType = 'pip'; Status = $status
        }
        if ($installed) { $itemArgs['CurrentVersion'] = $installed }
        if ($latest)    { $itemArgs['TargetVersion']  = $latest }
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
            -Text ("Found {0} planned + {1} missing pip package(s)." `
                -f $plannedCount, $missingCount)
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try { Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text ("Phase failed: {0}" -f $errMsg) } catch {}
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' `
                -Name 'check phase error' -Category 'pip' -SourceType 'pip' `
                -Status 'failed' | Out-Null
        } catch {}
        try { [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir) } catch {}
    }
    [Console]::Error.WriteLine("pip/check.ps1 FAILED: $errMsg")
    exit 1
}
