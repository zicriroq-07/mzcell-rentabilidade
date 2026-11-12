@echo off
chcp 65001 >nul
title Terminal MZ
cd /d "%~dp0"
if not exist "%~dp0mz-consulta.ps1" (
  echo Nao encontrei o arquivo mz-consulta.ps1 ao lado deste .bat.
  echo Extraia o zip inteiro para uma pasta ^(ex.: C:\TerminalMZ^) e abra o Terminal-MZ.bat de la.
  echo Pasta atual: %~dp0
  pause
  exit /b 1
)
rem arquivos baixados pelo Slack/navegador vem "bloqueados" pelo Windows: libera
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' | Unblock-File" >nul 2>&1
mode con: cols=80 lines=40
REM Servidor: edite a linha abaixo se o IP do PC servidor mudar.
set MZ_SERVIDOR=http://192.168.15.200:8001
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mz-consulta.ps1" tela
if errorlevel 1 (
  echo.
  echo A Terminal MZ fechou com erro ^(codigo %errorlevel%^).
  echo Tire um print desta tela e envie ao suporte.
  pause
)
