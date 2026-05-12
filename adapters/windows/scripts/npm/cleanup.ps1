<#
.SYNOPSIS
    npm cleanup phase. No-op for now — `npm cache clean --force` would
    destroy cached tarballs that the user might still need for offline
    re-installs. Emit an empty success sidecar so the orchestrator's
    5-phase contract is satisfied.
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
        DryRun = [bool]$DryRun; Phase = 'cleanup'; Category = 'npm'
        ToolName = 'npm'; ToolVersion = $toolVersion
    }
    if ($toolBinaryPath) { $newSidecarArgs['ToolBinaryPath'] = $toolBinaryPath }
    $sidecar = New-Sidecar @newSidecarArgs

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text "npm cleanup is a no-op (cache pruning would impact offline re-installs)."

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try { Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text ("Phase failed: {0}" -f $errMsg) } catch {}
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' `
                -Name 'cleanup phase error' -Category 'npm' -SourceType 'npm' `
                -Status 'failed' | Out-Null
        } catch {}
        try { [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir) } catch {}
    }
    [Console]::Error.WriteLine("npm/cleanup.ps1 FAILED: $errMsg")
    exit 1
}
