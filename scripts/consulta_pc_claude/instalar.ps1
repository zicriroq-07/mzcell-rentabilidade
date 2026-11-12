param([switch]$SemAbrir)
# Instala o necessário para a Terminal MZ com o Claude neste PC: Claude Code, PATH, atalho na área de trabalho.
# A única etapa manual é autorizar o acesso no navegador, na primeira abertura. Não precisa de administrador.
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$pasta = Split-Path -Parent $MyInvocation.MyCommand.Path
$bin = Join-Path $env:USERPROFILE ".local\bin"

function Achar-Claude {
    $c = Get-Command claude -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $exe = Join-Path $bin "claude.exe"
    if (Test-Path $exe) { return $exe }
    return $null
}

Write-Host "1/4  Liberando os arquivos baixados..."
Get-ChildItem -LiteralPath $pasta -Recurse -File | Unblock-File

Write-Host "2/4  Conferindo o Claude Code..."
$claude = Achar-Claude
if (-not $claude) {
    Write-Host "     Não encontrado. Instalando (pode levar 1 ou 2 minutos)..."
    try { Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression } catch { Write-Host "     Falha na instalação: $_" -ForegroundColor Red; exit 1 }
    $claude = Achar-Claude
    if (-not $claude) { Write-Host "     A instalação terminou mas o Claude não apareceu. Chame o suporte." -ForegroundColor Red; exit 1 }
}
Write-Host "     OK: $claude"

Write-Host "3/4  Ajustando o PATH..."
$atual = [Environment]::GetEnvironmentVariable("Path", "User")
if (($atual -split ";") -notcontains $bin) {
    [Environment]::SetEnvironmentVariable("Path", ($atual.TrimEnd(";") + ";" + $bin), "User")
    Write-Host "     Adicionado $bin (vale em janelas novas)."
} else { Write-Host "     Já estava certo." }

Write-Host "4/4  Criando o atalho na área de trabalho..."
$desk = [Environment]::GetFolderPath("Desktop")
$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desk "Terminal MZ (Claude).lnk"))
$s.TargetPath = Join-Path $pasta "iniciar-consulta.bat"
$s.WorkingDirectory = $pasta
$s.Description = "Terminal MZ com Claude"
$s.Save()
Write-Host "     Atalho 'Terminal MZ (Claude)' criado."

Write-Host ""
Write-Host "Pronto. Na PRIMEIRA vez o Claude abre o navegador pedindo para autorizar a sua conta:" -ForegroundColor Green
Write-Host "clique em Autorizar, volte a esta janela e use normalmente." -ForegroundColor Green
if (-not $SemAbrir) {
    Write-Host ""
    Read-Host "Aperte Enter para abrir agora"
    Start-Process -FilePath (Join-Path $pasta "iniciar-consulta.bat") -WorkingDirectory $pasta
}
