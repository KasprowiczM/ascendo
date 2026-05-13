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
#   5. All 5 phases × winget (apply runs --dry-run; no real mutations).
#   6. npm manager 5-phase smokes — skip-on-missing.        (Sesja 58)
#   7. pip manager 5-phase smokes — skip-on-missing.        (Sesja 58)
#   8. web manager check / plan / apply --dry-run.          (Sesja 58)
#   9. `ascendo web {start,status,restart,stop}` lifecycle. (Sesja 56)
#  10. `ascendo build-inventory` enumerates packages.       (Sesja 57)
#  11. bin/ convenience wrappers exist + forward correctly. (Sesja 58)
#  12. Dashboard launches in background, /version + /health respond, then
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

    # ── 6. npm manager (Sesja 58) ──────────────────────────────────────────
    # Skip-on-missing: if npm isn't on PATH, doctor reports degraded but
    # the script must NOT fail — the operator may not have Node.js
    # installed and that's a legitimate state.
    Write-Step "6. npm manager (Sesja 58)"

    $npmDoctor = & python -m ascendo doctor 2>&1
    $npmLine = ($npmDoctor | Select-String -Pattern '^\s*npm\s+' | Select-Object -First 1)
    if ($npmLine -and $npmLine.Line -match '^\s*npm\s+ok') {
        Test-Result "6.1 doctor: npm component" $true $npmLine.Line.Trim()
    } elseif ($npmLine) {
        Write-Host "  [SKIP] 6.1 npm $($npmLine.Line.Trim())" -ForegroundColor DarkGray
    } else {
        Write-Host "  [SKIP] 6.1 npm component not reported by doctor (adapter may not declare it)" -ForegroundColor DarkGray
    }

    $npmOnPath = $null -ne (Get-Command npm -ErrorAction SilentlyContinue)
    if ($npmOnPath) {
        # 6.2 npm check
        $rid = [guid]::NewGuid().ToString()
        $out = Join-Path $env:TEMP "ascendo-validate-npm-check-$rid"
        New-Item -ItemType Directory -Force -Path $out | Out-Null
        & python -B -m ascendo run --category npm --phase check --runs-dir $out 2>&1 | Out-Null
        $npmCheckExit = $LASTEXITCODE
        $npmCheckSidecar = Get-ChildItem -Path $out -Recurse -Filter 'check__npm.json' -ErrorAction SilentlyContinue | Select-Object -First 1
        Test-Result "6.2 npm check produced sidecar" ($npmCheckExit -in @(0,1) -and $null -ne $npmCheckSidecar) "exit=$npmCheckExit  sidecar=$($null -ne $npmCheckSidecar)"

        # 6.3 npm plan
        $rid = [guid]::NewGuid().ToString()
        $out = Join-Path $env:TEMP "ascendo-validate-npm-plan-$rid"
        New-Item -ItemType Directory -Force -Path $out | Out-Null
        & python -B -m ascendo run --category npm --phase plan --runs-dir $out 2>&1 | Out-Null
        Test-Result "6.3 npm plan exit 0/1" ($LASTEXITCODE -in @(0,1)) "exit=$LASTEXITCODE"
    } else {
        Write-Host "  [SKIP] 6.2 / 6.3 — npm not on PATH (install Node.js if you want npm management)" -ForegroundColor DarkGray
    }

    # ── 7. pip manager (Sesja 58) ──────────────────────────────────────────
    Write-Step "7. pip manager (Sesja 58)"

    $pipDoctor = & python -m ascendo doctor 2>&1
    $pipLine = ($pipDoctor | Select-String -Pattern '^\s*pip\s+' | Select-Object -First 1)
    if ($pipLine -and $pipLine.Line -match '^\s*pip\s+ok') {
        Test-Result "7.1 doctor: pip component" $true $pipLine.Line.Trim()
    } elseif ($pipLine) {
        Write-Host "  [SKIP] 7.1 pip $($pipLine.Line.Trim())" -ForegroundColor DarkGray
    } else {
        Write-Host "  [SKIP] 7.1 pip component not reported by doctor (adapter may not declare it)" -ForegroundColor DarkGray
    }

    # pip is virtually always present alongside Python, but probe defensively anyway.
    $pipOnPath = ($null -ne (Get-Command pip -ErrorAction SilentlyContinue)) -or ($null -ne (Get-Command pip3 -ErrorAction SilentlyContinue))
    if ($pipOnPath) {
        # 7.2 pip check
        $rid = [guid]::NewGuid().ToString()
        $out = Join-Path $env:TEMP "ascendo-validate-pip-check-$rid"
        New-Item -ItemType Directory -Force -Path $out | Out-Null
        & python -B -m ascendo run --category pip --phase check --runs-dir $out 2>&1 | Out-Null
        $pipCheckExit = $LASTEXITCODE
        $pipCheckSidecar = Get-ChildItem -Path $out -Recurse -Filter 'check__pip.json' -ErrorAction SilentlyContinue | Select-Object -First 1
        Test-Result "7.2 pip check produced sidecar" ($pipCheckExit -in @(0,1) -and $null -ne $pipCheckSidecar) "exit=$pipCheckExit  sidecar=$($null -ne $pipCheckSidecar)"

        # 7.3 pip plan
        $rid = [guid]::NewGuid().ToString()
        $out = Join-Path $env:TEMP "ascendo-validate-pip-plan-$rid"
        New-Item -ItemType Directory -Force -Path $out | Out-Null
        & python -B -m ascendo run --category pip --phase plan --runs-dir $out 2>&1 | Out-Null
        Test-Result "7.3 pip plan exit 0/1" ($LASTEXITCODE -in @(0,1)) "exit=$LASTEXITCODE"
    } else {
        Write-Host "  [SKIP] 7.2 / 7.3 — pip not on PATH" -ForegroundColor DarkGray
    }

    # ── 8. web manager (Sesja 58) ──────────────────────────────────────────
    # Tier-A check is real (probes vendor endpoints for installed apps);
    # apply is Tier-B (trigger-only by design for Sparkle / GH / built-in
    # updaters). Success criterion: exit 0/1 + sidecar emitted.
    Write-Step "8. web manager (Sesja 58)"

    $webDoctor = & python -m ascendo doctor 2>&1
    $webLine = ($webDoctor | Select-String -Pattern '^\s*web(_registry)?\s+' | Select-Object -First 1)
    if ($webLine -and $webLine.Line -match '\bok\b') {
        Test-Result "8.1 doctor: web/web_registry component" $true $webLine.Line.Trim()
    } elseif ($webLine) {
        Write-Host "  [SKIP] 8.1 web $($webLine.Line.Trim())" -ForegroundColor DarkGray
    } else {
        Write-Host "  [SKIP] 8.1 web component not reported by doctor (adapter may not declare it)" -ForegroundColor DarkGray
    }

    # 8.2 web check
    $rid = [guid]::NewGuid().ToString()
    $out = Join-Path $env:TEMP "ascendo-validate-web-check-$rid"
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    & python -B -m ascendo run --category web --phase check --runs-dir $out 2>&1 | Out-Null
    $webCheckExit = $LASTEXITCODE
    $webCheckSidecar = Get-ChildItem -Path $out -Recurse -Filter 'check__web.json' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($webCheckExit -in @(0,1,2) -and ($null -ne $webCheckSidecar -or $webCheckExit -eq 2)) {
        # Exit 2 = bad usage (e.g. web category not supported by this adapter) — skip cleanly
        if ($webCheckExit -eq 2 -and $null -eq $webCheckSidecar) {
            Write-Host "  [SKIP] 8.2 web check — category not supported by this adapter (exit=2)" -ForegroundColor DarkGray
            Write-Host "  [SKIP] 8.3 / 8.4 — skipping further web phases" -ForegroundColor DarkGray
        } else {
            Test-Result "8.2 web check produced sidecar" ($null -ne $webCheckSidecar) "exit=$webCheckExit  sidecar=$($null -ne $webCheckSidecar)"

            # 8.3 web plan
            $rid = [guid]::NewGuid().ToString()
            $out = Join-Path $env:TEMP "ascendo-validate-web-plan-$rid"
            New-Item -ItemType Directory -Force -Path $out | Out-Null
            & python -B -m ascendo run --category web --phase plan --runs-dir $out 2>&1 | Out-Null
            Test-Result "8.3 web plan exit 0/1" ($LASTEXITCODE -in @(0,1)) "exit=$LASTEXITCODE"

            # 8.4 web apply --dry-run (Tier-B is trigger-only by design)
            $rid = [guid]::NewGuid().ToString()
            $out = Join-Path $env:TEMP "ascendo-validate-web-apply-$rid"
            New-Item -ItemType Directory -Force -Path $out | Out-Null
            & python -B -m ascendo run --category web --phase apply --dry-run --runs-dir $out 2>&1 | Out-Null
            $webApplyExit = $LASTEXITCODE
            $webApplySidecar = Get-ChildItem -Path $out -Recurse -Filter 'apply__web.json' -ErrorAction SilentlyContinue | Select-Object -First 1
            Test-Result "8.4 web apply --dry-run produced sidecar" ($webApplyExit -in @(0,1) -and $null -ne $webApplySidecar) "exit=$webApplyExit  sidecar=$($null -ne $webApplySidecar)"
        }
    } else {
        Test-Result "8.2 web check produced sidecar" $false "exit=$webCheckExit  sidecar=$($null -ne $webCheckSidecar)"
    }

    # ── 9. ascendo web lifecycle (Sesja 56) ────────────────────────────────
    # CLI-driven lifecycle (pidfile-tracked) — different surface from the
    # dashboard tested in stage 12. Use a different port to avoid collisions.
    Write-Step "9. ascendo web lifecycle (Sesja 56)"

    $LifecyclePort = $DashboardPort + 100

    # First, make sure no prior leftover is running on the lifecycle port.
    & python -m ascendo web stop --force 2>&1 | Out-Null
    Start-Sleep -Milliseconds 500

    # 9.1 web start
    & python -m ascendo web start --port $LifecyclePort --no-open 2>&1 | Out-Null
    $startExit = $LASTEXITCODE
    Start-Sleep -Seconds 2

    # 9.2 web status — should report running
    $statusOk = $false
    $statusObj = $null
    try {
        $statusJson = & python -m ascendo web status --json 2>&1 | Out-String
        $statusObj = $statusJson | ConvertFrom-Json
        $statusOk = ($statusObj.pidfile_present -and $statusObj.pid_alive -and $statusObj.port_listening)
    } catch {
        $statusOk = $false
    }
    if ($statusOk) {
        Test-Result "9.1+9.2 web start + status (running)" $true "pid=$($statusObj.pid) port=$($statusObj.port)"
    } else {
        Test-Result "9.1+9.2 web start + status (running)" $false "start_exit=$startExit  status=$(if ($statusObj) { $statusObj | ConvertTo-Json -Compress } else { 'no-output' })"
    }

    # 9.3 web restart
    if ($statusOk) {
        $oldPid = $statusObj.pid
        & python -m ascendo web restart --port $LifecyclePort --no-open 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        $statusObj2 = $null
        try {
            $statusObj2 = (& python -m ascendo web status --json 2>&1 | Out-String) | ConvertFrom-Json
        } catch {}
        $restartOk = ($null -ne $statusObj2) -and $statusObj2.port_listening -and ($statusObj2.pid -ne $oldPid)
        if ($restartOk) {
            Test-Result "9.3 web restart (new pid)" $true "old_pid=$oldPid  new_pid=$($statusObj2.pid)"
        } else {
            # restart that reuses pid is unusual but not a hard failure; warn only.
            Write-Host "  [WARN] 9.3 web restart — pid did not change (old=$oldPid  new=$(if ($statusObj2) { $statusObj2.pid } else { 'n/a' }))" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [SKIP] 9.3 web restart — start did not succeed" -ForegroundColor DarkGray
    }

    # 9.4 web stop
    & python -m ascendo web stop --force 2>&1 | Out-Null
    Start-Sleep -Seconds 1
    $stopOk = $false
    try {
        $statusObj3 = (& python -m ascendo web status --json 2>&1 | Out-String) | ConvertFrom-Json
        $stopOk = (-not $statusObj3.pid_alive) -and (-not $statusObj3.port_listening)
    } catch {
        # status command may exit non-zero when no pidfile present — treat as stopped
        $stopOk = $true
    }
    Test-Result "9.4 web stop" $stopOk "pid_alive/port_listening cleared"

    # 9.5 web start idempotency — running 'start' twice on the same port
    # should not error out (the second call should detect the existing
    # process and report it cleanly).
    & python -m ascendo web start --port $LifecyclePort --no-open 2>&1 | Out-Null
    $firstStartExit = $LASTEXITCODE
    Start-Sleep -Seconds 2
    & python -m ascendo web start --port $LifecyclePort --no-open 2>&1 | Out-Null
    $secondStartExit = $LASTEXITCODE
    Test-Result "9.5 web start idempotency" (($firstStartExit -eq 0) -and ($secondStartExit -eq 0)) "first_exit=$firstStartExit  second_exit=$secondStartExit"

    # Cleanup so we don't leak a background dashboard
    & python -m ascendo web stop --force 2>&1 | Out-Null

    # ── 10. ascendo build-inventory (Sesja 57) ─────────────────────────────
    Write-Step "10. ascendo build-inventory (Sesja 57)"

    $invOut = & python -m ascendo build-inventory --no-db 2>&1
    $invExit = $LASTEXITCODE
    $invText = ($invOut -join "`n")
    $invOk = ($invExit -eq 0) -and ($invText -match 'scanned \d+ package')
    if ($invOk) {
        $matchInfo = ([regex]::Match($invText, 'scanned \d+ package\S*'))
        Test-Result "10.1 build-inventory enumerates packages" $true $matchInfo.Value
    } else {
        Test-Result "10.1 build-inventory enumerates packages" $false "exit=$invExit  output_head=$(if ($invText.Length -gt 200) { $invText.Substring(0,200) + '...' } else { $invText })"
    }

    # ── 11. bin/ convenience wrappers (Sesja 58) ───────────────────────────
    Write-Step "11. bin/ convenience wrappers (Sesja 58)"

    $wrappers = @(
        'ascendo-web-start.ps1',
        'ascendo-web-stop.ps1',
        'ascendo-web-restart.ps1',
        'ascendo-web-status.ps1',
        'build-inventory.ps1'
    )
    foreach ($wrapper in $wrappers) {
        $wrapperPath = Join-Path $RepoRoot "bin\$wrapper"
        if (-not (Test-Path $wrapperPath)) {
            Test-Result "11.x $wrapper exists" $false "missing: $wrapperPath"
            continue
        }
        $wrapperContent = Get-Content $wrapperPath -Raw -ErrorAction SilentlyContinue
        $forwards = ($wrapperContent -match 'python\s+-m\s+ascendo') -or ($wrapperContent -match '\$python\s+-m\s+ascendo') -or ($wrapperContent -match '&\s+\$python\s+.*ascendo')
        if ($forwards) {
            Test-Result "11.x $wrapper forwards to python -m ascendo" $true ""
        } else {
            Test-Result "11.x $wrapper forwards to python -m ascendo" $false "wrapper exists but doesn't reference 'python -m ascendo'"
        }
    }

    # ── 12. dashboard smoke ────────────────────────────────────────────────
    if ($SkipDashboard) {
        Write-Host "  [SKIP] dashboard checks (--SkipDashboard)" -ForegroundColor DarkGray
    } else {
        Write-Step "12. ascendo dashboard smoke (start in background, hit /version + /health, stop)"
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

                # Poll up to 240s. A 'check' run across all 8 Windows
                # package sources (winget + msstore + npm + pip + web +
                # plugin + registry_arp + windows_update -- Sesja 58
                # expanded from 4 to 8) is IO-bound and can take 60-150s
                # on a healthy machine. Web check also runs the Sesja-59
                # registry auto-discovery which walks the three ARP
                # roots and classifies against winget/msstore/curated
                # lists (adds ~5-15s). Combined with windows_update
                # pre-scan (Sesja 59 fast-path: ~5-15s when 0 pending)
                # the realistic ceiling is ~3 minutes.
                $pollOk = $false
                $elapsedMs = 0
                for ($i = 0; $i -lt 960; $i++) {
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
