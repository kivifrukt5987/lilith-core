@echo off
REM ===========================================================================
REM  LILITH-CORE - run the test suite on Windows
REM  MAINTAINERS: keep CRLF line endings and ASCII-only text (see start.bat).
REM ===========================================================================
REM --- the window must NEVER self-close: relaunch ourselves under cmd /k ---
if not defined LILITH_KEEP (
    set LILITH_KEEP=1
    title LILITH-CORE tests
    cmd /k "%~f0" %*
    exit /b
)

setlocal
cd /d "%~dp0"

if not exist "pyproject.toml" (
    echo  [ERROR] run_tests.bat must run from INSIDE the project folder.
    pause
    exit /b 1
)

set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

"%PYTHON%" -c "import pytest" 2>nul
if errorlevel 1 (
    echo  [LILITH] installing dev dependencies...
    "%PYTHON%" -m pip install -e ".[dev,memory]"
)

echo.
echo  [LILITH] running tests...
echo.
"%PYTHON%" -m pytest %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo  [OK] all tests green.
) else (
    echo  [FAIL] some tests failed, exit code %RC%.
)
endlocal & exit /b %RC%
