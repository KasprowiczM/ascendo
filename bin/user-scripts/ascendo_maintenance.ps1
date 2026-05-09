# ascendo_maintenance.ps1 — high-level maintenance ops.
#
# Usage:
#   ascendo_maintenance full
#   ascendo_maintenance quick
#   ascendo_maintenance dry-run
#   ascendo_maintenance category=<name>
#   ascendo_maintenance rebuild-inventory
#   ascendo_maintenance check-errors
$ErrorActionPreference = 'Stop'
$Home_ = if ($Env:ASCENDO_HOME) { $Env:ASCENDO_HOME } else { Join-Path $Env:LOCALAPPDATA 'Ascendo\src' }
$Py = Join-Path $Home_ 'venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }

$cmd = if ($args.Count -ge 1) { $args[0] } else { '' }

switch -Wildcard ($cmd) {
    'full' {
        & $Py -m ascendo run --profile full
        exit $LASTEXITCODE
    }
    'quick' {
        & $Py -m ascendo run --profile quick
        exit $LASTEXITCODE
    }
    'dry-run' {
        & $Py -m ascendo run --dry-run
        exit $LASTEXITCODE
    }
    'category=*' {
        $cat = $cmd.Substring('category='.Length)
        & $Py -m ascendo run --category $cat
        exit $LASTEXITCODE
    }
    'rebuild-inventory' {
        $InvDb = Join-Path $Env:USERPROFILE '.ascendo\inventory.db'
        if (Test-Path $InvDb) { Remove-Item -Force $InvDb }
        Write-Host 'ascendo_maintenance: inventory.db removed; running check phase to rebuild...'
        & $Py -m ascendo run --profile quick
        exit $LASTEXITCODE
    }
    'check-errors' {
        Write-Host 'ascendo_maintenance: scanning ~/.ascendo/runs for failed sidecars...'
        $RunsDir = Join-Path $Env:USERPROFILE '.ascendo\runs'
        if (Test-Path $RunsDir) {
            $marker = Join-Path $RunsDir '.last-error-scan'
            $sidecars = if (Test-Path $marker) {
                $markerTime = (Get-Item $marker).LastWriteTime
                Get-ChildItem $RunsDir -Recurse -Filter '*.json' -ErrorAction SilentlyContinue |
                    Where-Object { $_.LastWriteTime -gt $markerTime }
            } else {
                Get-ChildItem $RunsDir -Recurse -Filter '*.json' -ErrorAction SilentlyContinue
            }
            $failed = $sidecars | Select-Object -First 100 |
                Where-Object { (Select-String -Path $_.FullName -Pattern '"status":\s*"failed"' -Quiet) } |
                Select-Object -First 20
            $failed | ForEach-Object { Write-Host $_.FullName }
            New-Item -ItemType File -Path $marker -Force | Out-Null
        }
    }
    { $_ -in '', 'help', '-h', '--help' } {
        @'
ascendo_maintenance — high-level maintenance ops

  full                  Run all categories, all phases (check->...->cleanup)
  quick                 Just the check phase across all categories
  dry-run               Plan only, no apply
  category=<name>       All phases for one category (e.g. brew, npm, pip, web)
  rebuild-inventory     Wipe + rebuild ~/.ascendo/inventory.db
  check-errors          Scan recent runs for failed sidecars
  help                  This message
'@ | Write-Host
    }
    default {
        Write-Error "ascendo_maintenance: unknown command '$cmd'. Try 'ascendo_maintenance help'."
        exit 2
    }
}
