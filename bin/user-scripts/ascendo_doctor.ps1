# ascendo_doctor.ps1 — health snapshot. Wraps `ascendo doctor` plus extra checks.
$ErrorActionPreference = 'Continue'
$Home_ = if ($Env:ASCENDO_HOME) { $Env:ASCENDO_HOME } else { Join-Path $Env:LOCALAPPDATA 'Ascendo\src' }
$Py = Join-Path $Home_ 'venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }

$rc = 0
Write-Host "== ascendo doctor =="
& $Py -m ascendo doctor @args
if ($LASTEXITCODE -ne 0) { $rc = $LASTEXITCODE }

Write-Host ""
Write-Host "== runtime checks =="

# 1. orphan dashboard processes
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'ascendo dashboard' }
$nProc = if ($procs) { $procs.Count } else { 0 }
Write-Host ("  dashboard processes : {0}" -f $nProc)

# 2. log dir size
$LogDir = Join-Path $Env:USERPROFILE '.ascendo\runs'
if (Test-Path $LogDir) {
    $size = (Get-ChildItem $LogDir -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    $nRuns = (Get-ChildItem $LogDir -Directory -ErrorAction SilentlyContinue | Measure-Object).Count
    $sizeMb = if ($size) { [math]::Round($size / 1MB, 1) } else { 0 }
    Write-Host ("  runs directory     : {0} MB ({1} runs)" -f $sizeMb, $nRuns)
}

# 3. inventory db integrity
$InvDb = Join-Path $Env:USERPROFILE '.ascendo\inventory.db'
if (Test-Path $InvDb) {
    $sqlite = Get-Command sqlite3 -ErrorAction SilentlyContinue
    if ($sqlite) {
        $result = & sqlite3 $InvDb 'PRAGMA integrity_check;' 2>$null | Select-Object -First 1
        Write-Host ("  inventory.db check : {0}" -f $result)
    } else {
        Write-Host "  inventory.db check : skipped (sqlite3 CLI not installed)"
    }
}

Write-Host ""
Write-Host "ascendo_doctor: done."
exit $rc
