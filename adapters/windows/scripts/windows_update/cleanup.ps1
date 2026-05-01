# =============================================================================
# adapters/windows/scripts/windows_update/cleanup.ps1 - WU cleanup phase
# =============================================================================
#
# Bounded post-run housekeeping for Windows Update:
#   * Reports what Cleanup-WindowsUpdate (PSWindowsUpdate's superseded-update
#     pruner) WOULD remove from C:\Windows\SoftwareDistribution.
#   * In non-DryRun mode, runs Cleanup-WindowsUpdate -SuperSeded -SuperSededOnSafe.
#
# Default behaviour is dry-run-only: even when -DryRun is omitted, this phase
# only emits informational items unless an operator explicitly opts in by
# setting the env var ASCENDO_WU_CLEANUP_APPLY=1. This is a safety net because
# Cleanup-WindowsUpdate is a destructive operation that is not reversible.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Windows Update CLEANUP phase - prune superseded update payloads.

.DESCRIPTION
    Reports (and optionally executes) PSWindowsUpdate's Cleanup-WindowsUpdate
    -SuperSeded operation. Default is dry-run-style behaviour; set the
    env var ASCENDO_WU_CLEANUP_APPLY=1 (or pass an external opt-in) to run
    the cleanup for real. -DryRun forces the dry-run path regardless.

.PARAMETER RunId
.PARAMETER Trigger
.PARAMETER ProfileName
.PARAMETER OutputDir
.PARAMETER DryRun
.PARAMETER ItemFilter
    See check.ps1 for parameter docs.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/cleanup__windows_update.json.
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

$ScriptDir = Split-Path -Parent $PSCommandPath
$WindowsAdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $WindowsAdapterDir 'lib'

Import-Module (Join-Path $LibDir 'AscendoJson.psm1')             -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoPSWindowsUpdate.psm1')  -Force -DisableNameChecking

function Get-PSWUVersion {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    try {
        $mod = Get-Module -ListAvailable -Name PSWindowsUpdate -ErrorAction SilentlyContinue |
            Sort-Object Version -Descending | Select-Object -First 1
        if ($null -ne $mod -and $mod.Version) { return [string]$mod.Version }
        return 'unknown'
    } catch { return 'unknown' }
}

function Write-FailureSidecarSynthetic {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RunId,
        [Parameter(Mandatory)] [string] $OutputDir,
        [Parameter(Mandatory)] [string] $ErrorMessage
    )
    $parsed = [Guid]::Empty
    if (-not [Guid]::TryParse($RunId, [ref]$parsed)) {
        $runIdNorm = '00000000-0000-0000-0000-000000000000'
    } else { $runIdNorm = $parsed.ToString() }

    $now = [DateTime]::UtcNow.ToString(
        'yyyy-MM-ddTHH:mm:ss.ffffffZ',
        [System.Globalization.CultureInfo]::InvariantCulture)

    $hostInfo = $null
    try { $hostInfo = Get-AscendoHostInfo } catch {
        $hostInfo = [ordered]@{
            'hostname'='unknown';'os'='windows';'os_version'='unknown';
            'arch'='unknown';'user'='unknown';'is_elevated'=$false;
            'elevation_method'='none';'locale'=$null
        }
    }

    $payload = [ordered]@{
        'schema'='ascendo/v1'
        'run' = [ordered]@{
            'id'=$runIdNorm;'trigger'='unknown';'profile'='unknown';
            'dry_run'=$false;'started_at'=$now;'finished_at'=$now;'invocation'=$null
        }
        'host'=$hostInfo
        'tool' = [ordered]@{ 'name'='pswindowsupdate';'version'='unknown';'binary_path'=$null }
        'phase'='cleanup';'category'='windows_update'
        'started_at'=$now;'finished_at'=$now;'status'='failed'
        'items'=@()
        'summary' = [ordered]@{
            'total'=0;'success'=0;'up_to_date'=0;'failed'=0;'skipped'=0;
            'planned'=0;'partial'=0;'duration_ms'=$null;'exit_code'=1
        }
        'messages' = @(
            [ordered]@{ 'level'='error';'text'="Phase failed before sidecar was initialised: $ErrorMessage";'timestamp'=$now }
        )
    }

    $runDir = Join-Path $OutputDir $runIdNorm
    if (-not (Test-Path -LiteralPath $runDir)) {
        New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    }
    $finalPath = Join-Path $runDir 'cleanup__windows_update.json'
    $json = $payload | ConvertTo-Json -Depth 100
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($finalPath, $json, $utf8NoBom)
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

$sidecar = $null

try {
    $toolVersion = Get-PSWUVersion

    $sidecar = New-Sidecar -RunId $RunId -Trigger $Trigger -ProfileName $ProfileName `
        -DryRun $DryRun -Phase 'cleanup' -Category 'windows_update' `
        -ToolName 'pswindowsupdate' -ToolVersion $toolVersion

    if (-not (Test-PSWindowsUpdateAvailable)) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ('PSWindowsUpdate module not installed; nothing to clean up. ' +
                   'Install via: Install-Module PSWindowsUpdate -Scope CurrentUser -Force')
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    # Effective dry-run mode: -DryRun OR opt-in env var unset.
    $applyOptIn = ($env:ASCENDO_WU_CLEANUP_APPLY -eq '1')
    $effectiveDry = ($DryRun -or -not $applyOptIn)

    $cleanupId   = 'pswu.cleanup-superseded'
    $cleanupName = 'Cleanup-WindowsUpdate -SuperSeded'

    if ($effectiveDry) {
        $messages = @()
        if ($DryRun) {
            $messages += @{ level='info'; text='DryRun: Cleanup-WindowsUpdate would remove superseded payloads from SoftwareDistribution cache.' }
        } else {
            $messages += @{ level='info'; text='Default dry-run: set env ASCENDO_WU_CLEANUP_APPLY=1 to run Cleanup-WindowsUpdate for real.' }
        }
        [void](Add-SidecarItem -Sidecar $sidecar `
            -Id $cleanupId -Name $cleanupName `
            -Category 'windows_update' -SourceType 'windows_update' `
            -Status 'planned' -Messages $messages)
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text 'Cleanup (dry-run): no changes applied.'
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    # Real cleanup path - explicitly opted in.
    try {
        Import-Module PSWindowsUpdate -Force -ErrorAction Stop | Out-Null
        # PSWindowsUpdate's pruner. -SuperSededOnSafe restricts to payloads
        # that are safe to remove (won't be needed for a downgrade).
        & Cleanup-WindowsUpdate -SuperSeded -SuperSededOnSafe -Confirm:$false -ErrorAction Stop | Out-Null
        [void](Add-SidecarItem -Sidecar $sidecar `
            -Id $cleanupId -Name $cleanupName `
            -Category 'windows_update' -SourceType 'windows_update' `
            -Status 'success' `
            -Messages @( @{ level='info'; text='Cleanup-WindowsUpdate -SuperSeded completed.' } ))
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text 'Cleanup: superseded Windows Update payloads pruned.'
    } catch {
        [void](Add-SidecarItem -Sidecar $sidecar `
            -Id $cleanupId -Name $cleanupName `
            -Category 'windows_update' -SourceType 'windows_update' `
            -Status 'failed' `
            -Messages @( @{ level='warn'; text=("Cleanup-WindowsUpdate failed: {0}" -f $_.Exception.Message) } ))
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try { Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text ("Phase failed: {0}" -f $errMsg) } catch { }
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' -Name 'cleanup phase error' `
                -Category 'windows_update' -SourceType 'windows_update' -Status 'failed' | Out-Null
        } catch { }
        try {
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {
            try { Write-FailureSidecarSynthetic -RunId $RunId -OutputDir $OutputDir -ErrorMessage $errMsg } catch { }
        }
    } else {
        try { Write-FailureSidecarSynthetic -RunId $RunId -OutputDir $OutputDir -ErrorMessage $errMsg } catch { }
    }
    [Console]::Error.WriteLine("cleanup__windows_update.ps1 FAILED: $errMsg")
    exit 1
}
