@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 더벨 News Clipper
cd /d "%~dp0"

echo ============================================================
echo    더벨 News Clipper
echo    Gateway to Capital Markets
echo ============================================================
echo.

REM ---- 0. 프로젝트 폴더 찾기 (배치를 어디에 둬도 동작) -------------------
REM requirements.txt 가 있는 곳이 진짜 프로젝트 폴더입니다.
set "REPO="

REM (a) 배치 파일과 같은 폴더
if exist "%~dp0requirements.txt" set "REPO=%~dp0"

REM (b) 알려진 설치 경로
if not defined REPO if exist "C:\Users\WD\tallguy_productivity\requirements.txt" set "REPO=C:\Users\WD\tallguy_productivity\"

REM (c) 배치 폴더 바로 아래 한 단계 하위 폴더들
if not defined REPO (
    for /d %%D in ("%~dp0*") do (
        if exist "%%~fD\requirements.txt" set "REPO=%%~fD\"
    )
)

REM (d) 사용자 폴더에서 tallguy_productivity 이름으로 탐색
if not defined REPO (
    for /d %%D in ("%USERPROFILE%\*tallguy*") do (
        if exist "%%~fD\requirements.txt" set "REPO=%%~fD\"
    )
)

if not defined REPO (
    echo [문제] 프로젝트 폴더를 찾을 수 없습니다.
    echo.
    echo   requirements.txt 가 들어있는 폴더 안에 이 배치 파일을 두고
    echo   다시 실행하거나, 프로젝트를 아래 경로에 두세요:
    echo   C:\Users\WD\tallguy_productivity
    echo.
    pause
    exit /b 1
)
cd /d "%REPO%"
echo [0/4] 프로젝트 폴더: %REPO%

REM ---- 최신 코드 받기 (git 이 있고 인터넷이 되면) --------------------------
where git >nul 2>&1
if not errorlevel 1 (
    if exist "%REPO%.git" (
        echo       최신 버전 확인 중...
        git -C "%REPO%." pull --ff-only >nul 2>&1
    )
)
echo.

REM ---- 1. Python 찾기 -----------------------------------------------------
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [문제] Python이 설치되어 있지 않습니다.
    echo.
    echo   https://www.python.org/downloads/ 에서 Python 3.11 이상을 설치하세요.
    echo   설치 화면에서 "Add Python to PATH" 를 꼭 체크하세요.
    echo.
    pause
    exit /b 1
)
echo [1/4] Python 확인 완료
echo.

REM ---- 2. 가상환경 준비 (프로젝트 폴더 안에) -----------------------------
if not exist "%REPO%.venv\Scripts\python.exe" (
    echo [2/4] 가상환경 만드는 중... ^(처음 한 번만^)
    %PY% -m venv "%REPO%.venv"
    if errorlevel 1 (
        echo [문제] 가상환경 생성에 실패했습니다.
        pause
        exit /b 1
    )
) else (
    echo [2/4] 가상환경 확인 완료
)
set "VENV_PY=%REPO%.venv\Scripts\python.exe"
echo.

REM ---- 3. 의존성 설치 (requirements.txt 가 바뀌었을 때만) -----------------
set "NEED_INSTALL=1"
if exist "%REPO%.venv\.installed" (
    for /f "delims=" %%A in ('certutil -hashfile "%REPO%requirements.txt" MD5 ^| find /v ":"') do set "REQ_HASH=%%A"
    set /p SAVED_HASH=<"%REPO%.venv\.installed"
    if "!REQ_HASH!"=="!SAVED_HASH!" set "NEED_INSTALL=0"
)
if "!NEED_INSTALL!"=="1" (
    echo [3/4] 필요한 프로그램 설치 중... ^(잠시 걸립니다^)
    "%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
    "%VENV_PY%" -m pip install -r "%REPO%requirements.txt"
    if errorlevel 1 (
        echo [문제] 설치에 실패했습니다. 인터넷 연결을 확인하세요.
        pause
        exit /b 1
    )
    for /f "delims=" %%A in ('certutil -hashfile "%REPO%requirements.txt" MD5 ^| find /v ":"') do set "REQ_HASH=%%A"
    > "%REPO%.venv\.installed" echo !REQ_HASH!
) else (
    echo [3/4] 필요한 프로그램 확인 완료
)
echo.

REM ---- 4. 실행 -----------------------------------------------------------
echo [4/4] 시작합니다. 브라우저가 자동으로 열립니다.
echo       종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.
echo.
"%VENV_PY%" "%REPO%launcher.py"

echo.
echo 프로그램이 종료되었습니다.
pause
