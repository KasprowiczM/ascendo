# ascendo_restart_web.ps1 — stop then start the FastAPI dashboard.
$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $ScriptDir 'ascendo_stop_web.ps1')
Start-Sleep -Seconds 1
& (Join-Path $ScriptDir 'ascendo_start_web.ps1') @args
