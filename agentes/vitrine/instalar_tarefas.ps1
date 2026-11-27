# agentes\vitrine\instalar_tarefas.ps1 — instala (ou remove com -Remover) as tarefas do Vitrine no Agendador do Windows
#   "MZ Vitrine - rodada": 08:00, 12:00, 16:00 e 20:00 + ao entrar no Windows; se o PC estava desligado no horário,
#                          roda assim que possível (o script faz só a rodada mais recente que ainda não foi concluída).
#   "MZ Vitrine - vigia" : a cada 2 horas (freio sem IA).
param([string[]]$Horarios = @("08:00", "12:00", "16:00", "20:00"), [switch]$Remover)

$script = Join-Path $PSScriptRoot "rodar_vitrine.ps1"
foreach ($n in "MZ Vitrine - diario", "MZ Vitrine - rodada", "MZ Vitrine - vigia") {
    if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $n -Confirm:$false }
}
if ($Remover) { "Tarefas do Vitrine removidas."; exit 0 }

$cfg = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew -WakeToRun   # acorda o PC no horário
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""

$rodada = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless powershell.exe $arg -Modo rodada"   # --headless: sem janela (fechar a janela matava a rodada)
$gat = @($Horarios | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logon.Delay = "PT10M"
$gat += $logon
Register-ScheduledTask -TaskName "MZ Vitrine - rodada" -Action $rodada -Trigger $gat -Settings $cfg `
    -Description "Agente Vitrine (Amazon 2): 4 rodadas por dia com o Claude; retoma rodada interrompida" | Out-Null

$vigia = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless powershell.exe $arg -Modo vigia"
$t3 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(30) -RepetitionInterval (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "MZ Vitrine - vigia" -Action $vigia -Trigger $t3 -Settings $cfg `
    -Description "Agente Vitrine: freio sem IA (PARAR, teto de gasto, fim do mandato)" | Out-Null

"Tarefas instaladas: 'MZ Vitrine - rodada' ($($Horarios -join ', ') e ao entrar no Windows) e 'MZ Vitrine - vigia' (a cada 2 h)."
