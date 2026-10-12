@echo off
setlocal

echo =======================================================
echo   Focus Engine - Install Windows Autostart Daemon
echo =======================================================

set "SCRIPT_DIR=%~dp0"
set "TARGET_VBS=%SCRIPT_DIR%launch_silent.vbs"
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_PATH=%STARTUP_FOLDER%\FocusEngine.lnk"
set "WORKING_DIR=D:\new_p\focus_engine"

if not exist "%TARGET_VBS%" (
    set "TARGET_VBS=D:\new_p\focus_engine\scripts\launch_silent.vbs"
)

if not exist "%TARGET_VBS%" (
    echo [ERROR] Target VBScript not found: %TARGET_VBS%
    goto :end
)

echo [*] Target VBScript:   %TARGET_VBS%
echo [*] Startup Directory: %STARTUP_FOLDER%
echo [*] Shortcut Output:   %SHORTCUT_PATH%

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$s = $ws.CreateShortcut('%SHORTCUT_PATH%'); " ^
    "$s.TargetPath = '%TARGET_VBS%'; " ^
    "$s.WorkingDirectory = '%WORKING_DIR%'; " ^
    "$s.Description = 'Focus Engine Headless Background Daemon'; " ^
    "$s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo.
    echo [OK] Autostart shortcut successfully installed in Windows Startup folder!
    echo      Target: %SHORTCUT_PATH%
    echo      The Focus Engine will now automatically launch silently on system boot.
) else (
    echo.
    echo [ERROR] Failed to create shortcut at %SHORTCUT_PATH%
)

:end
echo =======================================================
endlocal
