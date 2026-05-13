# =============================================================================
# adapters/windows/scripts/winget/apply.ps1 - winget apply phase
# =============================================================================
#
# Mutating per-package upgrade pass. THIS IS THE FIRST MUTATING SCRIPT IN
# ASCENDO - it kills user-facing processes, optionally uninstalls packages,
# runs `winget upgrade`, and emits a sidecar with rollback metadata.
#
# Invoked by ascendo_windows.WingetManager.run_phase(Phase.APPLY, ...).
# See adapters/windows/ascendo_windows/managers/winget.py for the IPC
# contract (parameter names match _build_argv exactly).
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Winget APPLY phase - per-package upgrade with process kill, uninstall-first,
    skip-list, dry-run, and rollback metadata.

.DESCRIPTION
    Spawned by the Python WingetManager. For each upgradable winget package:

      1. Filter by ItemFilter and skip list.
      2. (Real run only) Stop any associated GUI/service processes per the
         APP_PROCESS_MAP, gracefully then forcefully.
      3. (Real run only) If the package is in the UNINSTALL_FIRST map, run
         the registry UninstallString silently.
      4. Run `winget upgrade --id <id>` (or `winget install` when the
         uninstall-first entry has InstallAfterUninstall=$true).
      5. Map the exit code via Convert-WingetExitCode and add an item to
         the sidecar with status, exit_code, current/target/resolved
         versions, and a rollback hashtable on success.

    Dry-run mode emits status='planned' items for every upgradable
    package and performs ZERO mutations: no process kill, no registry
    uninstall, no winget upgrade.

.PARAMETER RunId
    UUID string from the orchestrator. Must round-trip through [Guid].

.PARAMETER Trigger
    'cli' | 'scheduler' | 'dashboard' | 'plugin' | 'unknown'.

.PARAMETER ProfileName
    Profile slug (aliased to 'Profile' so the Python caller's
    '-Profile <slug>' binds here).

.PARAMETER OutputDir
    Base directory under which the run subdirectory is created.
    Sidecar lands at <OutputDir>/<RunId>/apply__winget.json.

.PARAMETER DryRun
    [bool]. When $true, emits planned items only and runs no mutations.

.PARAMETER ItemFilter
    Comma-separated package IDs. Empty string = no filter (default).

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/apply__winget.json.

.EXAMPLE
    pwsh -File apply.ps1 -RunId 11111111-2222-3333-4444-555555555555 `
        -Trigger cli -Profile full -DryRun $true -OutputDir C:\Temp\runs

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

    # NB: see check.ps1 for why this is [switch] rather than [bool].
    [Parameter()] [switch] $DryRun,
    [Parameter()] [string] $ItemFilter = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# Module imports (sibling lib/ directory)
# -----------------------------------------------------------------------------

$ScriptDir = Split-Path -Parent $PSCommandPath
# scripts/winget/apply.ps1 -> adapters/windows/scripts -> adapters/windows
$WindowsAdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $WindowsAdapterDir 'lib'

Import-Module (Join-Path $LibDir 'AscendoJson.psm1')          -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoWinget.psm1')        -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoWingetActions.psm1') -Force -DisableNameChecking

# -----------------------------------------------------------------------------
# Inline helpers (consumer logic, not exported into the lib modules)
# -----------------------------------------------------------------------------

function Get-WingetVersion {
    <#
    .SYNOPSIS
        Returns the winget self-reported version string (without leading 'v'),
        or 'unknown' on failure.
    #>
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
    <#
    .SYNOPSIS
        Returns the absolute path to the resolved winget binary, or $null.
    #>
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

function Get-TruncatedOutput {
    <#
    .SYNOPSIS
        Trim a multi-line winget output blob to the last $MaxLines lines.
    .DESCRIPTION
        winget can emit hundreds of progress lines per package; the sidecar
        message field has a 4096-char hard cap (per AscendoJson). We keep
        the LAST 30 lines (more diagnostic value than the first 30) and
        further truncate to 3500 chars to leave headroom for surrounding
        decoration if the caller wraps the message.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string]$RawOutput,
        [Parameter()] [int]$MaxLines = 30,
        [Parameter()] [int]$MaxChars = 3500
    )

    if ([string]::IsNullOrEmpty($RawOutput)) { return '' }

    $lines = $RawOutput -split "`n" | ForEach-Object { $_.TrimEnd("`r") }
    if ($lines.Count -gt $MaxLines) {
        $lines = $lines[($lines.Count - $MaxLines)..($lines.Count - 1)]
    }
    $joined = ($lines -join "`n").Trim()
    if ($joined.Length -gt $MaxChars) {
        $joined = ('...<truncated>...' + "`n" + $joined.Substring($joined.Length - $MaxChars))
    }
    return $joined
}

function Get-StderrTailExcerpt {
    <#
    .SYNOPSIS
        Return the last $MaxLines non-empty lines of a stderr capture
        file, joined with newlines and truncated to $MaxChars total.
    .DESCRIPTION
        Mirror of the macOS apply.sh pattern (npm/web): capture last 12
        non-empty stderr lines, cap at 1500 chars, surface as a sidecar
        message so the operator sees the actual failure reason
        (registry 404, EACCES, network blip, etc.) instead of bare exit
        codes.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $StderrFile,
        [Parameter()]          [int]    $MaxLines = 12,
        [Parameter()]          [int]    $MaxChars = 1500
    )
    if (-not (Test-Path -LiteralPath $StderrFile)) { return '' }
    try {
        $lines = Get-Content -LiteralPath $StderrFile -ErrorAction SilentlyContinue |
                 Where-Object { $_ -and $_.Trim() } |
                 Select-Object -Last $MaxLines
        if (-not $lines) { return '' }
        $joined = ($lines -join "`n")
        if ($joined.Length -gt $MaxChars) {
            $joined = $joined.Substring($joined.Length - $MaxChars)
        }
        return $joined
    } catch {
        return ''
    }
}

function Invoke-WingetMutation {
    <#
    .SYNOPSIS
        Run `winget upgrade` (or `winget install`) on a single package and
        return the captured output + exit code + stderr tail excerpt.
    .PARAMETER PackageId
        winget package ID.
    .PARAMETER Action
        'upgrade' (default) or 'install'. Use 'install' when the
        uninstall-first entry has InstallAfterUninstall=$true.
    .OUTPUTS
        [pscustomobject] with: Output [string], ExitCode [int],
        StderrExcerpt [string] (last 12 non-empty stderr lines, capped
        at 1500 chars; empty when the run succeeded or no stderr was
        produced).
    .NOTES
        --disable-interactivity is mandatory. --silent is supplied so EXE
        installers run without UI. --accept-*-agreements to avoid the
        first-run consent prompt that would block unattended runs.

        Stderr is captured to a separate temp file via
        `Start-Process -RedirectStandardError` so we can surface the
        actual failure reason (registry 404, EACCES, etc.) in the
        sidecar message — parity with the macOS apply.sh pattern from
        Sesja 34.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [string]$PackageId,
        [Parameter()] [ValidateSet('upgrade','install')] [string]$Action = 'upgrade'
    )

    $argv = @(
        $Action
        '--id'
        $PackageId
        '--exact'
        '--silent'
        '--accept-package-agreements'
        '--accept-source-agreements'
        '--disable-interactivity'
    )
    if ($Action -eq 'upgrade') { $argv += '--include-unknown' }

    Write-Verbose ("Invoke-WingetMutation: 'winget {0}'" -f ($argv -join ' '))

    # Resolve winget binary path so Start-Process can target it with
    # -FilePath. Get-Command is more portable than relying on it being
    # at a fixed path.
    $wingetCmd = Get-Command -Name 'winget' -ErrorAction SilentlyContinue
    if ($null -eq $wingetCmd) {
        $wingetCmd = Get-Command -Name 'winget.exe' -ErrorAction SilentlyContinue
    }
    if ($null -eq $wingetCmd) {
        # Fall back to legacy & winget path so tests with a fake winget
        # on PATH still work (Get-Command would resolve a fake .ps1 to
        # itself, not its target).
        $wingetTarget = 'winget'
    } else {
        $wingetTarget = [string]$wingetCmd.Source
    }

    $stdoutFile = $null
    $stderrFile = $null
    $raw    = ''
    $code   = -1
    $stderr = ''

    try {
        $stdoutFile = [System.IO.Path]::GetTempFileName()
        $stderrFile = [System.IO.Path]::GetTempFileName()

        # Start-Process -RedirectStandardError gives us a clean stderr
        # stream separate from stdout. -NoNewWindow + -Wait + -PassThru
        # is the canonical pattern for synchronous capture.
        $proc = Start-Process -FilePath $wingetTarget `
                              -ArgumentList $argv `
                              -RedirectStandardOutput $stdoutFile `
                              -RedirectStandardError  $stderrFile `
                              -NoNewWindow -Wait -PassThru `
                              -ErrorAction Stop
        $code = [int]$proc.ExitCode

        if (Test-Path -LiteralPath $stdoutFile) {
            $raw = Get-Content -LiteralPath $stdoutFile -Raw -ErrorAction SilentlyContinue
            if ($null -eq $raw) { $raw = '' }
        }

        # Stream-log mirroring (parity with the legacy 2>&1 path): when
        # ASCENDO_STREAM_LOG is set, append both the invocation marker
        # and the captured stdout so the SSE endpoint sees the install
        # progress lines. Stderr is appended too so failures are
        # visible in the live log.
        $streamLog = $env:ASCENDO_STREAM_LOG
        if ($streamLog) {
            try {
                ">>> winget $($argv -join ' ')" | Out-File -FilePath $streamLog -Append -Encoding utf8 -ErrorAction SilentlyContinue
                if ($raw) { $raw | Out-File -FilePath $streamLog -Append -Encoding utf8 -ErrorAction SilentlyContinue }
                if (Test-Path -LiteralPath $stderrFile) {
                    $stderrAll = Get-Content -LiteralPath $stderrFile -Raw -ErrorAction SilentlyContinue
                    if ($stderrAll) { $stderrAll | Out-File -FilePath $streamLog -Append -Encoding utf8 -ErrorAction SilentlyContinue }
                }
            } catch { }
        }

        if ($code -ne 0) {
            $stderr = Get-StderrTailExcerpt -StderrFile $stderrFile
        }
    }
    finally {
        foreach ($p in @($stdoutFile, $stderrFile)) {
            if ($p -and (Test-Path -LiteralPath $p)) {
                Remove-Item -LiteralPath $p -ErrorAction SilentlyContinue
            }
        }
    }

    return [pscustomobject]@{
        Output        = $raw
        ExitCode      = [int]$code
        StderrExcerpt = $stderr
    }
}

function Write-FailureSidecarSynthetic {
    <#
    .SYNOPSIS
        Write a minimal-but-valid v1 sidecar to disk when the normal
        sidecar object could not be built (e.g. New-Sidecar threw).
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
        'phase'       = 'apply'
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
    $finalPath = Join-Path $runDir 'apply__winget.json'

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
    # ── 1. Initialise sidecar ──────────────────────────────────────────
    $toolVersion    = Get-WingetVersion
    $toolBinaryPath = Get-WingetBinaryPath

    $newSidecarArgs = @{
        RunId       = $RunId
        Trigger     = $Trigger
        ProfileName = $ProfileName
        DryRun      = $DryRun
        Phase       = 'apply'
        Category    = 'winget'
        ToolName    = 'winget'
        ToolVersion = $toolVersion
    }
    if ($null -ne $toolBinaryPath -and $toolBinaryPath -ne '') {
        $newSidecarArgs['ToolBinaryPath'] = $toolBinaryPath
    }
    $sidecar = New-Sidecar @newSidecarArgs

    # ── 2. Configure winget output encoding ────────────────────────────
    $prevEnc = Initialize-WingetEnvironment

    # ── 3. Parse the item filter (comma-separated IDs) ─────────────────
    $itemFilterArray = $null
    if ($null -ne $ItemFilter -and $ItemFilter.Trim() -ne '') {
        $itemFilterArray = @(
            $ItemFilter -split ',' |
                ForEach-Object { $_.Trim() } |
                Where-Object   { $_ -ne '' }
        )
        if ($itemFilterArray.Count -eq 0) { $itemFilterArray = $null }
    }
    if ($null -ne $itemFilterArray) {
        Write-Verbose ("ItemFilter: {0} ID(s) -> {1}" -f
            $itemFilterArray.Count, ($itemFilterArray -join ', '))
    } else {
        Write-Verbose 'ItemFilter: <none>'
    }

    # ── 4. Enumerate upgradable + installed packages ───────────────────
    $upgradable = @()
    try {
        $upgradable = @(Get-WingetUpgradable -ErrorAction Stop)
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-WingetUpgradable failed: {0}" -f $_.Exception.Message)
    }
    Write-Verbose ("Get-WingetUpgradable returned {0} row(s)" -f $upgradable.Count)

    $installed = @()
    try {
        $installed = @(Get-WingetInstalled -ErrorAction Stop)
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-WingetInstalled failed: {0}" -f $_.Exception.Message)
    }

    # Build Id -> Version index (more authoritative than upgrade output's
    # Version column, which is 'Unknown' for ARP-detected packages).
    $installedById = @{}
    foreach ($pkg in $installed) {
        if ($pkg.Id -and -not $installedById.ContainsKey($pkg.Id)) {
            $installedById[$pkg.Id] = $pkg.Version
        }
    }

    # ── 5. Apply loop ──────────────────────────────────────────────────
    $rebootRequired = $false

    # Heartbeat keeps the SSE log stream visibly alive during long
    # `winget upgrade` invocations (a 250 MB package can be silent for
    # several minutes). Skipped on dry-run — no mutation = no silence.
    # See Start-AscendoHeartbeat in AscendoJson.psm1 (mirror of
    # Ubuntu Sesja 55 heartbeat work in lib/json.sh).
    $hb = $null
    try {
        if (-not $DryRun) {
            $hb = Start-AscendoHeartbeat -IntervalSeconds 10 `
                -Label ("winget upgrade ({0} package(s))" -f $upgradable.Count)
        }

    foreach ($pkg in $upgradable) {
        if (-not $pkg.Id) { continue }

        # 5a. ItemFilter check
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg.Id)) {
            Write-Verbose ("Skipping (filter): {0}" -f $pkg.Id)
            continue
        }

        # Compute current version (prefer `winget list` over upgrade output).
        $current = $null
        if ($installedById.ContainsKey($pkg.Id)) {
            $candidate = [string]$installedById[$pkg.Id]
            if (-not [string]::IsNullOrWhiteSpace($candidate) -and $candidate -ne 'Unknown') {
                $current = $candidate
            }
        }
        if ($null -eq $current -and $pkg.PSObject.Properties['Version']) {
            $candidate = [string]$pkg.Version
            if (-not [string]::IsNullOrWhiteSpace($candidate) -and $candidate -ne 'Unknown') {
                $current = $candidate
            }
        }

        # Compute target version from upgrade output's Available column.
        $target = $null
        if ($pkg.PSObject.Properties['Available']) {
            $candidate = [string]$pkg.Available
            if (-not [string]::IsNullOrWhiteSpace($candidate)) {
                $target = $candidate
            }
        }
        if (-not $target) {
            # Nothing to upgrade - skip silently. (Get-WingetUpgradable
            # generally only returns rows with Available, but be defensive.)
            continue
        }

        $sourceFeed = $null
        if ($pkg.PSObject.Properties['Source'] -and $pkg.Source) {
            $sourceFeed = [string]$pkg.Source
        }

        # 5a-bis. Sesja 66: honour Sesja 63's apply-mark in apply too.
        # Without this, an Unknown-version package whose mark.target ==
        # Available gets re-applied every full run (winget list still
        # says Unknown post-install; without the mark check, plan + apply
        # treat the package as outdated forever). check.ps1 already
        # consults the mark and reports up_to_date; mirror that here so
        # the orchestrator does not re-install a package that's already
        # at the marked version. Emit as 'up_to_date' so the run summary
        # is honest about what actually happened (zero work performed).
        if ([string]::IsNullOrWhiteSpace($current) -or $current -eq 'Unknown') {
            try {
                $mark = Get-AscendoApplyMark -Id ([string]$pkg.Id)
            } catch { $mark = $null }
            if ($null -ne $mark -and $mark.target -and $mark.target -eq $target) {
                $skipArgs = @{
                    Sidecar        = $sidecar
                    Id             = [string]$pkg.Id
                    Name           = [string]$pkg.Name
                    Category       = 'winget'
                    SourceType     = 'winget'
                    Status         = 'up_to_date'
                    CurrentVersion = [string]$mark.target
                    TargetVersion  = $target
                    Messages       = @(
                        @{
                            level = 'info'
                            text  = ("Already at marked version {0} (applied {1}); skipping re-install." -f $mark.target, $mark.appliedAt)
                        }
                    )
                }
                if ($sourceFeed) { $skipArgs['SourceFeed'] = $sourceFeed }
                [void](Add-SidecarItem @skipArgs)
                continue
            }
        }

        # 5b. Skip-list check
        if (Test-PackageSkipped -PackageId $pkg.Id) {
            $skipArgs = @{
                Sidecar    = $sidecar
                Id         = [string]$pkg.Id
                Name       = [string]$pkg.Name
                Category   = 'winget'
                SourceType = 'winget'
                Status     = 'skipped'
                Messages   = @(
                    @{
                        level = 'info'
                        text  = 'Package is on the apply-phase skip list (configured manual/vendor update path).'
                    }
                )
            }
            if ($sourceFeed)             { $skipArgs['SourceFeed']     = $sourceFeed }
            if ($current)                { $skipArgs['CurrentVersion'] = $current }
            if ($target)                 { $skipArgs['TargetVersion']  = $target }
            [void](Add-SidecarItem @skipArgs)
            continue
        }

        # 5c. Dry-run path - emit 'planned' item with rollback hint, no mutation
        if ($DryRun) {
            $rollback = Get-AscendoWingetRollbackMethod `
                -PackageId $pkg.Id -PreviousVersion $current

            $plannedArgs = @{
                Sidecar    = $sidecar
                Id         = [string]$pkg.Id
                Name       = [string]$pkg.Name
                Category   = 'winget'
                SourceType = 'winget'
                Status     = 'planned'
                Rollback   = $rollback
                Messages   = @(
                    @{
                        level = 'info'
                        text  = ("DryRun: would upgrade '{0}' from {1} to {2}." -f `
                                $pkg.Id,
                                $(if ($current) { $current } else { 'Unknown' }),
                                $target)
                    }
                )
            }
            if ($sourceFeed) { $plannedArgs['SourceFeed']     = $sourceFeed }
            if ($current)    { $plannedArgs['CurrentVersion'] = $current }
            if ($target)     { $plannedArgs['TargetVersion']  = $target }
            [void](Add-SidecarItem @plannedArgs)
            continue
        }

        # ----- Real-run path ---------------------------------------------
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $itemMessages = @()

        # 5d. Stop processes
        $stopResults = @()
        try {
            $stopResults = @(Stop-PackageProcesses -PackageId $pkg.Id)
        } catch {
            $itemMessages += @{
                level = 'warn'
                text  = ("Stop-PackageProcesses threw: {0}" -f $_.Exception.Message)
            }
        }
        $stopFailures = @($stopResults | Where-Object { $_.Stopped -eq $false -and $_.Error })
        if ($stopFailures.Count -gt 0) {
            foreach ($f in $stopFailures) {
                $itemMessages += @{
                    level = 'warn'
                    text  = ("Could not stop {0} (PID {1}): {2}" -f $f.ProcessName, $f.Pid, $f.Error)
                }
            }
        }

        # 5e. Uninstall-first if applicable
        $wingetAction         = 'upgrade'
        $skipPackageOnFailure = $false
        $uninstallMap         = Get-AscendoWingetUninstallFirstMap
        if ($uninstallMap.ContainsKey($pkg.Id)) {
            $cfg = $uninstallMap[$pkg.Id]

            # Stop any extra processes the uninstall-first config calls out
            # (typically a superset of APP_PROCESS_MAP, but be explicit).
            if ($cfg.ProcessNames -and $cfg.ProcessNames.Count -gt 0) {
                foreach ($p in $cfg.ProcessNames) {
                    try {
                        Get-Process -Name $p -ErrorAction SilentlyContinue |
                            Stop-Process -Force -ErrorAction SilentlyContinue
                    } catch {
                        # Best effort; the next step will surface a real error if it matters.
                    }
                }
            }

            $uninstallResult = $null
            try {
                $uninstallResult = Uninstall-PackageViaRegistry -UninstallName $cfg.UninstallName
            } catch {
                $itemMessages += @{
                    level = 'error'
                    text  = ("Uninstall-PackageViaRegistry threw for '{0}': {1}" -f $cfg.UninstallName, $_.Exception.Message)
                }
                $skipPackageOnFailure = $true
            }

            if (-not $skipPackageOnFailure) {
                if ($uninstallResult.Found -and $uninstallResult.ExitCode -ne 0 -and $uninstallResult.ExitCode -ne 3010) {
                    # Hard uninstall failure - record and skip the upgrade.
                    $itemMessages += @{
                        level = 'error'
                        text  = ("Uninstall-first failed (exit {0}): {1}" -f $uninstallResult.ExitCode, $uninstallResult.Error)
                    }
                    $skipPackageOnFailure = $true
                } elseif ($uninstallResult.Found) {
                    $itemMessages += @{
                        level = 'info'
                        text  = ("Uninstall-first removed '{0}' (exit {1})." -f $cfg.UninstallName, $uninstallResult.ExitCode)
                    }
                } else {
                    $itemMessages += @{
                        level = 'info'
                        text  = ("Uninstall-first: no existing ARP entry matched '{0}'." -f $cfg.UninstallName)
                    }
                }
            }

            if ($skipPackageOnFailure) {
                $sw.Stop()
                $failArgs = @{
                    Sidecar    = $sidecar
                    Id         = [string]$pkg.Id
                    Name       = [string]$pkg.Name
                    Category   = 'winget'
                    SourceType = 'winget'
                    Status     = 'failed'
                    DurationMs = [int]$sw.Elapsed.TotalMilliseconds
                    Messages   = $itemMessages
                }
                if ($sourceFeed) { $failArgs['SourceFeed']     = $sourceFeed }
                if ($current)    { $failArgs['CurrentVersion'] = $current }
                if ($target)     { $failArgs['TargetVersion']  = $target }
                [void](Add-SidecarItem @failArgs)
                continue
            }

            # InstallAfterUninstall=$true means the next step is install, not upgrade.
            if ($cfg.InstallAfterUninstall) {
                $wingetAction = 'install'
            }
        }

        # 5e2. Pre-dispatch up_to_date guard.
        # Mirrors the macOS web/apply.sh pattern (Sesja 40): if installed
        # version already equals the candidate, skip the wasteful
        # `winget upgrade` spawn and emit an `up_to_date` item directly.
        # We only guard the plain 'upgrade' path; uninstall-first +
        # install always forces the install (different version semantics).
        if ($wingetAction -eq 'upgrade' -and $current -and $target -and ($current -eq $target)) {
            $sw.Stop()
            $skipArgs = @{
                Sidecar         = $sidecar
                Id              = [string]$pkg.Id
                Name            = [string]$pkg.Name
                Category        = 'winget'
                SourceType      = 'winget'
                Status          = 'up_to_date'
                ExitCode        = 0
                DurationMs      = [int]$sw.Elapsed.TotalMilliseconds
                CurrentVersion  = $current
                TargetVersion   = $current
                ResolvedVersion = $current
                Messages        = @( @{ level='info'; text='already at latest version' } )
            }
            if ($sourceFeed) { $skipArgs['SourceFeed'] = $sourceFeed }
            [void](Add-SidecarItem @skipArgs)
            continue
        }

        # 5f. Run upgrade (or install)
        $mutation = $null
        try {
            $mutation = Invoke-WingetMutation -PackageId $pkg.Id -Action $wingetAction
        } catch {
            $itemMessages += @{
                level = 'error'
                text  = ("winget {0} threw: {1}" -f $wingetAction, $_.Exception.Message)
            }
            $sw.Stop()
            $failArgs = @{
                Sidecar    = $sidecar
                Id         = [string]$pkg.Id
                Name       = [string]$pkg.Name
                Category   = 'winget'
                SourceType = 'winget'
                Status     = 'failed'
                DurationMs = [int]$sw.Elapsed.TotalMilliseconds
                Messages   = $itemMessages
            }
            if ($sourceFeed) { $failArgs['SourceFeed']     = $sourceFeed }
            if ($current)    { $failArgs['CurrentVersion'] = $current }
            if ($target)     { $failArgs['TargetVersion']  = $target }
            [void](Add-SidecarItem @failArgs)
            continue
        }
        $sw.Stop()

        # 5g. Map exit code
        $result = Convert-WingetExitCode -ExitCode $mutation.ExitCode

        # Capture the tail of the output for the sidecar message.
        $outputTail = Get-TruncatedOutput -RawOutput $mutation.Output
        if ($outputTail) {
            $itemMessages += @{
                level = 'info'
                text  = $outputTail
            }
        }

        # Surface stderr excerpt on non-zero exit (parity with macOS
        # apply.sh — last 12 non-empty lines, capped at 1500 chars).
        # Mapped to 'error' level so the SPA + post-apply REPORT.md can
        # filter on it. PowerShell's [pscustomobject] supports direct
        # property access without PSObject.Properties when the property
        # was set in the New-Object/[pscustomobject]@{} literal.
        if ($mutation.ExitCode -ne 0 -and $mutation.StderrExcerpt) {
            $itemMessages += @{
                level = 'error'
                text  = ("stderr (last 12 lines): {0}" -f $mutation.StderrExcerpt)
            }
        }

        # 5h. Translate Convert-WingetExitCode status to ItemStatus
        # (per the schema: ItemStatus has no 'reboot_required' value, so
        # reboot_required collapses to 'success' but flips $rebootRequired
        # for the phase-level message).
        $itemStatus = switch ($result.Status) {
            'success'         { 'success' }
            'up_to_date'      { 'up_to_date' }
            'reboot_required' { 'success' }
            default           { 'failed' }   # 'failed' and 'id_not_found'
        }
        if ($result.Status -eq 'reboot_required') {
            $rebootRequired = $true
            $itemMessages += @{
                level = 'warn'
                text  = 'Installer succeeded but a system restart is required to complete this update.'
            }
        }
        if ($result.Status -eq 'id_not_found') {
            # MVP: log and fail the item. A future iteration can add the
            # name-based fallback that 3_Update-Programs.ps1 attempts.
            $itemMessages += @{
                level = 'warn'
                text  = ('winget could not find an installed package matching id ''{0}''. ' +
                         'A future Ascendo iteration will attempt a name-based fallback. ' +
                         '(See 3_Update-Programs.ps1 line ~1815 for the source-side logic.)') -f $pkg.Id
            }
        }

        # 5i. Compute resolved_version + rollback (success path only)
        $resolved = $null
        $rollback = $null
        if ($itemStatus -eq 'success' -or $itemStatus -eq 'up_to_date') {
            # MVP: trust target_version as resolved on a clean upgrade. A
            # future iteration can re-query `winget list --id <id>` after
            # upgrade for true confirmation. For 'up_to_date' the resolved
            # version equals the previously installed version.
            if ($itemStatus -eq 'success') {
                $resolved = $target
            } else {
                $resolved = $current
            }
            if ($itemStatus -eq 'success') {
                $rollback = Get-AscendoWingetRollbackMethod `
                    -PackageId $pkg.Id -PreviousVersion $current
            }
        }

        # 5j. Persist the unknown-version apply-mark on success when the
        # pre-install reading was Unknown / blank. Future check phases
        # use the mark to suppress the false-positive "outdated" state
        # that hits packages like SoftSea.IMGtoISO whose Inno Setup
        # uninstaller never writes DisplayVersion to the registry.
        # See AscendoWinget.psm1::Set-AscendoApplyMark + the operator
        # observation logged there (DP5520WMK 2026-05-13).
        if ($itemStatus -eq 'success' -and $target -and ($target -ne 'Unknown')) {
            $needsMark = (-not $current) -or ($current -eq 'Unknown')
            if ($needsMark) {
                try {
                    Set-AscendoApplyMark -Id ([string]$pkg.Id) -Target ([string]$target)
                } catch {
                    # Marking is best-effort -- never abort a successful
                    # install because the state file is unwritable.
                    Write-Verbose ("Set-AscendoApplyMark threw for {0}: {1}" -f $pkg.Id, $_)
                }
            }
        }

        # 5k. Add to sidecar
        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = [string]$pkg.Id
            Name       = [string]$pkg.Name
            Category   = 'winget'
            SourceType = 'winget'
            Status     = $itemStatus
            ExitCode   = [int]$mutation.ExitCode
            DurationMs = [int]$sw.Elapsed.TotalMilliseconds
            Messages   = $itemMessages
        }
        if ($sourceFeed)              { $itemArgs['SourceFeed']      = $sourceFeed }
        if ($current)                 { $itemArgs['CurrentVersion']  = $current }
        if ($target)                  { $itemArgs['TargetVersion']   = $target }
        if ($resolved)                { $itemArgs['ResolvedVersion'] = $resolved }
        if ($null -ne $rollback)      { $itemArgs['Rollback']        = $rollback }
        [void](Add-SidecarItem @itemArgs)
    }
    } finally {
        Stop-AscendoHeartbeat $hb
    }

    # ── 6. Phase-level reboot message ──────────────────────────────────
    if ($rebootRequired) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text 'Reboot required to complete one or more upgrades.'
    }

    if ($null -ne $itemFilterArray) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("ItemFilter applied: {0} ID(s) requested." -f $itemFilterArray.Count)
    }

    if ($DryRun) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text 'DryRun: no mutations were performed.'
    }

    # ── 7. Save sidecar atomically ─────────────────────────────────────
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
            # Adding the message itself failed - sidecar may be in an
            # inconsistent state. Fall through to Save-Sidecar anyway.
        }

        # Inject a synthetic failed item so Save-Sidecar's status heuristic
        # flips to 'failed' (without a failed item it would still see
        # 'success' from zero items).
        try {
            Add-SidecarItem -Sidecar $sidecar `
                -Id   '__phase_error__' `
                -Name 'apply phase error' `
                -Category 'winget' -SourceType 'winget' `
                -Status 'failed' | Out-Null
        } catch {
            # Best-effort.
        }

        try {
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {
            try {
                Write-FailureSidecarSynthetic -RunId $RunId `
                    -OutputDir $OutputDir -ErrorMessage $errMsg
            } catch {
                # Nothing more we can do.
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

    [Console]::Error.WriteLine("apply__winget.ps1 FAILED: $errMsg")
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
# MANUAL TRACE - dry-run path (Windows-side reviewer reference)
# =============================================================================
#
# Invocation (matches WingetManager._build_argv):
#
#   pwsh.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
#       -File <scripts_dir>\winget\apply.ps1 `
#       -RunId aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee `
#       -Trigger cli `
#       -Profile full `
#       -DryRun $true `
#       -OutputDir C:\Users\MK\AppData\Local\Temp\ascendo-runs
#
# -----------------------------------------------------------------------------
# Mocked $upgradable (3 packages):
# -----------------------------------------------------------------------------
#
#   #1: Mozilla.Firefox            current=122.0    available=122.0.1   source=winget
#   #2: Microsoft.DotNet.DesktopRuntime.10
#                                  current=10.0.3   available=10.0.4    source=winget
#       (on the SKIP_IDS list)
#   #3: Microsoft.VisualStudioCode current=1.86.0   available=1.86.1    source=winget
#
# -----------------------------------------------------------------------------
# Trace through the apply loop (DryRun=$true):
# -----------------------------------------------------------------------------
#
# rebootRequired = $false
#
# iter #1  pkg = Mozilla.Firefox
#   5a. ItemFilter empty -> not skipped
#   current = '122.0' (from installedById)
#   target  = '122.0.1' (from $pkg.Available)
#   sourceFeed = 'winget'
#   5b. Test-PackageSkipped 'Mozilla.Firefox' -> $false (not in SKIP_IDS)
#   5c. DryRun=$true -> emit 'planned' item:
#       - Status         = 'planned'
#       - CurrentVersion = '122.0'
#       - TargetVersion  = '122.0.1'
#       - SourceFeed     = 'winget'
#       - Rollback       = { available=$true,
#                            method='winget install --id Mozilla.Firefox --version 122.0 ...' }
#       - Messages       = [{level='info', text="DryRun: would upgrade 'Mozilla.Firefox' from 122.0 to 122.0.1."}]
#       continue (no mutations)
#
# iter #2  pkg = Microsoft.DotNet.DesktopRuntime.10
#   5a. not filtered
#   5b. Test-PackageSkipped 'Microsoft.DotNet.DesktopRuntime.10' -> $true
#       -> emit 'skipped' item, info message about skip-list, continue
#
# iter #3  pkg = Microsoft.VisualStudioCode
#   5a. not filtered
#   current = '1.86.0', target = '1.86.1'
#   5b. not on skip list
#   5c. DryRun=$true -> emit 'planned' item:
#       - Rollback = { available=$true, method='winget install --id Microsoft.VisualStudioCode --version 1.86.0 ...' }
#
# -----------------------------------------------------------------------------
# After loop:
#   $rebootRequired = $false -> no warn message
#   $itemFilterArray = $null -> no filter info message
#   $DryRun = $true          -> info message 'DryRun: no mutations were performed.'
#
# Save-Sidecar:
#   Items: planned x 2, skipped x 1
#   Summary: total=3, success=0, up_to_date=0, failed=0, skipped=1, planned=2
#   Phase status heuristic:
#     failed=0 + success=0 + skipped(1) != total(3) -> 'success'
#
# File: <OutputDir>/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/apply__winget.json
# Exit code: 0
#
# -----------------------------------------------------------------------------
# Real-run trace (DryRun=$false), one package, hypothetical:
# -----------------------------------------------------------------------------
#
# pkg = Mozilla.Firefox, current=122.0, target=122.0.1
#   5d. Stop-PackageProcesses 'Mozilla.Firefox' -> matches APP_PROCESS_MAP
#       -> kills firefox.exe (graceful CloseMainWindow then Stop-Process -Force)
#   5e. Mozilla.Firefox not in UNINSTALL_FIRST_MAP -> wingetAction='upgrade'
#   5f. Invoke-WingetMutation 'Mozilla.Firefox' upgrade
#       -> winget upgrade --id Mozilla.Firefox --exact --silent
#          --accept-package-agreements --accept-source-agreements
#          --disable-interactivity --include-unknown
#       Output captured, exit code 0
#   5g. Convert-WingetExitCode 0 -> Status='success'
#   5h. itemStatus = 'success'
#   5i. resolved = '122.0.1', rollback = { available=$true,
#         method='winget install --id Mozilla.Firefox --version 122.0 ...' }
#   5j. Add-SidecarItem with Status='success', ExitCode=0, DurationMs,
#         CurrentVersion='122.0', TargetVersion='122.0.1',
#         ResolvedVersion='122.0.1', Rollback, Messages with output tail.
#
# =============================================================================
