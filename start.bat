@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Press any key to close this window.
  pause >nul
)

endlocal
