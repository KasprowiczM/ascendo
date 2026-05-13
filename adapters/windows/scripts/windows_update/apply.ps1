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

    # Fast-path pre-check: enumerate pending updates BEFORE invoking
    # Install-WindowsUpdateBatch. The PSWindowsUpdate Install-WindowsUpdate
    # cmdlet wraps the Windows Update Agent COM API, which can hang for
    # MANY minutes inside Search even when the result is empty. By calling
    # Get-PendingWindowsUpdates first (a lighter read-only scan that uses
    # the same WUA but doesn't pause on the install pipeline), we can
    # short-circuit with "nothing to apply" in a few seconds instead of
    # blocking the entire run pipeline.
    #
    # Concrete bug this prevents (run e5f0e0f1, 2026-05-12): check phase
    # reported 4 KBs all already installed (0 pending); apply.ps1 still
    # called Install-WindowsUpdateBatch, which wedged for so long the
    # operator killed the dashboard. The orchestrator never got to
    # subsequent categories (plugin, etc.) and no apply__windows_update.json
    # was ever written. With this pre-check, the same scenario writes a
    # success sidecar in under 20s and the run proceeds.
    Write-AscendoStreamLine -Text ">>> Scanning pending Windows updates (pre-check)..."
    $hbPre = Start-AscendoHeartbeat -IntervalSeconds 10 -Label "Windows Update pre-check scan"
    $pending = @()
    try {
        $pending = @(Get-PendingWindowsUpdates -ErrorAction Stop)
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-PendingWindowsUpdates failed during apply pre-check: {0}" -f $_.Exception.Message)
    } finally {
        Stop-AscendoHeartbeat $hbPre
    }

    # Apply ItemFilter to the pending set so the empty-after-filter case
    # also short-circuits cleanly.
    if ($null -ne $filterArray -and $filterArray.Count -gt 0) {
        $pending = @(
            $pending | Where-Object {
                $kb = if ($null -ne $_ -and $_.PSObject.Properties['KB']) { [string]$_.KB } else { '' }
                $kb -and ($filterArray -contains $kb)
            }
        )
    }

    if ($pending.Count -eq 0) {
        # Nothing pending (or nothing matched by ItemFilter). Emit success
        # with zero items and exit. This is the canonical "no work to do"
        # path now that the dashboard fires apply unconditionally after
        # plan (rather than gating apply on plan having items).
        $reason = if ($null -ne $filterArray -and $filterArray.Count -gt 0) {
            ("No pending Windows updates matched the requested filter ({0})." -f ($filterArray -join ','))
        } else {
            'No pending Windows updates; nothing to install.'
        }
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' -Text $reason
        Write-AscendoStreamLine -Text ('>>> ' + $reason)
        [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        exit 0
    }

    Write-AscendoStreamLine -Text (">>> Install-WindowsUpdateBatch starting for {0} pending update(s) (this may take several minutes)" -f $pending.Count)

    # Heartbeat matters most here — some KB installs run 10+ minutes
    # with zero output while servicing stack processes the payload.
    # Mirror of Ubuntu Sesja 55 heartbeat. See Start-AscendoHeartbeat
    # in AscendoJson.psm1.
    $hb = Start-AscendoHeartbeat -IntervalSeconds 10 -Label ("Windows Update install ({0} update(s))" -f $pending.Count)

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
        # Mirror of the HResult defensive normalisation below: PSWindowsUpdate
        # sometimes returns RebootRequired as an Object[] across multiple
        # operation stages. Reduce to a single bool via -or aggregation
        # (any stage requiring reboot means the whole row does).
        $rebootForThisRow = $false
        if ($null -ne $r.RebootRequired) {
            $rr = $r.RebootRequired
            if ($rr -is [System.Array]) {
                foreach ($v in $rr) { if ($v) { $rebootForThisRow = $true; break } }
            } else {
                try { $rebootForThisRow = [bool]$rr } catch { $rebootForThisRow = $false }
            }
        }
        if ($rebootForThisRow) { $rebootRequired = $true }

        $messages = @()
        $messages += @{ level = 'info'; text = ('PSWindowsUpdate result: ' + [string]$r.Result) }
        if ($rebootForThisRow) {
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
        # HResult can come back as scalar Int32 (the common case) OR as
        # Object[] when PSWindowsUpdate aggregates results from multiple
        # operation stages (Search/Download/Install). The naive
        # [int]$r.HResult cast throws "Cannot convert the System.Object[]
        # value of type 'System.Object[]' to type 'System.Int32'" on
        # those rows -- which under StrictMode propagates up to the
        # outer catch and aborts the whole phase. Normalise defensively:
        # if it's an array, take the LAST element (the final stage's
        # HResult is the one the operator wants to see); ignore if the
        # value isn't coercible to int.
        if ($null -ne $r.HResult) {
            $hrRaw = $r.HResult
            if ($hrRaw -is [System.Array]) {
                if ($hrRaw.Length -gt 0) { $hrRaw = $hrRaw[-1] } else { $hrRaw = $null }
            }
            if ($null -ne $hrRaw) {
                try { $itemArgs['ExitCode'] = [int]$hrRaw } catch { }
            }
        }
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
    # Capture the full PowerShell stack trace and the inner-most error
    # invocation line so operators can pinpoint which line of apply.ps1
    # / which AscendoPSWindowsUpdate helper raised. Without this, a
    # plain "Phase failed: <message>" hides the line number entirely
    # (the original Sesja 59 user-side bug took 30 minutes to triage
    # because we had no stack trace).
    $stackTrace = ''
    try { $stackTrace = [string]$_.ScriptStackTrace } catch { }
    $invocation = ''
    try { $invocation = [string]$_.InvocationInfo.PositionMessage } catch { }
    $errorType = ''
    try { $errorType = $_.Exception.GetType().FullName } catch { }
    $detailedMsg = "Phase failed: $errMsg"
    if ($errorType)  { $detailedMsg += " [type=$errorType]" }
    if ($invocation) { $detailedMsg += "`n  at: $invocation" }
    if ($stackTrace) { $detailedMsg += "`n  stack:`n$stackTrace" }
    if ($null -ne $sidecar) {
        try { Add-SidecarMessage -Sidecar $sidecar -Level 'error' -Text $detailedMsg } catch { }
        try {
            Add-SidecarItem -Sidecar $sidecar -Id '__phase_error__' -Name 'apply phase error' `
                -Category 'windows_update' -SourceType 'windows_update' -Status 'failed' | Out-Null
        } catch { }
        try {
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {
            try { Write-FailureSidecarSynthetic -RunId $RunId -OutputDir $OutputDir -ErrorMessage $detailedMsg } catch { }
        }
    } else {
        try { Write-FailureSidecarSynthetic -RunId $RunId -OutputDir $OutputDir -ErrorMessage $detailedMsg } catch { }
    }
    [Console]::Error.WriteLine("apply__windows_update.ps1 FAILED: $errMsg")
    if ($invocation) { [Console]::Error.WriteLine("  at: $invocation") }
    exit 1
}
