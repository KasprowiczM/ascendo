# =============================================================================
# adapters/windows/scripts/windows_update/check.ps1 - WU check phase
# =============================================================================
#
# Read-only enumeration of pending Windows OS updates via PSWindowsUpdate.
# Emits ascendo/v1 sidecar to <OutputDir>/<RunId>/check__windows_update.json.
#
# Invoked by ascendo_windows.WindowsUpdateManager.run_phase(Phase.CHECK, ...).
# Mirrors the structure of scripts/winget/check.ps1.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Windows Update CHECK phase - read-only enumeration of pending KBs.

.DESCRIPTION
    Spawned by the Python WindowsUpdateManager. Performs a non-mutating
    sweep of pending OS updates via PSWindowsUpdate's Get-WindowsUpdate
    cmdlet and reports each as one item entry.

    Per the M3.10 contract, every pending update is emitted with
    status='success' (where 'success' here means "successfully discovered
    a pending update"; the apply phase is what flips status to actual
    install success/failure).

    On hosts without PSWindowsUpdate installed, the script emits zero
    items + a phase-level [warn] message documenting the install path:
        Install-Module PSWindowsUpdate -Scope CurrentUser -Force

.PARAMETER RunId
    UUID string from the orchestrator. Must round-trip through [Guid].

.PARAMETER Trigger
    'cli' | 'scheduler' | 'dashboard' | 'plugin' | 'unknown'.

.PARAMETER ProfileName
    Profile slug. Aliased to 'Profile' so the Python caller's
    '-Profile <slug>' binds here.

.PARAMETER OutputDir
    Base directory under which the run subdirectory is created.
    Sidecar lands at <OutputDir>/<RunId>/check__windows_update.json.

.PARAMETER DryRun
    Switch. Recorded in run.dry_run. Check is read-only so the value is
    informational only.

.PARAMETER ItemFilter
    Comma-separated KB ids ('KB5034441,KB5037997'). Empty = no filter.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/check__windows_update.json.

.NOTES
    PowerShell 5.1 + 7.x compatible. No PS 7+ syntax used.
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

# -----------------------------------------------------------------------------
# Module imports (sibling lib/ directory)
# -----------------------------------------------------------------------------

$ScriptDir = Split-Path -Parent $PSCommandPath
# scripts/windows_update/check.ps1 -> adapters/windows/scripts -> adapters/windows
$WindowsAdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $WindowsAdapterDir 'lib'

Import-Module (Join-Path $LibDir 'AscendoJson.psm1')             -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoPSWindowsUpdate.psm1')  -Force -DisableNameChecking

# -----------------------------------------------------------------------------
# Inline helpers
# -----------------------------------------------------------------------------

function Get-PSWUVersion {
    <#
    .SYNOPSIS
        Returns the PSWindowsUpdate module version, or 'unknown' if missing.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    try {
        $mod = Get-Module -ListAvailable -Name PSWindowsUpdate -ErrorAction SilentlyContinue |
            Sort-Object Version -Descending | Select-Object -First 1
        if ($null -ne $mod -and $mod.Version) { return [string]$mod.Version }
        return 'unknown'
    } catch {
        Write-Verbose "Get-PSWUVersion: failed: $_"
        return 'unknown'
    }
}

function Write-FailureSidecarSynthetic {
    <#
    .SYNOPSIS
        Minimal-but-valid v1 sidecar fallback for the case where New-Sidecar
        itself threw before $sidecar was bound.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RunId,
        [Parameter(Mandatory)] [string] $OutputDir,
        [Parameter(Mandatory)] [string] $ErrorMessage,
        [Parameter()] [string] $Phase = 'check'
    )

    $parsed = [Guid]::Empty
    if (-not [Guid]::TryParse($RunId, [ref]$parsed)) {
        $runIdNorm = '00000000-0000-0000-0000-000000000000'
    } else {
        $runIdNorm = $parsed.ToString()
    }

    $now = [DateTime]::UtcNow.ToString(
        'yyyy-MM-ddTHH:mm:ss.ffffffZ',
        [System.Globalization.CultureInfo]::InvariantCulture)

    $hostInfo = $null
    try {
        $hostInfo = Get-AscendoHostInfo
    } catch {
        $hostInfo = [ordered]@{
            'hostname'         = $env:COMPUTERNAME
            'os'               = 'windows'
            'os_version'       = 'unknown'
            'arch'             = 'unknown'
            'user'             = $env:USERNAME
            'is_elevated'      = $false
            'elevation_method' = 'none'
            'locale'           = $null
        }
    }

    $payload = [ordered]@{
        'schema'      = 'ascendo/v1'
        'run'         = [ordered]@{
            'id'          = $runIdNorm
            'trigger'     = 'unknown'
            'profile'     = 'unknown'
            'dry_run'     = $false
            'started_at'  = $now
            'finished_at' = $now
            'invocation'  = $null
        }
        'host'        = $hostInfo
        'tool'        = [ordered]@{
            'name'        = 'pswindowsupdate'
            'version'     = 'unknown'
            'binary_path' = $null
        }
        'phase'       = $Phase
        'category'    = 'windows_update'
        'started_at'  = $now
        'finished_at' = $now
        'status'      = 'failed'
        'items'       = @()
        'summary'     = [ordered]@{
            'total'       = 0
            'success'     = 0
            'up_to_date'  = 0
            'failed'      = 0
            'skipped'     = 0
            'planned'     = 0
            'partial'     = 0
            'duration_ms' = $null
            'exit_code'   = 1
        }
        'messages'    = @(
            [ordered]@{
                'level'     = 'error'
                'text'      = "Phase failed before sidecar was initialised: $ErrorMessage"
                'timestamp' = $now
            }
        )
    }

    $runDir = Join-Path $OutputDir $runIdNorm
    if (-not (Test-Path -LiteralPath $runDir)) {
        New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    }
    $finalPath = Join-Path $runDir ('{0}__windows_update.json' -f $Phase)

    $json = $payload | ConvertTo-Json -Depth 100
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($finalPath, $json, $utf8NoBom)
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

$sidecar = $null

try {
    # 1. Initialise sidecar
    $toolVersion = Get-PSWUVersion

    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = $DryRun
        Phase       = 'check'
        Category    = 'windows_update'
        ToolName    = 'pswindowsupdate'
        ToolVersion = $toolVersion
    }
    $sidecar = New-Sidecar @newSidecarArgs

    # 2. Parse the item filter (comma-separated KB ids).
    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    # 3. Module availability gate.
    if (-not (Test-PSWindowsUpdateAvailable)) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ('PSWindowsUpdate module not installed. To enable Windows Update ' +
                   'maintenance, run: Install-Module PSWindowsUpdate -Scope CurrentUser -Force')
        $written = Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir
        Write-Verbose ("Wrote (no-PSWU) sidecar: {0}" -f $written.FullName)
        exit 0
    }

    # 4. Enumerate pending updates.
    $pending = @()
    try {
        $pending = @(Get-PendingWindowsUpdates -ErrorAction Stop)
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-PendingWindowsUpdates failed: {0}" -f $_.Exception.Message)
    }
    Write-Verbose ("Get-PendingWindowsUpdates returned {0} row(s)" -f $pending.Count)

    # 5. Emit one item per pending update (after filter).
    $emitted = 0
    foreach ($u in $pending) {
        if (-not $u) { continue }
        $kb    = [string]$u.KB
        $title = if ($u.Title) { [string]$u.Title } else { $kb }
        if (-not $kb) { continue }

        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $kb)) {
            continue
        }

        $messages = @()
        $detail = @()
        if ($u.Size)             { $detail += ('size {0}' -f $u.Size) }
        if ($u.Severity)         { $detail += ('severity {0}' -f $u.Severity) }
        if ($u.IsDownloaded)     { $detail += 'already downloaded' }
        if ($u.IsRebootRequired) { $detail += 'reboot required' }
        if ($detail.Count -gt 0) {
            $messages += @{ level = 'info'; text = ('Pending update: ' + ($detail -join ', ')) }
        }

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = $kb
            Name       = $title
            Category   = 'windows_update'
            SourceType = 'windows_update'
            # Per spec: 'success' marks this entry as a successfully discovered
            # pending update (the apply phase is what reports actual install
            # success/failure).
            Status     = 'success'
        }
        if ($messages.Count -gt 0) { $itemArgs['Messages'] = $messages }

        [void](Add-SidecarItem @itemArgs)
        $emitted++
    }

    # 6. Phase-level info message.
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ('Found {0} pending Windows update(s).' -f $emitted)

    if ($null -ne $itemFilterArray) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("ItemFilter applied: {0} ID(s) requested, {1} item(s) emitted." -f
                $itemFilterArray.Count, $emitted)
    }

    # 7. Save sidecar atomically.
    $written = Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir
    Write-Verbose ("Wrote sidecar: {0}" -f $written.FullName)

    exit 0
} catch {
    $errMsg = $_.Exception.Message

    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("Phase failed: {0}" -f $errMsg)
        } catch {
            # Best-effort
        }

        try {
            Add-SidecarItem -Sidecar $sidecar `
                -Id   '__phase_error__' `
                -Name 'check phase error' `
                -Category 'windows_update' -SourceType 'windows_update' `
                -Status 'failed' | Out-Null
        } catch {
            # Best-effort
        }

        try {
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {
            try {
                Write-FailureSidecarSynthetic -RunId $RunId `
                    -OutputDir $OutputDir -ErrorMessage $errMsg -Phase 'check'
            } catch {
                # Last resort failed; let original exception surface.
            }
        }
    } else {
        try {
            Write-FailureSidecarSynthetic -RunId $RunId `
                -OutputDir $OutputDir -ErrorMessage $errMsg -Phase 'check'
        } catch {
            # Even the synthetic write failed.
        }
    }

    [Console]::Error.WriteLine("check__windows_update.ps1 FAILED: $errMsg")
    exit 1
}
