@echo off
rem Instala o atalho "Consulta MZ" na area de trabalho (abre a pagina como um programa). Nao precisa de administrador.
set "MZ_URL=http://192.168.15.200:8001/consulta/tela"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u=$env:MZ_URL; $nav=@('C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe','C:\Program Files\Microsoft\Edge\Application\msedge.exe','C:\Program Files\Google\Chrome\Application\chrome.exe','C:\Program Files (x86)\Google\Chrome\Application\chrome.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1; $d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Consulta MZ.lnk')); if($nav){$s.TargetPath=$nav; $s.Arguments='--app='+$u; $s.IconLocation=$nav+',0'}else{$s.TargetPath=$u}; $s.Description='Consulta MZ'; $s.Save(); Write-Host ('Atalho criado em: '+$d)"
if errorlevel 1 (
  echo Nao foi possivel criar o atalho. Chame o suporte.
) else (
  echo.
  echo Pronto! Use o atalho "Consulta MZ" da area de trabalho.
  echo Precisa estar na rede da empresa.
)
echo.
pause
