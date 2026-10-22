@echo off
setlocal
cd /d "%~dp0"

rem 如果后端未监听 8000，自动打开后端窗口，避免页面出现 Failed to fetch。
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo Backend is not running. Starting it now...
    start "AI Workbench Backend" cmd /k call "%~dp0start-backend.bat"
    timeout /t 3 /nobreak >nul
)

cd /d "%~dp0frontend"
call npm install
if errorlevel 1 (
    echo Failed to install frontend dependencies.
    pause
    exit /b 1
)
npm run dev
