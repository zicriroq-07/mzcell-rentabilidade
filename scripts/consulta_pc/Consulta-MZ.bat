@echo off
chcp 65001 >nul
title Consulta MZ
REM Servidor: edite a linha abaixo se o IP do PC servidor mudar.
set MZ_SERVIDOR=http://192.168.15.200:8001
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mz-consulta.ps1" interativo
