# =============================================================================
# bin\launch-app.ps1 — actual launcher logic for Ascendo Windows app
# =============================================================================
#
# Called by bin\Ascendo.cmd (the click-to-launch entry point).
#
# Behaviour:
#   1. Verifies python + ascendo are installed; if not, prints the install
#      command and exits.
#   2. Starts the dashboard on the requested port (default 8765).
#   3. Waits up to 10s for the port to bind.
#   4. Opens the user's default browser to http://127.0.0.1:<port>/ (the SPA)
#      The Swagger UI is still reachable at /docs for API exploration.
#   5. Foregrounds the dashboard process so its logs are visible AND so
#      Ctrl+C in the console gracefully stops it.
#
# Useful flags:
#   -Port <n>          Bind to a different TCP port. Default 8765.
#   -NoBrowser         Don't auto-open the browser.
#   -RunsDir <path>    Override where sidecars are persisted.
#                      Default: ~/.ascendo/runs
# =============================================================================

[CmdletBinding()]
param(
    [int] $Port = 8765,
    [switch] $NoBrowser,
    [string] $RunsDir
)

$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

function Write-AscendoBanner {
    $title = '  Ascendo  -  Cross-platform update orchestrator  '
    $line = '=' * $title.Length
    Write-Host ''
    Write-Host $line -ForegroundColor Cyan
    Write-Host $title -ForegroundColor Cyan
    Write-Host $line -ForegroundColor Cyan
    Write-Host ''
}

# ── Sanity: python on PATH? ────────────────────────────────────────────────
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Host "ERROR: python not found on PATH." -ForegroundColor Red
    Write-Host "Install Python 3.11+ from https://www.python.org or via:"
    Write-Host "    winget install Python.Python.3.14"
    exit 70  # EX_SOFTWARE
}

# ── Sanity: ascendo importable? ────────────────────────────────────────────
$probe = & python -c "import ascendo; import ascendo_windows; print('OK')" 2>&1
if ($LASTEXITCODE -ne 0 -or $probe -notmatch '^OK') {
    Write-Host "ERROR: Ascendo packages not installed." -ForegroundColor Red
    Write-Host ""
    Write-Host "Run this once to install:" -ForegroundColor Yellow
    Write-Host "    cd $(Split-Path -Parent $PSScriptRoot)" -ForegroundColor Yellow
    Write-Host "    .\bin\install-dev.ps1" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Then double-click Ascendo.cmd again."
    exit 71
}

Write-AscendoBanner

# ── Build the dashboard argv ──────────────────────────────────────────────
$argv = @('-m', 'ascendo', 'dashboard', '--port', $Port)
if ($RunsDir) {
    $argv += @('--runs-dir', $RunsDir)
}

Write-Host "Starting dashboard on http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

# ── Open browser after a short delay (in background) ──────────────────────
if (-not $NoBrowser) {
    $url = "http://127.0.0.1:$Port/"
    Write-Host "Opening $url in your default browser..." -ForegroundColor Cyan
    Start-Job -ScriptBlock {
        param($u, $p)
        # Wait up to 10s for the port to be reachable.
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p/version" `
                    -UseBasicParsing -TimeoutSec 1
                if ($r.StatusCode -eq 200) { break }
            } catch {}
        }
        Start-Process $u
    } -ArgumentList $url, $Port | Out-Null
}

# ── Foreground the dashboard process ─────────────────────────────────────
# Console stays attached, logs visible, Ctrl+C stops cleanly.
& python @argv
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Ascendo dashboard exited with code $exitCode." -ForegroundColor Yellow
}

exit $exitCode
