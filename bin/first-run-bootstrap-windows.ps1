#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Smart first-run bootstrap for the Windows .msi/.exe installer.

.DESCRIPTION
  After the Tauri-bundled MSI/NSIS installer drops the Ascendo files
  into %ProgramFiles%\Ascendo\ (or %LOCALAPPDATA%\Ascendo\), it invokes
  this script to:

    1. Detect Python 3.11+; install python@3.12 via winget if missing.
    2. Detect git, curl; install via winget if missing.
    3. Run install.ps1 from the canonical repo to set up the user-side
       venv + editable install + PATH shim.
    4. Verify with `ascendo doctor`.
    5. Drop a marker file at %LOCALAPPDATA%\Ascendo\.bootstrapped so
       subsequent launches go straight to the dashboard.

  Idempotent: re-running on an already-bootstrapped machine is a no-op
  unless -Force is passed.

.PARAMETER Edition
  basic | dev. Default: basic.

.PARAMETER Profile
  cli | web | desktop | full. Default: full.

.PARAMETER Force
  Re-run even if the marker file exists.

.PARAMETER NonInteractive
  Bail rather than prompt; mirrors install.ps1's same-named flag.

.NOTES
  Triggered by:
    - The MSI's CustomAction at install-finish (deferred custom action,
      runs in user context with elevation).
    - The NSIS hook NSIS_HOOK_POSTINSTALL when ASCENDO_INSTALL_AS_SERVICE
      is NOT set (when set, install-service.ps1 takes over).
    - bin/Ascendo.cmd on first run.
#>
[CmdletBinding()]
param(
    [ValidateSet('basic','dev')]
    [string]$Edition,

    [ValidateSet('cli','web','desktop','full')]
    [string]$Profile,

    [switch]$Force,
    [switch]$NonInteractive
)

# Resolve defaults from env vars (PS5.1-compatible — no ternary).
if (-not $Edition) {
    if ($env:ASCENDO_EDITION) { $Edition = $env:ASCENDO_EDITION } else { $Edition = 'basic' }
}
if (-not $Profile) {
    if ($env:ASCENDO_PROFILE) { $Profile = $env:ASCENDO_PROFILE } else { $Profile = 'full' }
}

$ErrorActionPreference = 'Stop'

$SupportDir = Join-Path $env:LOCALAPPDATA 'Ascendo'
$Marker = Join-Path $SupportDir '.bootstrapped'
$Log = Join-Path $SupportDir 'first-run.log'

New-Item -ItemType Directory -Force -Path $SupportDir | Out-Null

function Write-Step($msg) { "`n==> $msg" | Tee-Object -FilePath $Log -Append }
function Write-Ok($msg)   { "  [OK] $msg"  | Tee-Object -FilePath $Log -Append }
function Write-Warn2($msg){ "  [WARN] $msg" | Tee-Object -FilePath $Log -Append; Write-Warning $msg }
function Write-Info($msg) { "  $msg" | Tee-Object -FilePath $Log -Append }
function Throw-Fail($msg) {
    "`n[FAIL] $msg" | Tee-Object -FilePath $Log -Append
    throw $msg
}

# Reset log on every full bootstrap.
Set-Content -Path $Log -Value "" -Force

if ((Test-Path $Marker) -and -not $Force) {
    Write-Ok "Already bootstrapped — nothing to do (re-run with -Force to redo)."
    exit 0
}

Write-Step "Ascendo first-run bootstrap (Windows)"
Write-Info "Edition:  $Edition"
Write-Info "Profile:  $Profile"
Write-Info "Marker:   $Marker"
Write-Info "Log:      $Log"

# ── 1. winget ────────────────────────────────────────────────────────────
Write-Step "Detect winget"
$wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
if (-not $wingetCmd) {
    Write-Warn2 "winget not on PATH. Ascendo uses winget to install missing dependencies."
    Write-Info  "Install winget via the Microsoft Store (App Installer) and re-launch Ascendo."
    Throw-Fail  "winget missing"
}
Write-Ok "winget: $($wingetCmd.Source)"

function Install-IfMissing {
    param([string]$Probe, [string]$WingetId, [string]$Friendly)
    if (Get-Command $Probe -ErrorAction SilentlyContinue) {
        Write-Ok "$Probe : $((Get-Command $Probe).Source)"
        return
    }
    Write-Info "Installing $Friendly via winget…"
    & winget install --id $WingetId --silent --accept-package-agreements --accept-source-agreements 2>&1 |
        Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { Throw-Fail "winget install $WingetId failed (exit $LASTEXITCODE)" }
    # Refresh PATH from registry without restarting the shell.
    $env:Path =
        [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path','User')
    Write-Ok "$Friendly installed"
}

# ── 2. Python 3.11+ ──────────────────────────────────────────────────────
Write-Step "Check + install Python ≥ 3.11"
$pythonOk = $false
foreach ($p in 'python3','python','py') {
    $cmd = Get-Command $p -ErrorAction SilentlyContinue
    if ($cmd) {
        $verExpr = 'import sys; print("%d.%d" % sys.version_info[:2])'
        $ver = & $cmd.Source -c $verExpr 2>$null
        if ($ver -match '^3\.(\d+)$') {
            $minor = [int]$Matches[1]
            if ($minor -ge 11) {
                Write-Ok "$($cmd.Name) $ver"
                $pythonOk = $true
                break
            }
        }
    }
}
if (-not $pythonOk) {
    Install-IfMissing -Probe 'python.exe' -WingetId 'Python.Python.3.12' -Friendly 'Python 3.12'
}

# ── 3. git + curl ────────────────────────────────────────────────────────
Write-Step "Check + install git, curl"
Install-IfMissing -Probe 'git'  -WingetId 'Git.Git'        -Friendly 'Git'
Install-IfMissing -Probe 'curl' -WingetId 'curl.curl'      -Friendly 'curl'

# ── 4. Run install.ps1 ──────────────────────────────────────────────────
Write-Step "Run Ascendo installer (install.ps1)"

$InstallerUrl = 'https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1'
$InstallerLocal = Join-Path $SupportDir 'install.ps1'

try {
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerLocal -UseBasicParsing -TimeoutSec 30
    Write-Ok "Downloaded $InstallerUrl"
} catch {
    Throw-Fail "Failed to download installer: $_"
}

$installArgs = @{
    Edition = $Edition
    Profile = $Profile
}
if ($NonInteractive) { $installArgs['NonInteractive'] = $true }

# Use & operator with a hashtable splat.
& $InstallerLocal @installArgs 2>&1 | Tee-Object -FilePath $Log -Append
if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
    Throw-Fail "install.ps1 exited with $LASTEXITCODE"
}
Write-Ok "install.ps1 completed"

# ── 5. Verify with ascendo doctor ────────────────────────────────────────
Write-Step "Verify install (ascendo doctor)"

# install.ps1 places the shim at %LOCALAPPDATA%\Microsoft\WindowsApps\ascendo.cmd.
$ascendoBin = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\ascendo.cmd'
if (-not (Test-Path $ascendoBin)) {
    $cmd = Get-Command ascendo -ErrorAction SilentlyContinue
    if ($cmd) { $ascendoBin = $cmd.Source }
}
if (-not (Test-Path $ascendoBin)) {
    Throw-Fail "ascendo binary not found after install"
}

& $ascendoBin doctor 2>&1 | Tee-Object -FilePath $Log -Append
if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
    Throw-Fail "ascendo doctor reported issues (exit $LASTEXITCODE) — see $Log"
}
Write-Ok "ascendo doctor passed"

# ── 6. Mark bootstrapped ─────────────────────────────────────────────────
$markerContent = @"
edition=$Edition
profile=$Profile
completed_at=$([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))
ascendo_bin=$ascendoBin
"@
Set-Content -Path $Marker -Value $markerContent -Force

Write-Ok "Wrote marker: $Marker"
Write-Step "Bootstrap complete — Ascendo ready"
exit 0
