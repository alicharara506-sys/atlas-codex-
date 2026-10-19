@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-backend.ps1" -Port 8800
if errorlevel 1 pause
endlocal
