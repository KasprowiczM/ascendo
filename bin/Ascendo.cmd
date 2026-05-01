@echo off
REM ============================================================================
REM Ascendo.cmd — one-click Windows launcher
REM ============================================================================
REM
REM Double-click this file (or its desktop/Start Menu shortcut, after running
REM bin\install-shortcut.ps1) to launch the Ascendo dashboard.
REM
REM What it does:
REM   1. Starts the FastAPI dashboard on http://127.0.0.1:8765/
REM   2. Opens your default browser to the interactive Swagger UI
REM   3. Console stays open showing live request logs
REM   4. Ctrl+C in this console stops the dashboard cleanly
REM
REM Requires:
REM   - Python 3.11+ on PATH
REM   - Ascendo installed (run bin\install-dev.ps1 once before first launch)
REM ============================================================================

setlocal
cd /d "%~dp0\.."

REM Use PowerShell to actually launch — gives nicer console output + colors.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-app.ps1" %*
if errorlevel 1 (
    echo.
    echo Ascendo exited with error %errorlevel%.
    echo Press any key to close this window...
    pause > nul
)
endlocal
