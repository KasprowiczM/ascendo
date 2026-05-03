#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Shared PowerShell launcher for dev-sync Python backends.

.DESCRIPTION
  Keeps the PowerShell entry points thin: resolve the repository-local backend,
  refresh PATH so newly installed Python/rclone are discoverable in this
  session, pick a Python interpreter, and pass all remaining arguments through
  unchanged.
#>
param(
    [Parameter(Mandatory = $true)]
    [string] $BackendScript,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ForwardArgs
)

$ErrorActionPreference = 'Stop'

try {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ($machine, $user | Where-Object { $_ }) -join ';'
} catch {
    # Best effort. If registry PATH cannot be read, keep the current process PATH.
}

$backendPath = Join-Path $PSScriptRoot $BackendScript
if (-not (Test-Path -LiteralPath $backendPath -PathType Leaf)) {
    Write-Error "dev-sync backend missing: $backendPath"
    exit 1
}

$python = $null
foreach ($candidate in @('py', 'python', 'python3')) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) {
        $python = $command.Source
        break
    }
}

if (-not $python) {
    Write-Error 'No Python found. Install Python, for example: winget install Python.Python.3.13'
    exit 2
}

$cleanForwardArgs = @($ForwardArgs | Where-Object { $null -ne $_ -and $_ -ne '' })

& $python $backendPath @cleanForwardArgs
exit $LASTEXITCODE
