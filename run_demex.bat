@echo off
REM ============================================================
REM  DEMEX daily launcher
REM  Starts the local bridge server and opens the app.
REM  Double-click this each morning. Close the window to stop.
REM ============================================================
setlocal
cd /d "%~dp0"

REM Full path to the office Python 3.12 (the bare 'python' on PATH is the
REM Microsoft Store stub and will NOT work -- see demex-python-env memory).
set "DEMEX_PY=C:\Users\Conference Room\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%DEMEX_PY%" (
    echo Could not find Python at:
    echo   %DEMEX_PY%
    echo Edit DEMEX_PY at the top of run_demex.bat to point at your python.exe.
    pause
    exit /b 1
)

REM The bridge opens the browser itself once the port is bound (more reliable
REM than launching it from here -- avoids nested-quote issues). See
REM demex_bridge.py main().
set "DEMEX_OPEN_BROWSER=1"

echo Starting DEMEX bridge on http://localhost:8765 ...
echo If your browser does not open automatically, open this address manually:
echo     http://localhost:8765/DEMEX.html
echo.
"%DEMEX_PY%" demex_bridge.py

echo.
echo DEMEX bridge stopped.
pause
