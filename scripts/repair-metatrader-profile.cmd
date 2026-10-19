@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0repair-metatrader-profile.ps1"
if errorlevel 1 pause
endlocal
