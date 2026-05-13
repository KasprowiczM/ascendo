# =============================================================================
# adapters/windows/scripts/winget/verify.ps1 - winget verify phase
# =============================================================================
#
# Post-apply validation. Reads the sibling apply__winget.json from the same
# run directory, re-queries winget, and compares actual installed state
# against the apply phase's intent.
#
# Emits ascendo/v1 sidecar to <OutputDir>/<RunId>/verify__winget.json.
#
# Soft-degrades when no apply sidecar is present (verify can run after
# check-only flows). In that case the phase is a no-op with a single info
# message documenting why.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Winget VERIFY phase - confirm apply's claimed outcomes match reality.

.DESCRIPTION
    Spawned by the Python WingetManager. For each item in the prior apply
    sidecar, this phase re-queries winget and reports:
      * 'success'  - apply claimed success and the package is now installed
                     at the resolved/target version.
      * 'failed'   - apply succeeded but version is wrong or package vanished;
                     OR apply already failed (passed through with note).
      * 'skipped'  - apply skipped this item; verify reports it as 'skipped'
                     so the operator sees the chain explicitly.

    The script:
      1. Imports the sibling AscendoJson + AscendoWinget PowerShell modules.
      2. Initialises a sidecar object via New-Sidecar (Phase='verify').
      3. Looks for the apply sidecar at <OutputDir>/<RunId>/apply__winget.json.
         Missing apply sidecar -> emit a single info message and finish clean.
      4. Re-queries `winget upgrade` + `winget list`.
      5. For each apply item, compares against current state and emits a
         verify item.
      6. Saves the sidecar atomically via Save-Sidecar.

    No mutations are performed.

.PARAMETER RunId
    UUID string from the orchestrator. Must round-trip through [Guid].

.PARAMETER Trigger
    'cli' | 'scheduler' | 'dashboard' | 'plugin' | 'unknown'.

.PARAMETER ProfileName
    Profile slug. Aliased to 'Profile' so Python WingetManager._build_argv
    (which passes '-Profile') binds here.

.PARAMETER OutputDir
    Base directory under which the run subdirectory is created.
    Sidecar lands at <OutputDir>/<RunId>/verify__winget.json.
    Apply sidecar is expected at <OutputDir>/<RunId>/apply__winget.json.

.PARAMETER DryRun
    Bool. Recorded in run.dry_run on the sidecar but otherwise ignored:
    verify is read-only.

.PARAMETER ItemFilter
    Comma-separated package IDs. Empty string = no filter (default).
    When set, restricts verification to those IDs even if more items exist
    in the apply sidecar.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/verify__winget.json.

.EXAMPLE
    pwsh -File verify.ps1 -RunId 11111111-2222-3333-4444-555555555555 `
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
# scripts/winget/verify.ps1 -> adapters/windows/scripts -> adapters/windows
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

function Read-ApplySidecar {
    <#
    .SYNOPSIS
        Read and parse the apply sidecar for the same run, returning a
        deserialised PSCustomObject (or $null if missing/unreadable).
    .DESCRIPTION
        Soft-degrades on every error path: a missing or malformed apply
        sidecar is NOT fatal to verify; the caller documents the condition
        in a phase-level info message and the sidecar finishes with zero
        items + status='success'.
    .PARAMETER ApplyPath
        Full path to the expected apply__winget.json file.
    .OUTPUTS
        [pscustomobject] - parsed JSON, or $null if not present / unreadable.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ApplyPath
    )

    if (-not (Test-Path -LiteralPath $ApplyPath)) {
        Write-Verbose "Read-ApplySidecar: not present at $ApplyPath"
        return $null
    }
    try {
        $raw = [System.IO.File]::ReadAllText($ApplyPath, [System.Text.UTF8Encoding]::new($false))
        return ($raw | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        Write-Verbose "Read-ApplySidecar: parse failed: $_"
        return $null
    }
}

function Write-FailureSidecarSynthetic {
    <#
    .SYNOPSIS
        Minimal-but-valid v1 sidecar fallback when New-Sidecar threw before
        $sidecar was bound. Mirrors check.ps1.
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
        'phase'       = 'verify'
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
    $finalPath = Join-Path $runDir 'verify__winget.json'

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
        Phase       = 'verify'
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

    # 4. Locate the apply sidecar from the same run directory.
    #    Save-Sidecar normalises the run-id via [Guid]::Parse, which lower-
    #    cases hex digits. New-Sidecar already validated $RunId, so we can
    #    safely reproduce the same normalisation here.
    $runIdNorm = ([Guid]::Parse($RunId)).ToString()
    # Sibling-sidecar lookup: per-phase tempdir first, fall back to
    # the canonical ``~/.ascendo/runs/<RunId>/``. Each phase script
    # runs in its own tempdir, so the apply sidecar isn't co-located
    # by default; without the fallback verify becomes a permanent
    # no-op (operator observation, DP5520WMK run 91769201).
    $applyPath = Find-AscendoSiblingSidecar `
        -OutputDir $OutputDir `
        -RunId     $runIdNorm `
        -Filename  'apply__winget.json'
    if (-not $applyPath) {
        $applyPath = Join-Path (Join-Path $OutputDir $runIdNorm) 'apply__winget.json'
    }
    $applySidecar = Read-ApplySidecar -ApplyPath $applyPath

    if ($null -eq $applySidecar) {
        # 4a. No apply sidecar -> verify is a documented no-op.
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("No apply sidecar found at {0}; verify is a no-op." -f $applyPath)

        $written = Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir
        Write-Verbose ("Wrote no-op sidecar: {0}" -f $written.FullName)
        exit 0
    }

    # 5. Re-query winget
    $upgradable = @()
    try {
        $upgradable = @(Get-WingetUpgradable -ErrorAction Stop)
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-WingetUpgradable failed: {0}" -f $_.Exception.Message)
    }
    $installed = @()
    try {
        $installed = @(Get-WingetInstalled -ErrorAction Stop)
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-WingetInstalled failed: {0}" -f $_.Exception.Message)
    }

    # Build current Id -> Version map (winget list is authoritative)
    $installedById = @{}
    foreach ($pkg in $installed) {
        if ($pkg.Id -and -not $installedById.ContainsKey($pkg.Id)) {
            $installedById[$pkg.Id] = [string]$pkg.Version
        }
    }

    # Build Id -> Available map from the upgrade output (used to detect
    # cases where a "successful" apply still left the package upgradable
    # at the same target version, i.e. the install didn't actually advance).
    $availableById = @{}
    foreach ($pkg in $upgradable) {
        if ($pkg.Id -and -not $availableById.ContainsKey($pkg.Id)) {
            $availableById[$pkg.Id] = [string]$pkg.Available
        }
    }

    # 6. Walk the apply sidecar's items[] and produce verify items.
    $applyItems = @()
    if ($applySidecar.PSObject.Properties['items'] -and $null -ne $applySidecar.items) {
        $applyItems = @($applySidecar.items)
    }

    foreach ($applyItem in $applyItems) {
        if (-not $applyItem) { continue }
        if (-not $applyItem.PSObject.Properties['id']) { continue }

        $id = [string]$applyItem.id
        if (-not $id) { continue }
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $id)) {
            continue
        }

        $name = if ($applyItem.PSObject.Properties['name'] -and $applyItem.name) {
            [string]$applyItem.name
        } else { $id }

        $applyStatus = if ($applyItem.PSObject.Properties['status'] -and $applyItem.status) {
            [string]$applyItem.status
        } else { 'unknown' }

        $resolvedVersion = $null
        if ($applyItem.PSObject.Properties['resolved_version'] -and $applyItem.resolved_version) {
            $resolvedVersion = [string]$applyItem.resolved_version
        }
        $targetVersion = $null
        if ($applyItem.PSObject.Properties['target_version'] -and $applyItem.target_version) {
            $targetVersion = [string]$applyItem.target_version
        }
        # Synonym used by some apply implementations (treat 'reboot_required'
        # as if it were a success: the install completed, just needs a reboot).
        $applyClaimedSuccess = ($applyStatus -eq 'success' -or
                                $applyStatus -eq 'reboot_required')

        $expected = if ($resolvedVersion) { $resolvedVersion } else { $targetVersion }
        $actual   = if ($installedById.ContainsKey($id)) { $installedById[$id] } else { $null }

        $verifyStatus = 'success'
        $verifyMessages = @()

        if ($applyClaimedSuccess) {
            if ($null -eq $actual -or [string]::IsNullOrWhiteSpace($actual)) {
                # Apply said success but winget no longer reports the package.
                $verifyStatus = 'failed'
                $verifyMessages += @{
                    level = 'error'
                    text  = 'Package missing post-apply'
                }
            } elseif ($null -ne $expected -and -not [string]::IsNullOrWhiteSpace($expected) `
                      -and $actual -ne 'Unknown' -and $actual -ne $expected) {
                # Apply claimed an upgrade but the installed version doesn't
                # match what apply said it landed.
                $verifyStatus = 'failed'
                $verifyMessages += @{
                    level = 'error'
                    text  = ("Version mismatch: expected {0}, got {1}" -f $expected, $actual)
                }
            } else {
                $verifyStatus = 'success'
                $verifyMessages += @{
                    level = 'info'
                    text  = if ($expected) {
                        ("Verified upgrade (installed {0})" -f $actual)
                    } else {
                        ("Verified upgrade (installed {0}, no target on apply)" -f $actual)
                    }
                }
            }
        } elseif ($applyStatus -eq 'failed') {
            # Apply already failed -- pass through with the same status.
            $verifyStatus = 'failed'
            $verifyMessages += @{
                level = 'warn'
                text  = 'Apply failed; verify confirms not upgraded'
            }
        } elseif ($applyStatus -eq 'skipped') {
            $verifyStatus = 'skipped'
            $verifyMessages += @{
                level = 'info'
                text  = 'Apply skipped; verify reflects the skip'
            }
        } else {
            # Anything else (planned/partial/up_to_date/unknown) - report as
            # skipped from verify's perspective: nothing to verify.
            $verifyStatus = 'skipped'
            $verifyMessages += @{
                level = 'info'
                text  = ("Apply status '{0}' has no verify action" -f $applyStatus)
            }
        }

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = $id
            Name       = $name
            Category   = 'winget'
            SourceType = 'winget'
            Status     = $verifyStatus
            Messages   = $verifyMessages
        }
        if ($null -ne $actual -and -not [string]::IsNullOrWhiteSpace($actual) -and $actual -ne 'Unknown') {
            $itemArgs['CurrentVersion'] = $actual
        }
        if ($null -ne $expected -and -not [string]::IsNullOrWhiteSpace($expected)) {
            $itemArgs['TargetVersion'] = $expected
        }
        # Carry the resolved version forward so downstream tooling can see
        # what apply claimed to install.
        if ($null -ne $resolvedVersion -and -not [string]::IsNullOrWhiteSpace($resolvedVersion)) {
            $itemArgs['ResolvedVersion'] = $resolvedVersion
        }
        if ($applyItem.PSObject.Properties['source'] -and $applyItem.source `
            -and $applyItem.source.PSObject.Properties['feed'] -and $applyItem.source.feed) {
            $itemArgs['SourceFeed'] = [string]$applyItem.source.feed
        }

        [void](Add-SidecarItem @itemArgs)
    }

    # 7. Phase-level summary line
    $verifySuccess = @(
        $sidecar['items'] | Where-Object { $_['status'] -eq 'success' }
    ).Count
    $verifyFailed = @(
        $sidecar['items'] | Where-Object { $_['status'] -eq 'failed' }
    ).Count
    $verifySkipped = @(
        $sidecar['items'] | Where-Object { $_['status'] -eq 'skipped' }
    ).Count

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("Verify: {0} verified, {1} failed, {2} skipped." -f
            $verifySuccess, $verifyFailed, $verifySkipped)

    if ($null -ne $itemFilterArray) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("ItemFilter applied: {0} ID(s) requested." -f $itemFilterArray.Count)
    }

    # 8. Save sidecar atomically
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
                -Name 'verify phase error' `
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

    [Console]::Error.WriteLine("verify__winget.ps1 FAILED: $errMsg")
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
#       -File <scripts_dir>\winget\verify.ps1 `
#       -RunId 11111111-2222-3333-4444-555555555555 `
#       -Trigger cli `
#       -Profile full `
#       -DryRun $false `
#       -OutputDir C:\Temp\runs `
#       [-ItemFilter Mozilla.Firefox]
#
# -----------------------------------------------------------------------------
# TRACE - apply sidecar with 2 success and 1 failed:
#
# Suppose <OutputDir>\<RunId>\apply__winget.json contains:
#
#   items = [
#     { id: "Mozilla.Firefox",            status: "success",
#       target_version: "122.0.1", resolved_version: "122.0.1" },
#     { id: "Microsoft.VisualStudioCode", status: "success",
#       target_version: "1.86.1",  resolved_version: "1.86.1"  },
#     { id: "Microsoft.PowerShell",       status: "failed",
#       target_version: "7.4.1.0", resolved_version: null      }
#   ]
#
# Re-query (Step 5):
#   winget list returns:
#     Mozilla.Firefox             @ 122.0.1   (matches resolved_version)
#     Microsoft.VisualStudioCode  @ 1.86.0    (mismatch: expected 1.86.1)
#     Microsoft.PowerShell        @ 7.4.0.0   (apply already said failed)
#
# Step 6 (verify loop):
#   - Mozilla.Firefox:
#       applyClaimedSuccess=true, expected=122.0.1, actual=122.0.1 -> match
#       -> Add-SidecarItem status='success' message='Verified upgrade (installed 122.0.1)'
#   - Microsoft.VisualStudioCode:
#       applyClaimedSuccess=true, expected=1.86.1, actual=1.86.0 -> mismatch
#       -> Add-SidecarItem status='failed'
#          message='Version mismatch: expected 1.86.1, got 1.86.0'
#   - Microsoft.PowerShell:
#       applyStatus='failed'
#       -> Add-SidecarItem status='failed'
#          message='Apply failed; verify confirms not upgraded'
#
# Step 7: info "Verify: 1 verified, 2 failed, 0 skipped."
#
# Step 8: Save-Sidecar:
#   total=3, success=1, failed=2, skipped=0
#   phase status: failed>0 + success>0 -> 'partial'
#
# -----------------------------------------------------------------------------
# TRACE - missing apply sidecar (verify after check-only):
#
# Step 4 finds no apply__winget.json -> Read-ApplySidecar returns $null.
# Step 4a emits info "No apply sidecar found at <path>; verify is a no-op."
# Then immediately Save-Sidecar -> empty items -> status='success'.
# Python WingetManager reads it as a clean no-op verify pass.
#
# -----------------------------------------------------------------------------
# TRACE - filtered run (-ItemFilter Mozilla.Firefox):
#
# Step 6 walks all 3 apply items but emits only Mozilla.Firefox; the other 2
# are filtered out. Final summary: total=1, success=1, status='success'.
#
# =============================================================================
