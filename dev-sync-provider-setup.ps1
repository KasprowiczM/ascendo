#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Configure the dev-sync private overlay provider (Windows-aware port of
  dev-sync/provider_setup.sh).

.DESCRIPTION
  GitHub stores tracked project files. The provider (Proton Drive via
  rclone, the Proton Drive native client's local mirror, or any local
  folder) stores ONLY the git-ignored private overlay (.env.local, agent
  settings, OAuth tokens, etc.).

  This script writes .dev_sync_config.json at the repo root. It does NOT
  upload, delete, or touch any files. After it finishes, run:

      .\dev-sync-export.ps1 --dry-run --verbose
      .\dev-sync-export.ps1

  Prerequisites for option 1 (rclone): ``rclone config`` must already be
  run interactively (browser-based OAuth) so a remote like ``protondrive``
  exists. Install rclone with ``winget install Rclone.Rclone``.

.EXAMPLE
  .\dev-sync-provider-setup.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot   = $PSScriptRoot
$configPath = Join-Path $repoRoot '.dev_sync_config.json'
$projectName = Split-Path -Leaf $repoRoot

function Write-Header($text) {
    Write-Host ""
    Write-Host $text -ForegroundColor Blue -NoNewline
    Write-Host ""
    Write-Host ('─' * 60) -ForegroundColor DarkGray
}
function Write-Info($text) { Write-Host "  $text" }
function Write-Ok($text)   { Write-Host "✓ $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "! $text" -ForegroundColor Yellow }
function Write-Err($text)  { Write-Host "✗ $text" -ForegroundColor Red }

function Test-Rclone() {
    return [bool] (Get-Command rclone -ErrorAction SilentlyContinue)
}

function Get-RcloneRemotes() {
    if (-not (Test-Rclone)) { return @() }
    try { return (& rclone listremotes 2>$null) } catch { return @() }
}

function Find-LocalProtonPath() {
    # Windows Proton Drive native client mirrors at one of these paths
    # depending on install variant + multi-account setup.
    $candidates = @(
        Join-Path $env:USERPROFILE 'Proton Drive',
        Join-Path $env:USERPROFILE 'ProtonDrive',
        Join-Path $env:USERPROFILE 'Proton Drive - Personal',
        Join-Path $env:USERPROFILE 'Proton Drive - Business'
    )
    foreach ($p in $candidates) {
        if (Test-Path -PathType Container $p) { return $p }
    }
    return $null
}

function Save-Config([hashtable] $cfg) {
    # ConvertTo-Json escapes properly; Compress=$false for human-readable diff.
    $json = $cfg | ConvertTo-Json -Depth 4
    $tmp = "$configPath.tmp"
    Set-Content -Path $tmp -Value $json -Encoding UTF8 -NoNewline
    Move-Item -Force $tmp $configPath
}

# ── Main ──────────────────────────────────────────────────────────────

Write-Header "Dev Sync Provider Setup"
Write-Info  "Project: $projectName"
Write-Info  "Config : $configPath"
Write-Host  ""
Write-Info  "Use rclone for Proton Drive on Windows unless you have the native"
Write-Info  "Proton Drive client and a verified local mirror folder."
Write-Info  "If you pick rclone, run 'rclone config' first to register your"
Write-Info  "Proton remote (interactive OAuth — opens a browser)."

$protonPath = Find-LocalProtonPath
$hasRclone  = Test-Rclone

Write-Host ""
Write-Host "Providers:"
if ($hasRclone) {
    Write-Host "  1) rclone remote      " -NoNewline
    Write-Host "available" -ForegroundColor Green
} else {
    Write-Host "  1) rclone remote      " -NoNewline
    Write-Host "not installed (winget install Rclone.Rclone)" -ForegroundColor Red
}
if ($protonPath) {
    Write-Host "  2) local Proton path  " -NoNewline
    Write-Host $protonPath -ForegroundColor Green
} else {
    Write-Host "  2) local Proton path  " -NoNewline
    Write-Host "not auto-detected" -ForegroundColor Yellow
}
Write-Host "  3) local/custom path"
Write-Host ""

$choice = Read-Host "Select provider [1-3]"
$choice = $choice.Trim()

$provider = ""; $providerPath = ""; $remote = ""; $remotePath = ""; $protonRoot = ""

switch ($choice) {
    '1' {
        if (-not $hasRclone) { Write-Err "rclone is not installed"; exit 1 }
        $provider = "rclone"
        Write-Host "Configured rclone remotes:"
        Get-RcloneRemotes | ForEach-Object { Write-Host "  $_" }
        Write-Host ""
        $remote = Read-Host "Enter rclone remote name [protondrive]"
        if ([string]::IsNullOrWhiteSpace($remote)) { $remote = "protondrive" }
        $remote = $remote.TrimEnd(':')
        $remotePath = Read-Host "Enter path inside remote [Dev_Env]"
        if ([string]::IsNullOrWhiteSpace($remotePath)) { $remotePath = "Dev_Env" }
    }
    '2' {
        if (-not $protonPath) {
            $protonPath = Read-Host "Enter full local Proton Drive path"
        }
        if (-not (Test-Path -PathType Container $protonPath)) {
            Write-Err "Path does not exist: $protonPath"
            exit 1
        }
        $provider     = "protondrive"
        $providerPath = $protonPath
        $protonRoot   = Join-Path (Join-Path $protonPath 'Dev_Env') $projectName
    }
    '3' {
        $provider = "local"
        $providerPath = Read-Host "Enter full local provider folder path"
        if (-not (Test-Path -PathType Container $providerPath)) {
            Write-Err "Path does not exist: $providerPath"
            exit 1
        }
    }
    default {
        Write-Err "Invalid provider choice"
        exit 1
    }
}

$project = Read-Host "Project folder name in provider [$projectName]"
if ([string]::IsNullOrWhiteSpace($project)) { $project = $projectName }

Write-Header "Configuration Summary"
Write-Info "Provider          : $provider"
if ($providerPath) { Write-Info "Provider path     : $providerPath" }
if ($remote)       { Write-Info "rclone remote     : $remote" }
if ($remotePath)   { Write-Info "rclone remote path: $remotePath" }
if ($protonRoot)   { Write-Info "Proton project root: $protonRoot" }
Write-Info "Project folder    : $project"
Write-Host ""
Write-Warn "This writes only .dev_sync_config.json. It does not upload or delete files."
$confirm = Read-Host "Save configuration? [y/N]"
if ($confirm -notmatch '^(y|Y|yes|YES)$') {
    Write-Err "Configuration cancelled"
    exit 1
}

Save-Config @{
    project_name        = $project
    provider            = $provider
    provider_path       = $providerPath
    rclone_remote       = $remote
    rclone_remote_path  = $remotePath
    proton_project_root = $protonRoot
    exclude_patterns    = @()
    include_always      = @()
}
Write-Ok "Configuration saved"
Write-Host ""
Write-Info "Next commands:"
Write-Info ".\dev-sync-export.ps1 --dry-run --verbose"
Write-Info ".\dev-sync-export.ps1"
Write-Info ".\dev-sync-verify-full.ps1"
