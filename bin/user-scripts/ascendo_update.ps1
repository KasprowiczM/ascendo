# ascendo_update.ps1 — pull latest Ascendo + refresh editable installs.
$ErrorActionPreference = 'Stop'
$Home_ = if ($Env:ASCENDO_HOME) { $Env:ASCENDO_HOME } else { Join-Path $Env:LOCALAPPDATA 'Ascendo\src' }
if (-not (Test-Path $Home_)) {
    Write-Error "ascendo_update: ASCENDO_HOME ($Home_) does not exist."
    exit 2
}
& (Join-Path $Home_ 'update.ps1') @args
