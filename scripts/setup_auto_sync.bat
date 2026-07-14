@echo off
REM ============================================================
REM  Auto-sync Notion → Obsidian every 30 minutes via Task Scheduler
REM  Run this script ONCE as Administrator to set up the scheduled task
REM ============================================================

SET PYTHON_PATH=python
SET SCRIPT_DIR=%~dp0..
SET SYNC_CMD=%PYTHON_PATH% -m src.sync.notion_to_obsidian --since 1h

echo Creating scheduled task: SecondBrain-NotionSync
echo Runs every 30 minutes, syncs Notion → Obsidian vault
echo.

schtasks /create ^
  /tn "SecondBrain-NotionSync" ^
  /tr "cmd /c cd /d \"%SCRIPT_DIR%\" && %SYNC_CMD%"  ^
  /sc minute ^
  /mo 30 ^
  /f

IF %ERRORLEVEL% EQU 0 (
    echo.
    echo [SUCCESS] Scheduled task created!
    echo - Task name: SecondBrain-NotionSync
    echo - Frequency: Every 30 minutes
    echo - To modify: Open Task Scheduler, find SecondBrain-NotionSync
    echo - To remove: schtasks /delete /tn "SecondBrain-NotionSync" /f
    echo - To run now: schtasks /run /tn "SecondBrain-NotionSync"
) ELSE (
    echo.
    echo [ERROR] Failed to create task. Try running as Administrator.
)

pause
