@echo off
title Remover SysFix da Inicializacao do Windows
chcp 65001 >nul

set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT=%STARTUP_DIR%\SysFix Servidor.lnk

if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    echo [SUCESSO] SysFix removido da inicializacao do Windows.
) else (
    echo O SysFix nao estava configurado na inicializacao.
)

pause
