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

    # ── 4b. plan + apply (dry-run) + verify + cleanup (dry-run) ──────────
    # Walks the remaining phases without ever mutating the system. Apply
    # runs in --dry-run so no real upgrades happen; the script emits
    # status='planned' items describing what an apply WOULD do.
    Write-Step "All 5 phases (apply runs --dry-run; no real mutations)"

    $phasesToTest = @(
        @{ Name = 'plan';    Args = @('--phase', 'plan')              ; ExpectedItemMin = 0 }
        @{ Name = 'apply';   Args = @('--phase', 'apply', '--dry-run'); ExpectedItemMin = 0 }
        @{ Name = 'verify';  Args = @('--phase', 'verify')            ; ExpectedItemMin = 0 }
        @{ Name = 'cleanup'; Args = @('--phase', 'cleanup', '--dry-run'); ExpectedItemMin = 0 }
    )

    foreach ($p in $phasesToTest) {
        $phaseRunsDir = Join-Path $env:TEMP "ascendo-validate-phase-$($p.Name)-$([guid]::NewGuid())"
        New-Item -ItemType Directory -Force -Path $phaseRunsDir | Out-Null
        $phaseArgv = @('-B', '-m', 'ascendo', 'run', '--category', 'winget') + $p.Args + @('--runs-dir', $phaseRunsDir)
        $phaseOut = & python @phaseArgv 2>&1
        $phaseExit = $LASTEXITCODE
        $phaseSidecar = Get-ChildItem -Path $phaseRunsDir -Recurse -Filter "$($p.Name)__winget.json" -ErrorAction SilentlyContinue | Select-Object -First 1
        Test-Result "phase=$($p.Name): exit 0/1 (not crashed)" ($phaseExit -in @(0, 1)) "exit=$phaseExit"
        $phaseSidecarDetail = if ($phaseSidecar) {
            "$($phaseSidecar.FullName)"
        } else {
            "no $($p.Name)__winget.json found in $phaseRunsDir"
        }
        Test-Result "phase=$($p.Name): produced sidecar" ($null -ne $phaseSidecar) $phaseSidecarDetail
        if ($phaseSidecar) {
            try {
                $sc = Get-Content $phaseSidecar.FullName -Raw | ConvertFrom-Json
                Test-Result "phase=$($p.Name): sidecar.phase matches" ($sc.phase -eq $p.Name) "phase=$($sc.phase)"
                Write-Host "         status=$($sc.status) total=$($sc.summary.total)" -ForegroundColor DarkGray
            } catch {
                Test-Result "phase=$($p.Name): sidecar parses" $false $_.Exception.Message
            }
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

                $body = @{ phases = @('check') } | ConvertTo-Json -Compress
                $async = Invoke-RestMethod "http://127.0.0.1:$DashboardPort/runs/async" `
                    -Method Post -Body $body -ContentType 'application/json'
                Test-Result "POST /runs/async returns run_id" ($async.run_id -match '^[0-9a-f-]{36}$') "run_id=$($async.run_id)"

                # Poll up to 90s. A 'check' run across all 4 Windows package
                # sources (winget+msstore+registry_arp+windows_update) is
                # IO-bound and can take 20-60s on a healthy machine — the
                # original 10s window only worked when winget was warm.
                $pollOk = $false
                $elapsedMs = 0
                for ($i = 0; $i -lt 360; $i++) {
                    Start-Sleep -Milliseconds 250
                    $elapsedMs += 250
                    try {
                        $s = Invoke-RestMethod "http://127.0.0.1:$DashboardPort/runs/$($async.run_id)/status"
                    } catch { continue }
                    if ($s.status -eq 'completed' -or $s.status -eq 'failed') { $pollOk = $true; break }
                }
                Test-Result "GET /runs/{id}/status reaches completed/failed" $pollOk "status=$($s.status) after $([Math]::Round($elapsedMs/1000,1))s"

                # New (Wave 2): real dashboard endpoints + SSE
                Write-Host "[validate] checking real dashboard endpoints..." -ForegroundColor Cyan

                $endpoints = @(
                    @{ path = "/categories";        contains = "categories" }
                    @{ path = "/inventory";         contains = "categories" }
                    @{ path = "/inventory/summary"; contains = "totals" }
                    @{ path = "/health/check";      contains = "score" }
                    @{ path = "/runs/active";       contains = "active" }
                )
                # NOTE on PS hashtable access: $h.contains is the Contains()
                # method (System.Collections.IDictionary inherits it case-
                # insensitively). To read the *key* named 'contains', always
                # index with $h['contains'] or $h.Item('contains'). Same trap
                # for any other "method-like" key name.
                foreach ($ep in $endpoints) {
                    $resp = Invoke-RestMethod -Uri ("http://127.0.0.1:" + $DashboardPort + $ep['path']) -ErrorAction Stop
                    $body = $resp | ConvertTo-Json -Depth 4
                    if ($body -notmatch [string]$ep['contains']) {
                        Write-Host "[FAIL] $($ep['path']): expected '$($ep['contains'])' in response" -ForegroundColor Red
                        exit 1
                    }
                    Write-Host "[OK]   $($ep['path'])" -ForegroundColor Green
                }

                # Frontend smoke
                $frontendChecks = @(
                    @{ path = "/";                                    contains = "apply-confirm-modal" }
                    @{ path = "/static/fonts/inter-tight-400.woff2";  expectStatus = 200 }
                )
                foreach ($c in $frontendChecks) {
                    $url = "http://127.0.0.1:" + $DashboardPort + $c['path']
                    $resp = Invoke-WebRequest -Uri $url -ErrorAction SilentlyContinue
                    $expectStatus = $c['expectStatus']
                    $expectContains = $c['contains']
                    if ($null -ne $expectStatus -and $resp.StatusCode -ne [int]$expectStatus) {
                        Write-Host "[FAIL] $($c['path']): status $($resp.StatusCode), expected $expectStatus" -ForegroundColor Red; exit 1
                    }
                    if ($null -ne $expectContains -and ($resp.Content -notmatch [string]$expectContains)) {
                        Write-Host "[FAIL] $($c['path']): expected '$expectContains'" -ForegroundColor Red; exit 1
                    }
                    Write-Host "[OK]   $($c['path'])" -ForegroundColor Green
                }
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
