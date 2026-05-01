# =============================================================================
# bin\validate-windows.ps1 — Ascendo Windows-side validation harness
# =============================================================================
#
# Run AFTER:
#   pip install -e .\core\
#   pip install fastapi 'uvicorn[standard]'
#
# Verifies (in order):
#   1. `python -m ascendo --help` works (no PATH dependency).
#   2. `python -m ascendo version` returns the version slug.
#   3. `python -m ascendo doctor` exits 0 with the Windows adapter healthy.
#   4. `python -m ascendo run --category winget --phase check --runs-dir TEMP`
#      runs end-to-end, produces a sidecar, exits 0/1 (not 2/3).
#   5. Dashboard launches in background, /version + /health respond, then
#      stopped cleanly.
#
# Run as:
#   PS> .\bin\validate-windows.ps1
#
# All work uses `python -m ascendo` so this works whether or not ascendo.exe
# is on PATH.
# =============================================================================

[CmdletBinding()]
param(
    [int] $DashboardPort = 8765,
    [switch] $SkipDashboard
)

# Each native command's exit code is checked explicitly — do NOT make
# pwsh treat any non-zero return as a terminating error. A non-zero exit
# from `ascendo doctor` (e.g. 3 = no adapter installed) is a valid
# signal that the script reports as [FAIL], not a fatal error.
$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

function Write-Step {
    param([string] $Title)
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function Test-Result {
    param([string] $Name, [bool] $Ok, [string] $Detail = '')
    if ($Ok) {
        Write-Host "  [PASS] $Name" -ForegroundColor Green
        if ($Detail) { Write-Host "         $Detail" -ForegroundColor DarkGray }
    } else {
        Write-Host "  [FAIL] $Name" -ForegroundColor Red
        if ($Detail) { Write-Host "         $Detail" -ForegroundColor Yellow }
        $script:Failures++
    }
}

$script:Failures = 0
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

try {
    # ── 1. python -m ascendo --help ────────────────────────────────────────
    Write-Step "ascendo --help (PATH-independent invocation)"
    $help = & python -m ascendo --help 2>&1
    Test-Result "module entry point" ($LASTEXITCODE -eq 0) (($help -join "`n").Substring(0, [Math]::Min(120, ($help -join "`n").Length)))

    # ── 2. version ─────────────────────────────────────────────────────────
    Write-Step "ascendo version"
    $version = & python -m ascendo version 2>&1
    Test-Result "version command" (($LASTEXITCODE -eq 0) -and ($version -match 'ascendo \d')) ("got: $version")

    # ── 3. doctor ──────────────────────────────────────────────────────────
    Write-Step "ascendo doctor"
    $doctor = & python -m ascendo doctor 2>&1
    $doctorExit = $LASTEXITCODE
    $doctorText = ($doctor -join "`n")
    if ($doctorExit -eq 0) {
        Test-Result "doctor command (adapter healthy)" $true $doctorText
    } elseif ($doctorExit -eq 3 -or $doctorText -match 'No adapter available') {
        # Specific, well-known failure mode — point user at the fix.
        Test-Result "doctor command — NO ADAPTER INSTALLED" $false `
            "Fix: cd $RepoRoot ; pip install -e .\adapters\windows\"
        Write-Host "  HINT: install the Windows adapter:" -ForegroundColor Yellow
        Write-Host "        pip install -e .\adapters\windows\" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Then re-run this script." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Skipping run/dashboard checks because they all need an adapter." -ForegroundColor DarkGray
        Pop-Location
        exit 1
    } else {
        Test-Result "doctor command (unhealthy)" $false "exit=$doctorExit`n$doctorText"
    }

    # ── 4. run --phase check ───────────────────────────────────────────────
    Write-Step "ascendo run --category winget --phase check (CLI -> orchestrator -> WingetManager -> check.ps1 -> sidecar)"

    # Defensively clear any __pycache__ dirs under core/ + adapters/ so
    # Python re-compiles fresh. Editable installs sometimes serve stale
    # bytecode on Windows when .py mtime resolution is coarse (FAT/NTFS
    # 2-second granularity vs sub-second Linux mounts → Python's
    # mtime-based cache invalidation occasionally misses fast edits).
    Get-ChildItem -Path "$RepoRoot\core","$RepoRoot\adapters" -Recurse -Force `
        -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }

    $runId = [guid]::NewGuid().ToString()
    $runsDir = Join-Path $env:TEMP "ascendo-validate-$runId"
    New-Item -ItemType Directory -Force -Path $runsDir | Out-Null
    # `-B` tells Python not to write .pyc files; combined with the cache
    # purge above, this guarantees we run the latest .py source.
    $runOut = & python -B -m ascendo run --category winget --phase check --runs-dir $runsDir 2>&1
    $runExit = $LASTEXITCODE
    $sidecars = Get-ChildItem -Path $runsDir -Recurse -Filter "*.json" -ErrorAction SilentlyContinue
    Test-Result "run command exited 0/1 (not crashed)" ($runExit -in @(0, 1)) "exit=$runExit"
    Test-Result "run produced at least one sidecar" ($sidecars.Count -ge 1) "found $($sidecars.Count) JSON file(s) in $runsDir"
    if ($sidecars.Count -gt 0) {
        try {
            $sc = Get-Content $sidecars[0].FullName -Raw | ConvertFrom-Json
            Test-Result "sidecar has schema=ascendo/v1" ($sc.schema -eq 'ascendo/v1') "schema=$($sc.schema)"
            Test-Result "sidecar has phase=check" ($sc.phase -eq 'check') "phase=$($sc.phase)"
            Test-Result "sidecar has category=winget" ($sc.category -eq 'winget') "category=$($sc.category)"
            Test-Result "sidecar has summary.total >= 0" ($null -ne $sc.summary.total) "summary.total=$($sc.summary.total)"
            # Always print the sidecar's reported status so the operator can see
            # whether this was a real success, a phase-level failure, or an
            # orchestrator-synthesized failure stub.
            Write-Host "         sidecar.status     = $($sc.status)" -ForegroundColor DarkGray
            Write-Host "         sidecar.tool       = $($sc.tool.name) $($sc.tool.version)" -ForegroundColor DarkGray
            if ($sc.messages -and $sc.messages.Count -gt 0) {
                Write-Host ""
                Write-Host "         === sidecar.messages[] (most recent first) ===" -ForegroundColor Yellow
                foreach ($m in $sc.messages) {
                    $color = if ($m.level -eq 'error') { 'Red' } elseif ($m.level -eq 'warn') { 'Yellow' } else { 'DarkGray' }
                    Write-Host "         [$($m.level.ToUpper())] $($m.text)" -ForegroundColor $color
                }
            }
            if ($runExit -ne 0) {
                # Surface the CLI output so the user can see WHY the run failed.
                Write-Host ""
                Write-Host "         === stdout/stderr from 'python -m ascendo run' ===" -ForegroundColor Yellow
                $runText = ($runOut -join "`n")
                if ($runText.Length -gt 1500) { $runText = $runText.Substring(0, 1500) + " [...truncated]" }
                Write-Host "$runText" -ForegroundColor DarkGray
            }
        } catch {
            Test-Result "sidecar parses as JSON" $false $_.Exception.Message
        }
    }

    # ── 5. dashboard smoke ─────────────────────────────────────────────────
    if ($SkipDashboard) {
        Write-Host "  [SKIP] dashboard checks (--SkipDashboard)" -ForegroundColor DarkGray
    } else {
        Write-Step "ascendo dashboard smoke (start in background, hit /version + /health, stop)"
        $dashJob = Start-Job -ScriptBlock {
            param($repoRoot, $port)
            Set-Location $repoRoot
            & python -m ascendo dashboard --port $port 2>&1
        } -ArgumentList $RepoRoot, $DashboardPort

        # Wait up to 10s for the server to bind.
        $up = $false
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:$DashboardPort/version" -UseBasicParsing -TimeoutSec 1
                if ($r.StatusCode -eq 200) { $up = $true; break }
            } catch {}
        }
        Test-Result "dashboard binds to 127.0.0.1:$DashboardPort within 10s" $up

        if ($up) {
            try {
                $v = Invoke-RestMethod "http://127.0.0.1:$DashboardPort/version"
                Test-Result "GET /version" ($null -ne $v.ascendo) "ascendo=$($v.ascendo) adapter=$($v.adapter)"

                $h = Invoke-RestMethod "http://127.0.0.1:$DashboardPort/health"
                Test-Result "GET /health" ($h.status -in @('ok','degraded','error')) "status=$($h.status)"

                # Async run + SSE roundtrip
                $body = @{ phases = @('check') } | ConvertTo-Json -Compress
                $async = Invoke-RestMethod "http://127.0.0.1:$DashboardPort/runs/async" `
                    -Method Post -Body $body -ContentType 'application/json'
                Test-Result "POST /runs/async returns run_id" ($async.run_id -match '^[0-9a-f-]{36}$') "run_id=$($async.run_id)"

                # Poll status until completed
                $pollOk = $false
                for ($i = 0; $i -lt 40; $i++) {
                    Start-Sleep -Milliseconds 250
                    $s = Invoke-RestMethod "http://127.0.0.1:$DashboardPort/runs/$($async.run_id)/status"
                    if ($s.status -eq 'completed' -or $s.status -eq 'failed') { $pollOk = $true; break }
                }
                Test-Result "GET /runs/{id}/status reaches completed/failed" $pollOk "status=$($s.status)"
            } catch {
                Test-Result "dashboard endpoint reachable" $false $_.Exception.Message
            }
        }

        Stop-Job $dashJob -ErrorAction SilentlyContinue | Out-Null
        Remove-Job $dashJob -Force -ErrorAction SilentlyContinue | Out-Null
    }

    Write-Host ""
    if ($script:Failures -eq 0) {
        Write-Host "ALL CHECKS PASSED." -ForegroundColor Green
        exit 0
    } else {
        Write-Host "FAILED: $script:Failures check(s) failed." -ForegroundColor Red
        exit 1
    }
}
finally {
    Pop-Location
}
