# ascendo_start_desktop.ps1 — launch the Tauri desktop shell.
$ErrorActionPreference = 'Stop'
$Home_ = if ($Env:ASCENDO_HOME) { $Env:ASCENDO_HOME } else { Join-Path $Env:LOCALAPPDATA 'Ascendo\src' }
$Launcher = Join-Path $Home_ 'bin\launch-desktop.ps1'
if (-not (Test-Path $Launcher)) {
    Write-Error "ascendo_start_desktop: launcher not found at $Launcher"
    exit 2
}
& $Launcher @args
