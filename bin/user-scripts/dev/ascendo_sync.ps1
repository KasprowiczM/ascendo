# ascendo_sync.ps1 — push the dev-sync overlay to Proton (dev edition only).
$ErrorActionPreference = 'Stop'
$Home_ = if ($Env:ASCENDO_HOME) { $Env:ASCENDO_HOME } else { Join-Path $Env:LOCALAPPDATA 'Ascendo\src' }
$Script_ = Join-Path $Home_ 'dev-sync-export.ps1'
if (-not (Test-Path $Script_)) {
    Write-Error "ascendo_sync: dev-sync-export.ps1 not found at $Script_ (dev-edition only)"
    exit 2
}
& $Script_ @args
