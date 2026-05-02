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
    [switch]$SkipDeps,
    [switch]$SkipPrereqCheck
)

$ErrorActionPreference = "Stop"

# Resolve repo root from this script's location: bin/launch-desktop.ps1 -> repo root.
$repoRoot = Split-Path -Parent $PSScriptRoot
$tauriDir = Join-Path $repoRoot "ui/desktop-tauri"

if (-not (Test-Path $tauriDir)) {
    throw "ui/desktop-tauri not found at $tauriDir"
}

# ── Pre-flight: PATH refresh + toolchain detection ──────────────────────
# Cargo / Rust often land in $USERPROFILE\.cargo\bin after a fresh
# `winget install Rustlang.Rustup` but the shell that launched this
# script may not yet have it on PATH. Inject it pre-emptively.
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if ((Test-Path $cargoBin) -and ($env:PATH -notlike "*$cargoBin*")) {
    $env:PATH = "$cargoBin;$env:PATH"
    Write-Host "[prereq] added $cargoBin to PATH for this session" -ForegroundColor DarkGray
}

if (-not $SkipPrereqCheck) {
    $missing = @()

    # Rust
    & rustc --version *> $null
    if ($LASTEXITCODE -ne 0) {
        $missing += @{
            name    = "Rust toolchain (rustc + cargo)"
            install = "winget install Rustlang.Rustup; rustup default stable"
            details = "After install, restart your shell so the new PATH takes effect."
        }
    }

    # MSVC C++ build tools — `winget install Microsoft.VisualStudio.2022.BuildTools`
    # alone installs ONLY the bootstrapper (no C++ workload). Tauri compiles
    # native crates and needs cl.exe / link.exe / Windows SDK headers.
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $hasMsvc = $false
    $bootstrapperPath = $null
    if (Test-Path $vswhere) {
        # Look for the C++ workload component specifically.
        $msvc = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
        if ($msvc) { $hasMsvc = $true }
        # Also detect whether the bootstrapper itself is installed (so we
        # can recommend `vs_installer modify` instead of `winget install`,
        # which silently no-ops on an already-installed package).
        $bootstrapperPath = & $vswhere -all -prerelease -products * -property installationPath 2>$null | Select-Object -First 1
    }
    if (-not $hasMsvc) {
        $vsInstaller = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vs_installer.exe"
        if ($bootstrapperPath -and (Test-Path $vsInstaller)) {
            # Bootstrapper is there but the C++ workload isn't selected.
            # Use `vs_installer modify` to add the workload to the existing
            # install. winget refuses to re-run with --override when the
            # package is "already installed".
            $cmd = "& '$vsInstaller' modify --installPath '$bootstrapperPath' --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --passive --wait"
            $missing += @{
                name    = "MSVC C++ workload (Microsoft.VisualStudio.Workload.VCTools)"
                install = $cmd
                details = "The Build Tools bootstrapper is installed at `"$bootstrapperPath`" but no C++ workload is selected. Run the modify command (UAC will prompt). 5-10 min."
            }
        } else {
            $missing += @{
                name    = "MSVC C++ build tools (Microsoft.VisualStudio.Workload.VCTools)"
                install = 'winget install Microsoft.VisualStudio.2022.BuildTools --override "--passive --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"'
                details = "First-time install: winget will pull the bootstrapper and run it with the workload flag in one shot."
            }
        }
    }

    # Node
    & node --version *> $null
    if ($LASTEXITCODE -ne 0) {
        $missing += @{
            name    = "Node.js 18+"
            install = "winget install OpenJS.NodeJS.LTS"
            details = ""
        }
    }

    if ($missing.Count -gt 0) {
        Write-Host "" -ForegroundColor Yellow
        Write-Host "Missing prerequisites for the Tauri build:" -ForegroundColor Yellow
        foreach ($m in $missing) {
            Write-Host "" -ForegroundColor Yellow
            Write-Host "  - $($m['name'])" -ForegroundColor Red
            Write-Host "    install: $($m['install'])" -ForegroundColor White
            if ($m['details']) {
                Write-Host "    note:    $($m['details'])" -ForegroundColor DarkGray
            }
        }
        Write-Host "" -ForegroundColor Yellow
        Write-Host "Install everything above (the MSVC workload step takes ~5 min), restart your shell, then re-run this script." -ForegroundColor Yellow
        Write-Host "To skip this check (e.g. you have an unconventional setup), pass -SkipPrereqCheck." -ForegroundColor DarkGray
        exit 2
    }

    Write-Host "[prereq] rustc, cargo, MSVC, node all present" -ForegroundColor Green
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
