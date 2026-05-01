# =============================================================================
# bin\run-apply.ps1 — Ascendo Windows real-mutation safety harness
# =============================================================================
#
# .SYNOPSIS
#   Run a real (non-dry-run) ascendo apply on Windows with safeguards.
#
# .DESCRIPTION
#   This is the v0.0.2-alpha harness for the FIRST real mutation on real
#   hardware. Behaviour:
#     1. Prints a loud warning that this WILL upgrade real packages.
#     2. Runs `ascendo run --phase plan` first to enumerate intended changes.
#     3. Parses the plan sidecar and prints a clean table.
#     4. Prompts for confirmation (literal 'apply' to proceed) unless
#        -IAcceptUpgradeRisk is set.
#     5. Runs `ascendo run --phase apply` for real (no --dry-run flag).
#     6. Parses + prints the apply sidecar (per-item status + messages).
#     7. Exits with the same code as the apply (0/1/2).
#
# .EXAMPLE
#   PS> .\bin\run-apply.ps1
#   Interactive — shows plan, prompts before applying.
#
# .EXAMPLE
#   PS> .\bin\run-apply.ps1 -IAcceptUpgradeRisk
#   Skips the confirmation prompt. Use with care.
#
# .EXAMPLE
#   PS> .\bin\run-apply.ps1 -Packages 'Microsoft.PowerShell','Mozilla.Firefox'
#   Apply only the listed packages.
# =============================================================================

[CmdletBinding()]
param(
    [string] $Category = 'winget',
    [string[]] $Packages,                  # Optional filter — e.g. -Packages 'Microsoft.PowerShell','Mozilla.Firefox'
    [switch] $IAcceptUpgradeRisk,         # Skip the interactive confirmation
    [string] $Profile = 'full'
)

Set-StrictMode -Version Latest

# Same exit-code policy as validate-windows.ps1: a non-zero exit from the
# child python process is an explicit signal we propagate, NOT a fatal
# pwsh terminating error.
$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

function Write-Step {
    param([string] $Title)
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function Write-Banner {
    param([string] $Text, [string] $Color = 'Yellow')
    $bar = '=' * 78
    Write-Host ""
    Write-Host $bar -ForegroundColor $Color
    Write-Host $Text -ForegroundColor $Color
    Write-Host $bar -ForegroundColor $Color
}

function Find-Sidecar {
    param([string] $RunsDir, [string] $FileName)
    return Get-ChildItem -Path $RunsDir -Recurse -Filter $FileName -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

try {
    # ── 0. Loud warning banner ─────────────────────────────────────────────
    Write-Banner "WARNING — REAL MUTATION HARNESS" 'Red'
    Write-Host "  This script WILL upgrade real packages via the '$Category' source." -ForegroundColor Yellow
    Write-Host "  Installer processes will be invoked; UAC prompts may appear for" -ForegroundColor Yellow
    Write-Host "  packages that require elevation." -ForegroundColor Yellow
    if ($Packages) {
        Write-Host "  Filter: only these package(s) will be considered:" -ForegroundColor Yellow
        foreach ($p in $Packages) { Write-Host "    - $p" -ForegroundColor Yellow }
    } else {
        Write-Host "  Filter: none (all upgradeable packages in scope)." -ForegroundColor Yellow
    }
    Write-Host "  Profile: $Profile" -ForegroundColor DarkGray

    # ── 1. Plan phase (read-only) ──────────────────────────────────────────
    Write-Step "Phase 1/3 — running plan to enumerate intended changes"

    $planRunsDir = Join-Path $env:TEMP "ascendo-pre-apply-plan-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Force -Path $planRunsDir | Out-Null

    $planArgv = @('-B', '-m', 'ascendo', 'run', '--category', $Category, '--phase', 'plan', '--runs-dir', $planRunsDir)
    if ($Packages) { $planArgv += @('--items', ($Packages -join ',')) }

    & python @planArgv
    $planExit = $LASTEXITCODE

    if ($planExit -notin @(0, 1)) {
        Write-Host ""
        Write-Host "  [FAIL] Plan exited with code $planExit — aborting before apply." -ForegroundColor Red
        exit $planExit
    }

    # ── 2. Parse plan sidecar + render table ───────────────────────────────
    $planSidecar = Find-Sidecar -RunsDir $planRunsDir -FileName "plan__$Category.json"
    if (-not $planSidecar) {
        Write-Host ""
        Write-Host "  [FAIL] No plan sidecar found at $planRunsDir\<run>\plan__$Category.json" -ForegroundColor Red
        Write-Host "         Cannot proceed to apply without a verified plan." -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    Write-Host "  Plan sidecar: $($planSidecar.FullName)" -ForegroundColor DarkGray

    try {
        $plan = Get-Content $planSidecar.FullName -Raw | ConvertFrom-Json
    } catch {
        Write-Host "  [FAIL] Plan sidecar is not valid JSON: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }

    $planItems = @()
    if ($plan.PSObject.Properties.Name -contains 'items' -and $plan.items) {
        $planItems = @($plan.items)
    }

    if ($planItems.Count -eq 0) {
        Write-Host ""
        Write-Host "  Nothing to upgrade. The plan returned 0 items." -ForegroundColor Green
        exit 0
    }

    Write-Host ""
    Write-Host "  The following $($planItems.Count) package(s) WILL be upgraded if you confirm:" -ForegroundColor Cyan
    Write-Host ""

    $rows = foreach ($it in $planItems) {
        $cur = if ($it.PSObject.Properties.Name -contains 'current_version' -and $it.current_version) { $it.current_version } else { '-' }
        $tgt = if ($it.PSObject.Properties.Name -contains 'target_version' -and $it.target_version) { $it.target_version } else { '-' }
        $src = if ($it.PSObject.Properties.Name -contains 'source' -and $it.source) { $it.source } else { $Category }
        [pscustomobject]@{
            Package = $it.id
            Current = $cur
            Target  = $tgt
            Source  = $src
        }
    }
    $rows | Format-Table -AutoSize | Out-String | Write-Host

    # ── 3. Confirmation gate ───────────────────────────────────────────────
    if (-not $IAcceptUpgradeRisk) {
        Write-Banner "CONFIRMATION REQUIRED" 'Yellow'
        $prompt = "About to upgrade $($planItems.Count) package(s) via $Category. Type 'apply' to proceed, anything else to abort"
        $answer = Read-Host $prompt
        if ($answer -ne 'apply') {
            Write-Host ""
            Write-Host "  Aborted by user (got: '$answer'). No changes made." -ForegroundColor Yellow
            exit 0
        }
    } else {
        Write-Host ""
        Write-Host "  -IAcceptUpgradeRisk set — skipping confirmation prompt." -ForegroundColor DarkGray
    }

    # ── 4. Real apply ──────────────────────────────────────────────────────
    Write-Step "Phase 2/3 — running REAL apply (no --dry-run)"

    $applyRunsDir = Join-Path $env:TEMP "ascendo-real-apply-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Force -Path $applyRunsDir | Out-Null

    $applyArgv = @('-B', '-m', 'ascendo', 'run', '--category', $Category, '--phase', 'apply', '--runs-dir', $applyRunsDir)
    if ($Packages) { $applyArgv += @('--items', ($Packages -join ',')) }

    & python @applyArgv
    $applyExit = $LASTEXITCODE

    # ── 5. Parse + display apply sidecar ──────────────────────────────────
    Write-Step "Phase 3/3 — apply sidecar"

    $applySidecar = Find-Sidecar -RunsDir $applyRunsDir -FileName "apply__$Category.json"
    if (-not $applySidecar) {
        Write-Host "  [WARN] No apply sidecar found at $applyRunsDir\<run>\apply__$Category.json" -ForegroundColor Yellow
        Write-Host "         apply exit code was: $applyExit" -ForegroundColor Yellow
        exit $applyExit
    }

    Write-Host "  Apply sidecar: $($applySidecar.FullName)" -ForegroundColor DarkGray

    try {
        $apply = Get-Content $applySidecar.FullName -Raw | ConvertFrom-Json
    } catch {
        Write-Host "  [WARN] Apply sidecar is not valid JSON: $($_.Exception.Message)" -ForegroundColor Yellow
        exit $applyExit
    }

    $applyStatus = if ($apply.PSObject.Properties.Name -contains 'status') { $apply.status } else { '?' }
    Write-Host "  status = $applyStatus" -ForegroundColor DarkGray
    if ($apply.PSObject.Properties.Name -contains 'summary' -and $apply.summary) {
        Write-Host "  summary.total = $($apply.summary.total)" -ForegroundColor DarkGray
    }

    $applyItems = @()
    if ($apply.PSObject.Properties.Name -contains 'items' -and $apply.items) {
        $applyItems = @($apply.items)
    }

    if ($applyItems.Count -gt 0) {
        Write-Host ""
        Write-Host "  Per-item results:" -ForegroundColor Cyan
        $applyRows = foreach ($it in $applyItems) {
            $cur = if ($it.PSObject.Properties.Name -contains 'current_version' -and $it.current_version) { $it.current_version } else { '-' }
            $tgt = if ($it.PSObject.Properties.Name -contains 'target_version' -and $it.target_version) { $it.target_version } else { '-' }
            $rsv = if ($it.PSObject.Properties.Name -contains 'resolved_version' -and $it.resolved_version) { $it.resolved_version } else { '-' }
            $st  = if ($it.PSObject.Properties.Name -contains 'status' -and $it.status) { $it.status } else { '-' }
            $ec  = if ($it.PSObject.Properties.Name -contains 'exit_code' -and $null -ne $it.exit_code) { $it.exit_code } else { '-' }
            [pscustomobject]@{
                Package  = $it.id
                Status   = $st
                Current  = $cur
                Target   = $tgt
                Resolved = $rsv
                Exit     = $ec
            }
        }
        $applyRows | Format-Table -AutoSize | Out-String | Write-Host
    }

    if ($apply.PSObject.Properties.Name -contains 'messages' -and $apply.messages -and @($apply.messages).Count -gt 0) {
        Write-Host "  === sidecar.messages[] ===" -ForegroundColor Yellow
        foreach ($m in $apply.messages) {
            $lvl = if ($m.PSObject.Properties.Name -contains 'level' -and $m.level) { $m.level } else { 'info' }
            $txt = if ($m.PSObject.Properties.Name -contains 'text' -and $m.text) { $m.text } else { '' }
            $color = switch ($lvl) {
                'error' { 'Red' }
                'warn'  { 'Yellow' }
                default { 'DarkGray' }
            }
            Write-Host "  [$($lvl.ToUpper())] $txt" -ForegroundColor $color
        }
    }

    Write-Host ""
    switch ($applyExit) {
        0 { Write-Host "APPLY SUCCEEDED (exit 0)." -ForegroundColor Green }
        1 { Write-Host "APPLY PARTIALLY SUCCEEDED (exit 1) — see per-item status above." -ForegroundColor Yellow }
        2 { Write-Host "APPLY FAILED (exit 2) — see messages and per-item status above." -ForegroundColor Red }
        default { Write-Host "APPLY exited with code $applyExit." -ForegroundColor Red }
    }

    exit $applyExit
}
finally {
    Pop-Location
}

# =============================================================================
# Happy-path trace (dry, for review):
#
#   PS> .\bin\run-apply.ps1
#
#   ============================================================================
#   WARNING — REAL MUTATION HARNESS
#   ============================================================================
#     This script WILL upgrade real packages via the 'winget' source.
#     ...
#
#   ==> Phase 1/3 — running plan to enumerate intended changes
#   <python -m ascendo run --phase plan output>
#   Plan sidecar: C:\...\Temp\ascendo-pre-apply-plan-<guid>\<run>\plan__winget.json
#
#     The following 1 package(s) WILL be upgraded if you confirm:
#
#     Package              Current  Target  Source
#     -------              -------  ------  ------
#     Microsoft.PowerShell 7.6.0    7.6.1   winget
#
#   ============================================================================
#   CONFIRMATION REQUIRED
#   ============================================================================
#   About to upgrade 1 package(s) via winget. Type 'apply' to proceed, anything
#   else to abort: apply
#
#   ==> Phase 2/3 — running REAL apply (no --dry-run)
#   <python -m ascendo run --phase apply output>
#
#   ==> Phase 3/3 — apply sidecar
#     Apply sidecar: C:\...\Temp\ascendo-real-apply-<guid>\<run>\apply__winget.json
#     status = ok
#     summary.total = 1
#
#     Per-item results:
#     Package              Status   Current  Target  Resolved  Exit
#     -------              ------   -------  ------  --------  ----
#     Microsoft.PowerShell applied  7.6.0    7.6.1   7.6.1     0
#
#   APPLY SUCCEEDED (exit 0).
#
# Brace/paren balance: try { ... } finally { ... } with one Push/Pop-Location;
# all if/else/foreach/switch/try blocks open and close in pairs.
# =============================================================================
