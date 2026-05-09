<#
.SYNOPSIS
    Ascendo — one-liner installer for Windows.

.DESCRIPTION
    Canonical usage (from PowerShell 5.1+ or 7.x):

        iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 | iex

    Detects + auto-installs prerequisites (Python 3.11+, git) via winget,
    clones to %LOCALAPPDATA%\Ascendo\src, creates a venv, pip-installs
    core/ + adapters/windows/ editable, drops an `ascendo.cmd` shim into
    %LOCALAPPDATA%\Microsoft\WindowsApps (which is on PATH by default
    on Win10 1809+), and runs `ascendo doctor` as a self-test.

.PARAMETER Reinstall
    Wipe %LOCALAPPDATA%\Ascendo\src and rebuild from scratch.

.PARAMETER Update
    Update an existing installation (git pull + pip --upgrade) and exit.

.PARAMETER Profile
    cli  — fastest, sparse checkout, ~30 MB.
    web  — adds FastAPI dashboard. (Default.)
    desktop — adds Tauri 2.x desktop shell prerequisites notice.

.PARAMETER Language
    en | pl. Default: en.

.PARAMETER NonInteractive
    Refuse to prompt; use defaults / parameter values.

.PARAMETER Verbose
    Trace every step.

.EXAMPLE
    iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 | iex

.EXAMPLE
    # Unattended install:
    $env:ASCENDO_NONINTERACTIVE = '1'
    $env:ASCENDO_PROFILE = 'web'
    iwr -useb .../install.ps1 | iex
#>
[CmdletBinding()]
param(
    [switch] $Reinstall,
    [switch] $Update,
    [ValidateSet('cli','web','desktop')] [string] $Profile = $env:ASCENDO_PROFILE,
    [ValidateSet('en','pl')]              [string] $Language = $env:ASCENDO_LANG,
    [switch] $NonInteractive,
    [switch] $Help
)

# ── Setup ─────────────────────────────────────────────────────────────────
$ErrorActionPreference = 'Stop'
if ($env:ASCENDO_VERBOSE -eq '1') { $VerbosePreference = 'Continue' }
if ($env:ASCENDO_NONINTERACTIVE -eq '1') { $NonInteractive = $true }
if (-not $Profile)  { $Profile = 'web' }
if (-not $Language) { $Language = 'en' }

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Full | Out-String | Write-Host
    exit 0
}

# ── Constants ─────────────────────────────────────────────────────────────
$ASCENDO_HOME       = if ($env:ASCENDO_HOME) { $env:ASCENDO_HOME } else { Join-Path $env:LOCALAPPDATA 'Ascendo' }
$INSTALL_DIR        = Join-Path $ASCENDO_HOME 'src'
$VENV_DIR           = Join-Path $ASCENDO_HOME 'venv'
$REPO_URL           = if ($env:ASCENDO_REPO_URL) { $env:ASCENDO_REPO_URL } else { 'https://github.com/KasprowiczM/ascendo.git' }
$REPO_BRANCH        = if ($env:ASCENDO_BRANCH)   { $env:ASCENDO_BRANCH }   else { 'main' }
$WINDOWSAPPS        = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'
$SHIM_PATH          = Join-Path $WINDOWSAPPS 'ascendo.cmd'
$MIN_DISK_MB        = 1024

# ── Logging ───────────────────────────────────────────────────────────────
function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Info($msg) { Write-Host "[info]  $msg" -ForegroundColor DarkGray }
function Write-OK($msg)   { Write-Host "[ ok ]  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[warn]  $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[fail]  $msg" -ForegroundColor Red; exit 1 }

# ── i18n (minimal — installer is short-lived) ─────────────────────────────
function T($key) {
    $strings = @{
        'en-WELCOME' = 'Welcome to the Ascendo installer (Windows).'
        'pl-WELCOME' = 'Witaj w instalatorze Ascendo (Windows).'
        'en-DONE'    = 'Installation complete.'
        'pl-DONE'    = 'Instalacja zakończona.'
    }
    $k = "$Language-$key"
    if ($strings.ContainsKey($k)) { return $strings[$k] }
    return $strings["en-$key"]
}

# ── Prereq checks ─────────────────────────────────────────────────────────
function Test-WindowsVersion {
    $ver = [System.Environment]::OSVersion.Version
    $build = $ver.Build
    if ($ver.Major -lt 10 -or ($ver.Major -eq 10 -and $build -lt 17763)) {
        Write-Fail "Windows 10 1809 (build 17763) or later required. Current: $($ver) build $build."
    }
    Write-OK "Windows $($ver.Major).$($ver.Minor) build $build"
}

function Test-Network {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com' -Method Head -TimeoutSec 8 -ErrorAction Stop
        Write-OK "Network: github.com reachable"
    } catch {
        Write-Fail "github.com unreachable. Check connection / proxy. Error: $($_.Exception.Message)"
    }
}

function Test-Disk {
    param([string] $Path)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $drive = (Get-Item $parent).PSDrive
    if ($drive) {
        $freeMB = [int]($drive.Free / 1MB)
        if ($freeMB -lt $MIN_DISK_MB) {
            Write-Fail "Less than $MIN_DISK_MB MB free on $($drive.Name): drive (have $freeMB MB)."
        }
        Write-OK "Disk: $freeMB MB free on $($drive.Name):"
    }
}

function Get-CommandPath($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source } else { return $null }
}

function Test-WingetAvailable {
    return [bool] (Get-CommandPath 'winget')
}

function Install-Python {
    if (Get-CommandPath 'python') {
        $ver = & python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>$null
        if ($ver -and ([Version]$ver -ge [Version]'3.11')) {
            Write-OK "Python $ver"
            return
        }
        Write-Warn "Python $ver detected, but 3.11+ required. Will offer to install 3.12."
    } else {
        Write-Warn "Python not found on PATH."
    }
    if (-not (Test-WingetAvailable)) {
        Write-Fail "winget is not available — install Python 3.12 manually from https://python.org and re-run."
    }
    if (-not $NonInteractive) {
        $reply = Read-Host "Install Python 3.12 via winget now? [Y/n]"
        if ($reply -and $reply -notmatch '^[Yy]') { Write-Fail "Cannot continue without Python 3.11+." }
    }
    Write-Info "winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements"
    & winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Write-Fail "winget failed to install Python (exit $LASTEXITCODE)." }
    # Refresh PATH from registry so this session picks up the new python.exe.
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
    if (-not (Get-CommandPath 'python')) {
        Write-Fail "Python install reported success but python.exe still not on PATH. Restart shell + retry."
    }
    Write-OK "Python installed: $(& python --version 2>&1)"
}

function Install-Git {
    if (Get-CommandPath 'git') {
        Write-OK "git: $(& git --version)"
        return
    }
    Write-Warn "git not found on PATH."
    if (-not (Test-WingetAvailable)) {
        Write-Fail "winget unavailable — install Git for Windows from https://git-scm.com/download/win and re-run."
    }
    if (-not $NonInteractive) {
        $reply = Read-Host "Install Git via winget now? [Y/n]"
        if ($reply -and $reply -notmatch '^[Yy]') { Write-Fail "Cannot continue without git." }
    }
    Write-Info "winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements"
    & winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Write-Fail "winget failed to install git (exit $LASTEXITCODE)." }
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
    if (-not (Get-CommandPath 'git')) {
        Write-Fail "git install reported success but git.exe still not on PATH. Restart shell + retry."
    }
    Write-OK "git installed."
}

# ── Update path ───────────────────────────────────────────────────────────
function Invoke-Update {
    if (-not (Test-Path (Join-Path $INSTALL_DIR '.git'))) {
        Write-Fail "No installation found at $INSTALL_DIR. Run install.ps1 (without -Update) first."
    }
    Write-Step "Updating Ascendo at $INSTALL_DIR"
    Test-Network

    $py = Join-Path $VENV_DIR 'Scripts\python.exe'
    if (-not (Test-Path $py)) { Write-Fail "venv missing at $VENV_DIR. Re-run with -Reinstall." }

    $oldVersion = (& $py -c 'from ascendo import __version__; print(__version__)' 2>$null)
    if (-not $oldVersion) { $oldVersion = 'unknown' }
    $oldCommit = (& git -C $INSTALL_DIR rev-parse --short HEAD 2>$null)
    Write-Info "Current: $oldVersion ($oldCommit)"

    Write-Step "git pull --ff-only origin $REPO_BRANCH"
    & git -C $INSTALL_DIR fetch --quiet origin $REPO_BRANCH
    & git -C $INSTALL_DIR pull --ff-only origin $REPO_BRANCH
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Cannot fast-forward. Local changes? Re-run install.ps1 -Reinstall."
    }

    Write-Step "Refreshing Python packages"
    & $py -m pip install --upgrade pip --quiet
    & $py -m pip install --upgrade -e (Join-Path $INSTALL_DIR 'core') --quiet
    $adapter = Join-Path $INSTALL_DIR 'adapters\windows'
    if (Test-Path $adapter) {
        & $py -m pip install --upgrade -e $adapter --no-deps --quiet
    }
    & $py -m pip show fastapi 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & $py -m pip install --upgrade fastapi 'uvicorn[standard]' httpx --quiet
    }

    # Restart Windows service if installed
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

    Write-Step "Self-test"
    $newVersion = (& $py -c 'from ascendo import __version__; print(__version__)' 2>$null)
    if (-not $newVersion) { $newVersion = 'unknown' }
    $newCommit = (& git -C $INSTALL_DIR rev-parse --short HEAD 2>$null)
    & $py -m ascendo doctor *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "ascendo doctor: green"
    } else {
        Write-Warn "ascendo doctor returned non-zero. Run '& '$py' -m ascendo doctor' to see why."
    }

    Write-Host ""
    Write-Host "  Before: $oldVersion ($oldCommit)" -ForegroundColor White
    Write-Host "  After:  $newVersion ($newCommit)" -ForegroundColor White
    if ($oldCommit -eq $newCommit) {
        Write-Info "Already up to date."
    } else {
        Write-OK "Upgraded $oldCommit -> $newCommit"
    }
    exit 0
}

# ── Main ──────────────────────────────────────────────────────────────────
Write-Step (T 'WELCOME')
Write-Info "Mode: $(if($Update){'update'}else{'install'})   Profile: $Profile   Lang: $Language   NonInteractive: $NonInteractive"

Test-WindowsVersion

if ($Update) { Invoke-Update }

Write-Step "Preflight"
Test-Network
Test-Disk -Path $ASCENDO_HOME
Install-Python
Install-Git

# Save language
$cfgDir = Join-Path $env:APPDATA 'Ascendo'
if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null }
Set-Content -Path (Join-Path $cfgDir 'locale.txt') -Value $Language -NoNewline -Encoding UTF8
Write-OK "Language saved to $cfgDir\locale.txt"

# Reinstall: nuke prior
if ($Reinstall -and (Test-Path $INSTALL_DIR)) {
    Write-Warn "-Reinstall: removing $INSTALL_DIR"
    Remove-Item -Recurse -Force $INSTALL_DIR
    if (Test-Path $VENV_DIR) { Remove-Item -Recurse -Force $VENV_DIR }
}

# Clone or pull
Write-Step "Repo @ $INSTALL_DIR"
if (Test-Path (Join-Path $INSTALL_DIR '.git')) {
    Write-Info "Existing checkout — pulling latest…"
    & git -C $INSTALL_DIR fetch --quiet origin $REPO_BRANCH
    & git -C $INSTALL_DIR pull --ff-only origin $REPO_BRANCH
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Fast-forward failed — local changes? Re-run with -Reinstall."
    }
} else {
    Write-Info "Cloning $REPO_URL (branch: $REPO_BRANCH)…"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $INSTALL_DIR) | Out-Null
    if ($Profile -eq 'cli') {
        & git clone --filter=blob:none --no-checkout -b $REPO_BRANCH $REPO_URL $INSTALL_DIR
        Push-Location $INSTALL_DIR
        try {
            & git sparse-checkout init --cone
            & git sparse-checkout set core adapters bin schemas docs plugins lib scripts share i18n
            & git checkout $REPO_BRANCH
        } finally { Pop-Location }
    } else {
        & git clone -b $REPO_BRANCH $REPO_URL $INSTALL_DIR
    }
    if ($LASTEXITCODE -ne 0) { Write-Fail "git clone failed (exit $LASTEXITCODE)." }
}
Write-OK "Repo ready"

# venv
Write-Step "Python venv"
if (-not (Test-Path (Join-Path $VENV_DIR 'Scripts\python.exe'))) {
    Write-Info "Creating venv at $VENV_DIR"
    & python -m venv $VENV_DIR
    if ($LASTEXITCODE -ne 0) { Write-Fail "python -m venv failed (exit $LASTEXITCODE)." }
}
$Py = Join-Path $VENV_DIR 'Scripts\python.exe'
& $Py -m pip install --upgrade pip --quiet

Write-Step "pip install -e core/ + adapters/windows/"
& $Py -m pip install -e (Join-Path $INSTALL_DIR 'core') --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail "core install failed." }

$adapter = Join-Path $INSTALL_DIR 'adapters\windows'
if (Test-Path $adapter) {
    & $Py -m pip install -e $adapter --no-deps --quiet
    & $Py -m pip install 'pywin32>=306' 'pywin32-ctypes>=0.2' --quiet
}

if ($Profile -eq 'web' -or $Profile -eq 'desktop') {
    Write-Step "pip install fastapi 'uvicorn[standard]' httpx"
    & $Py -m pip install 'fastapi>=0.111' 'uvicorn[standard]>=0.30' 'httpx>=0.27' --quiet
}

if ($Profile -eq 'desktop') {
    Write-Step "Desktop toolchain notice"
    Write-Info "Tauri 2.x desktop builds require Rust + Node.js + WebView2."
    Write-Info "Install (one-time) with:"
    Write-Info "  winget install Rustlang.Rustup"
    Write-Info "  winget install OpenJS.NodeJS.LTS"
    Write-Info "Then run: bin\launch-desktop.ps1 -Build"
}

# Shim
Write-Step "CLI shim"
if (-not (Test-Path $WINDOWSAPPS)) { New-Item -ItemType Directory -Force -Path $WINDOWSAPPS | Out-Null }
@"
@echo off
REM Ascendo CLI shim — generated by install.ps1.
"$Py" -m ascendo %*
"@ | Set-Content -Path $SHIM_PATH -Encoding ASCII
Write-OK "Shim at $SHIM_PATH (WindowsApps is on PATH by default)"

# Self-test
Write-Step "Self-test"
& $Py -m ascendo doctor *> $null
if ($LASTEXITCODE -eq 0) {
    Write-OK "ascendo doctor: green"
} else {
    Write-Warn "ascendo doctor returned non-zero (exit $LASTEXITCODE). Run with -Verbose to debug."
}

# Done
Write-Step (T 'DONE')
Write-Host ""
Write-Host "  ascendo doctor                 # health snapshot" -ForegroundColor Green
Write-Host "  ascendo run --phase check      # find updates (read-only)" -ForegroundColor Green
Write-Host "  ascendo run --phase apply      # apply updates (gated)" -ForegroundColor Green
if ($Profile -eq 'web' -or $Profile -eq 'desktop') {
    Write-Host "  ascendo dashboard --port 8765  # open http://127.0.0.1:8765/" -ForegroundColor Green
}
Write-Host ""
Write-Host "  Repo:   $INSTALL_DIR"
Write-Host "  venv:   $VENV_DIR"
Write-Host "  Update: iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.ps1 | iex"
Write-Host ""
Write-OK (T 'DONE')
