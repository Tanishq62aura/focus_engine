@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo   Focus Engine - Stopping Background Services
echo =======================================================

set "KILLED_ANY=0"

:: 1. Search for and terminate any process bound to port 8000
for /f "tokens=5" %%P in ('netstat -ano ^| findstr :8000') do (
    set "PID=%%P"
    if defined PID (
        if "!PID!" neq "0" (
            echo [*] Found active listener on port 8000 with PID: !PID!
            taskkill /PID !PID! /F >nul 2>&1
            if !errorlevel! equ 0 (
                echo [OK] Backend PID !PID! terminated successfully.
                set "KILLED_ANY=1"
            ) else (
                set "KILLED_ANY=1"
            )
        )
    )
)

:: 2. Search for and terminate any running cloudflared.exe instances
tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | find /I /N "cloudflared.exe" >nul
if !ERRORLEVEL! EQU 0 (
    echo [*] Found active cloudflared tunnel process. Terminating...
    taskkill /IM cloudflared.exe /F >nul 2>&1
    if !errorlevel! equ 0 (
        echo [OK] cloudflared.exe terminated successfully.
        set "KILLED_ANY=1"
    )
)

if "!KILLED_ANY!"=="1" (
    echo [OK] Focus Engine backend and Cloudflare Tunnel stopped successfully.
) else (
    echo [*] No running Focus Engine or Cloudflare Tunnel processes found.
)

echo =======================================================
endlocal
