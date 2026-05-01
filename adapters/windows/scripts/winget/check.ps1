# =============================================================================
# adapters/windows/scripts/winget/check.ps1 - winget check phase
# =============================================================================
#
# Read-only inventory of installed packages + upgrade availability via winget.
# Emits ascendo/v1 sidecar to <OutputDir>/<RunId>/check__winget.json.
#
# Invoked by ascendo_windows.WingetManager.run_phase(Phase.CHECK, ...).
# See adapters/windows/ascendo_windows/managers/winget.py for the IPC contract.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Winget CHECK phase - read-only inventory + upgrade discovery.

.DESCRIPTION
    Spawned by the Python WingetManager. Performs a non-mutating sweep of
    installed packages and reports any with upgrades available via the
    ascendo/v1 sidecar schema.

    The script:
      1. Imports the sibling AscendoJson + AscendoWinget PowerShell modules.
      2. Initialises a sidecar object via New-Sidecar.
      3. Enumerates upgradable packages (winget upgrade) and installed
         packages (winget list) using the column-position parser.
      4. Adds one item per package (status='planned' for upgradable,
         status='up_to_date' for everything else).
      5. Saves the sidecar atomically via Save-Sidecar.

    No mutations are performed. winget itself never modifies system state
    in `winget upgrade` (no-args) or `winget list` mode.

.PARAMETER RunId
    UUID string from the orchestrator. Must round-trip through [Guid].

.PARAMETER Trigger
    'cli' | 'scheduler' | 'dashboard' | 'plugin' | 'unknown'.

.PARAMETER ProfileName
    Profile slug (e.g. 'full', 'safe', 'quick'). Aliased to 'Profile' so
    Python WingetManager._build_argv (which passes '-Profile') binds here.
    Renamed from $Profile because that name conflicts with PowerShell's
    automatic $Profile variable.

.PARAMETER OutputDir
    Base directory under which the run subdirectory is created.
    Sidecar lands at <OutputDir>/<RunId>/check__winget.json.

.PARAMETER DryRun
    Bool. Recorded in run.dry_run on the sidecar but otherwise ignored:
    check is read-only, so dry-run = real-run for this phase.

.PARAMETER ItemFilter
    Comma-separated package IDs. Empty string = no filter (default).
    Comma is forbidden inside winget package IDs so this is unambiguous.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/check__winget.json.

.EXAMPLE
    pwsh -File check.ps1 -RunId 11111111-2222-3333-4444-555555555555 `
        -Trigger cli -Profile full -OutputDir C:\Temp\runs

.NOTES
    PowerShell 5.1 + 7.x compatible. No PS 7+ syntax used.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RunId,

    [Parameter(Mandatory)]
        [ValidateSet('cli','scheduler','dashboard','plugin','unknown')]
        [string] $Trigger,

    # Aliased to 'Profile' for the Python caller (WingetManager passes
    # '-Profile <slug>'). The script-side variable name is ProfileName
    # because $Profile collides with PowerShell's automatic variable.
    [Parameter(Mandatory)]
        [Alias('Profile')]
        [string] $ProfileName,

    [Parameter(Mandatory)] [string] $OutputDir,

    # NB: declared as [switch] (not [bool]) — PowerShell's [bool] parameter
    # binder for `-File` invocations rejects string args like "True"/"False"/"1"
    # from external subprocesses. [switch] accepts the canonical pattern:
    # caller includes `-DryRun` to enable, omits it to disable.
    [Parameter()] [switch] $DryRun,
    [Parameter()] [string] $ItemFilter = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# Module imports (sibling lib/ directory)
# -----------------------------------------------------------------------------

$ScriptDir = Split-Path -Parent $PSCommandPath
# scripts/winget/check.ps1 -> adapters/windows/scripts -> adapters/windows
$WindowsAdapterDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$LibDir = Join-Path $WindowsAdapterDir 'lib'

Import-Module (Join-Path $LibDir 'AscendoJson.psm1')   -Force -DisableNameChecking
Import-Module (Join-Path $LibDir 'AscendoWinget.psm1') -Force -DisableNameChecking

# -----------------------------------------------------------------------------
# Inline helpers (consumer logic, not exported into AscendoWinget)
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
        # winget reports e.g. 'v1.28.240'; strip leading 'v' if present.
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

function Write-FailureSidecarSynthetic {
    <#
    .SYNOPSIS
        Write a minimal-but-valid v1 sidecar to disk when the normal
        sidecar object could not be built (e.g. New-Sidecar threw).
    .DESCRIPTION
        This is the fallback for the catch block when $sidecar was never
        initialised. Used so the Python caller always gets a sidecar to
        parse instead of seeing a missing file. Best-effort: if even this
        fails, the catch block lets the exception bubble.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RunId,
        [Parameter(Mandatory)] [string] $OutputDir,
        [Parameter(Mandatory)] [string] $ErrorMessage
    )

    # Synthesise just enough fields to satisfy the Sidecar Pydantic model.
    # If RunId is itself invalid we substitute the nil UUID so the file
    # at least parses; the error message captures the original input.
    $parsed = [Guid]::Empty
    if (-not [Guid]::TryParse($RunId, [ref]$parsed)) {
        $runIdNorm = '00000000-0000-0000-0000-000000000000'
    } else {
        $runIdNorm = $parsed.ToString()
    }

    $now = [DateTime]::UtcNow.ToString(
        'yyyy-MM-ddTHH:mm:ss.ffffffZ',
        [System.Globalization.CultureInfo]::InvariantCulture)

    # Reuse Get-AscendoHostInfo if AscendoJson loaded; otherwise minimal.
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
        'phase'       = 'check'
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
    $finalPath = Join-Path $runDir 'check__winget.json'

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
        Phase       = 'check'
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
        # Treat enumeration failure as a soft warning, not a hard error.
        # winget upgrade can fail for transient reasons (no internet,
        # source not reachable). The sidecar status will be 'success'
        # with 0 items + a warn message.
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
    Write-Verbose ("Get-WingetInstalled returned {0} row(s)" -f $installed.Count)

    # Build Id -> Version index from installed list (more authoritative
    # than the Version column in the upgrade output, which can be 'Unknown'
    # for ARP-detected packages).
    $installedById = @{}
    foreach ($pkg in $installed) {
        if ($pkg.Id -and -not $installedById.ContainsKey($pkg.Id)) {
            $installedById[$pkg.Id] = $pkg.Version
        }
    }

    # Track which Ids we've already emitted to avoid double-counting when
    # a package shows up in both `upgrade` and `list` output.
    $emittedIds = @{}

    # ── 5. Emit one item per upgradable package ────────────────────────
    foreach ($pkg in $upgradable) {
        if (-not $pkg.Id) { continue }
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg.Id)) {
            continue
        }

        $hasAvailable = ($null -ne $pkg.Available) -and ([string]$pkg.Available).Trim() -ne ''
        if ($hasAvailable) {
            $status = 'planned'
        } else {
            $status = 'up_to_date'
        }

        # Prefer the Version reported by `winget list` (more authoritative)
        # over the upgrade output Version (sometimes 'Unknown').
        $current = $null
        if ($installedById.ContainsKey($pkg.Id)) {
            $current = [string]$installedById[$pkg.Id]
        }
        if ([string]::IsNullOrWhiteSpace($current) -or $current -eq 'Unknown') {
            if ($pkg.PSObject.Properties['Version']) {
                $candidate = [string]$pkg.Version
                if (-not [string]::IsNullOrWhiteSpace($candidate) -and $candidate -ne 'Unknown') {
                    $current = $candidate
                }
            }
        }

        $target = $null
        if ($hasAvailable) { $target = [string]$pkg.Available }

        $itemArgs = @{
            Sidecar     = $sidecar
            Id          = [string]$pkg.Id
            Name        = [string]$pkg.Name
            Category    = 'winget'
            SourceType  = 'winget'
            Status      = $status
        }
        if ($pkg.PSObject.Properties['Source'] -and $pkg.Source) {
            $itemArgs['SourceFeed'] = [string]$pkg.Source
        }
        if (-not [string]::IsNullOrWhiteSpace($current)) {
            $itemArgs['CurrentVersion'] = $current
        }
        if ($null -ne $target -and -not [string]::IsNullOrWhiteSpace($target)) {
            $itemArgs['TargetVersion'] = $target
        }

        [void](Add-SidecarItem @itemArgs)
        $emittedIds[$pkg.Id] = $true
    }

    # ── 6. Emit installed packages NOT in the upgrade list ─────────────
    foreach ($pkg in $installed) {
        if (-not $pkg.Id) { continue }
        if ($emittedIds.ContainsKey($pkg.Id)) { continue }
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg.Id)) {
            continue
        }

        $current = [string]$pkg.Version
        if ([string]::IsNullOrWhiteSpace($current) -or $current -eq 'Unknown') {
            $current = $null
        }

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = [string]$pkg.Id
            Name       = [string]$pkg.Name
            Category   = 'winget'
            SourceType = 'winget'
            Status     = 'up_to_date'
        }
        if ($pkg.PSObject.Properties['Source'] -and $pkg.Source) {
            $itemArgs['SourceFeed'] = [string]$pkg.Source
        }
        if ($null -ne $current) {
            $itemArgs['CurrentVersion'] = $current
        }

        [void](Add-SidecarItem @itemArgs)
        $emittedIds[$pkg.Id] = $true
    }

    # ── 7. Phase-level info message when upgrades exist ────────────────
    $upgradeCount = @(
        $sidecar['items'] | Where-Object { $_['status'] -eq 'planned' }
    ).Count
    if ($upgradeCount -gt 0) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("Found {0} package(s) with upgrades available." -f $upgradeCount)
    }

    if ($null -ne $itemFilterArray) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("ItemFilter applied: {0} ID(s) requested, {1} item(s) emitted." -f
                $itemFilterArray.Count, $emittedIds.Count)
    }

    # ── 8. Save sidecar atomically ─────────────────────────────────────
    $written = Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir
    Write-Verbose ("Wrote sidecar: {0}" -f $written.FullName)

    exit 0
} catch {
    $errMsg = $_.Exception.Message

    if ($null -ne $sidecar) {
        # Preferred path: append the error and let Save-Sidecar finalise.
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("Phase failed: {0}" -f $errMsg)
        } catch {
            # Adding the message itself failed - sidecar may be in an
            # inconsistent state. Fall through to Save-Sidecar anyway.
        }

        # Save-Sidecar's status heuristic only flips to 'failed' when at
        # least one item is status='failed'. With zero items + an error
        # message it would still report 'success'. Inject a synthetic
        # failed item so downstream callers see status=failed.
        try {
            Add-SidecarItem -Sidecar $sidecar `
                -Id   '__phase_error__' `
                -Name 'check phase error' `
                -Category 'winget' -SourceType 'winget' `
                -Status 'failed' | Out-Null
        } catch {
            # Best-effort; if this throws (sidecar mutated badly) we
            # still try to save what we have.
        }

        try {
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {
            # Save itself failed - last resort: synthetic minimal sidecar.
            try {
                Write-FailureSidecarSynthetic -RunId $RunId `
                    -OutputDir $OutputDir -ErrorMessage $errMsg
            } catch {
                # Nothing more we can do; let the original exception
                # surface to the caller via the non-zero exit.
            }
        }
    } else {
        # New-Sidecar (or earlier) failed - synthesise a minimal sidecar
        # by hand so the Python caller sees a parseable file at the
        # expected path.
        try {
            Write-FailureSidecarSynthetic -RunId $RunId `
                -OutputDir $OutputDir -ErrorMessage $errMsg
        } catch {
            # Even the synthetic write failed; nothing more to do.
        }
    }

    # Surface the original error on stderr for the Python caller's
    # debugging tail (see WingetManager._format_missing_sidecar_error).
    [Console]::Error.WriteLine("check__winget.ps1 FAILED: $errMsg")
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
# Invocation (matches WingetManager._build_argv exactly):
#
#   pwsh.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
#       -File <scripts_dir>\winget\check.ps1 `
#       -RunId 11111111-2222-3333-4444-555555555555 `
#       -Trigger cli `
#       -Profile full `
#       -DryRun $false `
#       -OutputDir C:\Users\MK\AppData\Local\Temp\ascendo-winget-XYZ `
#       [-ItemFilter Mozilla.Firefox,Microsoft.PowerShell]
#
# Note: Python passes -Profile (not -ProfileName). The script declares
# [Alias('Profile')] on $ProfileName so '-Profile full' binds to
# $ProfileName='full'. PowerShell's prefix-binding would also resolve
# '-Profile' -> '-ProfileName' since no other parameter starts with
# 'Profile'; the alias is belt-and-braces.
#
# -----------------------------------------------------------------------------
# TRACE - simulated `winget upgrade` output (Fixture 1 from AscendoWinget):
# -----------------------------------------------------------------------------
#
#   Name                              Id                          Version  Available  Source
#   --------------------------------------------------------------------------------------------
#   Mozilla Firefox (x64 en-US)       Mozilla.Firefox             122.0    122.0.1    winget
#   Visual Studio Code                Microsoft.VisualStudioCode  1.86.0   1.86.1     winget
#   PowerShell 7-x64                  Microsoft.PowerShell        7.4.0.0  7.4.1.0    winget
#   3 upgrades available.
#
# Get-WingetUpgradable returns 3 rows.
#
# Simulated `winget list` (only those 3, plus 7-Zip already up-to-date):
#
#   Name                              Id                          Version  Source
#   ----------------------------------------------------------------------------------
#   7-Zip 23.01 (x64)                 7zip.7zip                   23.01    winget
#   Mozilla Firefox (x64 en-US)       Mozilla.Firefox             122.0    winget
#   Microsoft Visual Studio Code      Microsoft.VisualStudioCode  1.86.0   winget
#   PowerShell 7-x64                  Microsoft.PowerShell        7.4.0.0  winget
#
# Get-WingetInstalled returns 4 rows.
#
# installedById:
#   '7zip.7zip'                  -> '23.01'
#   'Mozilla.Firefox'            -> '122.0'
#   'Microsoft.VisualStudioCode' -> '1.86.0'
#   'Microsoft.PowerShell'       -> '7.4.0.0'
#
# Step 5 (upgradable loop):
#   - Mozilla.Firefox            -> status=planned, current=122.0,   target=122.0.1
#   - Microsoft.VisualStudioCode -> status=planned, current=1.86.0,  target=1.86.1
#   - Microsoft.PowerShell       -> status=planned, current=7.4.0.0, target=7.4.1.0
#
# Step 6 (installed-not-in-upgrade loop):
#   - 7zip.7zip                  -> status=up_to_date, current=23.01, target=null
#   (the other three are already in $emittedIds so they're skipped)
#
# Step 7: $upgradeCount = 3 -> info message "Found 3 package(s) with upgrades
#         available."
#
# Step 8: Save-Sidecar writes:
#   <OutputDir>\11111111-2222-3333-4444-555555555555\check__winget.json
#
# Sidecar summary will be:
#   total=4, success=0, up_to_date=1, failed=0, skipped=0, planned=3
# Sidecar status:
#   failed==0 + success==0 + skippedCount(0) != total(4) -> 'success'
#
# This matches the Python expectation (read_phase parses status='success'
# with planned items the orchestrator will pass to the plan/apply phases).
#
# -----------------------------------------------------------------------------
# TRACE - filtered run (-ItemFilter Mozilla.Firefox):
# -----------------------------------------------------------------------------
#
# Step 3: $itemFilterArray = @('Mozilla.Firefox')
# Step 5: only Mozilla.Firefox emitted (other two upgradable rows skipped)
# Step 6: 7zip.7zip / VSCode / PowerShell all skipped (not in filter)
# Step 7: $upgradeCount = 1 -> info message
# Step 7b: filter info message also emitted
# Final: total=1, planned=1, status='success'
#
# -----------------------------------------------------------------------------
# TRACE - error path (winget binary missing):
# -----------------------------------------------------------------------------
#
# Get-WingetVersion returns 'unknown' (caught), Get-WingetBinaryPath
# returns $null. New-Sidecar still succeeds (ToolVersion is just a string).
# Initialize-WingetEnvironment succeeds (just sets encoding).
# Get-WingetUpgradable throws "winget : The term 'winget' is not recognized"
# -> caught, warn message added, $upgradable = @().
# Same for Get-WingetInstalled.
# Step 7: $upgradeCount = 0, no info message.
# Step 8: Save-Sidecar writes status='success' (zero items, two warn
# messages). The Python WingetManager treats this as a successful but
# empty check phase, with the warn messages visible in the dashboard.
#
# -----------------------------------------------------------------------------
# TRACE - hard error path (New-Sidecar throws on bad RunId):
# -----------------------------------------------------------------------------
#
# Argv: -RunId 'not-a-uuid' (the Python side validates this, but defence-
# in-depth). New-Sidecar throws "Invalid RunId 'not-a-uuid' (not a UUID)."
# -> $sidecar is still $null, control transfers to the catch block ->
# Write-FailureSidecarSynthetic writes a stub sidecar to:
#   <OutputDir>\00000000-0000-0000-0000-000000000000\check__winget.json
# (substituting the nil UUID because the original RunId is unparseable).
# Status='failed', summary.exit_code=1, one error-level message.
# Script exits 1. Python's WingetManager.run_phase notices the non-zero
# exit but finds a parseable sidecar at the expected ItemFilter path -
# wait, actually NO. The path uses the ORIGINAL run.id from the Python
# RunInfo, but the sidecar wrote to the nil UUID directory because we
# couldn't parse the RunId. This is intentional: a malformed RunId is a
# hard programmer error - the orchestrator will hit `not sidecar_path.
# exists()` and raise ManagerError with a clear "no sidecar produced"
# message. The synthetic sidecar at the nil-UUID path is a debugging
# breadcrumb for the operator, not a contract path.
#
# =============================================================================
