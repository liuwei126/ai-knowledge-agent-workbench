@echo off
setlocal
cd /d "%~dp0backend"

rem 优先使用项目虚拟环境，避免依赖 Windows 的 python 命令别名。
if not exist ".venv\Scripts\python.exe" (
    echo Creating backend virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo Failed to create .venv. Install Python 3 and ensure the py launcher is available.
        pause
        exit /b 1
    )
)

rem 使用虚拟环境的解释器安装依赖并启动 API。
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install backend dependencies.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
