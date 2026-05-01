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

function Invoke-WingetMutation {
    <#
    .SYNOPSIS
        Run `winget upgrade` (or `winget install`) on a single package and
        return the captured output + exit code.
    .PARAMETER PackageId
        winget package ID.
    .PARAMETER Action
        'upgrade' (default) or 'install'. Use 'install' when the
        uninstall-first entry has InstallAfterUninstall=$true.
    .OUTPUTS
        [pscustomobject] with: Output [string], ExitCode [int].
    .NOTES
        --disable-interactivity is mandatory. --silent is supplied so EXE
        installers run without UI. --accept-*-agreements to avoid the
        first-run consent prompt that would block unattended runs.
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
    $raw = & winget @argv 2>&1 | Out-String
    $code = $LASTEXITCODE

    return [pscustomobject]@{
        Output   = $raw
        ExitCode = [int]$code
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

        # 5j. Add to sidecar
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
