@echo off
echo ============================================================
echo  Alert CSV Sync
echo ============================================================
python "%~dp0sync_alerts.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Sync failed. Check logs\ folder for details.
    pause
) else (
    echo.
    echo [OK] Sync complete.
)
