@echo off
setlocal EnableExtensions

cd /d "%~dp0"
title Hello Streamer - source runner
set "PYTHONUTF8=1"
set "PYTHONFAULTHANDLER=1"
set "EXIT_CODE=0"

echo.
echo ============================================================
echo  Hello Streamer - source runner
echo  Folder: %CD%
echo ============================================================
echo.

rem This broad process check prevents starting the source runner beside the packaged app.
tasklist /FI "IMAGENAME eq HelloStreamer.exe" 2>nul | findstr /I /C:"HelloStreamer.exe" >nul
if not errorlevel 1 (
    echo [ERROR] HelloStreamer.exe is already running.
    echo Close the packaged app, then run this BAT again.
    set "EXIT_CODE=2"
    goto :finish
)

where uv >nul 2>&1
if not errorlevel 1 goto :run_with_uv
goto :run_with_python

:run_with_uv
echo [1/2] Checking and creating the uv environment...
uv sync --extra dev
if errorlevel 1 (
    echo.
    echo [ERROR] uv sync failed. The traceback above is intentionally kept visible.
    set "EXIT_CODE=1"
    goto :finish
)
echo [2/2] Starting the source application in the foreground...
uv run --no-sync python -m stream_monitor %*
set "EXIT_CODE=%ERRORLEVEL%"
goto :finish

:run_with_python
echo [INFO] uv was not found; using the Python venv fallback.
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv ".venv"
    ) else (
        where python >nul 2>&1
        if errorlevel 1 (
            echo [ERROR] Neither uv nor Python was found on PATH.
            set "EXIT_CODE=1"
            goto :finish
        )
        python -m venv ".venv"
    )
    if errorlevel 1 (
        echo [ERROR] Could not create .venv.
        set "EXIT_CODE=1"
        goto :finish
    )
)

echo [1/2] Installing/updating the Python environment...
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 (
    echo.
    echo [ERROR] Python dependency installation failed.
    set "EXIT_CODE=1"
    goto :finish
)
echo [2/2] Starting the source application in the foreground...
".venv\Scripts\python.exe" -m stream_monitor %*
set "EXIT_CODE=%ERRORLEVEL%"

:finish
echo.
if "%EXIT_CODE%"=="0" (
    echo Hello Streamer exited normally.
) else (
    echo Hello Streamer exited with code %EXIT_CODE%.
    echo Keep the console output above when reporting the problem.
)
echo.
pause
exit /b %EXIT_CODE%
