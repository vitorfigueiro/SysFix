@echo off
title SysFix - Servidor Local
chcp 65001 >nul
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar_sysfix.ps1"
if errorlevel 1 (
    echo.
    echo [ERRO] O servidor SysFix foi encerrado com falha.
    pause
)
