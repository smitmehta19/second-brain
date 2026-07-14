@echo off
REM setup_git.cmd -- cmd-native launcher for setup_git.ps1
REM Run by double-clicking this file or typing: setup_git.cmd

setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_git.ps1"
set RC=%ERRORLEVEL%

echo.
if %RC% NEQ 0 (
    echo Script exited with code %RC%.
) else (
    echo Done.
)
echo.
pause
endlocal
exit /b %RC%
