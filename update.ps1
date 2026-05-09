<#
.SYNOPSIS
    Ascendo — one-liner updater for Windows.

.DESCRIPTION
    Canonical usage:

        iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.ps1 | iex

    Detects an existing installation under %LOCALAPPDATA%\Ascendo\src,
    runs `git pull --ff-only`, refreshes editable pip installs, restarts
    the AscendoDashboard service if it's installed, and runs
    `ascendo doctor` as a self-test.

.PARAMETER Verbose
    Print every command before running it.

.EXAMPLE
    iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.ps1 | iex

.EXAMPLE
    # From a checkout:
    .\update.ps1 -Verbose
#>
[CmdletBinding()]
param(
    [switch] $Help
)

$ErrorActionPreference = 'Stop'
if ($env:ASCENDO_VERBOSE -eq '1') { $VerbosePreference = 'Continue' }

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Full | Out-String | Write-Host
    exit 0
}

$ASCENDO_HOME = if ($env:ASCENDO_HOME) { $env:ASCENDO_HOME } else { Join-Path $env:LOCALAPPDATA 'Ascendo' }
$INSTALL_DIR  = Join-Path $ASCENDO_HOME 'src'
$VENV_DIR     = Join-Path $ASCENDO_HOME 'venv'
$REPO_BRANCH  = if ($env:ASCENDO_BRANCH) { $env:ASCENDO_BRANCH } else { 'main' }

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Info($msg) { Write-Host "[info]  $msg" -ForegroundColor DarkGray }
function Write-OK($msg)   { Write-Host "[ ok ]  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[warn]  $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[fail]  $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $INSTALL_DIR '.git'))) {
    Write-Warn "No installation found at $INSTALL_DIR."
    Write-Info "Run the installer first:"
    Write-Info "  iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 | iex"
    exit 1
}

Write-Step "Updating Ascendo"
Write-Info "Install dir: $INSTALL_DIR"
Write-Info "Branch:      $REPO_BRANCH"

# Network preflight
try {
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com' -Method Head -TimeoutSec 8 -ErrorAction Stop | Out-Null
    Write-OK "Network: github.com reachable"
} catch {
    Write-Fail "github.com unreachable. Check connection / proxy."
}

# venv check
$Py = Join-Path $VENV_DIR 'Scripts\python.exe'
if (-not (Test-Path $Py)) {
    Write-Warn "venv missing at $VENV_DIR — recreating."
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Fail "python.exe not on PATH."
    }
    & python -m venv $VENV_DIR
    if ($LASTEXITCODE -ne 0) { Write-Fail "venv create failed." }
}

# Pre-update version
$oldVersion = (& $Py -c 'from ascendo import __version__; print(__version__)' 2>$null)
if (-not $oldVersion) { $oldVersion = 'unknown' }
$oldCommit = (& git -C $INSTALL_DIR rev-parse --short HEAD 2>$null)
Write-Info "Current: $oldVersion ($oldCommit)"

# Refuse merge if local changes
$dirty = (& git -C $INSTALL_DIR status --porcelain 2>$null)
if ($dirty) {
    Write-Fail "Local changes in $INSTALL_DIR. Stash/commit them, or run install.ps1 -Reinstall."
}

Write-Step "git pull --ff-only origin $REPO_BRANCH"
& git -C $INSTALL_DIR fetch --quiet origin $REPO_BRANCH
if ($LASTEXITCODE -ne 0) { Write-Fail "git fetch failed." }
& git -C $INSTALL_DIR pull --ff-only origin $REPO_BRANCH
if ($LASTEXITCODE -ne 0) { Write-Fail "Cannot fast-forward. Re-run install.ps1 -Reinstall." }

Write-Step "Refreshing Python packages"
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install --upgrade -e (Join-Path $INSTALL_DIR 'core') --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail "core upgrade failed." }

$adapter = Join-Path $INSTALL_DIR 'adapters\windows'
if (Test-Path $adapter) {
    & $Py -m pip install --upgrade -e $adapter --no-deps --quiet
}

# Refresh dashboard deps if present
& $Py -m pip show fastapi *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Info "Refreshing dashboard deps…"
    & $Py -m pip install --upgrade fastapi 'uvicorn[standard]' httpx --quiet
}

# Restart service if installed
$svc = Get-Service -Name 'AscendoDashboard' -ErrorAction SilentlyContinue
if ($svc) {
    Write-Step "Restarting AscendoDashboard service"
    try {
        Restart-Service -Name 'AscendoDashboard' -ErrorAction Stop
        Write-OK "Service restarted"
    } catch {
        Write-Warn "Could not restart service automatically (need admin?). $($_.Exception.Message)"
    }
}

# Self-test
Write-Step "Self-test"
$newVersion = (& $Py -c 'from ascendo import __version__; print(__version__)' 2>$null)
if (-not $newVersion) { $newVersion = 'unknown' }
$newCommit = (& git -C $INSTALL_DIR rev-parse --short HEAD 2>$null)

& $Py -m ascendo doctor *> $null
if ($LASTEXITCODE -eq 0) {
    Write-OK "ascendo doctor: green"
} else {
    Write-Warn "ascendo doctor returned non-zero. Run '& '$Py' -m ascendo doctor' to inspect."
}

Write-Step "Update complete"
Write-Host "  Before: $oldVersion ($oldCommit)" -ForegroundColor White
Write-Host "  After:  $newVersion ($newCommit)" -ForegroundColor White
if ($oldCommit -eq $newCommit) {
    Write-Info "Already up to date."
} else {
    Write-OK "Upgraded $oldCommit -> $newCommit"
}
