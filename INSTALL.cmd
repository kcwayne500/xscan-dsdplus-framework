@echo off
setlocal
title XScan DSDPlus Framework Installer

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL.ps1" %*
set "installExit=%ERRORLEVEL%"

if not "%installExit%"=="0" (
    echo.
    echo Installation did not complete successfully.
    pause
)
exit /b %installExit%
