# ascendo_start_web.ps1 — start the FastAPI dashboard in the background.
$ErrorActionPreference = 'Stop'
$Home_ = if ($Env:ASCENDO_HOME) { $Env:ASCENDO_HOME } else { Join-Path $Env:LOCALAPPDATA 'Ascendo\src' }
$Py = Join-Path $Home_ 'venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }
Start-Process -FilePath $Py -ArgumentList (@('-m','ascendo','dashboard','--background') + $args) -WindowStyle Hidden
