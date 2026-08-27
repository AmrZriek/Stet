@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

:: Auto-elevate to admin (required for global hotkeys on Windows)
net session >nul 2>&1
if %errorlevel% neq 0 (
    if /i not "%~1"=="--no-admin" (
        echo Requesting administrator privileges...
        if "%~1"=="" (
            powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs" 2>nul
        ) else (
            powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs" 2>nul
        )
        if !errorlevel! equ 0 (
            exit /b 0
        )
        echo [NOTE] Administrator elevation skipped or unavailable; continuing in standard user mode...
    )
)

:: ── Step 1: Verify existing virtual environment ─────────────────────────────
set "VENV_PYTHON="
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 (
        set "VENV_PYTHON=venv\Scripts\python.exe"
    )
)

:: ── Step 2: Auto-create / repair venv if missing or broken ──────────────────
if not defined VENV_PYTHON (
    echo [Stet] Virtual environment missing or invalid. Locating Python...

    set "SYS_PYTHON="
    for %%P in (python "py -3" py "%LocalAppData%\Programs\Python\Python313\python.exe" "%LocalAppData%\Programs\Python\Python312\python.exe" "%LocalAppData%\Programs\Python\Python311\python.exe" "C:\Python313\python.exe" "C:\Python312\python.exe" "C:\Python311\python.exe") do (
        if not defined SYS_PYTHON (
            %%~P -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
            if !errorlevel! equ 0 (
                set "SYS_PYTHON=%%~P"
            )
        )
    )

    if not defined SYS_PYTHON (
        echo [ERROR] No compatible Python 3.11+ installation found!
        echo Please install Python 3.11 or newer from https://www.python.org/
        echo Make sure to check "Add Python to PATH" during installation.
        echo.
        pause
        exit /b 1
    )

    echo [Stet] Creating virtual environment using !SYS_PYTHON!...
    where uv >nul 2>&1
    if !errorlevel! equ 0 (
        uv venv venv --clear
    ) else (
        !SYS_PYTHON! -m venv venv --clear
    )

    if not exist "venv\Scripts\python.exe" (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    set "VENV_PYTHON=venv\Scripts\python.exe"
)

:: ── Step 3: Verify and install dependencies ─────────────────────────────────
"%VENV_PYTHON%" -c "import PyQt6, requests, pyperclip, gguf" >nul 2>&1
if !errorlevel! neq 0 (
    echo [Stet] Installing dependencies from requirements.txt...
    where uv >nul 2>&1
    if !errorlevel! equ 0 (
        uv pip install -p "%VENV_PYTHON%" -r requirements.txt
    ) else (
        "%VENV_PYTHON%" -m pip install -r requirements.txt
    )
    if !errorlevel! neq 0 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

:: ── Step 4: Launch application ──────────────────────────────────────────────
if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" -m stet.main %*
) else (
    start "" "%VENV_PYTHON%" -m stet.main %*
)
