@echo off
setlocal
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m cloudbridge.launcher --profile "%~dp0configs\oneclick.yaml" stop
pause
