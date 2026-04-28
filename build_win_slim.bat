@echo off
REM ============================================================
REM   TN160 Toolbox - Windows EXE builder (SLIM / production test)
REM   Double-click to build dist\thermocam_gui\thermocam_gui.exe
REM
REM   Differences vs build_win.bat:
REM     * onedir (folder) instead of onefile  -> instant startup
REM       (no temp-extract on every launch)
REM     * only PySide6 QtCore/QtGui/QtWidgets bundled
REM       (no Qt3D / WebEngine / Multimedia / Charts / Quick / etc.)
REM     * no matplotlib sample data; only the qtagg backend
REM     * scipy / pandas / PIL / IPython etc. excluded
REM     * final folder zipped to dist\thermocam_gui_slim.zip
REM ============================================================
setlocal enableextensions
cd /d "%~dp0"

echo.
echo [1/6] Looking for Python ...
where python >nul 2>&1
if errorlevel 1 (
    echo   ERROR: python not found on PATH.
    echo   Install Python 3.11+ from https://www.python.org/downloads/ ^(tick "Add to PATH"^).
    pause
    exit /b 1
)
python --version

echo.
echo [2/6] Creating local venv .venv_win (one-time) ...
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
echo [3/6] Upgrading pip + installing deps ...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements_win.txt
if errorlevel 1 (
    echo   ERROR: dependency install failed.
    pause
    exit /b 1
)
REM matplotlib >= 3.8 hard-requires Pillow at import time
"%PY%" -m pip install "pillow>=10.0"
if errorlevel 1 (
    echo   ERROR: pillow install failed.
    pause
    exit /b 1
)

echo.
echo [4/6] Cleaning previous build ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist thermocam_gui.spec del /q thermocam_gui.spec
if exist dist\thermocam_gui_slim.zip del /q dist\thermocam_gui_slim.zip

echo.
echo [5/6] Running PyInstaller (slim) ...
"%PY%" -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --windowed ^
    --name thermocam_gui ^
    --hidden-import matplotlib.backends.backend_qtagg ^
    --exclude-module PyQt5 ^
    --exclude-module PyQt6 ^
    --exclude-module PySide2 ^
    --exclude-module tkinter ^
    --exclude-module scipy ^
    --exclude-module pandas ^
    --exclude-module IPython ^
    --exclude-module ipykernel ^
    --exclude-module jupyter ^
    --exclude-module jupyter_client ^
    --exclude-module jupyter_core ^
    --exclude-module notebook ^
    --exclude-module pytest ^
    --exclude-module sphinx ^
    --exclude-module sqlite3 ^
    --exclude-module lib2to3 ^
    --exclude-module pydoc_data ^
    --exclude-module distutils ^
    --exclude-module setuptools ^
    --exclude-module pip ^
    --exclude-module wheel ^
    --exclude-module PySide6.Qt3DAnimation ^
    --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.Qt3DExtras ^
    --exclude-module PySide6.Qt3DInput ^
    --exclude-module PySide6.Qt3DLogic ^
    --exclude-module PySide6.Qt3DRender ^
    --exclude-module PySide6.QtBluetooth ^
    --exclude-module PySide6.QtCharts ^
    --exclude-module PySide6.QtConcurrent ^
    --exclude-module PySide6.QtDataVisualization ^
    --exclude-module PySide6.QtDBus ^
    --exclude-module PySide6.QtDesigner ^
    --exclude-module PySide6.QtHelp ^
    --exclude-module PySide6.QtHttpServer ^
    --exclude-module PySide6.QtLocation ^
    --exclude-module PySide6.QtMultimedia ^
    --exclude-module PySide6.QtMultimediaWidgets ^
    --exclude-module PySide6.QtNetwork ^
    --exclude-module PySide6.QtNetworkAuth ^
    --exclude-module PySide6.QtNfc ^
    --exclude-module PySide6.QtOpenGL ^
    --exclude-module PySide6.QtOpenGLWidgets ^
    --exclude-module PySide6.QtPdf ^
    --exclude-module PySide6.QtPdfWidgets ^
    --exclude-module PySide6.QtPositioning ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtQuick3D ^
    --exclude-module PySide6.QtQuickControls2 ^
    --exclude-module PySide6.QtQuickWidgets ^
    --exclude-module PySide6.QtRemoteObjects ^
    --exclude-module PySide6.QtScxml ^
    --exclude-module PySide6.QtSensors ^
    --exclude-module PySide6.QtSerialBus ^
    --exclude-module PySide6.QtSerialPort ^
    --exclude-module PySide6.QtSpatialAudio ^
    --exclude-module PySide6.QtSql ^
    --exclude-module PySide6.QtStateMachine ^
    --exclude-module PySide6.QtSvg ^
    --exclude-module PySide6.QtSvgWidgets ^
    --exclude-module PySide6.QtTest ^
    --exclude-module PySide6.QtTextToSpeech ^
    --exclude-module PySide6.QtUiTools ^
    --exclude-module PySide6.QtWebChannel ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineQuick ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtWebSockets ^
    --exclude-module PySide6.QtXml ^
    app.py
if errorlevel 1 (
    echo   ERROR: PyInstaller failed.
    pause
    exit /b 1
)

echo.
echo [6/6] Zipping dist\thermocam_gui  -^>  dist\thermocam_gui_slim.zip ...
REM small wait so antivirus / indexer releases the freshly written files
timeout /t 3 /nobreak >nul
if exist dist\thermocam_gui_slim.zip del /q dist\thermocam_gui_slim.zip
"%PY%" -c "import shutil; shutil.make_archive('dist/thermocam_gui_slim', 'zip', 'dist', 'thermocam_gui')"
if errorlevel 1 (
    echo   WARN: zip step failed, but the EXE is still under dist\thermocam_gui\
)

echo.
echo ============================================================
echo   DONE.
echo     EXE folder : %cd%\dist\thermocam_gui\
echo     Run        : %cd%\dist\thermocam_gui\thermocam_gui.exe
echo     Ship as    : %cd%\dist\thermocam_gui_slim.zip
echo ============================================================
echo.
pause
