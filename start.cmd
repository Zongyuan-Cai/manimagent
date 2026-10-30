@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install Python 3.12 and uv, then run: uv sync --frozen --extra animation --extra dev
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\launch_local.py
pause
