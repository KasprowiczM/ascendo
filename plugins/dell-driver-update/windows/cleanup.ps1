<#
.SYNOPSIS
  Dell Driver Update - cleanup phase. No-op (DCU manages its own staging
  cache under %LOCALAPPDATA%\Dell\CommandUpdate; we don't prune it).

.DESCRIPTION
  StrictMode-safe rewrite (per design spec A4). Emits an empty-success
  sidecar so the orchestrator's per-phase inventory stays consistent
  across categories. summary.total = 0, status='success'.
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

$sidecar = $null
try {
    $dcuPath    = Get-DcuPath
    $dcuVersion = if ($dcuPath) { Get-DcuVersion -DcuPath $dcuPath } else { 'unknown' }

    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = [bool]$DryRun
        Phase       = 'cleanup'
        Category    = 'plugin'
        ToolName    = 'dcu-cli'
        ToolVersion = $dcuVersion
    }
    if ($null -ne $dcuPath -and $dcuPath -ne '') {
        $newSidecarArgs['ToolBinaryPath'] = $dcuPath
    }
    $sidecar = New-Sidecar @newSidecarArgs

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text 'no cleanup needed for dell-driver-update (DCU manages its own staging)'

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
}
catch {
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("dell_driver_update cleanup infrastructure failure: {0}" -f $_.Exception.Message)
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch { }
    }
    [Console]::Error.WriteLine("cleanup.ps1 (dell_driver_update) FAILED: $($_.Exception.Message)")
    exit 1
}
