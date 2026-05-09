# ascendo_push.ps1 — push current branch to GitHub (dev edition only).
$ErrorActionPreference = 'Stop'
$Home_ = if ($Env:ASCENDO_HOME) { $Env:ASCENDO_HOME } else { Join-Path $Env:LOCALAPPDATA 'Ascendo\src' }
if (-not (Test-Path (Join-Path $Home_ '.git'))) {
    Write-Error "ascendo_push: $Home_ is not a git checkout (dev-edition only)"
    exit 2
}
Push-Location $Home_
try {
    & git push @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
