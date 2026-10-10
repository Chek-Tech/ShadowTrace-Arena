@echo off
setlocal EnableExtensions
for %%I in ("%~dp0\..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python.exe"
cd /d "%PROJECT_ROOT%"
"%PYTHON_EXE%" -m shadowtrace_arena %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
