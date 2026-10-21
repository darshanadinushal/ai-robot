@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher not found. Install Python 3.13 from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Setup complete! Double-click RUN_WINDOWS.bat to launch your robot.
pause
exit /b 0
:failed
echo Setup failed. Check the error messages above.
pause
exit /b 1
