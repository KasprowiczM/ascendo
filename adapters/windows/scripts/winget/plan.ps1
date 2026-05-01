# =============================================================================
# adapters/windows/scripts/winget/plan.ps1 - winget plan phase
# =============================================================================
#
# Side-effect-free enumeration of exactly what the apply phase WOULD change.
# Emits ascendo/v1 sidecar to <OutputDir>/<RunId>/plan__winget.json.
#
# Distinct from check by semantic intent:
#   * check  - inventory + upgrade discovery (every installed package)
#   * plan   - just the upgrade work apply would attempt (filtered set)
#   * apply  - actually performs the upgrades
#
# Plan emits items ONLY for packages it WOULD touch (upgradable set).
# It does NOT emit installed-but-up-to-date entries -- that's check's job.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Winget PLAN phase - enumerate what apply would change.

.DESCRIPTION
    Spawned by the Python WingetManager. Performs a non-mutating sweep of
    upgradable packages and reports each as status='planned' with full
    target_version and a rollback recipe so the apply phase (or a manual
    operator) can reproduce or roll back the operation.

    The script:
      1. Imports the sibling AscendoJson + AscendoWinget PowerShell modules.
      2. Initialises a sidecar object via New-Sidecar (Phase='plan').
      3. Enumerates upgradable packages (winget upgrade) and installed
         packages (winget list) using the column-position parser.
      4. For each upgradable item (after filter), emits one item with
         status='planned', current_version, target_version, and a rollback
         block. Skips packages with no Available version (already on latest).
      5. Saves the sidecar atomically via Save-Sidecar.

    No mutations are performed - winget itself never modifies system state
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
    Sidecar lands at <OutputDir>/<RunId>/plan__winget.json.

.PARAMETER DryRun
    Bool. Recorded in run.dry_run on the sidecar but otherwise ignored:
    plan is read-only, so dry-run = real-run for this phase.

.PARAMETER ItemFilter
    Comma-separated package IDs. Empty string = no filter (default).
    Comma is forbidden inside winget package IDs so this is unambiguous.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/plan__winget.json.

.EXAMPLE
    pwsh -File plan.ps1 -RunId 11111111-2222-3333-4444-555555555555 `
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
# scripts/winget/plan.ps1 -> adapters/windows/scripts -> adapters/windows
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
        Mirrors the helper in check.ps1; used by the catch block when
        $sidecar was never initialised.
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
        'phase'       = 'plan'
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
    $finalPath = Join-Path $runDir 'plan__winget.json'

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
        Phase       = 'plan'
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

    # 3. Parse the item filter (comma-separated IDs)
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

    # 4. Enumerate upgradable + installed packages
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
    Write-Verbose ("Get-WingetInstalled returned {0} row(s)" -f $installed.Count)

    # Authoritative current-version map (winget list > winget upgrade for ARP)
    $installedById = @{}
    foreach ($pkg in $installed) {
        if ($pkg.Id -and -not $installedById.ContainsKey($pkg.Id)) {
            $installedById[$pkg.Id] = $pkg.Version
        }
    }

    # 5. Emit one 'planned' item per upgradable package
    #    KEY: plan does NOT emit installed-but-up-to-date items.
    #    Only packages WITH an Available version that apply WOULD touch.
    $emittedIds = @{}
    $upgradeCount = 0

    foreach ($pkg in $upgradable) {
        if (-not $pkg.Id) { continue }
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg.Id)) {
            continue
        }

        $hasAvailable = ($null -ne $pkg.Available) -and ([string]$pkg.Available).Trim() -ne ''
        if (-not $hasAvailable) {
            # No Available column -> nothing to plan. Skip.
            continue
        }

        # Resolve current version (prefer winget list, fall back to upgrade row)
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

        $target = [string]$pkg.Available

        # Build rollback recipe inline. Plan does not import AscendoWingetActions
        # (which the apply phase will own); duplicating this small recipe is
        # cheaper than introducing a cross-phase dependency for one string.
        # Per spec, $current is the literal pre-upgrade version we'd reinstall.
        if (-not [string]::IsNullOrWhiteSpace($current) -and $current -ne 'Unknown') {
            $rollback = @{
                available = $true
                method    = ("winget install --id {0} --version {1} --silent " +
                             "--accept-package-agreements --accept-source-agreements " +
                             "--disable-interactivity") -f $pkg.Id, $current
            }
        } else {
            $rollback = @{ available = $false }
        }

        $itemArgs = @{
            Sidecar       = $sidecar
            Id            = [string]$pkg.Id
            Name          = [string]$pkg.Name
            Category      = 'winget'
            SourceType    = 'winget'
            Status        = 'planned'
            TargetVersion = $target
            Rollback      = $rollback
        }
        if ($pkg.PSObject.Properties['Source'] -and $pkg.Source) {
            $itemArgs['SourceFeed'] = [string]$pkg.Source
        }
        if (-not [string]::IsNullOrWhiteSpace($current) -and $current -ne 'Unknown') {
            $itemArgs['CurrentVersion'] = $current
        }

        [void](Add-SidecarItem @itemArgs)
        $emittedIds[$pkg.Id] = $true
        $upgradeCount++
    }

    # 6. Phase-level info message
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("Plan: {0} package(s) would be upgraded." -f $upgradeCount)

    if ($null -ne $itemFilterArray) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("ItemFilter applied: {0} ID(s) requested, {1} item(s) emitted." -f
                $itemFilterArray.Count, $emittedIds.Count)
    }

    # 7. Save sidecar atomically
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

        # Inject synthetic failed item so Save-Sidecar's status heuristic
        # flips to 'failed' instead of reporting 'success' on zero items.
        try {
            Add-SidecarItem -Sidecar $sidecar `
                -Id   '__phase_error__' `
                -Name 'plan phase error' `
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
                # Last resort failed; let the original exception bubble.
            }
        }
    } else {
        try {
            Write-FailureSidecarSynthetic -RunId $RunId `
                -OutputDir $OutputDir -ErrorMessage $errMsg
        } catch {
            # Even the synthetic write failed; nothing more to do.
        }
    }

    [Console]::Error.WriteLine("plan__winget.ps1 FAILED: $errMsg")
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
#       -File <scripts_dir>\winget\plan.ps1 `
#       -RunId 11111111-2222-3333-4444-555555555555 `
#       -Trigger cli `
#       -Profile full `
#       -DryRun $false `
#       -OutputDir C:\Users\MK\AppData\Local\Temp\ascendo-winget-XYZ `
#       [-ItemFilter Mozilla.Firefox,Microsoft.PowerShell]
#
# -----------------------------------------------------------------------------
# TRACE - using the same fixture as check.ps1 trace:
#
#   winget upgrade returns 3 rows:
#     Mozilla.Firefox             122.0   -> 122.0.1
#     Microsoft.VisualStudioCode  1.86.0  -> 1.86.1
#     Microsoft.PowerShell        7.4.0.0 -> 7.4.1.0
#
#   winget list returns 4 rows (the 3 above plus 7zip.7zip @ 23.01).
#
# Step 5 (upgradable loop):
#   - Mozilla.Firefox            -> emit status=planned, current=122.0,
#                                   target=122.0.1, rollback.method=
#                                   "winget install --id Mozilla.Firefox
#                                    --version 122.0 --silent ..."
#   - Microsoft.VisualStudioCode -> emit (same shape, version 1.86.0)
#   - Microsoft.PowerShell       -> emit (same shape, version 7.4.0.0)
#
#   7zip.7zip is NOT emitted -- it has no Available, so plan skips it.
#   This is the KEY difference vs check, which would emit 7zip with
#   status='up_to_date'. Plan's job is to describe ONLY work to be done.
#
# Step 6: info message "Plan: 3 package(s) would be upgraded."
#
# Step 7: Save-Sidecar writes:
#   <OutputDir>\11111111-2222-3333-4444-555555555555\plan__winget.json
#
# Sidecar summary will be:
#   total=3, success=0, up_to_date=0, failed=0, skipped=0, planned=3
# Sidecar status:
#   failed==0, skippedCount(0) != total(3) -> 'success'
#
# (Note: the phase's overall status is 'success' even though item statuses
# are all 'planned'. That's the contract: phase status reports whether the
# phase itself ran cleanly, not whether items were already up-to-date.)
#
# -----------------------------------------------------------------------------
# TRACE - filtered run (-ItemFilter Mozilla.Firefox):
#
#   Step 5 emits only Mozilla.Firefox; the other 2 upgradable rows skipped.
#   Step 6: info "Plan: 1 package(s) would be upgraded."
#   Step 6b: filter info message also emitted.
#   Final: total=1, planned=1, status='success'.
#
# -----------------------------------------------------------------------------
# TRACE - error path (winget binary missing):
#
#   Get-WingetVersion / Get-WingetBinaryPath -> 'unknown' / $null.
#   New-Sidecar succeeds, Initialize-WingetEnvironment succeeds.
#   Get-WingetUpgradable + Get-WingetInstalled both throw -> caught,
#   warn messages added, $upgradable = $installed = @().
#   Step 5 loop runs 0 times, $upgradeCount=0.
#   Step 6: info "Plan: 0 package(s) would be upgraded."
#   Step 7: Save writes status='success' (zero items, two warns).
#   Same shape as check.ps1's empty-but-clean exit. Python WingetManager
#   reads it as a no-op plan with telemetry breadcrumbs in messages[].
#
# =============================================================================
