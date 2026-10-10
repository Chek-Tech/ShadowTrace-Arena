@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion

set "DEPLOY_SCRIPT=%~dp0scripts\deploy-windows.ps1"
if not exist "%DEPLOY_SCRIPT%" (
    echo [ERROR] Deployment script not found: %DEPLOY_SCRIPT%
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%DEPLOY_SCRIPT%" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%SHADOWTRACE_NO_PAUSE%"=="1" if "%~1"=="" pause
exit /b %EXIT_CODE%
