#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Manage the AscendoDashboard Windows service.

.DESCRIPTION
  Wraps NSSM (the Non-Sucking Service Manager) so the Ascendo FastAPI
  dashboard runs as a real Windows service: auto-start at boot, recover
  on crashes, and listen on 127.0.0.1:8765 24/7. The user's mental model
  is "Settings -> Service: Install" -> always-on dashboard, double-click
  the desktop icon and the SPA loads instantly without spawning a
  sidecar.

  This script is the canonical front-end for service-related actions.
  The dashboard's REST surface (POST /service/{install,uninstall,...})
  ultimately funnels through the same NSSM commands via the Python
  service manager wrapper.

.PARAMETER Action
  install    -- download NSSM if missing, register the service, start it,
                health-check it, exit 0 on success.
  uninstall  -- stop + remove the service. Idempotent; exit 0 even if
                the service does not exist.
  start      -- start the (already-installed) service.
  stop       -- stop the service.
  restart    -- stop + start (no-op if already stopped).
  status     -- print human or JSON status of the service.

.PARAMETER InstallPath
  Override the install root. Defaults to the parent of this script
  (the repo root, or %ProgramFiles%\Ascendo if shipped via the
  installer). The service launches python from this directory's venv
  if one exists, otherwise falls back to the system python or to the
  bundled PyInstaller exe.

.PARAMETER Json
  Status mode only: emit a single-line JSON document on stdout.

.PARAMETER PurgeLogs
  Uninstall mode only: also delete the service log directory at
  %LocalAppData%\Ascendo\logs\service.

.PARAMETER ServiceName
  Override the service name. Default: AscendoDashboard.

.PARAMETER Port
  TCP port the dashboard binds to. Default: 8765. The health probe
  during install uses this same port.

.EXAMPLE
  # Install (run from elevated PowerShell):
  pwsh -File bin\install-service.ps1 -Action install

.EXAMPLE
  # Uninstall + remove logs:
  pwsh -File bin\install-service.ps1 -Action uninstall -PurgeLogs

.EXAMPLE
  # Machine-readable status for the dashboard backend:
  pwsh -File bin\install-service.ps1 -Action status -Json

.NOTES
  Requires admin/elevation for install/uninstall/start/stop/restart.
  status works without elevation (read-only via sc.exe query).

  Logs land in %LocalAppData%\Ascendo\logs\service\{stdout,stderr}.log
  with NSSM's AppRotate policy (10 MB, daily). To tweak the service
  config after install, run: nssm edit AscendoDashboard.

  Exit codes:
    0  -- success
    1  -- generic failure (caller should inspect stderr)
    2  -- bad args / unknown action
    10 -- NSSM download / verification failed (no network, hash
          mismatch, write permission denied)
    11 -- elevation required but not held
    12 -- service installed but health probe failed within 15s
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'restart', 'status')]
    [string] $Action,

    [string] $InstallPath,

    [string] $ServiceName = 'AscendoDashboard',

    [int] $Port = 8765,

    [switch] $Json,

    [switch] $PurgeLogs
)

$ErrorActionPreference = 'Stop'

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

# NSSM 2.24 (released 2014-08-31) is the last GA release. The author has
# never produced 2.25; we pin to 2.24 + the documented SHA-256 so a
# tampered mirror cannot impersonate it.
$NssmReleaseUrl    = 'https://nssm.cc/release/nssm-2.24.zip'
$NssmReleaseSha256 = 'BE7B3577C6E3A280E5106A9E9DB5B3775931CEFC3A0D7BB45C4D0AEFC15D4D85'
$NssmVersion       = '2.24'

# %LocalAppData%\Ascendo\bin\nssm.exe is the install location for the
# NSSM binary. The service log dir is a sibling: %LocalAppData%\Ascendo\
# logs\service\.
$AscendoRoot = Join-Path $env:LOCALAPPDATA 'Ascendo'
$NssmBinDir  = Join-Path $AscendoRoot 'bin'
$NssmExe     = Join-Path $NssmBinDir  'nssm.exe'
$LogDir      = Join-Path $AscendoRoot 'logs\service'

# Health probe knobs.
$HealthUrl     = "http://127.0.0.1:$Port/health"
$HealthTimeout = 15  # seconds total to keep polling after Start

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

function Write-Info($msg) { Write-Host "[service] $msg" -ForegroundColor Cyan }
function Write-Ok  ($msg) { Write-Host "[service] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[service] $msg" -ForegroundColor Yellow }
function Write-Err ($msg) { Write-Host "[service] $msg" -ForegroundColor Red }

function Test-IsElevated {
    try {
        $identity  = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Resolve-InstallPath {
    if ($InstallPath) {
        if (-not (Test-Path $InstallPath)) {
            throw "install path '$InstallPath' does not exist"
        }
        return (Resolve-Path $InstallPath).Path
    }
    # Default: parent of bin\ (the repo root or installer-staged tree).
    return (Split-Path -Parent $PSScriptRoot)
}

function Find-PythonExe {
    param([string] $Root)

    # Preference order:
    #  1. PyInstaller-bundled ascendo.exe (sub-project 3 ships this)
    #  2. .venv\Scripts\pythonw.exe (windowed Python -- no console pop)
    #  3. .venv\Scripts\python.exe
    #  4. System pythonw.exe / python.exe on PATH
    $candidates = @(
        @{ Path = Join-Path $Root 'binaries\python-sidecar\ascendo.exe'; Mode = 'pyinstaller' }
        @{ Path = Join-Path $Root '.venv\Scripts\pythonw.exe';            Mode = 'venv-w'      }
        @{ Path = Join-Path $Root '.venv\Scripts\python.exe';             Mode = 'venv'        }
    )

    foreach ($c in $candidates) {
        if (Test-Path $c.Path) {
            return [pscustomobject]@{
                Exe  = $c.Path
                Mode = $c.Mode
            }
        }
    }

    # System fallback. Prefer pythonw to avoid a console window.
    foreach ($name in @('pythonw.exe', 'python.exe')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            return [pscustomobject]@{
                Exe  = $cmd.Source
                Mode = ($name -replace '\.exe$', '')
            }
        }
    }

    throw "could not locate ascendo.exe / pythonw.exe / python.exe under '$Root' or on PATH"
}

function Get-ServiceArgs {
    param([pscustomobject] $Python, [string] $Root)

    # PyInstaller bundle: invoke `ascendo.exe dashboard --host ... --port ...`
    if ($Python.Mode -eq 'pyinstaller') {
        return @('dashboard', '--host', '127.0.0.1', '--port', "$Port")
    }
    # Anything else -> `python -m ascendo dashboard --host ... --port ...`.
    # The -m form is PATH-independent and matches the rest of the project.
    return @('-m', 'ascendo', 'dashboard', '--host', '127.0.0.1', '--port', "$Port")
}

function Ensure-Nssm {
    if (Test-Path $NssmExe) {
        return $NssmExe
    }

    Write-Info "NSSM not present at $NssmExe -- downloading..."

    if (-not (Test-Path $NssmBinDir)) {
        New-Item -ItemType Directory -Path $NssmBinDir -Force | Out-Null
    }

    $zipPath = Join-Path $env:TEMP "nssm-$NssmVersion.zip"
    $tmpDir  = Join-Path $env:TEMP "nssm-$NssmVersion-extract-$([guid]::NewGuid().ToString('N'))"

    try {
        # Modern TLS — Win10 default is sometimes 1.0/1.1.
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor `
                [Net.SecurityProtocolType]::Tls12
        } catch { }

        Write-Info "Fetching $NssmReleaseUrl ..."
        try {
            Invoke-WebRequest -Uri $NssmReleaseUrl -OutFile $zipPath -UseBasicParsing
        } catch {
            Write-Err "NSSM download failed: $($_.Exception.Message)"
            exit 10
        }

        $hash = (Get-FileHash -Algorithm SHA256 -Path $zipPath).Hash.ToUpperInvariant()
        if ($hash -ne $NssmReleaseSha256) {
            Write-Err "NSSM hash mismatch."
            Write-Err "  expected: $NssmReleaseSha256"
            Write-Err "  got:      $hash"
            Write-Err "Refusing to install a possibly-tampered NSSM. Delete $zipPath and retry."
            exit 10
        }
        Write-Ok "SHA-256 verified ($hash)"

        if (Test-Path $tmpDir) {
            Remove-Item $tmpDir -Recurse -Force
        }
        Expand-Archive -Path $zipPath -DestinationPath $tmpDir -Force

        # The release zip lays out:
        #   nssm-2.24/win64/nssm.exe
        #   nssm-2.24/win32/nssm.exe
        # Pick whichever matches the host arch; default to win64.
        $arch = if ([Environment]::Is64BitOperatingSystem) { 'win64' } else { 'win32' }
        $src  = Join-Path $tmpDir "nssm-$NssmVersion\$arch\nssm.exe"
        if (-not (Test-Path $src)) {
            Write-Err "NSSM zip extracted but expected file '$src' missing"
            exit 10
        }
        Copy-Item -Path $src -Destination $NssmExe -Force
        Write-Ok "NSSM staged at $NssmExe"
        return $NssmExe
    } finally {
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force -ErrorAction SilentlyContinue }
        if (Test-Path $tmpDir)  { Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Get-NssmStatus {
    # Returns one of: SERVICE_RUNNING, SERVICE_STOPPED, SERVICE_PAUSED,
    # SERVICE_START_PENDING, SERVICE_STOP_PENDING, ABSENT, UNKNOWN.
    if (-not (Test-Path $NssmExe)) {
        # Fall back to sc.exe for read-only status when NSSM hasn't been
        # downloaded yet. sc returns 1060 for "service does not exist".
        $out = & sc.exe query $ServiceName 2>$null
        if ($LASTEXITCODE -eq 1060) { return 'ABSENT' }
        if ($out -match 'STATE\s+:\s+\d+\s+(\S+)') { return "SERVICE_$($matches[1])" }
        return 'UNKNOWN'
    }
    $raw = & $NssmExe status $ServiceName 2>$null
    if ($LASTEXITCODE -ne 0) { return 'ABSENT' }
    $line = ($raw | Select-Object -First 1).ToString().Trim()
    if (-not $line) { return 'UNKNOWN' }
    return $line
}

function Get-ServicePid {
    try {
        $svc = Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
        if ($svc -and $svc.ProcessId -gt 0) { return [int]$svc.ProcessId }
    } catch { }
    return 0
}

function Get-ServiceStartedAt {
    # Walk the System event log for the most recent "service started" or
    # "process started" entry. Source = "Service Control Manager", id 7036
    # ("entered the running state"). Best-effort -- if event log access
    # fails, return $null.
    try {
        $entry = Get-WinEvent -LogName System -MaxEvents 200 -FilterXPath `
            "*[System[Provider[@Name='Service Control Manager'] and (EventID=7036)]]" `
            -ErrorAction Stop |
            Where-Object {
                $_.Properties[0].Value -eq $ServiceName -and `
                $_.Properties[1].Value -eq 'running'
            } |
            Select-Object -First 1
        if ($entry) {
            return $entry.TimeCreated.ToUniversalTime().ToString("o")
        }
    } catch { }
    return $null
}

function Test-PortListening {
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return [bool]$conns
    } catch {
        # Fallback: try connecting.
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
            $ok  = $iar.AsyncWaitHandle.WaitOne(500)
            if ($ok -and $client.Connected) {
                $client.EndConnect($iar)
                $client.Close()
                return $true
            }
            $client.Close()
        } catch { }
        return $false
    }
}

function Test-Health {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($resp.StatusCode -ne 200) { return $null }
        $body = $resp.Content | ConvertFrom-Json -ErrorAction Stop
        return $body.status
    } catch {
        return $null
    }
}

function Wait-ForHealth {
    param([int] $TimeoutSec = $HealthTimeout)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $status = Test-Health
        if ($status -eq 'ok') { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Require-Elevation {
    if (-not (Test-IsElevated)) {
        Write-Err "this action requires administrator privileges"
        Write-Err "right-click PowerShell -> Run as Administrator, then re-run"
        exit 11
    }
}

# ──────────────────────────────────────────────────────────────────────
# Action: install
# ──────────────────────────────────────────────────────────────────────

function Invoke-Install {
    Require-Elevation
    Ensure-Nssm | Out-Null

    $root   = Resolve-InstallPath
    $python = Find-PythonExe -Root $root
    $args   = Get-ServiceArgs -Python $python -Root $root

    Write-Info "install-path: $root"
    Write-Info "python:       $($python.Exe) ($($python.Mode))"
    Write-Info "args:         $($args -join ' ')"

    if (-not (Test-Path $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }
    $stdoutLog = Join-Path $LogDir 'stdout.log'
    $stderrLog = Join-Path $LogDir 'stderr.log'

    $existing = Get-NssmStatus
    if ($existing -ne 'ABSENT') {
        Write-Info "service '$ServiceName' already present (state=$existing) -- reconfiguring"
        # Stop first so we can edit safely.
        & $NssmExe stop $ServiceName confirm | Out-Null
    } else {
        Write-Info "registering '$ServiceName' via NSSM"
        & $NssmExe install $ServiceName $python.Exe @args
        if ($LASTEXITCODE -ne 0) { throw "nssm install exit $LASTEXITCODE" }
    }

    # Apply / refresh all settings idempotently. Quoting via stop-parsing
    # token (--%) prevents PowerShell from re-quoting the args.
    & $NssmExe set $ServiceName Application $python.Exe | Out-Null
    & $NssmExe set $ServiceName AppDirectory $root | Out-Null
    & $NssmExe set $ServiceName AppParameters ($args -join ' ') | Out-Null
    & $NssmExe set $ServiceName DisplayName "Ascendo Dashboard" | Out-Null
    & $NssmExe set $ServiceName Description "Ascendo unified-updates FastAPI dashboard (loopback only, port $Port)." | Out-Null

    # Auto-start, but delayed so we don't slow login on boxes with
    # already-busy startup sequences.
    & $NssmExe set $ServiceName Start SERVICE_DELAYED_AUTO_START | Out-Null

    # Output redirection + 10 MiB rotation, kept across restarts.
    & $NssmExe set $ServiceName AppStdout $stdoutLog | Out-Null
    & $NssmExe set $ServiceName AppStderr $stderrLog | Out-Null
    & $NssmExe set $ServiceName AppRotateFiles 1 | Out-Null
    & $NssmExe set $ServiceName AppRotateOnline 1 | Out-Null
    & $NssmExe set $ServiceName AppRotateBytes 10485760 | Out-Null

    # Marker so the service code can branch on "running as service" if
    # desired (matches the task brief).
    & $NssmExe set $ServiceName AppEnvironmentExtra ASCENDO_RUN_AS_SERVICE=1 | Out-Null

    # Recovery: restart on first/second failure, do nothing on the third.
    # NSSM exposes this as AppExit + AppRestartDelay; sc.exe failure
    # actions are richer so we use that for "do nothing on third".
    & $NssmExe set $ServiceName AppExit Default Restart | Out-Null
    & $NssmExe set $ServiceName AppRestartDelay 5000 | Out-Null
    & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/5000//0 | Out-Null

    Write-Info "starting '$ServiceName'..."
    & $NssmExe start $ServiceName | Out-Null

    if (-not (Wait-ForHealth)) {
        Write-Err "service started but /health did not return status=ok within ${HealthTimeout}s"
        Write-Err "check logs at:"
        Write-Err "  $stdoutLog"
        Write-Err "  $stderrLog"
        exit 12
    }

    $servicePid = Get-ServicePid
    Write-Ok "Service $ServiceName installed; running; PID $servicePid; auto-start enabled"
    exit 0
}

# ──────────────────────────────────────────────────────────────────────
# Action: uninstall
# ──────────────────────────────────────────────────────────────────────

function Invoke-Uninstall {
    Require-Elevation

    $state = Get-NssmStatus
    if ($state -eq 'ABSENT') {
        Write-Info "service '$ServiceName' not installed -- nothing to do"
        if ($PurgeLogs -and (Test-Path $LogDir)) {
            Write-Info "purging $LogDir"
            Remove-Item $LogDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        exit 0
    }

    if (Test-Path $NssmExe) {
        Write-Info "stopping '$ServiceName'"
        & $NssmExe stop $ServiceName confirm | Out-Null
        Write-Info "removing '$ServiceName'"
        & $NssmExe remove $ServiceName confirm | Out-Null
    } else {
        # Fallback: sc.exe-only path when NSSM is gone but the service
        # registration somehow remained.
        Write-Warn "NSSM not present; falling back to sc.exe"
        & sc.exe stop $ServiceName | Out-Null
        & sc.exe delete $ServiceName | Out-Null
    }

    if ($PurgeLogs -and (Test-Path $LogDir)) {
        Write-Info "purging $LogDir"
        Remove-Item $LogDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Ok "Service $ServiceName uninstalled"
    exit 0
}

# ──────────────────────────────────────────────────────────────────────
# Action: start / stop / restart
# ──────────────────────────────────────────────────────────────────────

function Invoke-Start {
    Require-Elevation
    if (-not (Test-Path $NssmExe)) {
        Write-Err "NSSM not present -- run -Action install first"
        exit 1
    }
    & $NssmExe start $ServiceName
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (Wait-ForHealth) { Write-Ok "Service started; /health returned ok" }
    else                { Write-Warn "Service started but /health did not respond within ${HealthTimeout}s" }
    exit 0
}

function Invoke-Stop {
    Require-Elevation
    if (-not (Test-Path $NssmExe)) {
        & sc.exe stop $ServiceName | Out-Null
        Write-Ok "Service stopped (via sc.exe)"
        exit 0
    }
    & $NssmExe stop $ServiceName
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Ok "Service stopped"
    exit 0
}

function Invoke-Restart {
    Require-Elevation
    if (-not (Test-Path $NssmExe)) {
        Write-Err "NSSM not present -- run -Action install first"
        exit 1
    }
    & $NssmExe restart $ServiceName
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (Wait-ForHealth) { Write-Ok "Service restarted; /health returned ok" }
    else                { Write-Warn "Service restarted but /health did not respond within ${HealthTimeout}s" }
    exit 0
}

# ──────────────────────────────────────────────────────────────────────
# Action: status
# ──────────────────────────────────────────────────────────────────────

function Invoke-Status {
    $state         = Get-NssmStatus
    $installed     = ($state -ne 'ABSENT' -and $state -ne 'UNKNOWN')
    $running       = ($state -eq 'SERVICE_RUNNING')
    $portListening = if ($running) { Test-PortListening } else { $false }
    $healthStatus  = if ($portListening) { Test-Health } else { $null }
    $servicePid    = if ($installed)   { Get-ServicePid } else { 0 }
    $lastStarted   = if ($installed)   { Get-ServiceStartedAt } else { $null }

    $payload = [ordered]@{
        installed      = $installed
        running        = $running
        port_listening = $portListening
        health         = $healthStatus
        last_started   = $lastStarted
        pid            = $servicePid
        service_name   = $ServiceName
        state          = $state
        port           = $Port
    }

    if ($Json) {
        # Single-line JSON so callers can pipe it through ConvertFrom-Json.
        Write-Output ($payload | ConvertTo-Json -Compress)
    } else {
        Write-Host "service:        $ServiceName"
        Write-Host "installed:      $(if ($installed) {'yes'} else {'no'})"
        Write-Host "running:        $(if ($running)   {'yes'} else {'no'})"
        Write-Host "port-listening: $(if ($portListening) {'yes'} else {'no'})"
        Write-Host "health:         $(if ($healthStatus) {$healthStatus} else {'-'})"
        Write-Host "last-started:   $(if ($lastStarted) {$lastStarted} else {'-'})"
        Write-Host "pid:            $servicePid"
        Write-Host "state:          $state"
    }
    exit 0
}

# ──────────────────────────────────────────────────────────────────────
# Dispatch
# ──────────────────────────────────────────────────────────────────────

switch ($Action) {
    'install'   { Invoke-Install   }
    'uninstall' { Invoke-Uninstall }
    'start'     { Invoke-Start     }
    'stop'      { Invoke-Stop      }
    'restart'   { Invoke-Restart   }
    'status'    { Invoke-Status    }
    default     {
        Write-Err "unknown action: $Action"
        exit 2
    }
}
