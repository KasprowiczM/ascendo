#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Launch the Ascendo Tauri 2.x desktop shell.

.DESCRIPTION
  Wraps `npm run tauri dev` (default) or `npm run tauri build` (-Build).
  Skips `npm install` with -SkipDeps if you've already done it once.

  Requirements (one-time install):
    - Rust 1.78+      (rustup default stable)
    - Node 18+        (winget install OpenJS.NodeJS.LTS)
    - MSVC build tools (winget install Microsoft.VisualStudio.2022.BuildTools)
    - WebView2 runtime (preinstalled on Win11)
#>
param(
    [switch]$Build,
    [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"

# Resolve repo root from this script's location: bin/launch-desktop.ps1 -> repo root.
$repoRoot = Split-Path -Parent $PSScriptRoot
$tauriDir = Join-Path $repoRoot "ui/desktop-tauri"

if (-not (Test-Path $tauriDir)) {
    throw "ui/desktop-tauri not found at $tauriDir"
}

Set-Location $tauriDir

if (-not $SkipDeps) {
    Write-Host "Installing npm deps..." -ForegroundColor Cyan
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed (exit $LASTEXITCODE)" }
}

if ($Build) {
    Write-Host "Building production bundle (this can take 5-10 min on first run)..." -ForegroundColor Cyan
    npm run tauri build
    if ($LASTEXITCODE -ne 0) { throw "tauri build failed (exit $LASTEXITCODE)" }
    Write-Host "Bundle written to ui/desktop-tauri/src-tauri/target/release/bundle/" -ForegroundColor Green
} else {
    Write-Host "Launching dev shell (Ctrl+C to stop)..." -ForegroundColor Green
    npm run tauri dev
}
