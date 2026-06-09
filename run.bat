@echo off
cd /d "%~dp0"

:: Auto-elevate to admin (required for global hotkeys on Windows)
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: Activate venv if present, otherwise use system Python
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

:: Launch without console window
start "" pythonw -m stet.main
if %errorlevel% neq 0 (
    :: pythonw not found — fall back to python
    start "" python -m stet.main
)
