@echo off
REM ============================================================
REM   TN160 Toolbox - Windows EXE builder
REM   Double-click to build dist\thermocam_gui.exe
REM ============================================================
setlocal enableextensions
cd /d "%~dp0"

echo.
echo [1/5] Looking for Python ...
where python >nul 2>&1
if errorlevel 1 (
    echo   ERROR: python not found on PATH.
    echo   Install Python 3.11+ from https://www.python.org/downloads/ ^(tick "Add to PATH"^).
    pause
    exit /b 1
)
python --version

echo.
echo [2/5] Creating local venv .venv_win (one-time) ...
if not exist ".venv_win\Scripts\python.exe" (
    python -m venv .venv_win
    if errorlevel 1 (
        echo   ERROR: venv creation failed.
        pause
        exit /b 1
    )
)

set "PY=.venv_win\Scripts\python.exe"

echo.
echo [3/5] Upgrading pip + installing deps ...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements_win.txt
if errorlevel 1 (
    echo   ERROR: dependency install failed.
    pause
    exit /b 1
)

echo.
echo [4/5] Cleaning previous build ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist thermocam_gui.spec del /q thermocam_gui.spec

echo.
echo [5/5] Running PyInstaller ...
"%PY%" -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name thermocam_gui ^
    --collect-submodules PySide6 ^
    --collect-data matplotlib ^
    --hidden-import matplotlib.backends.backend_qtagg ^
    --exclude-module PyQt5 ^
    --exclude-module PyQt6 ^
    --exclude-module tkinter ^
    app.py
if errorlevel 1 (
    echo   ERROR: PyInstaller failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   DONE.  Output:  %cd%\dist\thermocam_gui.exe
echo ============================================================
echo.
pause
