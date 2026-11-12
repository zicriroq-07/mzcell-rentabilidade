@echo off
chcp 65001 >nul
title Consulta MZ
REM Abre o Claude Code na pasta de consulta (somente leitura). Nao ha senha aqui: a rede interna entra direto.
REM Servidor: edite a linha abaixo se o IP do PC servidor mudar.
set MZ_SERVIDOR=http://192.168.15.200:8001
cd /d "%~dp0"

REM Acha o Claude mesmo que o Windows ainda nao o tenha no PATH (o instalador grava em .local\bin)
set CLAUDE=claude
where claude >nul 2>nul
if errorlevel 1 (
  if exist "%USERPROFILE%\.local\bin\claude.exe" (
    set CLAUDE="%USERPROFILE%\.local\bin\claude.exe"
  ) else (
    echo O Claude Code nao foi encontrado neste PC.
    echo Instale uma vez no PowerShell com:  irm https://claude.ai/install.ps1 ^| iex
    echo Depois rode este arquivo de novo. Passo a passo: INSTALAR_CLAUDE.md
    pause
    exit /b 1
  )
)

powershell -NoProfile -Command "try { Invoke-RestMethod ($env:MZ_SERVIDOR + '/sistema/versao') -TimeoutSec 5 | Out-Null } catch { exit 1 }"
if errorlevel 1 (
  echo Nao consegui falar com o servidor %MZ_SERVIDOR%. Confira se o PC servidor esta ligado e se este PC esta na rede da empresa.
  pause
  exit /b 1
)

echo Consulta MZ pronta. Pergunte em portugues, por exemplo: quantos checkouts tenho em aberto?
%CLAUDE%
