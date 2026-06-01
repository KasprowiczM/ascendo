# =============================================================================
# adapters/windows/tests/ps/Dedup.Gate.Tests.ps1
# =============================================================================
#
# PowerShell execution test for the cross-source deduplicator uninstall GATE
# (audit ASCENDO_ULTRA_REVIEW_2 §4 Windows — the P0 destructive path).
#
# Proves the single most important safety property: a stray
# DEDUPLICATION_TASKS.json on disk can NEVER trigger an uninstall unless the
# operator explicitly authorized it (env opt-in OR per-run approval marker).
#
# No Pester dependency (the reference box ships Pester 3.4.0; assertion style
# differs across 3.x/4.x/5.x). Plain assertions; exit 0 on pass, 1 on fail.
# Invoked by:
#   * adapters/windows/tests/test_dedup_gate_ps.py   (the CI pytest leg)
#   * bin/validate-windows.ps1                        (the native validate run)
#
# Compatibility: PowerShell 5.1 + 7.x.
# =============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$LibDir = Resolve-Path (Join-Path $PSScriptRoot '..\..\lib')
Import-Module (Join-Path $LibDir 'AscendoJson.psm1') -Force -DisableNameChecking

$script:Failures = 0
function Assert-Equal {
    param($Expected, $Actual, [string] $Label)
    $e = ($Expected | Sort-Object) -join ','
    $a = ($Actual   | Sort-Object) -join ','
    if ($e -ne $a) {
        Write-Host ("[FAIL] {0}: expected [{1}] got [{2}]" -f $Label, $e, $a)
        $script:Failures++
    } else {
        Write-Host ("[ OK ] {0}" -f $Label)
    }
}

function New-Scenario {
    param([string] $Tasks)
    $runDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ascendo-dedup-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $runDir 'DEDUPLICATION_TASKS.json') -Value $Tasks -Encoding UTF8
    return $runDir
}

$tasksJson = '{"winget":["Evil.Package"],"npm":["evil-cli"],"pip":["evilpkg"]}'

# --- Scenario A: file present, NO env, NO marker => MUST return empty --------
$env:ASCENDO_DEDUP_AUTO_UNINSTALL = $null
Remove-Item Env:\ASCENDO_DEDUP_AUTO_UNINSTALL -ErrorAction SilentlyContinue
$runA = New-Scenario -Tasks $tasksJson
$resA = @(Get-AscendoDedupUninstalls -RunDir $runA -Source 'winget')
Assert-Equal -Expected @() -Actual $resA -Label 'A: stray file + no opt-in => NO winget uninstall'

# --- Scenario B: file present + env opt-in => returns the ids ----------------
$env:ASCENDO_DEDUP_AUTO_UNINSTALL = '1'
$runB = New-Scenario -Tasks $tasksJson
$resB = @(Get-AscendoDedupUninstalls -RunDir $runB -Source 'winget')
Assert-Equal -Expected @('Evil.Package') -Actual $resB -Label 'B: env opt-in => winget uninstall authorized'
Remove-Item Env:\ASCENDO_DEDUP_AUTO_UNINSTALL -ErrorAction SilentlyContinue

# --- Scenario C: file present + approval marker (no env) => returns the ids --
$runC = New-Scenario -Tasks $tasksJson
New-Item -ItemType File -Path (Join-Path $runC 'DEDUPLICATION_APPROVED') -Force | Out-Null
$resC = @(Get-AscendoDedupUninstalls -RunDir $runC -Source 'npm')
Assert-Equal -Expected @('evil-cli') -Actual $resC -Label 'C: approval marker => npm uninstall authorized'

# --- Scenario D: no tasks file => empty (and no throw) -----------------------
$runD = Join-Path ([System.IO.Path]::GetTempPath()) ("ascendo-dedup-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runD -Force | Out-Null
$resD = @(Get-AscendoDedupUninstalls -RunDir $runD -Source 'pip')
Assert-Equal -Expected @() -Actual $resD -Label 'D: no tasks file => empty'

# --- Scenario E: authorized but source absent in file => empty ---------------
$env:ASCENDO_DEDUP_AUTO_UNINSTALL = '1'
$runE = New-Scenario -Tasks '{"winget":["X.Y"]}'
$resE = @(Get-AscendoDedupUninstalls -RunDir $runE -Source 'pip')
Assert-Equal -Expected @() -Actual $resE -Label 'E: authorized + source missing => empty'
Remove-Item Env:\ASCENDO_DEDUP_AUTO_UNINSTALL -ErrorAction SilentlyContinue

if ($script:Failures -gt 0) {
    Write-Host ("DEDUP GATE TESTS FAILED ({0})" -f $script:Failures)
    exit 1
}
Write-Host 'DEDUP GATE TESTS PASSED'
exit 0
