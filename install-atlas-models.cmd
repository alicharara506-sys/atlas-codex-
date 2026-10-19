@echo off
setlocal
title Atlas Quant - Install Four Local Models
set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"

echo ============================================================
echo  ATLAS QUANT - FOUR LOCAL LLM INSTALLER
echo ============================================================
echo.

if not exist "%OLLAMA%" (
  echo Ollama was not found at:
  echo %OLLAMA%
  echo.
  echo Please install Ollama first, then run this file again.
  pause
  exit /b 1
)

echo This downloads about 8 GB. Keep this window open.
echo.

call :pull "llama3.2:1b-instruct-q3_K_L" "1 of 4 - Llama 3.2 1B"
if errorlevel 1 goto :failed
call :pull "qwen2.5:3b" "2 of 4 - Qwen 2.5 3B"
if errorlevel 1 goto :failed
call :pull "phi4-mini" "3 of 4 - Phi-4 Mini"
if errorlevel 1 goto :failed
call :pull "ministral-3:3b" "4 of 4 - Ministral 3 3B"
if errorlevel 1 goto :failed

echo.
echo ============================================================
echo  SUCCESS: ALL FOUR ATLAS MODELS ARE INSTALLED
echo ============================================================
echo.
"%OLLAMA%" list
echo.
echo Return to Codex and write: FOUR MODELS DOWNLOADED
pause
exit /b 0

:pull
echo ============================================================
echo  %~2
echo ============================================================
"%OLLAMA%" show %~1 >nul 2>&1
if not errorlevel 1 (
  echo Already installed - skipping download.
  exit /b 0
)

for /L %%A in (1,1,10) do (
  echo Download attempt %%A of 10...
  "%OLLAMA%" pull %~1
  if not errorlevel 1 exit /b 0
  echo Network interruption detected. The partial download is safe.
  echo Refreshing DNS, then retrying in 15 seconds...
  ipconfig /flushdns >nul 2>&1
  timeout /t 15 /nobreak >nul
)
exit /b 1

:failed
echo.
echo A model download failed. Keep this window open and show the
echo error to Codex, or run this installer again to resume safely.
pause
exit /b 1
