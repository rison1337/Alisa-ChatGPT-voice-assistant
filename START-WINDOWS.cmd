@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
"%~dp0windows\runtime\python.exe" -X utf8 "%~dp0windows\install.py" %*
if errorlevel 1 (
    echo.
    echo Installation stopped. Check the error message above.
    pause
)
