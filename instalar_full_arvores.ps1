<#
  Instala (ou remove) a tarefa do teste diario de vaga de Full das arvores de Natal (Amazon 01).
  O teste cria um plano FBA e o CANCELA em seguida (nao agenda nem emite NF) e avisa no Slack quando a Amazon aceitar.

  Uso:
    .\instalar_agenda.ps1                              # 08:00, 13:00 e 18:00, todos os dias
    .\instalar_agenda.ps1 -Horarios "09:00","14:00","19:00"
    .\instalar_agenda.ps1 -Remover                     # apaga a tarefa

  Roda no usuario atual (sem pedir senha). Se o PC estiver desligado no horario, a tarefa
  executa assim que ele ligar. Reexecutar o script substitui a tarefa existente.
#>
param(
    [string[]]$Horarios = @("08:00", "13:00", "18:00"),
    [switch]$Remover,
    [string]$Nome = "MZCell Full Arvores"
)
$ErrorActionPreference = "Stop"

if ($Remover) {
    if (Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
        Write-Host "Tarefa '$Nome' removida."
    } else {
        Write-Host "Tarefa '$Nome' nao existe."
    }
    exit 0
}

$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $raiz "venv\Scripts\python.exe"
$script = Join-Path $raiz "full_arvores_agenda.py"
if (-not (Test-Path $py)) { throw "Python do venv nao encontrado: $py (crie o venv e instale o requirements.txt)" }
if (-not (Test-Path $script)) { throw "full_arvores_agenda.py nao encontrado em $raiz" }
foreach ($h in $Horarios) {
    if ($h -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "Horario invalido: '$h' (use HH:mm, ex.: 08:00)" }
}

# pythonw.exe nao abre janela de console. Com python.exe a tarefa roda numa janela na sua
# sessao e, se ela for fechada, o Windows mata o processo (codigo 0xC000013A) sem log algum.
# Sem janela, o resultado vai para full_arvores_agenda.log (inclusive erros fatais).
$pyw = Join-Path $raiz "venv\Scripts\pythonw.exe"
if (Test-Path $pyw) { $exe = $pyw } else { $exe = $py }
$acao = New-ScheduledTaskAction -Execute $exe -Argument "full_arvores_agenda.py" -WorkingDirectory $raiz
$gatilhos = @($Horarios | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $Nome -Action $acao -Trigger $gatilhos -Settings $config `
    -Description "Testa vaga de Full das arvores (cria e cancela plano) e avisa no Slack" -Force | Out-Null

$t = Get-ScheduledTask -TaskName $Nome
Write-Host "Tarefa '$Nome' instalada. Horarios: $($Horarios -join ', ')"
Write-Host "Proxima execucao: $((Get-ScheduledTaskInfo -TaskName $Nome).NextRunTime)"
Write-Host "Teste manual:  .\venv\Scripts\python.exe full_arvores_agenda.py --dry"
Write-Host "Resultado das execucoes agendadas: full_arvores_agenda.log"
