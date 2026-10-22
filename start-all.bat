@echo off
setlocal
cd /d "%~dp0"

echo Starting backend on http://127.0.0.1:8000 ...
start "AI Workbench Backend" cmd /k call "%~dp0start-backend.bat"

rem 给 FastAPI 留出初始化时间；前端即使先打开，也会显示明确的后端连接提示。
timeout /t 3 /nobreak >nul

echo Starting frontend on http://127.0.0.1:5173 ...
start "AI Workbench Frontend" cmd /k call "%~dp0start-frontend.bat"

echo.
echo Services are starting. Open http://127.0.0.1:5173 in your browser.
endlocal
