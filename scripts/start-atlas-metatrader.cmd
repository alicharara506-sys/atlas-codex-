@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-atlas-metatrader.ps1" -Restart
if errorlevel 1 pause
endlocal
