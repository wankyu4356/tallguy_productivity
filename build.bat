@echo off
REM Build the standalone executable on Windows.
setlocal
cd /d "%~dp0"

echo ==========================================
echo   TheBell News Clipper - Build
echo ==========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    pause
    exit /b 1
)

echo [1/2] Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed.
    pause
    exit /b 1
)

echo.
echo [2/2] Building executable...
python build_exe.py --clean
if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo Done. See the dist folder.
pause
