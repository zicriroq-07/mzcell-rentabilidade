# agentes\vitrine\instalar_tarefas.ps1 — instala (ou remove com -Remover) as tarefas do Vitrine no Agendador do Windows
#   "MZ Vitrine - diario": 09:00 todo dia + ao entrar no Windows; se o PC estava desligado no horário, roda assim que possível.
#   "MZ Vitrine - vigia" : a cada 2 horas (freio sem IA).
param([string]$Horario = "09:00", [switch]$Remover)

$script = Join-Path $PSScriptRoot "rodar_vitrine.ps1"
foreach ($n in "MZ Vitrine - diario", "MZ Vitrine - vigia") {
    if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $n -Confirm:$false }
}
if ($Remover) { "Tarefas do Vitrine removidas."; exit 0 }

$cfg = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""

$diario = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "$arg -Modo diario"
$t1 = New-ScheduledTaskTrigger -Daily -At $Horario
$t2 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2.Delay = "PT10M"
Register-ScheduledTask -TaskName "MZ Vitrine - diario" -Action $diario -Trigger $t1, $t2 -Settings $cfg `
    -Description "Agente Vitrine (Amazon 2): rodada diária com o Claude" | Out-Null

$vigia = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "$arg -Modo vigia"
$t3 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(30) -RepetitionInterval (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "MZ Vitrine - vigia" -Action $vigia -Trigger $t3 -Settings $cfg `
    -Description "Agente Vitrine: freio sem IA (PARAR, teto de gasto, fim do mandato)" | Out-Null

"Tarefas instaladas: 'MZ Vitrine - diario' ($Horario e ao entrar no Windows) e 'MZ Vitrine - vigia' (a cada 2 h)."
