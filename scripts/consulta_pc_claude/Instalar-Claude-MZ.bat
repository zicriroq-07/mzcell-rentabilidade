@echo off
chcp 65001 >nul
title Instalar Terminal MZ com Claude
cd /d "%~dp0"
if not exist "%~dp0instalar.ps1" (
  echo Nao encontrei o instalar.ps1 ao lado deste .bat.
  echo Extraia o zip inteiro para uma pasta ^(ex.: C:\TerminalMZ-Claude^) e abra de la.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
if errorlevel 1 (
  echo.
  echo A instalacao nao terminou. Tire um print desta tela e envie ao suporte.
  pause
)
