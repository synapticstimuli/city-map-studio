@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3.12 "%~dp0launch_local.py" %*
    if %ERRORLEVEL% EQU 0 exit /b 0
    echo Python 3.12 launcher did not complete. Check the error above.
    pause
    exit /b 1
)
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python "%~dp0launch_local.py" %*
    if %ERRORLEVEL% EQU 0 exit /b 0
    pause
    exit /b 1
)
echo City Map Studio needs Python 3.12 installed and available on PATH.
echo Visit https://www.python.org/downloads/
pause
exit /b 1
