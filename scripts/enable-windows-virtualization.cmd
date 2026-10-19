@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable-windows-virtualization.ps1"
if errorlevel 1 pause
endlocal
