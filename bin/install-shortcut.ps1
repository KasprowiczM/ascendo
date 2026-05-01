# =============================================================================
# bin\install-shortcut.ps1 — create Desktop + Start Menu shortcuts for Ascendo
# =============================================================================
#
# Run once after .\bin\install-dev.ps1. Creates:
#   - Desktop:    %USERPROFILE%\Desktop\Ascendo.lnk
#   - Start Menu: %APPDATA%\Microsoft\Windows\Start Menu\Programs\Ascendo.lnk
#
# Both shortcuts launch bin\Ascendo.cmd (which starts the dashboard +
# opens a browser tab). User can pin the Start Menu entry to taskbar from
# there.
#
# Use:
#   PS> .\bin\install-shortcut.ps1                # install shortcuts
#   PS> .\bin\install-shortcut.ps1 -Uninstall     # remove them
#   PS> .\bin\install-shortcut.ps1 -DesktopOnly   # skip Start Menu
# =============================================================================

[CmdletBinding()]
param(
    [switch] $Uninstall,
    [switch] $DesktopOnly,
    [switch] $StartMenuOnly
)

$ErrorActionPreference = 'Stop'

# Resolve absolute paths
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$LauncherCmd = Join-Path $RepoRoot 'bin\Ascendo.cmd'

if (-not (Test-Path $LauncherCmd)) {
    Write-Host "ERROR: $LauncherCmd not found." -ForegroundColor Red
    Write-Host "Did you clone the repo correctly? Re-run from repo root." -ForegroundColor Yellow
    exit 1
}

# Resolve the icon — fall back gracefully if branding/icon.ico is missing
$IconCandidates = @(
    (Join-Path $RepoRoot 'branding\icon.ico'),
    (Join-Path $RepoRoot 'branding\Ascendo.ico'),
    (Join-Path $RepoRoot 'branding\ascendo.ico')
)
$IconPath = $IconCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $IconPath) {
    # Fallback: use cmd.exe's icon (any .cmd has a default Windows shell icon)
    $IconPath = "$env:SystemRoot\System32\cmd.exe,0"
    Write-Host "(no branding/icon.ico found; using cmd.exe default icon)" -ForegroundColor DarkGray
}

# Shortcut targets
$DesktopLnk   = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Ascendo.lnk'
$StartMenuLnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'Ascendo.lnk'

function New-AscendoShortcut {
    param([string] $LinkPath, [string] $Description)

    $shell = New-Object -ComObject WScript.Shell
    $sc    = $shell.CreateShortcut($LinkPath)
    $sc.TargetPath        = $LauncherCmd
    $sc.WorkingDirectory  = $RepoRoot
    $sc.WindowStyle       = 1  # 1 = Normal, 7 = Minimized, 3 = Maximized
    $sc.Description       = $Description
    $sc.IconLocation      = $IconPath
    $sc.Save()
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($shell) | Out-Null

    Write-Host "  ✓ $LinkPath" -ForegroundColor Green
}

function Remove-AscendoShortcut {
    param([string] $LinkPath)

    if (Test-Path $LinkPath) {
        Remove-Item $LinkPath -Force
        Write-Host "  ✓ removed $LinkPath" -ForegroundColor Green
    } else {
        Write-Host "  - $LinkPath (already absent)" -ForegroundColor DarkGray
    }
}

# ── Main ────────────────────────────────────────────────────────────────────

Write-Host ''
Write-Host "Ascendo shortcuts" -ForegroundColor Cyan
Write-Host ('-' * 40) -ForegroundColor Cyan

if ($Uninstall) {
    Write-Host "Removing shortcuts..." -ForegroundColor Yellow
    if (-not $StartMenuOnly) { Remove-AscendoShortcut $DesktopLnk }
    if (-not $DesktopOnly)   { Remove-AscendoShortcut $StartMenuLnk }
    Write-Host ''
    Write-Host "Done. Run .\bin\install-shortcut.ps1 (without -Uninstall) to re-create." -ForegroundColor DarkGray
    exit 0
}

Write-Host "Installing shortcuts pointing at:" -ForegroundColor DarkGray
Write-Host "  $LauncherCmd" -ForegroundColor DarkGray
Write-Host "  Icon: $IconPath" -ForegroundColor DarkGray
Write-Host ''

if (-not $StartMenuOnly) {
    New-AscendoShortcut -LinkPath $DesktopLnk -Description 'Ascendo - Cross-platform update orchestrator'
}
if (-not $DesktopOnly) {
    New-AscendoShortcut -LinkPath $StartMenuLnk -Description 'Ascendo - Cross-platform update orchestrator'
}

Write-Host ''
Write-Host "Done. You can now:" -ForegroundColor Green
Write-Host '  - Double-click "Ascendo" on your desktop, OR'
Write-Host '  - Press the Windows key, type "Ascendo", and Enter, OR'
Write-Host '  - Right-click the Start Menu entry and "Pin to taskbar"'
Write-Host ''
Write-Host "Each will open the dashboard in your browser at http://127.0.0.1:8765/docs" -ForegroundColor Cyan
