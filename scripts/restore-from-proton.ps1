#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Restore private overlay files through dev-sync on Windows.

.DESCRIPTION
  PowerShell mirror of scripts/restore-from-proton.sh. GitHub remains
  authoritative for tracked files; this wrapper restores only the Git-ignored
  private overlay from the configured dev-sync provider.

.EXAMPLE
  .\scripts\restore-from-proton.ps1 --dry-run --verbose

.EXAMPLE
  .\scripts\restore-from-proton.ps1 -Verbose
#>
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $RemainingArgs)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$repoRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $repoRoot '.dev_sync_config.json'
$preflightScript = Join-Path $repoRoot 'dev-sync-restore-preflight.ps1'
$importScript = Join-Path $repoRoot 'dev-sync-import.ps1'
$verifyScript = Join-Path $repoRoot 'dev-sync-verify-full.ps1'

$dryRun = $false
$verboseOutput = $false
$skipPreflight = $false

foreach ($arg in $RemainingArgs) {
    switch -Regex ($arg) {
        '^(--dry-run|-DryRun|-dryrun)$' { $dryRun = $true; continue }
        '^(--verbose|-Verbose|-v)$' { $verboseOutput = $true; continue }
        '^(--skip-preflight|-SkipPreflight|-skippref)$' { $skipPreflight = $true; continue }
        '^(-h|--help|-Help)$' {
            @'
Usage: .\scripts\restore-from-proton.ps1 [--dry-run] [--verbose] [--skip-preflight]

Restores only Git-ignored private overlay files through dev-sync. Git-tracked
files remain authoritative from GitHub and are not restored from Proton/rclone.
'@ | Write-Host
            exit 0
        }
        default {
            Write-Error "Unknown argument: $arg"
            exit 2
        }
    }
}

function Write-Header {
    param([string] $Title)
    Write-Host ''
    Write-Host "== $Title ==" -ForegroundColor Cyan
}

function Write-Info {
    param([string] $Message)
    Write-Host "  $Message" -ForegroundColor DarkGray
}

function Write-Warn {
    param([string] $Message)
    Write-Host "  WARN: $Message" -ForegroundColor Yellow
}

function Invoke-CheckedScript {
    param(
        [Parameter(Mandatory = $true)] [string] $Path,
        [string[]] $Arguments = @()
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required script missing: $Path"
    }
    & $Path @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        exit $exitCode
    }
}

function New-ProjectMutex {
    $name = 'Global\AscendoRestoreFromProton'
    $createdNew = $false
    $mutex = [System.Threading.Mutex]::new($false, $name, [ref] $createdNew)
    if (-not $mutex.WaitOne(0)) {
        Write-Error 'Another Ascendo restore/dev-sync workflow is already running.'
        exit 75
    }
    return $mutex
}

Write-Header 'Restore From Proton/dev-sync'
$mutex = New-ProjectMutex

try {
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        Write-Warn 'Missing .dev_sync_config.json'
        Write-Info 'Run: .\dev-sync\provider_setup.ps1'
        Write-Info 'Or : .\dev-sync-provider-setup.ps1'
        exit 2
    }

    $sharedArgs = @()
    if ($verboseOutput) { $sharedArgs += '--verbose' }

    if (-not $skipPreflight) {
        Invoke-CheckedScript -Path $preflightScript -Arguments $sharedArgs
    }

    $importArgs = @()
    if ($dryRun) { $importArgs += '--dry-run' }
    if ($verboseOutput) { $importArgs += '--verbose' }
    Invoke-CheckedScript -Path $importScript -Arguments $importArgs

    if (-not $dryRun) {
        Invoke-CheckedScript -Path $verifyScript -Arguments $sharedArgs
    }
} finally {
    if ($mutex) {
        $mutex.ReleaseMutex() | Out-Null
        $mutex.Dispose()
    }
}
