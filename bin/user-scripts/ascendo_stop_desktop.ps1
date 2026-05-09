# ascendo_stop_desktop.ps1 — stop the Tauri shell process.
$ErrorActionPreference = 'Stop'
$names = @('ascendo-desktop', 'Ascendo')
$stopped = 0
foreach ($n in $names) {
    $procs = Get-Process -Name $n -ErrorAction SilentlyContinue
    if ($procs) {
        $procs | Stop-Process -Force -ErrorAction SilentlyContinue
        $stopped += $procs.Count
    }
}
if ($stopped -gt 0) {
    Write-Host "ascendo_stop_desktop: stopped $stopped process(es)."
} else {
    Write-Host "ascendo_stop_desktop: no desktop process running."
}
