# bin/build-inventory.ps1 -- convenience wrapper for `ascendo build-inventory`.
# Resolves repo root + PYTHONPATH + python automatically so it works from any cwd.
# Pass-through: any args after the script name go to the underlying ascendo command.
#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]] $Args
)
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pyPathSep = if ($IsWindows -or $env:OS -eq 'Windows_NT') { ';' } else { ':' }
$prepend = @(
    (Join-Path $repoRoot 'core'),
    (Join-Path $repoRoot 'adapters\windows')
) -join $pyPathSep
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$prepend$pyPathSep$($env:PYTHONPATH)"
} else {
    $env:PYTHONPATH = $prepend
}

$python = $null
foreach ($cand in @('py','python3','python')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source; break }
}
if (-not $python) {
    Write-Host 'error: no Python on PATH (tried py / python3 / python).' -ForegroundColor Red
    exit 1
}

Write-Host "==> ascendo build-inventory" -ForegroundColor Cyan
& $python -m ascendo build-inventory @Args
exit $LASTEXITCODE
