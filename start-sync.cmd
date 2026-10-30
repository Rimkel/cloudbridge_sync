@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0configs\oneclick.yaml" (
  echo Run setup-oneclick.cmd first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m cloudbridge.launcher --profile "%~dp0configs\oneclick.yaml" start
if errorlevel 1 pause
