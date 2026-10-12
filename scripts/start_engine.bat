@echo off
setlocal enabledelayedexpansion

:: 1. Change directory to the workspace root D:\new_p\focus_engine
cd /d "D:\new_p\focus_engine"

:: 2. Check if .venv\Scripts\activate.bat exists; if so, activate virtual environment
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

:: 3. Ensure the logs directory exists
if not exist "logs" (
    mkdir "logs"
)

:: Ensure user Python scripts directory is available in PATH
if exist "%APPDATA%\Python\Python314\Scripts" (
    set "PATH=%APPDATA%\Python\Python314\Scripts;%PATH%"
)

:: 4. Start cloudflared tunnel if not already running
tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | find /I /N "cloudflared.exe" >nul
if !ERRORLEVEL! NEQ 0 (
    if exist "cloudflared.exe" (
        start "" /B cmd /c "cloudflared.exe tunnel --url http://127.0.0.1:8000 >> logs\tunnel.log 2>&1"
    ) else (
        where cloudflared >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            start "" /B cmd /c "cloudflared tunnel --url http://127.0.0.1:8000 >> logs\tunnel.log 2>&1"
        )
    )
)

:: 5. Run uvicorn server on 0.0.0.0 (LAN access) redirecting stdout & stderr to logs/backend.log
where uvicorn >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    uvicorn app.main:app --host 0.0.0.0 --port 8000 >> logs\backend.log 2>&1
) else (
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> logs\backend.log 2>&1
)

endlocal