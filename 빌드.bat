@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 더벨 News Clipper - EXE 빌드
cd /d "%~dp0"

echo ============================================================
echo    더벨 News Clipper - 실행 파일(EXE) 만들기
echo ============================================================
echo.

REM ---- 0. 프로젝트 폴더 찾기 ---------------------------------------------
set "REPO="
if exist "%~dp0build_exe.py" set "REPO=%~dp0"
if not defined REPO if exist "C:\Users\WD\tallguy_productivity\build_exe.py" set "REPO=C:\Users\WD\tallguy_productivity\"
if not defined REPO (
    for /d %%D in ("%~dp0*") do if exist "%%~fD\build_exe.py" set "REPO=%%~fD\"
)
if not defined REPO (
    for /d %%D in ("%USERPROFILE%\*tallguy*") do if exist "%%~fD\build_exe.py" set "REPO=%%~fD\"
)
if not defined REPO (
    echo [문제] 프로젝트 폴더를 찾을 수 없습니다.
    echo   build_exe.py 가 들어있는 폴더 안에 이 배치를 두고 다시 실행하세요.
    pause
    exit /b 1
)
cd /d "%REPO%"
echo [0/4] 프로젝트 폴더: %REPO%

REM 최신 코드 받기
where git >nul 2>&1
if not errorlevel 1 if exist "%REPO%.git" (
    echo       최신 버전 확인 중...
    git -C "%REPO%." pull --ff-only >nul 2>&1
)
echo.

REM ---- 1. Python -------------------------------------------------------
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY ( python --version >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [문제] Python이 없습니다. https://www.python.org/downloads/ 에서 3.11+ 설치.
    pause
    exit /b 1
)
echo [1/4] Python 확인 완료
echo.

REM ---- 2. 가상환경 -----------------------------------------------------
if not exist "%REPO%.venv\Scripts\python.exe" (
    echo [2/4] 가상환경 만드는 중...
    %PY% -m venv "%REPO%.venv"
    if errorlevel 1 ( echo [문제] 가상환경 생성 실패 & pause & exit /b 1 )
) else (
    echo [2/4] 가상환경 확인 완료
)
set "VENV_PY=%REPO%.venv\Scripts\python.exe"
echo.

REM ---- 3. 빌드 도구 설치 (앱 의존성 + PyInstaller + PyArmor) ------------
echo [3/4] 빌드 도구 설치 중... ^(처음엔 몇 분 걸립니다^)
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r "%REPO%requirements.txt"
if errorlevel 1 ( echo [문제] 의존성 설치 실패. 인터넷 확인. & pause & exit /b 1 )
"%VENV_PY%" -m pip install "pyinstaller>=6.0" pyarmor pillow
if errorlevel 1 ( echo [문제] 빌드 도구 설치 실패. & pause & exit /b 1 )
echo.

REM ---- 4. 빌드 (난독화 + 하드닝) ---------------------------------------
echo [4/4] 실행 파일 빌드 중... ^(수 분 소요, 창을 닫지 마세요^)
echo.
"%VENV_PY%" "%REPO%build_exe.py" --clean --obfuscate --allow-trial
if errorlevel 1 (
    echo.
    echo [문제] 빌드에 실패했습니다. 위 메시지를 확인하세요.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   완료!  dist 폴더에 TheBellNewsClipper.exe 가 생겼습니다.
echo ============================================================
if exist "%REPO%dist" start "" "%REPO%dist"
pause
