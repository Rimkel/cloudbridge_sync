@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_oneclick.ps1"
if errorlevel 1 echo Check failed. Review configs\oneclick.yaml and the error above.
pause
