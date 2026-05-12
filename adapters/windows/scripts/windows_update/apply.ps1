# =============================================================================
# adapters/windows/scripts/windows_update/apply.ps1 - WU apply phase
# =============================================================================
#
# Mutating: actually installs pending Windows OS updates via PSWindowsUpdate.
# CRITICAL: never auto-reboots. The script always passes -AutoReboot:$false to
# the underlying cmdlet and surfaces the reboot-required signal as a phase-
# level WARN message.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Windows Update APPLY phase - install pending KBs (no auto-reboot).

.DESCRIPTION
    Spawned by the Python WindowsUpdateManager. Two paths:

    DryRun  - emits status='planned' items for every pending update; runs
              ZERO mutations. PSWindowsUpdate's Install-WindowsUpdate is
              never called.

    Real run - calls Install-WindowsUpdateBatch (-AutoReboot:$false). Each
               result is mapped via Convert-WUResultToItemStatus into an
               ascendo/v1 ItemStatus ('success', 'partial', 'failed').
               If ANY KB reports RebootRequired, a phase-level [warn] message
               is emitted ("Reboot required to complete one or more updates.").

.PARAMETER RunId
.PARAMETER Trigger
.PARAMETER ProfileName
.PARAMETER OutputDir
.PARAMETER DryRun
.PARAMETER ItemFilter
    See check.ps1 for parameter docs.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/apply__windows_update.json.

.NOTES
    Requires Administrator rights to actually install updates. PSWindowsUpdate
    will fail silently (zero results) under a non-elevated session; we emit a
    [warn] message in that case so the operator can re-run elevated.
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
        'phase'='apply';'category'='windows_update'
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
    $finalPath = Join-Path $runDir 'apply__windows_update.json'
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
        -DryRun $DryRun -Phase 'apply' -Category 'windows_update' `
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
                   'apply, run: Install-Module PSWindowsUpdate -Scope CurrentUser -Force')
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    # ── DryRun path: emit planned items only, run no mutations ────────
    if ($DryRun) {
        $pending = @()
        try {
            $pending = @(Get-PendingWindowsUpdates -ErrorAction Stop)
        } catch {
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("Get-PendingWindowsUpdates failed: {0}" -f $_.Exception.Message)
        }

        $plannedCount = 0
        foreach ($u in $pending) {
            if (-not $u) { continue }
            $kb    = [string]$u.KB
            $title = if ($u.Title) { [string]$u.Title } else { $kb }
            if (-not $kb) { continue }
            if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $kb)) { continue }

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
                Messages   = @( @{ level='info'; text=('DryRun: would install ' + $kb) } )
            }
            [void](Add-SidecarItem @itemArgs)
            $plannedCount++
        }

        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ('DryRun: {0} Windows update(s) would be installed; no mutations performed.' -f $plannedCount)

        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    # ── Real-run path ──────────────────────────────────────────────────
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    $filterArray = $null
    if ($null -ne $itemFilterArray) { $filterArray = $itemFilterArray }

    Write-AscendoStreamLine -Text ">>> Install-WindowsUpdateBatch starting (this may take several minutes)"

    # Heartbeat matters most here — some KB installs run 10+ minutes
    # with zero output while servicing stack processes the payload.
    # Mirror of Ubuntu Sesja 55 heartbeat. See Start-AscendoHeartbeat
    # in AscendoJson.psm1.
    $hb = Start-AscendoHeartbeat -IntervalSeconds 10 -Label "Windows Update install"

    $results = @()
    try {
        if ($null -ne $filterArray) {
            $results = @(Install-WindowsUpdateBatch -Filter $filterArray -AcceptAll $true -AutoReboot $false -ErrorAction Stop)
        } else {
            $results = @(Install-WindowsUpdateBatch -AcceptAll $true -AutoReboot $false -ErrorAction Stop)
        }
    } catch {
        # Mirror the macOS apply.sh Sesja 34 stderr-tail pattern: when
        # the cmdlet throws, surface BOTH the exception message AND
        # any captured PSWindowsUpdate error-stream output (last 12
        # non-empty lines, capped at 1500 chars).
        Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
            -Text ("Install-WindowsUpdateBatch threw: {0}" -f $_.Exception.Message)
        Write-AscendoStreamLine -Text ("[error] Install-WindowsUpdateBatch threw: {0}" -f $_.Exception.Message)
        $stderrTail = ''
        try { $stderrTail = Get-WUInstallStderr } catch { }
        if ($stderrTail) {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("stderr (last 12 lines): {0}" -f $stderrTail)
            Write-AscendoStreamLine -Text ("[stderr] {0}" -f $stderrTail)
        }
        Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' -Name 'apply phase error' `
            -Category 'windows_update' -SourceType 'windows_update' -Status 'failed' | Out-Null
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 1
    } finally {
        Stop-AscendoHeartbeat $hb
    }
    $sw.Stop()

    if ($results.Count -eq 0) {
        # Surface error-stream tail if any. PSWindowsUpdate sometimes
        # returns zero results because of transient agent issues (e.g.
        # WUSA agent busy, certificate untrusted) — those used to be
        # silently swallowed via `2>$null`. Now they appear in the
        # sidecar so the operator knows whether to retry or investigate.
        $stderrTail = ''
        try { $stderrTail = Get-WUInstallStderr } catch { }
        if ($stderrTail) {
            Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
                -Text ("PSWindowsUpdate emitted errors but no result rows. stderr (last 12 lines): {0}" -f $stderrTail)
        } else {
            Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
                -Text 'No Windows updates were installed (none pending or PSWindowsUpdate produced no results).'
        }
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    $rebootRequired = $false
    $perItemMs = if ($results.Count -gt 0) { [int]($sw.Elapsed.TotalMilliseconds / $results.Count) } else { 0 }

    # Capture the per-batch stderr ONCE before the per-item loop —
    # PSWindowsUpdate doesn't tag errors with KBs, so we surface the
    # batch-level tail on each failed item. Cheap (just a script-var
    # accessor) and means failed items don't ship as bare exit codes.
    $batchStderr = ''
    try { $batchStderr = Get-WUInstallStderr } catch { }

    foreach ($r in $results) {
        if (-not $r) { continue }
        $kb    = [string]$r.KB
        $title = if ($r.Title) { [string]$r.Title } else { $kb }
        if (-not $kb) { continue }

        $itemStatus = Convert-WUResultToItemStatus -Result ([string]$r.Result)
        if ($r.RebootRequired) { $rebootRequired = $true }

        $messages = @()
        $messages += @{ level = 'info'; text = ('PSWindowsUpdate result: ' + [string]$r.Result) }
        if ($r.RebootRequired) {
            $messages += @{ level = 'warn'; text = 'Update installed but a system restart is required to complete it.' }
        }
        # Stderr tail on failed items — parity with macOS apply.sh.
        if ($itemStatus -eq 'failed' -and $batchStderr) {
            $messages += @{
                level = 'error'
                text  = ("stderr (last 12 lines): {0}" -f $batchStderr)
            }
        }

        $kbNumeric = if ($kb -match '^KB(\d+)$') { $matches[1] } else { $null }
        $rollback = $null
        if (($itemStatus -eq 'success') -and $kbNumeric) {
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
            Status     = $itemStatus
            DurationMs = $perItemMs
            Messages   = $messages
        }
        if ($null -ne $r.HResult) { $itemArgs['ExitCode'] = [int]$r.HResult }
        if ($null -ne $rollback)  { $itemArgs['Rollback'] = $rollback }

        [void](Add-SidecarItem @itemArgs)
    }

    if ($rebootRequired) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text 'Reboot required to complete one or more Windows updates.'
    }

    $successCnt = @($sidecar['items'] | Where-Object { $_['status'] -eq 'success' }).Count
    $partialCnt = @($sidecar['items'] | Where-Object { $_['status'] -eq 'partial' }).Count
    $failedCnt  = @($sidecar['items'] | Where-Object { $_['status'] -eq 'failed' }).Count
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ('Apply: {0} installed, {1} partial, {2} failed.' -f $successCnt, $partialCnt, $failedCnt)

    [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
    exit 0
} catch {
    $errMsg = $_.Exception.Message
    if ($null -ne $sidecar) {
        try { Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text ("Phase failed: {0}" -f $errMsg) } catch { }
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' -Name 'apply phase error' `
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
    [Console]::Error.WriteLine("apply__windows_update.ps1 FAILED: $errMsg")
    exit 1
}
