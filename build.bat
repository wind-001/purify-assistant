@echo off
setlocal
cd /d %~dp0

rem Use the project venv if it exists, otherwise fall back to system Python.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo [1/2] Installing dependencies...
"%PY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo [2/2] Building dist\Purify.exe ...
"%PY%" -m PyInstaller --noconfirm --onefile --windowed --name Purify --add-data "assets;assets" main.py
if errorlevel 1 goto :fail

echo.
echo Done. Your exe is at dist\Purify.exe
pause
exit /b 0

:fail
echo.
echo Build failed - check the log above.
pause
exit /b 1
