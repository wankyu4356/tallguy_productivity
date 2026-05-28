# DealSitePlus News Clipper - Setup & Run
# UTF-8 output
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  DealSitePlus News Clipper Setup & Run" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Python ──
Write-Host "[1/6] Python..." -ForegroundColor Yellow
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python\s+(\d+\.\d+)") {
            $pythonCmd = $cmd
            Write-Host "       $ver" -ForegroundColor Green
            break
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host "       Python not found. Installing..." -ForegroundColor Red
    Write-Host ""

    # Try winget
    $wingetOk = $false
    try {
        $null = winget --version 2>&1
        $wingetOk = $true
    } catch {}

    if ($wingetOk) {
        Write-Host "       Installing Python via winget..." -ForegroundColor Yellow
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "[!] Python installed. Please CLOSE this window and" -ForegroundColor Magenta
            Write-Host "    double-click setup_and_run.bat again." -ForegroundColor Magenta
            Write-Host ""
            Read-Host "Press Enter to exit"
            exit 0
        }
    }

    # Try direct download
    Write-Host "       Downloading Python installer..." -ForegroundColor Yellow
    $installerUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
    $installerPath = "$env:TEMP\python_installer.exe"
    try {
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        Write-Host "       Running installer (silent)..." -ForegroundColor Yellow
        Start-Process -FilePath $installerPath -ArgumentList "/passive", "InstallAllUsers=0", "PrependPath=1", "Include_test=0" -Wait
        Remove-Item $installerPath -ErrorAction SilentlyContinue
        Write-Host ""
        Write-Host "[!] Python installed. Please CLOSE this window and" -ForegroundColor Magenta
        Write-Host "    double-click setup_and_run.bat again." -ForegroundColor Magenta
        Write-Host ""
        Read-Host "Press Enter to exit"
        exit 0
    } catch {
        Write-Host ""
        Write-Host "[ERROR] Auto-install failed." -ForegroundColor Red
        Write-Host ""
        Write-Host "  Manual install:" -ForegroundColor White
        Write-Host "  1. Go to https://www.python.org/downloads/" -ForegroundColor White
        Write-Host "  2. Download Python 3.12" -ForegroundColor White
        Write-Host "  3. CHECK 'Add Python to PATH' during install" -ForegroundColor White
        Write-Host "  4. Run this file again" -ForegroundColor White
        Write-Host ""
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ── 2. pip ──
Write-Host "[2/6] pip..." -ForegroundColor Yellow
& $pythonCmd -m pip --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $pythonCmd -m ensurepip --upgrade 2>&1 | Out-Null
}
& $pythonCmd -m pip install --upgrade pip 2>&1 | Out-Null
Write-Host "       pip OK" -ForegroundColor Green

# ── 3. Edge Browser ──
Write-Host "[3/6] Edge Browser..." -ForegroundColor Yellow
$edgePaths = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)
$edgeFound = $false
foreach ($p in $edgePaths) {
    if (Test-Path $p) { $edgeFound = $true; break }
}
if ($edgeFound) {
    Write-Host "       Edge found (EdgeDriver auto-downloaded by Selenium)" -ForegroundColor Green
} else {
    Write-Host "       [WARNING] Edge not found." -ForegroundColor Red
    Write-Host "       Install from: https://www.microsoft.com/edge" -ForegroundColor Red
}

# ── 4. pip packages ──
Write-Host "[4/6] Installing packages..." -ForegroundColor Yellow
& $pythonCmd -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Package install failed." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "       Packages installed" -ForegroundColor Green

# ── 5. .env ──
Write-Host "[5/6] Environment config..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "  .env file not found. Please enter your credentials:" -ForegroundColor White
    Write-Host ""
    $dsId = Read-Host "  DealSitePlus ID"
    $dsPw = Read-Host "  DealSitePlus Password"
    $apiKey = Read-Host "  Anthropic API Key (sk-ant-...)"

    @"
# DealSitePlus credentials
DEALSITEPLUS_ID=$dsId
DEALSITEPLUS_PW=$dsPw

# Claude API
ANTHROPIC_API_KEY=$apiKey
CLAUDE_MODEL=claude-sonnet-4-20250514

# App settings
OUTPUT_DIR=./output
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8000

# Browser settings
BROWSER_HEADLESS=true
CRAWL_TIMEOUT_MS=30000
NAVIGATION_TIMEOUT_MS=15000
MAX_CONCURRENT_PAGES=3

# Cleanup
CLEANUP_HOURS=24
"@ | Out-File -FilePath ".env" -Encoding utf8

    Write-Host ""
    Write-Host "       .env created" -ForegroundColor Green
} else {
    Write-Host "       .env exists (keeping current settings)" -ForegroundColor Green
}

# ── 6. Run server ──
Write-Host ""
Write-Host "[6/6] Starting server..." -ForegroundColor Yellow

# Find a free port starting from 8000
$serverPort = 8000
while ($serverPort -lt 8100) {
    $conn = Get-NetTCPConnection -LocalPort $serverPort -ErrorAction SilentlyContinue
    if (-not $conn) { break }
    Write-Host "       Port $serverPort in use, trying next..." -ForegroundColor Yellow
    $serverPort++
}
if ($serverPort -ge 8100) {
    Write-Host "[ERROR] No free port found (8000-8099)" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Server: http://localhost:$serverPort" -ForegroundColor Cyan
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

Start-Process "http://localhost:$serverPort"

& $pythonCmd -m uvicorn app.main:app --host 0.0.0.0 --port $serverPort

Write-Host ""
Write-Host "Server stopped." -ForegroundColor Yellow
Read-Host "Press Enter to exit"
