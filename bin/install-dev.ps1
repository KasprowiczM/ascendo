# =============================================================================
# bin\install-dev.ps1 — one-shot dev install for Ascendo on Windows
# =============================================================================
#
# Installs:
#   1. The `ascendo` core package (editable, -e .\core\)
#   2. The Windows adapter (`ascendo-windows`, editable, -e .\adapters\windows\)
#   3. Dashboard runtime deps (fastapi, uvicorn[standard])
#   4. Optionally runs the validation harness at the end
#
# Use:
#   PS> .\bin\install-dev.ps1                # install + validate
#   PS> .\bin\install-dev.ps1 -SkipValidate  # install only
#   PS> .\bin\install-dev.ps1 -Reinstall     # force reinstall both packages
#
# Why this exists:
#   The naïve `pip install -e .\adapters\windows\` fails with
#   "Could not find a version that satisfies the requirement ascendo>=…"
#   because pip's resolver looks on PyPI for the core dependency. We've
#   loosened the dep to just `"ascendo"` (no version pin) — but the
#   resolver also needs to be told NOT to fetch from PyPI for the core
#   package. `--no-build-isolation` and ordering the installs (core
#   first, then adapter) is the most reliable combination.
# =============================================================================

[CmdletBinding()]
param(
    [switch] $SkipValidate,
    [switch] $Reinstall
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

function Write-Step {
    param([string] $Title)
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

try {
    $forceFlag = if ($Reinstall) { '--force-reinstall' } else { '' }

    # Step 1 — core (must be first; the adapter depends on it).
    Write-Step "pip install -e .\core\"
    & python -m pip install -e .\core\ $forceFlag
    if ($LASTEXITCODE -ne 0) { throw "core install failed (exit $LASTEXITCODE)" }

    # Step 2 — Windows adapter. --no-deps avoids pip trying to re-resolve
    # `ascendo` from PyPI (which isn't there). pydantic + pywin32 are
    # already pulled in by the core install above.
    Write-Step "pip install -e .\adapters\windows\ --no-deps"
    & python -m pip install -e .\adapters\windows\ --no-deps $forceFlag
    if ($LASTEXITCODE -ne 0) { throw "adapter install failed (exit $LASTEXITCODE)" }

    # Step 3 — pywin32 (the adapter declares it but --no-deps skipped it).
    Write-Step "pip install pywin32 pywin32-ctypes (adapter runtime deps that --no-deps skipped)"
    & python -m pip install 'pywin32>=306' 'pywin32-ctypes>=0.2'
    if ($LASTEXITCODE -ne 0) { throw "pywin32 install failed (exit $LASTEXITCODE)" }

    # Step 4 — dashboard deps.
    Write-Step "pip install fastapi 'uvicorn[standard]' (dashboard runtime)"
    & python -m pip install 'fastapi>=0.111' 'uvicorn[standard]>=0.30' 'httpx>=0.27'
    if ($LASTEXITCODE -ne 0) { throw "dashboard deps install failed (exit $LASTEXITCODE)" }

    Write-Host ""
    Write-Host "Install complete. Verifying:" -ForegroundColor Green
    & python -m pip show ascendo ascendo-windows fastapi uvicorn 2>&1 |
        Where-Object { $_ -match '^(Name|Version|Location):' } |
        ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

    if (-not $SkipValidate) {
        Write-Step "Running .\bin\validate-windows.ps1"
        & "$PSScriptRoot\validate-windows.ps1"
        exit $LASTEXITCODE
    } else {
        Write-Host ""
        Write-Host "Install OK. Run .\bin\validate-windows.ps1 when ready to test end-to-end." -ForegroundColor Green
    }
}
finally {
    Pop-Location
}
