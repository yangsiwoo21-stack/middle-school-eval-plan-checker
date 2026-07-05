@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_app.ps1"
if errorlevel 1 (
  echo.
  echo The assessment checker could not start. Please check the message above.
  pause
)
