@echo off
REM ============================================================
REM  DEMEX desktop app builder
REM  Double-click this once. It produces:
REM     dist\DEMEX\DEMEX.exe        (the app -- double-click to run)
REM     dist\DEMEX-Setup-x.y.z.exe  (installer, if Inno Setup is installed)
REM  and puts a DEMEX shortcut on your Desktop.
REM ============================================================
setlocal
cd /d "%~dp0"
set "DEMEX_VERSION=1.0.0"

set "DEMEX_PY=C:\Users\Conference Room\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%DEMEX_PY%" (
    echo Could not find Python at: %DEMEX_PY%
    echo Edit DEMEX_PY at the top of build_desktop.bat to point at your python.exe.
    pause & exit /b 1
)

echo [1/5] Installing build tools and app libraries...
"%DEMEX_PY%" -m pip install --quiet --upgrade pip
"%DEMEX_PY%" -m pip install --quiet -r requirements.txt pywebview pyinstaller pillow
if errorlevel 1 ( echo pip install failed. & pause & exit /b 1 )

echo [2/5] Building DEMEX.exe (this takes a few minutes the first time)...
if exist dist\DEMEX rmdir /s /q dist\DEMEX
"%DEMEX_PY%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build desktop\DEMEX.spec
if errorlevel 1 ( echo PyInstaller failed -- see messages above. & pause & exit /b 1 )

echo [3/5] Copying app files next to the exe...
set "D=dist\DEMEX"
copy /y DEMEX.html "%D%\" >nul
copy /y demex_bridge.py "%D%\" >nul
copy /y demex_desktop.py "%D%\" >nul
copy /y email_ingestor.py "%D%\" >nul
copy /y takeoff_scalar.py "%D%\" >nul
copy /y pricing_engine.py "%D%\" >nul
copy /y learning_loop.py "%D%\" >nul
copy /y proposal_pdf.py "%D%\" >nul
copy /y _migrate_schema_v5_standards.py "%D%\" >nul
copy /y .env.example "%D%\" >nul
if exist demex_seed.json copy /y demex_seed.json "%D%\" >nul
if exist demex_rate_reference.json copy /y demex_rate_reference.json "%D%\" >nul
if exist demex_full_history_import.json copy /y demex_full_history_import.json "%D%\" >nul
if exist learning_state.json copy /y learning_state.json "%D%\" >nul
robocopy tools "%D%\tools" /e /xd __pycache__ >nul
robocopy integrations "%D%\integrations" /e /xd __pycache__ >nul
robocopy standards "%D%\standards" /e /xd __pycache__ /xf *.pdf >nul
REM Your live data travels with the first build so the app opens with everything in it.
if exist demex_records.db copy /y demex_records.db "%D%\" >nul
if exist demex_records.db-wal copy /y demex_records.db-wal "%D%\" >nul
if exist .env copy /y .env "%D%\" >nul
REM Tell the packaged app where the morning inbox sweep drops its files.
findstr /b /c:"DEMEX_IMPORTS_DIR=" "%D%\.env" >nul 2>&1 || (
    echo DEMEX_IMPORTS_DIR=%CD%>>"%D%\.env"
)
if exist projects robocopy projects "%D%\projects" /e >nul

echo [4/5] Creating Desktop shortcut...
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\DEMEX.lnk');" ^
  "$s.TargetPath=(Resolve-Path 'dist\DEMEX\DEMEX.exe').Path;" ^
  "$s.WorkingDirectory=(Resolve-Path 'dist\DEMEX').Path;" ^
  "$s.Description='DEMEX estimating app';$s.Save()"

echo [5/5] Building installer (optional)...
set "ISCC="
for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
    if exist %%p set "ISCC=%%~p"
)
if defined ISCC (
    "%ISCC%" /Q desktop\installer.iss
    if errorlevel 1 ( echo Installer build failed -- DEMEX.exe still works from dist\DEMEX. ) else (
        echo Installer ready: dist\DEMEX-Setup-%DEMEX_VERSION%.exe
    )
) else (
    echo Inno Setup not found -- skipping the installer. DEMEX.exe in dist\DEMEX works on its own.
    echo To get a Setup.exe, install Inno Setup 6 from https://jrsoftware.org/isinfo.php and run this again.
)

echo.
echo ================================================================
echo  Done. Double-click the DEMEX shortcut on your Desktop to run it.
echo  App folder: %CD%\dist\DEMEX
echo ================================================================
pause
