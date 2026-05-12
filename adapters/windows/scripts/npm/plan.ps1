<#
.SYNOPSIS
    npm plan phase. Side-effect-free. Emits only the packages that would
    actually change on apply (skips up_to_date, includes missing + planned).
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
Import-Module (Join-Path $LibDir 'AscendoNpm.psm1')  -Force -DisableNameChecking

$ManifestPath = Join-Path (Join-Path $AdapterDir 'config') 'npm_global_clis.txt'

function _Compare-Version {
    param([string]$Installed, [string]$Latest)
    if (-not $Installed -or -not $Latest) { return 0 }
    try {
        $a = $Installed.TrimStart('v','V')
        $b = $Latest.TrimStart('v','V')
        $aClean = ($a -split '[^0-9.]')[0]
        $bClean = ($b -split '[^0-9.]')[0]
        if (-not $aClean -or -not $bClean) { return 0 }
        return ([version]$aClean).CompareTo([version]$bClean)
    } catch { return 0 }
}

$sidecar = $null
try {
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
        RunId = $RunId; Trigger = $Trigger; ProfileName = $ProfileName
        DryRun = [bool]$DryRun; Phase = 'plan'; Category = 'npm'
        ToolName = 'npm'; ToolVersion = $toolVersion
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

    $manifest = Read-AscendoNpmManifest -Path $ManifestPath
    foreach ($pkg in $manifest) {
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg)) { continue }
        $installed = Get-AscendoNpmInstalledVersion -PackageName $pkg
        $latest    = Get-AscendoNpmLatestVersion    -PackageName $pkg

        # plan emits only what apply would touch.
        if (-not $installed) {
            $status = 'missing'
        } elseif ($latest -and (_Compare-Version $installed $latest) -lt 0) {
            $status = 'planned'
        } else {
            continue  # up_to_date - not in plan
        }

        $itemArgs = @{
            Sidecar = $sidecar; Id = $pkg; Name = $pkg
            Category = 'npm'; SourceType = 'npm'; Status = $status
        }
        if ($installed) { $itemArgs['CurrentVersion'] = $installed }
        if ($latest)    { $itemArgs['TargetVersion']  = $latest }
        [void](Add-SidecarItem @itemArgs)
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try { Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text ("Phase failed: {0}" -f $errMsg) } catch {}
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' `
                -Name 'plan phase error' -Category 'npm' -SourceType 'npm' `
                -Status 'failed' | Out-Null
        } catch {}
        try { [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir) } catch {}
    }
    [Console]::Error.WriteLine("npm/plan.ps1 FAILED: $errMsg")
    exit 1
}
