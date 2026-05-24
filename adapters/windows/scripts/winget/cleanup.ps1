# =============================================================================
# adapters/windows/scripts/winget/cleanup.ps1 - winget cleanup phase
# =============================================================================
#
# Bounded post-run housekeeping. Trivial in MVP:
#   1. Reset the winget source cache (winget source reset --force).
#   2. Prune log files older than $LOG_RETAIN_DAYS in the orchestrator's
#      Logs/ directory next to OutputDir.
#
# Each step is best-effort: a failure produces a sidecar item with
# status='failed' and a warn message, but the phase as a whole still
# finishes. Honours -DryRun: in dry-run mode no actual deletion / reset
# is performed; instead status='planned' items describe what would happen.
#
# Emits ascendo/v1 sidecar to <OutputDir>/<RunId>/cleanup__winget.json.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Winget CLEANUP phase - bounded housekeeping after a run.

.DESCRIPTION
    Spawned by the Python WingetManager at the end of a run. Two passes:

      A. Source cache reset
         `winget source reset --force --disable-interactivity` -- best-effort.
         Sidecar item id='winget.source-reset' captures the outcome.

      B. Old log retention
         Scans <OutputDir>/../Logs (the runtime log directory, mirroring
         Ascendo\0_Run-Maintenance.ps1 LOG_RETAIN_DAYS=60).
         Files older than $LogRetainDays are deleted. One sidecar item per
         deleted file; status='success' on delete, status='failed' on error.
         Whole pass is wrapped in try/catch -- a Logs/ directory that
         doesn't exist or isn't readable produces a single info message
         and zero items, not a phase failure.

    DryRun mode: both passes emit status='planned' items describing the
    actions instead of performing them. winget source reset is also
    skipped (no actual side effect runs on dry-run).

.PARAMETER RunId
    UUID string from the orchestrator. Must round-trip through [Guid].

.PARAMETER Trigger
    'cli' | 'scheduler' | 'dashboard' | 'plugin' | 'unknown'.

.PARAMETER ProfileName
    Profile slug. Aliased to 'Profile' so Python WingetManager._build_argv
    (which passes '-Profile') binds here.

.PARAMETER OutputDir
    Base directory under which the run subdirectory is created.
    Sidecar lands at <OutputDir>/<RunId>/cleanup__winget.json.
    Logs/ is resolved as <OutputDir>/../Logs.

.PARAMETER DryRun
    Bool. When $true, the source reset and log deletes are skipped; the
    sidecar instead records status='planned' for each action that would
    have run.

.PARAMETER ItemFilter
    Comma-separated package IDs. Empty string = no filter (default).
    Cleanup is not per-package, so this parameter is accepted for
    interface compatibility but only filters the synthetic item ids
    ('winget.source-reset' and 'winget.log-prune.*') if explicitly listed.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/cleanup__winget.json.

.EXAMPLE
    pwsh -File cleanup.ps1 -RunId 11111111-2222-3333-4444-555555555555 `
        -Trigger cli -Profile full -OutputDir C:\Temp\runs

.NOTES
    PowerShell 5.1 + 7.x compatible. No PS 7+ syntax used.
    LOG_RETAIN_DAYS=60 sourced from Ascendo\0_Run-Maintenance.ps1.
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

    # NB: see check.ps1 for why this is [switch] rather than [bool].
    [Parameter()] [switch] $DryRun,
    [Parameter()] [string] $ItemFilter = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Tunables
$LogRetainDays = 60

# -----------------------------------------------------------------------------
# Module imports (sibling lib/ directory)
# -----------------------------------------------------------------------------

$ScriptDir = Split-Path -Parent $PSCommandPath
# scripts/winget/cleanup.ps1 -> adapters/windows/scripts -> adapters/windows
$WindowsAdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $WindowsAdapterDir 'lib'

Import-Module (Join-Path $LibDir 'AscendoJson.psm1')   -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoWinget.psm1') -Force -DisableNameChecking

# -----------------------------------------------------------------------------
# Inline helpers
# -----------------------------------------------------------------------------

function Get-WingetVersion {
    [CmdletBinding()]
    [OutputType([string])]
    param()

    try {
        $raw = & winget --version 2>$null | Out-String
        if ($null -eq $raw) { return 'unknown' }
        $line = $raw.Trim()
        if (-not $line) { return 'unknown' }
        if ($line.StartsWith('v') -or $line.StartsWith('V')) {
            $line = $line.Substring(1)
        }
        return $line
    } catch {
        Write-Verbose "Get-WingetVersion: failed: $_"
        return 'unknown'
    }
}

function Get-WingetBinaryPath {
    [CmdletBinding()]
    param()

    try {
        $cmd = Get-Command -Name 'winget' -ErrorAction SilentlyContinue
        if ($null -eq $cmd) {
            $cmd = Get-Command -Name 'winget.exe' -ErrorAction SilentlyContinue
        }
        if ($null -ne $cmd -and $cmd.Source) {
            return [string]$cmd.Source
        }
        return $null
    } catch {
        Write-Verbose "Get-WingetBinaryPath: failed: $_"
        return $null
    }
}

function Write-FailureSidecarSynthetic {
    <#
    .SYNOPSIS
        Minimal-but-valid v1 sidecar fallback when New-Sidecar threw.
        Mirrors check.ps1.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RunId,
        [Parameter(Mandatory)] [string] $OutputDir,
        [Parameter(Mandatory)] [string] $ErrorMessage
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
            'name'        = 'winget'
            'version'     = 'unknown'
            'binary_path' = $null
        }
        'phase'       = 'cleanup'
        'category'    = 'winget'
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
    $finalPath = Join-Path $runDir 'cleanup__winget.json'

    $json = $payload | ConvertTo-Json -Depth 100
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($finalPath, $json, $utf8NoBom)
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

$sidecar = $null
$prevEnc = $null

try {
    # 1. Initialise sidecar
    $toolVersion    = Get-WingetVersion
    $toolBinaryPath = Get-WingetBinaryPath

    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = $DryRun
        Phase       = 'cleanup'
        Category    = 'winget'
        ToolName    = 'winget'
        ToolVersion = $toolVersion
    }
    if ($null -ne $toolBinaryPath -and $toolBinaryPath -ne '') {
        $newSidecarArgs['ToolBinaryPath'] = $toolBinaryPath
    }
    $sidecar = New-Sidecar @newSidecarArgs

    # 2. Configure winget output encoding
    $prevEnc = Initialize-WingetEnvironment

    # 3. Parse the item filter
    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }

    # Helper: should we emit this synthetic id?
    $shouldEmit = {
        param($id)
        if ($null -eq $itemFilterArray) { return $true }
        return ($itemFilterArray -contains $id)
    }

    # ---------------------------------------------------------------------
    # Pass A: winget source cache reset (best-effort)
    # ---------------------------------------------------------------------
    $resetId = 'winget.source-reset'
    if (& $shouldEmit $resetId) {
        if ($DryRun) {
            [void](Add-SidecarItem -Sidecar $sidecar `
                -Id $resetId `
                -Name 'winget source cache reset' `
                -Category 'winget' -SourceType 'winget' `
                -Status 'planned' `
                -Messages @( @{ level='info'; text='DryRun: would run "winget source reset --force"' } ))
        } else {
            try {
                & winget source reset --force --disable-interactivity 2>&1 | Out-Null
                [void](Add-SidecarItem -Sidecar $sidecar `
                    -Id $resetId `
                    -Name 'winget source cache reset' `
                    -Category 'winget' -SourceType 'winget' `
                    -Status 'success' `
                    -ExitCode ([Nullable[int]]$LASTEXITCODE))
            } catch {
                [void](Add-SidecarItem -Sidecar $sidecar `
                    -Id $resetId `
                    -Name 'winget source cache reset' `
                    -Category 'winget' -SourceType 'winget' `
                    -Status 'failed' `
                    -Messages @( @{ level='warn'; text=$_.Exception.Message } ))
            }
        }
    }

    # ---------------------------------------------------------------------
    # Pass B: old log retention. Soft-fail the whole pass on any error.
    # ---------------------------------------------------------------------
    try {
        $logsDir = Join-Path (Split-Path -Parent $OutputDir) 'Logs'
        if (-not (Test-Path -LiteralPath $logsDir)) {
            Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
                -Text ("Logs directory not present at {0}; nothing to prune." -f $logsDir)
        } else {
            $cutoff   = (Get-Date).AddDays(-$LogRetainDays)
            $oldFiles = @(
                Get-ChildItem -LiteralPath $logsDir -File -Recurse -ErrorAction SilentlyContinue |
                    Where-Object { $_.LastWriteTime -lt $cutoff }
            )
            Write-Verbose ("Cleanup: {0} log file(s) older than {1} days" -f
                $oldFiles.Count, $LogRetainDays)

            foreach ($f in $oldFiles) {
                $itemId   = ('winget.log-prune.{0}' -f $f.Name)
                $itemName = ('Prune log: {0}' -f $f.Name)
                if (-not (& $shouldEmit $itemId)) { continue }

                if ($DryRun) {
                    [void](Add-SidecarItem -Sidecar $sidecar `
                        -Id $itemId `
                        -Name $itemName `
                        -Category 'winget' -SourceType 'winget' `
                        -Status 'planned' `
                        -Messages @( @{
                            level = 'info'
                            text  = ("DryRun: would delete {0} (lastWrite {1})" -f
                                $f.FullName, $f.LastWriteTime.ToString('o'))
                        } ))
                } else {
                    try {
                        Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
                        [void](Add-SidecarItem -Sidecar $sidecar `
                            -Id $itemId `
                            -Name $itemName `
                            -Category 'winget' -SourceType 'winget' `
                            -Status 'success' `
                            -Messages @( @{
                                level = 'info'
                                text  = ("Deleted {0} (lastWrite {1})" -f
                                    $f.FullName, $f.LastWriteTime.ToString('o'))
                            } ))
                    } catch {
                        [void](Add-SidecarItem -Sidecar $sidecar `
                            -Id $itemId `
                            -Name $itemName `
                            -Category 'winget' -SourceType 'winget' `
                            -Status 'failed' `
                            -Messages @( @{
                                level = 'warn'
                                text  = ("Delete failed: {0}" -f $_.Exception.Message)
                            } ))
                    }
                }
            }

            if ($oldFiles.Count -eq 0) {
                Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
                    -Text ("No log files older than {0} days in {1}." -f
                        $LogRetainDays, $logsDir)
            }
        }
    } catch {
        # Whole-pass soft-fail. Don't fail the phase: emit a warn message.
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Log prune pass failed: {0}" -f $_.Exception.Message)
    }

    # 4. Phase-level summary
    $successCnt = @($sidecar['items'] | Where-Object { $_['status'] -eq 'success' }).Count
    $failedCnt  = @($sidecar['items'] | Where-Object { $_['status'] -eq 'failed' }).Count
    $plannedCnt = @($sidecar['items'] | Where-Object { $_['status'] -eq 'planned' }).Count

    if ($DryRun) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("Cleanup (DryRun): {0} action(s) planned." -f $plannedCnt)
    } else {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("Cleanup: {0} action(s) succeeded, {1} failed." -f
                $successCnt, $failedCnt)
    }

    # 5. Save sidecar atomically
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
                -Name 'cleanup phase error' `
                -Category 'winget' -SourceType 'winget' `
                -Status 'failed' | Out-Null
        } catch {
            # Best-effort
        }

        try {
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {
            try {
                Write-FailureSidecarSynthetic -RunId $RunId `
                    -OutputDir $OutputDir -ErrorMessage $errMsg
            } catch {
                # Last resort failed.
            }
        }
    } else {
        try {
            Write-FailureSidecarSynthetic -RunId $RunId `
                -OutputDir $OutputDir -ErrorMessage $errMsg
        } catch {
            # Even the synthetic write failed.
        }
    }

    [Console]::Error.WriteLine("cleanup__winget.ps1 FAILED: $errMsg")
    exit 1
} finally {
    if ($null -ne $prevEnc) {
        try {
            Restore-WingetEnvironment -PreviousEncoding $prevEnc
        } catch {
            Write-Verbose "Restore-WingetEnvironment failed: $_"
        }
    }
}

# =============================================================================
# MANUAL TRACE (Windows-side reviewer reference)
# =============================================================================
#
# Invocation:
#
#   pwsh.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
#       -File <scripts_dir>\winget\cleanup.ps1 `
#       -RunId 11111111-2222-3333-4444-555555555555 `
#       -Trigger cli `
#       -Profile full `
#       -DryRun $false `
#       -OutputDir C:\Temp\runs
#
# -----------------------------------------------------------------------------
# TRACE - normal run, 3 old log files, source reset works:
#
# Step 3: itemFilterArray = $null (no filter).
# Pass A: source reset id='winget.source-reset' emitted with status='success',
#         exit_code=$LASTEXITCODE (typically 0).
# Pass B: $logsDir = C:\Temp\Logs (Split-Path C:\Temp\runs -> C:\Temp).
#         Suppose 3 files older than 60 days are found:
#           DCU_Apply_2025-12-01.log, WindowsUpdate_2025-12-15.log,
#           Programs_2026-01-05.log.
#         For each: Remove-Item succeeds -> Add-SidecarItem status='success',
#         id='winget.log-prune.<filename>'.
#
# Step 4: info "Cleanup: 4 action(s) succeeded, 0 failed."
# Step 5: Save-Sidecar -> total=4, success=4 -> phase status='success'.
#
# -----------------------------------------------------------------------------
# TRACE - DryRun:
#
# Pass A: emits item status='planned' with message
#         'DryRun: would run "winget source reset --force"'.
# Pass B: each candidate file gets status='planned' instead of being deleted.
#         No actual file modifications.
# Step 4: info "Cleanup (DryRun): N action(s) planned."
# Step 5: total=N, planned=N -> phase status='success' (Save-Sidecar treats
#         planned as a clean state when no failures present).
#
# -----------------------------------------------------------------------------
# TRACE - missing Logs directory:
#
# Pass A runs as normal.
# Pass B: Test-Path returns false -> single phase-level info message:
#         "Logs directory not present at <path>; nothing to prune."
#         Zero log-prune items emitted. No exception.
# Step 4: info "Cleanup: 1 action(s) succeeded, 0 failed."
# Step 5: total=1, success=1 -> phase status='success'.
#
# -----------------------------------------------------------------------------
# TRACE - source reset throws:
#
# Pass A catches the exception -> Add-SidecarItem status='failed' with
# warn message containing the .Exception.Message.
# Pass B continues normally.
# Step 4: success=N-1, failed=1.
# Step 5: failed>0 + success>0 -> phase status='partial' (or 'failed' if
# success=0). Phase still completes; the operator sees the failure and the
# successful log prunes side-by-side in the sidecar.
#
# =============================================================================
