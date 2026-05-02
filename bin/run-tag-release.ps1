#!/usr/bin/env pwsh
<#
.SYNOPSIS
  End-to-end Windows MVP validation + first real apply + v0.0.7-alpha tag.

.DESCRIPTION
  Run from an Admin PowerShell. Walks the user through:
    1. preflight (admin? worktree? dependencies?)
    2. snapshot via VSS
    3. plan: print what would be upgraded
    4. confirm gate (literal 'apply')
    5. real apply via the Ascendo CLI
    6. verify
    7. cleanup
    8. tag v0.0.7-alpha (skip if --no-tag)

.PARAMETER NoTag
  Skip the git tag step. Useful for re-running the apply path
  without polluting the tag namespace.

.PARAMETER NoSnapshot
  Skip the VSS snapshot. The apply still runs.

.PARAMETER Category
  Default: 'winget'. Pass a comma-separated list to widen scope.

.PARAMETER IAcceptUpgradeRisk
  Skip the interactive 'apply' confirmation. Use only in automation.

.PARAMETER WhatIf
  Print everything but don't mutate. Maps to Ascendo's --dry-run.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$NoTag,
    [switch]$NoSnapshot,
    [string]$Category = 'winget',
    [switch]$IAcceptUpgradeRisk
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# 1. Preflight ──────────────────────────────────────────
function Test-IsAdmin {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltinRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Host "ERROR: this script must run from an elevated PowerShell." -ForegroundColor Red
    Write-Host "       Right-click PowerShell -> Run as Administrator." -ForegroundColor Yellow
    exit 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
Write-Host "[preflight] repo: $repoRoot" -ForegroundColor Cyan

# Worktree path resolution: prepend our core/ to PYTHONPATH so we use
# THIS branch's code (not the editable install pointing at the primary).
$env:PYTHONPATH = "$repoRoot\core" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { "" })
Write-Host "[preflight] PYTHONPATH=$env:PYTHONPATH" -ForegroundColor DarkGray

# 2. Snapshot ───────────────────────────────────────────
if (-not $NoSnapshot) {
    Write-Host "[1/7] creating VSS restore point..." -ForegroundColor Cyan
    if ($PSCmdlet.ShouldProcess("VSS", "Checkpoint-Computer")) {
        Checkpoint-Computer -Description "Ascendo run-tag-release $(Get-Date -Format 'yyyy-MM-dd_HH-mm')" `
                            -RestorePointType MODIFY_SETTINGS
        Write-Host "       snapshot ok" -ForegroundColor Green
    }
} else {
    Write-Host "[1/7] skipping snapshot (per --NoSnapshot)" -ForegroundColor Yellow
}

# 3. Plan ───────────────────────────────────────────────
Write-Host "[2/7] running plan phase..." -ForegroundColor Cyan
$planExit = (Start-Process -FilePath python -NoNewWindow -Wait -PassThru `
                -ArgumentList "-m","ascendo","run","--category",$Category,"--phase","plan").ExitCode
if ($planExit -ne 0) {
    Write-Host "       plan failed with exit $planExit" -ForegroundColor Red
    exit $planExit
}

# 4. Confirm ────────────────────────────────────────────
if (-not $IAcceptUpgradeRisk -and -not $WhatIfPreference) {
    Write-Host "" -ForegroundColor Yellow
    Write-Host "About to run REAL APPLY on category(ies): $Category" -ForegroundColor Red -BackgroundColor Black
    Write-Host "Type 'apply' to proceed, anything else aborts:" -ForegroundColor Yellow
    $answer = Read-Host
    if ($answer -ne 'apply') {
        Write-Host "Aborted by user." -ForegroundColor Yellow
        exit 0
    }
}

# 5. Apply ──────────────────────────────────────────────
$applyArgs = @("-m","ascendo","run","--category",$Category,"--phase","apply")
if ($WhatIfPreference) { $applyArgs += "--dry-run" }
Write-Host "[3/7] running apply phase..." -ForegroundColor Cyan
$applyExit = (Start-Process -FilePath python -NoNewWindow -Wait -PassThru `
                  -ArgumentList $applyArgs).ExitCode
$rebootNeeded = $applyExit -eq 75
if ($rebootNeeded) {
    Write-Host "       apply ok, REBOOT REQUIRED" -ForegroundColor Yellow
} elseif ($applyExit -eq 0) {
    Write-Host "       apply ok" -ForegroundColor Green
} else {
    Write-Host "       apply failed with exit $applyExit" -ForegroundColor Red
    exit $applyExit
}

# 6. Verify ─────────────────────────────────────────────
Write-Host "[4/7] running verify phase..." -ForegroundColor Cyan
python -m ascendo run --category $Category --phase verify

# 7. Cleanup ────────────────────────────────────────────
Write-Host "[5/7] running cleanup phase..." -ForegroundColor Cyan
python -m ascendo run --category $Category --phase cleanup

# 8. Health card ────────────────────────────────────────
Write-Host "[6/7] post-run health snapshot..." -ForegroundColor Cyan
python -m ascendo doctor

# 9. Tag ────────────────────────────────────────────────
if (-not $NoTag -and -not $WhatIfPreference) {
    Write-Host "[7/7] tagging v0.0.7-alpha..." -ForegroundColor Cyan
    $existing = (git tag -l 'v0.0.7-alpha').Trim()
    if ($existing) {
        Write-Host "       tag v0.0.7-alpha already exists locally; skipping." -ForegroundColor Yellow
        Write-Host "       to retag (move to current HEAD): git tag -fa v0.0.7-alpha -m '...'" -ForegroundColor DarkGray
    } else {
        git tag -a v0.0.7-alpha -m "Windows MVP feature-complete on real DP5520WMK + frontend apply UX + Tauri 2.x scaffold"
        if ($LASTEXITCODE -eq 0) {
            Write-Host "       tag created. Push with: git push --tags" -ForegroundColor Green
        } else {
            Write-Host "       tag creation failed (exit $LASTEXITCODE)." -ForegroundColor Red
        }
    }
} else {
    Write-Host "[7/7] skipping tag (per -NoTag or -WhatIf)" -ForegroundColor Yellow
}

if ($rebootNeeded) {
    Write-Host ""
    Write-Host "+--------------------------------------------------------+" -ForegroundColor Yellow
    Write-Host "| reboot required to complete updates.                  |" -ForegroundColor Yellow
    Write-Host "| run: shutdown /r /t 30  (30-second countdown)         |" -ForegroundColor Yellow
    Write-Host "+--------------------------------------------------------+" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==> Ascendo run-tag-release complete." -ForegroundColor Green
