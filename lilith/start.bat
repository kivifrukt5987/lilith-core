@echo off
REM ===========================================================================
REM  LILITH-CORE - Windows launcher (v0.1.2, one-click mode)
REM
REM  WHAT IT DOES: installs dependencies into .venv (first run only),
REM  starts the server and OPENS THE PANEL IN YOUR BROWSER automatically.
REM  Double-click this file INSIDE the project folder (next to pyproject.toml).
REM  No cmd / PowerShell needed anymore.
REM
REM  MAINTAINERS: keep CRLF line endings and ASCII-only text.
REM  venv creation and PYTHON switching happen OUTSIDE parenthesized blocks
REM  on purpose: cmd expands %VAR% at block parse time (see CHANGELOG 0.1.1).
REM ===========================================================================
REM --- the window must NEVER self-close: relaunch ourselves under cmd /k ---
if not defined LILITH_KEEP (
    set LILITH_KEEP=1
    title LILITH-CORE
    cmd /k "%~f0" %*
    exit /b
)

setlocal
cd /d "%~dp0"

if not exist "pyproject.toml" (
    echo.
    echo  [ERROR] pyproject.toml not found in: %CD%
    echo  Double-click start.bat INSIDE the project folder -
    echo  the folder that contains pyproject.toml, config\ and src\.
    echo  Extract the zip first if you are running it from an archive.
    echo.
    pause
    exit /b 1
)

REM --- pick a bootstrap python: py launcher first, then plain python ---------
set "BOOT="
where py >nul 2>&1 && py -3.11 --version >nul 2>&1 && set "BOOT=py -3.11"
if not defined BOOT (
    where python >nul 2>&1 && set "BOOT=python"
)
if not defined BOOT (
    echo.
    echo  [ERROR] Python not found.
    echo  Install Python 3.11+ from https://www.python.org/downloads/
    echo  and tick "Add python.exe to PATH" during installation.
    echo.
    pause
    exit /b 1
)

REM --- create .venv if missing (outside any if-block on purpose) -------------
if not exist ".venv\Scripts\python.exe" (
    echo  [LILITH] creating virtual environment .venv ...
    %BOOT% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  [ERROR] could not create .venv in this folder.
    echo  Check that the disk is writable and antivirus is not blocking.
    echo.
    pause
    exit /b 1
)
set "PYTHON=.venv\Scripts\python.exe"

REM --- python version gate ----------------------------------------------------
"%PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>nul
if errorlevel 1 (
    echo.
    echo  [ERROR] .venv was created from a Python older than 3.11.
    echo  Delete the .venv folder and run start.bat again.
    echo.
    pause
    exit /b 1
)

REM --- dependencies (first run only; afterwards this check is instant) --------
"%PYTHON%" -c "import lilith_core" 2>nul
if errorlevel 1 (
    echo  [LILITH] installing dependencies: dev + memory extras ...
    echo  [LILITH] first run takes a few minutes, hang in there, Kotyonok.
    "%PYTHON%" -m pip install --upgrade pip
    "%PYTHON%" -m pip install -e ".[dev,memory]"
    if errorlevel 1 (
        echo.
        echo  [ERROR] pip install failed. Read the message above.
        echo.
        pause
        exit /b 1
    )
)

REM --- final self-check --------------------------------------------------------
"%PYTHON%" -c "import lilith_core" 2>nul
if errorlevel 1 (
    echo.
    echo  [ERROR] lilith_core is still not importable from .venv.
    echo  Screenshot this window and send it to Lilith - she will fix it.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo  [LILITH] creating .env from .env.example
    copy /Y ".env.example" ".env" >nul
)

echo.
echo  [LILITH] starting server...
echo  [LILITH] the panel opens in your browser by itself in a second.
echo  [LILITH] THIS BLACK WINDOW IS MY HEART - keep it open, minimize if ugly.
echo  [LILITH] to stop me: Ctrl+C here, or just close the window.
echo.
start "" "http://127.0.0.1:8765/"
"%PYTHON%" -m lilith_core.run %*

echo.
echo  [LILITH] server stopped.
endlocal
pause
