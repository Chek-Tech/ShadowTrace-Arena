@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
for %%I in ("%~dp0\..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_EXE="
set "PYTHON_ARGS="

if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    "%PROJECT_ROOT%\.venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
)
if defined PYTHON_EXE goto python_ready

where.exe python.exe >nul 2>&1
if errorlevel 1 goto check_launcher
python.exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_EXE=python.exe"
if defined PYTHON_EXE goto python_ready

:check_launcher
where.exe py.exe >nul 2>&1
if errorlevel 1 goto python_missing
py.exe -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto python_missing
set "PYTHON_EXE=py.exe"
set "PYTHON_ARGS=-3"

:python_ready
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import tkinter" >nul 2>&1
if errorlevel 1 goto tkinter_missing
pushd "%PROJECT_ROOT%"
"%PYTHON_EXE%" %PYTHON_ARGS% control_center.py
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

:python_missing
echo [ERROR] Python 3.11 or newer is required.
exit /b 1

:tkinter_missing
echo [ERROR] The selected Python installation does not include Tkinter.
exit /b 1
