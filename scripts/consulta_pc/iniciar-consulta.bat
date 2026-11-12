@echo off
chcp 65001 >nul
title Consulta MZ
REM Abre o Claude Code na pasta de consulta (somente leitura). Nao ha senha aqui: a rede interna entra direto.
REM Servidor: edite a linha abaixo se o IP do PC servidor mudar.
set MZ_SERVIDOR=http://192.168.15.200:8001
cd /d "%~dp0"

where claude >nul 2>nul
if errorlevel 1 (
  echo O Claude Code ainda nao esta instalado neste PC.
  echo Instale uma vez com:  npm install -g @anthropic-ai/claude-code
  echo Depois rode este arquivo de novo. No primeiro uso o Claude pede o login da conta da empresa, uma unica vez.
  pause
  exit /b 1
)

powershell -NoProfile -Command "try { Invoke-RestMethod ($env:MZ_SERVIDOR + '/sistema/versao') -TimeoutSec 5 | Out-Null } catch { exit 1 }"
if errorlevel 1 (
  echo Nao consegui falar com o servidor %MZ_SERVIDOR%. Confira se o PC servidor esta ligado e se este PC esta na rede da empresa.
  pause
  exit /b 1
)

echo Consulta MZ pronta. Pergunte em portugues, por exemplo: quantos checkouts tenho em aberto?
claude
