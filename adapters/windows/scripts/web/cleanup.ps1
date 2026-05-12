<#
.SYNOPSIS
    Web (third-party app) cleanup phase. No-op in v1.

.DESCRIPTION
    v1 apply is Tier-B trigger-only — Ascendo never downloads .exe files
    or stages installers in a cache directory. Cleanup is therefore a
    structural no-op: emit a sidecar with status=success, no items, and
    a single info message documenting that there's nothing to prune yet.

    When apply graduates to Tier-A (downloads + runs .exe installers),
    cleanup will prune the staging cache here.
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
    [Parameter()] [string] $ItemFilter = '',
    [Parameter()] [string] $ConfigDir = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $PSCommandPath
$AdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $AdapterDir 'lib'
Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking

$sidecar = $null
try {
    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'cleanup'
        Category    = 'web'
        ToolName    = 'ascendo-web'
        ToolVersion = '0.1.0'
    }
    $sidecar = New-Sidecar @newSidecarArgs

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text "Web cleanup is a no-op in v1 (Tier-B trigger-only apply doesn't stage installers)."

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("Phase failed: {0}" -f $errMsg)
            Add-SidecarItem -Sidecar $sidecar `
                -Id '__phase_error__' -Name 'web cleanup error' `
                -Category 'web' -SourceType 'web' -Status 'failed' | Out-Null
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {}
    }
    [Console]::Error.WriteLine("cleanup__web.ps1 FAILED: $errMsg")
    exit 1
}
