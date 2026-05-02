# =============================================================================
# adapters/windows/scripts/inventory/list.ps1 - Windows inventory enumeration
# =============================================================================
#
# Read-only inventory of installed packages via winget. Emits an ascendo/v1
# sidecar to <OutputDir>/<RunId>/check__winget.json that lists every package
# the local winget index knows about (winget-source AND ARP/MSIX detected).
#
# This is M3.11's IInventory implementation for Windows: same JSON contract as
# the WingetManager check phase but a separate native entry point so the
# inventory can be invoked OUTSIDE the 5-phase pipeline (it's used by the
# dashboard's "what's installed" view, plugin context, scheduled snapshots).
#
# Invoked by ascendo_windows.WindowsInventory.list_installed(...).
# See adapters/windows/ascendo_windows/inventory.py for the IPC contract.
#
# Layer 6 (native script) per ADR-0005.
# =============================================================================

<#
.SYNOPSIS
    Windows inventory list - read-only enumeration of installed packages.

.DESCRIPTION
    Spawned by the Python WindowsInventory. Runs `winget list` (via the
    AscendoWinget module's Get-WingetInstalled helper) and emits one
    ascendo/v1 sidecar item per installed package.

    The script:
      1. Imports the sibling AscendoJson + AscendoWinget PowerShell modules.
      2. Initialises a sidecar object via New-Sidecar (phase=check,
         category=winget). An INFO-level message records that this was
         emitted by inventory/list.ps1, so consumers can distinguish it
         from a WingetManager phase=check sidecar even though the
         (phase, category) tuple is the same.
      3. Enumerates installed packages with the column-position parser.
      4. Emits one item per package with status='success' (the package
         IS installed; nothing was attempted, but the inventory contract
         expects every successfully-enumerated row to count as success).
      5. Saves the sidecar atomically via Save-Sidecar.

    No mutations are performed.

.PARAMETER RunId
    UUID string from the Python caller. The Python WindowsInventory
    generates a fresh uuid4 per call; this run-id is independent of any
    orchestrator phase pipeline run-id.

.PARAMETER Trigger
    'cli' | 'scheduler' | 'dashboard' | 'plugin' | 'unknown'.

.PARAMETER ProfileName
    Profile slug. Aliased to 'Profile' so Python (which passes -Profile)
    binds here. Renamed because $Profile collides with PowerShell's
    automatic variable.

.PARAMETER OutputDir
    Base directory under which the run subdirectory is created.
    Sidecar lands at <OutputDir>/<RunId>/check__winget.json.

.PARAMETER DryRun
    Switch. Recorded in run.dry_run on the sidecar but otherwise ignored:
    inventory is read-only, so dry-run = real-run.

.PARAMETER ItemFilter
    Comma-separated package IDs. Empty string = no filter (default).
    When set, only matching IDs are emitted.

.OUTPUTS
    None on stdout. Side effect: <OutputDir>/<RunId>/check__winget.json.

.EXAMPLE
    pwsh -File list.ps1 -RunId 11111111-2222-3333-4444-555555555555 `
        -Trigger plugin -Profile inventory -OutputDir C:\Temp\inventory

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
# scripts/inventory/list.ps1 -> adapters/windows/scripts -> adapters/windows
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
                'text'      = "Inventory failed before sidecar was initialised: $ErrorMessage"
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
    # 1. Initialise sidecar
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

    # Sentinel info message: distinguishes this sidecar from a
    # WingetManager phase=check emission.
    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text 'Generated by inventory/list.ps1, not a phase pipeline.'

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

    # 4. Enumerate installed packages
    $installed = @()
    try {
        $installed = @(Get-WingetInstalled -ErrorAction Stop)
    } catch {
        Add-SidecarMessage -Sidecar $sidecar -Level 'warn' `
            -Text ("Get-WingetInstalled failed: {0}" -f $_.Exception.Message)
    }
    Write-Verbose ("Get-WingetInstalled returned {0} row(s)" -f $installed.Count)

    # 5. Emit one item per installed package, classified by source.
    #
    # ``winget list`` already returns Microsoft Store / MSIX packages and
    # ARP-detected (Add-or-Remove-Programs) entries alongside true winget-
    # source packages. Hard-coding ``Category='winget'`` for every row
    # collapsed all of them into the dashboard's single ``winget`` bucket
    # and hid the per-source inventory the user expects to see in the
    # Categories tab.
    #
    # Classification (in order):
    #   1. Source column says 'msstore' (case-insensitive)         -> msstore
    #   2. Source column says 'winget'                              -> winget
    #   3. Id starts with 'MSIX\'  (Store-installed AppX/MSIX)      -> msstore
    #   4. Id starts with 'ARP\'   (Add/Remove Programs registry)   -> registry_arp
    #   5. Otherwise                                                -> winget
    #
    # Items keep their winget-reported ``Source`` in ``SourceFeed`` for
    # diagnostics; only the SourceType changes per the bucket.
    function Resolve-InventoryCategory {
        param(
            [string] $Id,
            [string] $Source
        )
        if ($Source) {
            $s = $Source.Trim().ToLowerInvariant()
            if ($s -eq 'msstore') { return 'msstore' }
            if ($s -eq 'winget')  { return 'winget' }
        }
        if ($Id -like 'MSIX\*') { return 'msstore' }
        if ($Id -like 'ARP\*')  { return 'registry_arp' }
        return 'winget'
    }

    $emittedIds       = @{}
    $perCategoryCount = @{
        'winget'         = 0
        'msstore'        = 0
        'registry_arp'   = 0
        'windows_update' = 0
    }
    foreach ($pkg in $installed) {
        if (-not $pkg.Id) { continue }
        if ($emittedIds.ContainsKey($pkg.Id)) { continue }
        if ($null -ne $itemFilterArray -and ($itemFilterArray -notcontains $pkg.Id)) {
            continue
        }

        $current = $null
        if ($pkg.PSObject.Properties['Version']) {
            $candidate = [string]$pkg.Version
            if (-not [string]::IsNullOrWhiteSpace($candidate) -and $candidate -ne 'Unknown') {
                $current = $candidate
            }
        }

        $sourceFeed = $null
        if ($pkg.PSObject.Properties['Source'] -and $pkg.Source) {
            $sourceFeed = [string]$pkg.Source
        }

        $itemCategory = Resolve-InventoryCategory -Id ([string]$pkg.Id) -Source $sourceFeed
        $perCategoryCount[$itemCategory] = $perCategoryCount[$itemCategory] + 1

        $itemArgs = @{
            Sidecar    = $sidecar
            Id         = [string]$pkg.Id
            Name       = [string]$pkg.Name
            Category   = $itemCategory
            SourceType = $itemCategory
            Status     = 'success'
        }
        if ($null -ne $sourceFeed) {
            $itemArgs['SourceFeed'] = $sourceFeed
        }
        if ($null -ne $current) {
            $itemArgs['CurrentVersion'] = $current
        }

        [void](Add-SidecarItem @itemArgs)
        $emittedIds[$pkg.Id] = $true
    }

    Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
        -Text ("Inventory enumerated {0} package(s): winget={1}, msstore={2}, registry_arp={3}." -f
            $emittedIds.Count,
            $perCategoryCount['winget'],
            $perCategoryCount['msstore'],
            $perCategoryCount['registry_arp'])

    if ($null -ne $itemFilterArray) {
        Add-SidecarMessage -Sidecar $sidecar -Level 'info' `
            -Text ("ItemFilter applied: {0} ID(s) requested, {1} item(s) emitted." -f
                $itemFilterArray.Count, $emittedIds.Count)
    }

    # 6. Save sidecar atomically
    $written = Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir
    Write-Verbose ("Wrote sidecar: {0}" -f $written.FullName)

    exit 0
} catch {
    $errMsg = $_.Exception.Message

    if ($null -ne $sidecar) {
        try {
            Add-SidecarMessage -Sidecar $sidecar -Level 'error' `
                -Text ("Inventory failed: {0}" -f $errMsg)
        } catch {
            # ignore
        }

        try {
            Add-SidecarItem -Sidecar $sidecar `
                -Id   '__phase_error__' `
                -Name 'inventory error' `
                -Category 'winget' -SourceType 'winget' `
                -Status 'failed' | Out-Null
        } catch {
            # ignore
        }

        try {
            [void](Save-Sidecar -Sidecar $sidecar -OutputDir $OutputDir)
        } catch {
            try {
                Write-FailureSidecarSynthetic -RunId $RunId `
                    -OutputDir $OutputDir -ErrorMessage $errMsg
            } catch {
                # last-resort: nothing more we can do
            }
        }
    } else {
        try {
            Write-FailureSidecarSynthetic -RunId $RunId `
                -OutputDir $OutputDir -ErrorMessage $errMsg
        } catch {
            # ignore
        }
    }

    [Console]::Error.WriteLine("inventory/list.ps1 FAILED: $errMsg")
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
