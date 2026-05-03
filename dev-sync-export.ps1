#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Export private repo overlay files to the configured cloud provider.

.DESCRIPTION
  Windows PowerShell mirror of dev-sync-export.sh. Both delegate to the
  cross-platform Python backend at dev-sync\dev_sync_export.py — this
  wrapper just resolves the right Python interpreter (py launcher first,
  then python, then python3) and forwards every CLI argument verbatim.

  See dev-sync\QUICK_START.md and docs\CROSS_PLATFORM.md for the full flow.

.PARAMETER args
  All arguments are passed through to the Python backend.
  Common flags: --dry-run, --verbose, --apply.

.EXAMPLE
  .\dev-sync-export.ps1 --dry-run --verbose
  .\dev-sync-export.ps1
#>
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Args)

$ErrorActionPreference = 'Stop'

# Refresh PATH from the registry so freshly-installed Python (e.g. via
# ``winget install Python.Python.3.13`` in this same shell) is found
# without restarting PowerShell. Same pattern lives in every other
# dev-sync-*.ps1 wrapper.
try {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ($machine, $user | Where-Object { $_ }) -join ';'
} catch { }

$repoRoot   = $PSScriptRoot
$pyBackend  = Join-Path $repoRoot 'dev-sync\dev_sync_export.py'

if (-not (Test-Path $pyBackend)) {
    Write-Error "dev-sync backend missing: $pyBackend"
    exit 1
}

# Pick the best Python on PATH. ``py`` is Microsoft's launcher, prefers
# the highest-version installed. Falls back to plain ``python`` /
# ``python3`` so the script works on dev boxes that pre-date the launcher.
$python = $null
foreach ($candidate in @('py', 'python', 'python3')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source; break }
}
if (-not $python) {
    Write-Error 'No Python found. winget install Python.Python.3.13 (then restart this shell, or call this script again so it re-reads PATH).'
    exit 2
}

# Forward all args verbatim. The Python backend handles --help itself.
& $python $pyBackend @Args
exit $LASTEXITCODE
