#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Build the Ascendo Windows installers (MSI + NSIS .exe).

.DESCRIPTION
  End-to-end pipeline:

    1. Run PyInstaller on `packaging/pyinstaller/ascendo.spec`. Produces
       `dist/pyinstaller/ascendo/ascendo.exe` plus its `_internal/`
       runtime folder.
    2. Stage the bundle into
       `ui/desktop-tauri/src-tauri/binaries/python-sidecar/`
       so Tauri picks it up via `bundle.resources` in tauri.conf.json
       and `main.rs::locate_sidecar`.
    3. Run `npm run tauri build` (delegating to bin/launch-desktop.ps1).
       This produces both `.msi` and `.exe` artifacts under
       `ui/desktop-tauri/src-tauri/target/release/bundle/`.
    4. Copy the artifacts to `dist/` at the repo root with the canonical
       names `Ascendo-<version>-x64.msi` and
       `Ascendo-<version>-x64-setup.exe`. Print the SHA256 of each.

.PARAMETER SkipPyInstaller
  Skip step 1 (re-use an existing `dist/pyinstaller/ascendo/`). Useful
  when iterating on the Rust shell or installer assets without paying
  the ~60s PyInstaller cost each time.

.PARAMETER SkipTauri
  Skip step 3. Combined with -SkipPyInstaller this gives you a no-op
  dry-run that just shows what would happen.

.PARAMETER SkipDeps
  Forwarded to bin/launch-desktop.ps1 — skips `npm install`.

.PARAMETER SkipPrereqCheck
  Forwarded to bin/launch-desktop.ps1 — skips Rust/MSVC/Node detection.

.EXAMPLE
  pwsh -File bin\build-installer.ps1
  # → dist/Ascendo-0.0.7-x64.msi
  # → dist/Ascendo-0.0.7-x64-setup.exe

.EXAMPLE
  pwsh -File bin\build-installer.ps1 -SkipPyInstaller
  # Quick rebuild after tweaking installer assets only.

.NOTES
  Code signing: NOT performed. To sign the resulting binaries, run
  `signtool sign /fd sha256 /tr <ts-url> /td sha256 /a <path>` on each
  artifact AFTER this script completes. See packaging/README.md.
#>
[CmdletBinding()]
param(
    [switch]$SkipPyInstaller,
    [switch]$SkipTauri,
    [switch]$SkipDeps,
    [switch]$SkipPrereqCheck
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

# Resolve version from the single source of truth so a future bump in
# core/ascendo/__version__.py automatically retitles the artifacts.
$VersionFile = Join-Path $RepoRoot "core/ascendo/__version__.py"
$VersionMatch = (Get-Content $VersionFile -Raw) | Select-String -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $VersionMatch) {
    throw "could not parse __version__ from $VersionFile"
}
$Version = $VersionMatch.Matches[0].Groups[1].Value
Write-Host "[ascendo-installer] version = $Version" -ForegroundColor Cyan

$DistDir = Join-Path $RepoRoot "dist"
$PyinstallerDist = Join-Path $RepoRoot "dist/pyinstaller"
$PyinstallerBuild = Join-Path $RepoRoot "dist/pyinstaller-build"
$SidecarStaging = Join-Path $RepoRoot "ui/desktop-tauri/src-tauri/binaries/python-sidecar"
$TauriBundleDir = Join-Path $RepoRoot "ui/desktop-tauri/src-tauri/target/release/bundle"

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

# ── Step 1: PyInstaller ───────────────────────────────────────────────
if (-not $SkipPyInstaller) {
    Write-Host "[1/4] running PyInstaller…" -ForegroundColor Cyan
    & python -m PyInstaller `
        (Join-Path $RepoRoot "packaging/pyinstaller/ascendo.spec") `
        --noconfirm `
        --distpath $PyinstallerDist `
        --workpath $PyinstallerBuild
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed (exit $LASTEXITCODE)"
    }
    if (-not (Test-Path (Join-Path $PyinstallerDist "ascendo/ascendo.exe"))) {
        throw "PyInstaller succeeded but ascendo.exe is missing from $PyinstallerDist/ascendo/"
    }
    Write-Host "      → $PyinstallerDist/ascendo/ascendo.exe" -ForegroundColor Green
} else {
    Write-Host "[1/4] -SkipPyInstaller: re-using $PyinstallerDist/ascendo/" -ForegroundColor Yellow
    if (-not (Test-Path (Join-Path $PyinstallerDist "ascendo/ascendo.exe"))) {
        throw "no existing ascendo.exe at $PyinstallerDist/ascendo/ — drop -SkipPyInstaller"
    }
}

# ── Step 2: Stage into Tauri sidecar slot + installer assets ─────────
Write-Host "[2/4] staging sidecar bundle + installer assets…" -ForegroundColor Cyan
if (Test-Path $SidecarStaging) {
    Remove-Item -Recurse -Force $SidecarStaging
}
New-Item -ItemType Directory -Force -Path $SidecarStaging | Out-Null
Copy-Item -Recurse -Force `
    -Path (Join-Path $PyinstallerDist "ascendo/*") `
    -Destination $SidecarStaging
Write-Host "      → sidecar    : $SidecarStaging" -ForegroundColor Green

# Tauri's path resolver does not handle `..` segments crossing the
# workspace root cleanly (verified on tauri@2.11), so we mirror the
# canonical sources from packaging/installer-assets/ + repo-root LICENSE
# into src-tauri/ at build time. The mirror is .gitignored.
$AssetsSrc = Join-Path $RepoRoot "packaging/installer-assets"
$AssetsDst = Join-Path $RepoRoot "ui/desktop-tauri/src-tauri/installer-assets"
$LicenseSrc = Join-Path $RepoRoot "LICENSE"
$LicenseDst = Join-Path $RepoRoot "ui/desktop-tauri/src-tauri/LICENSE"
New-Item -ItemType Directory -Force -Path $AssetsDst | Out-Null
foreach ($f in @("installer-banner-nsis.bmp","installer-banner-wix.bmp",
                 "installer-sidebar-nsis.bmp","installer-sidebar-wix.bmp",
                 "nsis-installer-hooks.nsh")) {
    Copy-Item -Force (Join-Path $AssetsSrc $f) (Join-Path $AssetsDst $f)
}
Copy-Item -Force $LicenseSrc $LicenseDst
Write-Host "      → assets     : $AssetsDst" -ForegroundColor Green
Write-Host "      → license    : $LicenseDst" -ForegroundColor Green

# ── Step 3: Tauri build ───────────────────────────────────────────────
if (-not $SkipTauri) {
    Write-Host "[3/4] running Tauri build (this can take 5-10 min on first run)…" -ForegroundColor Cyan
    # Splat as a hashtable, NOT an array — array splatting drops switch
    # names through nested script invocation in some PS edges (verified
    # on PS7.6 against bin/launch-desktop.ps1 with `[switch]$Build`).
    $launchArgs = @{ Build = $true }
    if ($SkipDeps)        { $launchArgs['SkipDeps']        = $true }
    if ($SkipPrereqCheck) { $launchArgs['SkipPrereqCheck'] = $true }
    & (Join-Path $PSScriptRoot "launch-desktop.ps1") @launchArgs
    if ($LASTEXITCODE -ne 0) {
        throw "tauri build failed (exit $LASTEXITCODE)"
    }
} else {
    Write-Host "[3/4] -SkipTauri: re-using existing artifacts under $TauriBundleDir" -ForegroundColor Yellow
}

# ── Step 4: Copy artifacts + SHA256 ───────────────────────────────────
Write-Host "[4/4] copying artifacts to $DistDir/…" -ForegroundColor Cyan

# Tauri produces names like `Ascendo_0.0.7_x64_en-US.msi` and
# `Ascendo_0.0.7_x64-setup.exe`. We rename to the project-canonical form
# `Ascendo-<version>-x64.msi` and `Ascendo-<version>-x64-setup.exe`.
$msiSrc = Get-ChildItem -Path (Join-Path $TauriBundleDir "msi") -Filter "*.msi" -ErrorAction SilentlyContinue | Select-Object -First 1
$nsisSrc = Get-ChildItem -Path (Join-Path $TauriBundleDir "nsis") -Filter "*setup.exe" -ErrorAction SilentlyContinue | Select-Object -First 1

$artifacts = @()

if ($msiSrc) {
    $msiDst = Join-Path $DistDir "Ascendo-$Version-x64.msi"
    Copy-Item -Force $msiSrc.FullName $msiDst
    $artifacts += $msiDst
} else {
    Write-Host "      ! no .msi found under $TauriBundleDir/msi/ — Tauri build may have skipped MSI" -ForegroundColor Yellow
}

if ($nsisSrc) {
    $nsisDst = Join-Path $DistDir "Ascendo-$Version-x64-setup.exe"
    Copy-Item -Force $nsisSrc.FullName $nsisDst
    $artifacts += $nsisDst
} else {
    Write-Host "      ! no setup.exe found under $TauriBundleDir/nsis/ — Tauri build may have skipped NSIS" -ForegroundColor Yellow
}

if ($artifacts.Count -eq 0) {
    throw "no installer artifacts produced — check the Tauri build log above"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Ascendo installer artifacts (v$Version)" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
foreach ($art in $artifacts) {
    $hash = (Get-FileHash -Algorithm SHA256 -Path $art).Hash.ToLower()
    $size = "{0:N1} MB" -f ((Get-Item $art).Length / 1MB)
    Write-Host ""
    Write-Host "  $art" -ForegroundColor White
    Write-Host "    size:   $size" -ForegroundColor DarkGray
    Write-Host "    sha256: $hash" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  - sign:   signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /a <artifact>" -ForegroundColor DarkGray
Write-Host "  - test:   sudo dpkg -i …  (no — Windows; just double-click on a clean VM)" -ForegroundColor DarkGray
Write-Host "  - winget: see packaging/winget-manifest/ (sub-project 5)" -ForegroundColor DarkGray
