@echo off
title Instalar SysFix na Inicializacao do Windows
chcp 65001 >nul
cd /d "%~dp0"

echo ======================================================
echo    CONFIGURAR INICIALIZACAO AUTOMATICA COM O WINDOWS
echo ======================================================
echo.

set TARGET_BAT=%~dp0iniciar_servidor_local.bat
if not exist "%TARGET_BAT%" (
    echo [ERRO] Arquivo iniciar_servidor_local.bat nao encontrado em %~dp0.
    pause
    exit /b 1
)

set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT=%STARTUP_DIR%\SysFix Servidor.lnk

powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$s = $ws.CreateShortcut('%SHORTCUT%'); " ^
    "$s.TargetPath = '%TARGET_BAT%'; " ^
    "$s.WorkingDirectory = '%~dp0'; " ^
    "$s.WindowStyle = 7; " ^
    "$s.Description = 'Iniciar SysFix Automaticamente'; " ^
    "$s.Save()"

if exist "%SHORTCUT%" (
    echo [SUCESSO] Atalho criado na pasta de inicializacao do Windows!
    echo.
    echo O SysFix agora vai iniciar automaticamente toda vez que o Windows ligar
    echo ^(ele abrira minimizado na barra de tarefas para nao atrapalhar^).
    echo.
    echo [AVISO] Ao iniciar sem a variavel de ambiente ADMIN_PASSWORD configurada,
    echo a janela solicitara a definicao da senha administrativa no terminal.
) else (
    echo [ERRO] Nao foi possivel criar o atalho automaticamente.
)

echo.
echo ======================================================
pause
