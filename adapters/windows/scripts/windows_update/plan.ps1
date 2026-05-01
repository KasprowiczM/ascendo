# =============================================================================
# adapters/windows/scripts/windows_update/plan.ps1 - WU plan phase
# =============================================================================
#
# Side-effect-free enumeration of the KBs the apply phase WOULD install.
# Mirrors check.ps1 except items are emitted with status='planned'.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Windows Update PLAN phase - enumerate what apply would install.

.DESCRIPTION
    Same logic as check.ps1 but emits items with status='planned' so the
    orchestrator and dashboard can render the upcoming work without
    triggering it. Read-only.

.PARAMETER RunId
.PARAMETER Trigger
.PARAMETER ProfileName
.PARAMETER OutputDir
.PARAMETER DryRun
.PARAMETER ItemFilter
    See check.ps1 for parameter docs - identical contract.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/plan__windows_update.json.
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
        'phase'='plan';'category'='windows_update'
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
    $finalPath = Join-Path $runDir 'plan__windows_update.json'
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
        -DryRun $DryRun -Phase 'plan' -Category 'windows_update' `
        -ToolName 'pswindowsupdate' -ToolVersion $toolVersion

    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    if (-not (Test-PSWindowsUpdateAvailable)) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ('PSWindowsUpdate module not installed. To enable Windows Update ' +
                   'maintenance, run: Install-Module PSWindowsUpdate -Scope CurrentUser -Force')
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    $pending = @()
    try {
        $pending = @(Get-PendingWindowsUpdates -ErrorAction Stop)
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-PendingWindowsUpdates failed: {0}" -f $_.Exception.Message)
    }

    $emitted = 0
    foreach ($u in $pending) {
        if (-not $u) { continue }
        $kb    = [string]$u.KB
        $title = if ($u.Title) { [string]$u.Title } else { $kb }
        if (-not $kb) { continue }
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $kb)) { continue }

        $messages = @()
        $detail = @()
        if ($u.Size)             { $detail += ('size {0}' -f $u.Size) }
        if ($u.Severity)         { $detail += ('severity {0}' -f $u.Severity) }
        if ($u.IsDownloaded)     { $detail += 'already downloaded' }
        if ($u.IsRebootRequired) { $detail += 'reboot required' }
        if ($detail.Count -gt 0) {
            $messages += @{ level = 'info'; text = ('Will install: ' + ($detail -join ', ')) }
        }

        # Rollback for KB removal goes via wusa.exe /uninstall; PSWindowsUpdate
        # has no generic rollback helper. Document the recipe in the rollback
        # block so the operator can paste it after a bad patch.
        $kbNumeric = if ($kb -match '^KB(\d+)$') { $matches[1] } else { $null }
        $rollback = @{ available = $false }
        if ($kbNumeric) {
            $rollback = @{
                available = $true
                method    = ('wusa.exe /uninstall /kb:{0} /quiet /norestart' -f $kbNumeric)
            }
        }

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = $kb
            Name       = $title
            Category   = 'windows_update'
            SourceType = 'windows_update'
            Status     = 'planned'
            Rollback   = $rollback
        }
        if ($messages.Count -gt 0) { $itemArgs['Messages'] = $messages }

        [void](Add-SidecarItem @itemArgs)
        $emitted++
    }

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ('Plan: {0} Windows update(s) would be installed.' -f $emitted)
    if ($null -ne $itemFilterArray) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("ItemFilter applied: {0} ID(s) requested, {1} item(s) emitted." -f
                $itemFilterArray.Count, $emitted)
    }

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text ("Phase failed: {0}" -f $errMsg)
        } catch { }
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' -Name 'plan phase error' `
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
    [Console]::Error.WriteLine("plan__windows_update.ps1 FAILED: $errMsg")
    exit 1
}
