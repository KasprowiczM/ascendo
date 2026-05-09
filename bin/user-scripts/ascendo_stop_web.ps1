# ascendo_stop_web.ps1 — terminate any running ascendo dashboard process.
$ErrorActionPreference = 'Stop'
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'ascendo dashboard' }
if ($procs) {
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "ascendo_stop_web: stopped $($procs.Count) process(es)."
} else {
    Write-Host "ascendo_stop_web: no dashboard process running."
}
