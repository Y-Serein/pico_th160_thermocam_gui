@echo off
REM ============================================================
REM   TN160 Thermal Workbench - canonical Windows package builder
REM
REM   Local run : dist\TN160_Thermal_Workbench\TN160_Thermal_Workbench.exe
REM   Send      : dist\TN160_Thermal_Workbench.zip
REM ============================================================
setlocal enableextensions
pushd "%~dp0"
if errorlevel 1 (
    echo   ERROR: cannot enter the project directory.
    exit /b 1
)

set "APP_NAME=TN160_Thermal_Workbench"
set "PY=.venv_win\Scripts\python.exe"
set "USER_DATA=.workbench_user_data"

echo.
echo [1/9] Looking for Python ...
where python >nul 2>&1
if errorlevel 1 (
    echo   ERROR: python not found on PATH.
    echo   Install Python 3.11+ and enable "Add python.exe to PATH".
    pause
    exit /b 1
)
python --version

echo.
echo [2/9] Preparing local build environment ...
if not exist "%PY%" (
    python -m venv .venv_win
    if errorlevel 1 goto :fail_venv
)

"%PY%" -c "import PySide6, serial, numpy, matplotlib, PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo   Installing build dependencies ...
    "%PY%" -m pip install -r requirements_win.txt
    if errorlevel 1 goto :fail_deps
)

echo.
echo [3/9] Preserving local runtime data outside dist ...
if not exist "%USER_DATA%" mkdir "%USER_DATA%"
if errorlevel 1 goto :fail_userdata
if exist "dist\%APP_NAME%\logs" (
    if not exist "%USER_DATA%\logs" mkdir "%USER_DATA%\logs"
    robocopy "dist\%APP_NAME%\logs" "%USER_DATA%\logs" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_userdata
)
if exist "dist\%APP_NAME%\cali_data_backup" (
    if not exist "%USER_DATA%\cali_data_backup" mkdir "%USER_DATA%\cali_data_backup"
    robocopy "dist\%APP_NAME%\cali_data_backup" "%USER_DATA%\cali_data_backup" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_userdata
)
if exist "dist\%APP_NAME%\thermal_state_capture" (
    if not exist "%USER_DATA%\thermal_state_capture" mkdir "%USER_DATA%\thermal_state_capture"
    robocopy "dist\%APP_NAME%\thermal_state_capture" "%USER_DATA%\thermal_state_capture" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_userdata
)
if exist "dist\%APP_NAME%\ffc_runtime_diagnostic" (
    if not exist "%USER_DATA%\ffc_runtime_diagnostic" mkdir "%USER_DATA%\ffc_runtime_diagnostic"
    robocopy "dist\%APP_NAME%\ffc_runtime_diagnostic" "%USER_DATA%\ffc_runtime_diagnostic" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_userdata
)
if exist "dist\%APP_NAME%\thermal_v2_calibration" (
    if not exist "%USER_DATA%\thermal_v2_calibration" mkdir "%USER_DATA%\thermal_v2_calibration"
    robocopy "dist\%APP_NAME%\thermal_v2_calibration" "%USER_DATA%\thermal_v2_calibration" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_userdata
)

echo.
echo [4/9] Cleaning previous generated output ...
tasklist /FI "IMAGENAME eq %APP_NAME%.exe" /NH | find /I "%APP_NAME%.exe" >nul
if not errorlevel 1 goto :fail_running
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist build goto :fail_clean
if exist dist goto :fail_clean

echo.
echo [5/9] Building the complete application folder ...
"%PY%" -m PyInstaller --noconfirm --clean thermocam_gui.spec
if errorlevel 1 goto :fail_build

echo.
echo [6/9] Adding distribution instructions ...
copy /y README_WIN.md "dist\%APP_NAME%\README_WIN.md" >nul
if errorlevel 1 goto :fail_package

echo.
echo [7/9] Creating the clean file that should be sent ...
"%PY%" -c "import shutil; shutil.make_archive(r'dist/%APP_NAME%', 'zip', r'dist', r'%APP_NAME%')"
if errorlevel 1 goto :fail_package
certutil -hashfile "dist\%APP_NAME%.zip" SHA256 > "dist\%APP_NAME%.zip.sha256.txt"
if errorlevel 1 goto :fail_package

echo.
echo [8/9] Restoring local runtime data to the runnable folder ...
if exist "%USER_DATA%\logs" (
    if not exist "dist\%APP_NAME%\logs" mkdir "dist\%APP_NAME%\logs"
    robocopy "%USER_DATA%\logs" "dist\%APP_NAME%\logs" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_restore
)
if exist "%USER_DATA%\cali_data_backup" (
    if not exist "dist\%APP_NAME%\cali_data_backup" mkdir "dist\%APP_NAME%\cali_data_backup"
    robocopy "%USER_DATA%\cali_data_backup" "dist\%APP_NAME%\cali_data_backup" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_restore
)
if exist "%USER_DATA%\thermal_state_capture" (
    if not exist "dist\%APP_NAME%\thermal_state_capture" mkdir "dist\%APP_NAME%\thermal_state_capture"
    robocopy "%USER_DATA%\thermal_state_capture" "dist\%APP_NAME%\thermal_state_capture" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_restore
)
if exist "%USER_DATA%\ffc_runtime_diagnostic" (
    if not exist "dist\%APP_NAME%\ffc_runtime_diagnostic" mkdir "dist\%APP_NAME%\ffc_runtime_diagnostic"
    robocopy "%USER_DATA%\ffc_runtime_diagnostic" "dist\%APP_NAME%\ffc_runtime_diagnostic" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_restore
)
if exist "%USER_DATA%\thermal_v2_calibration" (
    if not exist "dist\%APP_NAME%\thermal_v2_calibration" mkdir "dist\%APP_NAME%\thermal_v2_calibration"
    robocopy "%USER_DATA%\thermal_v2_calibration" "dist\%APP_NAME%\thermal_v2_calibration" /E /COPY:D /DCOPY:D /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail_restore
)

echo.
echo [9/9] Removing temporary PyInstaller cache ...
if exist build rmdir /s /q build

echo.
echo ============================================================
echo   DONE
echo   Local run : %cd%\dist\%APP_NAME%\%APP_NAME%.exe
echo   Send      : %cd%\dist\%APP_NAME%.zip
echo ============================================================
echo.
if not defined TN160_NONINTERACTIVE pause
popd
exit /b 0

:fail_venv
echo   ERROR: local Python environment creation failed.
goto :fail

:fail_deps
echo   ERROR: dependency installation failed.
goto :fail

:fail_build
echo   ERROR: PyInstaller build failed. See build\ for diagnostics.
goto :fail

:fail_userdata
echo   ERROR: local runtime data could not be preserved.
echo   Existing dist was not intentionally cleaned after this error.
goto :fail

:fail_running
echo   ERROR: %APP_NAME%.exe is still running.
echo   Close the application normally, then run this builder again.
goto :fail

:fail_clean
echo   ERROR: build\ or dist\ could not be removed.
echo   Close programs that use files in those directories, then retry.
goto :fail

:fail_package
echo   ERROR: distribution package creation failed.
goto :fail

:fail_restore
echo   ERROR: package built, but local runtime data restore failed.
echo   The protected copy remains in %cd%\%USER_DATA%.

:fail
echo   Build did not complete; dist\ is not a valid delivery.
echo   Protected runtime data, if any, remains in %cd%\%USER_DATA%.
if not defined TN160_NONINTERACTIVE pause
popd
exit /b 1
