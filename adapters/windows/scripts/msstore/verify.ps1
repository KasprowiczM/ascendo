<#
.SYNOPSIS
  Microsoft Store verify phase. Re-runs upgrade enumeration; any package
  still listed as upgradable is a verify failure.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RunId,
    [Parameter(Mandatory)] [ValidateSet('cli','scheduler','dashboard','plugin','unknown')] [string] $Trigger,
    [Parameter(Mandatory)] [Alias('Profile')] [string] $ProfileName,
    [Parameter(Mandatory)] [string] $OutputDir,
    [Parameter()] [switch] $DryRun,
    [Parameter()] [string] $ItemFilter = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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
    $_sidecarArgs = @{
        RunId = $RunId; Trigger = $Trigger; ProfileName = $ProfileName
        DryRun = [bool]$DryRun; Phase = 'verify'; Category = 'msstore'
        ToolName = 'winget'; ToolVersion = (_Get-WingetVersionString)
    }
    $sidecar = New-Sidecar @_sidecarArgs
    $prevEnc = Initialize-WingetEnvironment

    $filterIds = @()
    if ($ItemFilter -and $ItemFilter.Trim() -ne '') {
        $filterIds = @($ItemFilter -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }

    $upgradable = @()
    try { $_all = @(Get-WingetUpgradable -ErrorAction Stop); $upgradable = @($_all | Where-Object { $_.PSObject.Properties['Source'] -and $_.Source -and ($_.Source -ieq 'msstore') }) } catch {}

    foreach ($u in $upgradable) {
        if (-not $u.Id) { continue }
        if ($filterIds.Count -gt 0 -and ($filterIds -notcontains $u.Id)) { continue }
        $args = @{
            Sidecar = $sidecar; Id = [string]$u.Id; Name = [string]$u.Name
            Category = 'msstore'; SourceType = 'msstore'; SourceFeed = 'msstore'
            Status = 'failed'
        }
        if ($u.PSObject.Properties['Version']  -and $u.Version)  { $args['CurrentVersion'] = [string]$u.Version }
        if ($u.PSObject.Properties['Available'] -and $u.Available) { $args['TargetVersion']  = [string]$u.Available }
        [void](Add-SidecarItem @args)
    }
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("msstore verify: {0} packages still pending upgrade" -f $upgradable.Count)
    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
                        Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("msstore verify infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {}
    }
    exit 1
}
finally {
    if ($null -ne $prevEnc) { try { Restore-WingetEnvironment -Previous $prevEnc } catch {} }
}
