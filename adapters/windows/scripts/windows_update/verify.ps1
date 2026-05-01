# =============================================================================
# adapters/windows/scripts/windows_update/verify.ps1 - WU verify phase
# =============================================================================
#
# Post-apply validation. Reads apply__windows_update.json from the same run
# directory, re-runs Get-PendingWindowsUpdates, and reports each previously
# applied KB as:
#   * 'success' if it's no longer pending (verified installed)
#   * 'failed'  if it's still pending (apply didn't take effect)
#
# Soft-degrades to a no-op when no apply sidecar is present.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Windows Update VERIFY phase - confirm apply's outcomes match WU state.

.DESCRIPTION
    Reads <OutputDir>/<RunId>/apply__windows_update.json (if present),
    re-queries pending updates via PSWindowsUpdate, and emits one verify
    item per apply item. KBs that no longer appear in the pending list
    are treated as installed (status='success'); KBs still present are
    flagged as failed (apply didn't actually install them).

.PARAMETER RunId
.PARAMETER Trigger
.PARAMETER ProfileName
.PARAMETER OutputDir
.PARAMETER DryRun
.PARAMETER ItemFilter
    See check.ps1 for parameter docs.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/verify__windows_update.json.
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

function Read-ApplySidecar {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $ApplyPath)
    if (-not (Test-Path -LiteralPath $ApplyPath)) { return $null }
    try {
        $raw = [System.IO.File]::ReadAllText($ApplyPath, [System.Text.UTF8Encoding]::new($false))
        return ($raw | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        Write-Verbose "Read-ApplySidecar parse failed: $_"
        return $null
    }
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
        'phase'='verify';'category'='windows_update'
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
    $finalPath = Join-Path $runDir 'verify__windows_update.json'
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
        -DryRun $DryRun -Phase 'verify' -Category 'windows_update' `
        -ToolName 'pswindowsupdate' -ToolVersion $toolVersion

    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    # Locate apply sidecar.
    $runIdNorm = ([Guid]::Parse($RunId)).ToString()
    $applyPath = Join-Path (Join-Path $OutputDir $runIdNorm) 'apply__windows_update.json'
    $applySidecar = Read-ApplySidecar -ApplyPath $applyPath

    if ($null -eq $applySidecar) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("No apply sidecar found at {0}; verify is a no-op." -f $applyPath)
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    # Build set of currently-pending KBs (post-apply state).
    $pendingKbs = @{}
    if (Test-PSWindowsUpdateAvailable) {
        $pending = @()
        try { $pending = @(Get-PendingWindowsUpdates -ErrorAction Stop) } catch {
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("Get-PendingWindowsUpdates failed: {0}" -f $_.Exception.Message)
        }
        foreach ($u in $pending) {
            if ($u -and $u.KB) { $pendingKbs[[string]$u.KB] = $true }
        }
    } else {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text 'PSWindowsUpdate module not available for verify; cannot re-query pending updates.'
    }

    # Walk apply items and produce verify items.
    $applyItems = @()
    if ($applySidecar.PSObject.Properties['items'] -and $null -ne $applySidecar.items) {
        $applyItems = @($applySidecar.items)
    }

    foreach ($applyItem in $applyItems) {
        if (-not $applyItem) { continue }
        if (-not $applyItem.PSObject.Properties['id']) { continue }
        $id = [string]$applyItem.id
        if (-not $id) { continue }
        if ($id.StartsWith('__') -and $id.EndsWith('__')) { continue }
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $id)) { continue }

        $name = if ($applyItem.PSObject.Properties['name'] -and $applyItem.name) {
            [string]$applyItem.name
        } else { $id }

        $applyStatus = if ($applyItem.PSObject.Properties['status'] -and $applyItem.status) {
            [string]$applyItem.status
        } else { 'unknown' }

        $verifyStatus = 'success'
        $verifyMessages = @()

        if ($applyStatus -eq 'success') {
            if ($pendingKbs.ContainsKey($id)) {
                $verifyStatus = 'failed'
                $verifyMessages += @{
                    level = 'error'
                    text  = ('{0} still appears in Get-PendingWindowsUpdates after apply.' -f $id)
                }
            } else {
                $verifyStatus = 'success'
                $verifyMessages += @{
                    level = 'info'
                    text  = ('{0} no longer pending; verified installed.' -f $id)
                }
            }
        } elseif ($applyStatus -eq 'partial') {
            # 'partial' means downloaded but not installed - still pending.
            if ($pendingKbs.ContainsKey($id)) {
                $verifyStatus = 'failed'
                $verifyMessages += @{
                    level = 'warn'
                    text  = ('{0} still pending (apply reported partial).' -f $id)
                }
            } else {
                $verifyStatus = 'success'
                $verifyMessages += @{
                    level = 'info'
                    text  = ('{0} cleared from pending list since partial apply.' -f $id)
                }
            }
        } elseif ($applyStatus -eq 'failed') {
            $verifyStatus = 'failed'
            $verifyMessages += @{
                level = 'warn'
                text  = 'Apply failed; verify confirms not installed.'
            }
        } elseif ($applyStatus -eq 'skipped' -or $applyStatus -eq 'planned') {
            $verifyStatus = 'skipped'
            $verifyMessages += @{
                level = 'info'
                text  = ("Apply status '{0}' has no verify action." -f $applyStatus)
            }
        } else {
            $verifyStatus = 'skipped'
            $verifyMessages += @{
                level = 'info'
                text  = ("Apply status '{0}' has no verify action." -f $applyStatus)
            }
        }

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = $id
            Name       = $name
            Category   = 'windows_update'
            SourceType = 'windows_update'
            Status     = $verifyStatus
            Messages   = $verifyMessages
        }
        [void](Add-SidecarItem @itemArgs)
    }

    $verifySuccess = @($sidecar['items'] | Where-Object { $_['status'] -eq 'success' }).Count
    $verifyFailed  = @($sidecar['items'] | Where-Object { $_['status'] -eq 'failed' }).Count
    $verifySkipped = @($sidecar['items'] | Where-Object { $_['status'] -eq 'skipped' }).Count
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ('Verify: {0} verified, {1} failed, {2} skipped.' -f $verifySuccess, $verifyFailed, $verifySkipped)

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try { Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text ("Phase failed: {0}" -f $errMsg) } catch { }
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' -Name 'verify phase error' `
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
    [Console]::Error.WriteLine("verify__windows_update.ps1 FAILED: $errMsg")
    exit 1
}
