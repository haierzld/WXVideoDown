@echo off
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.8+ is required. Please install Python and retry.
  pause
  exit /b 1
)
start "WXVideoDown" /min python "%~dp0app.py"
