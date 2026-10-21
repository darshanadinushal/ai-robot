@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run SETUP_WINDOWS.bat once before starting the app.
  pause
  exit /b 1
)
.venv\Scripts\python.exe main.py --windowed
if errorlevel 1 pause
