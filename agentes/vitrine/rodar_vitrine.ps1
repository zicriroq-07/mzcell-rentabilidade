# agentes\vitrine\rodar_vitrine.ps1 — rodadas do Vitrine (chamado pelo Agendador do Windows)
# -Modo rodada : 4 rodadas por dia com o Claude (08h, 12h, 16h, 20h). Cada disparo faz a rodada do horário mais recente
#                que ainda não foi concluída; se o PC estava desligado, roda assim que possível (só a mais recente).
#                Se o Claude parar no meio (limite de turnos, erro), ele é chamado de novo para CONTINUAR, até concluir
#                (até 4 tentativas por disparo). A rodada só conta como feita quando o agente roda `concluir <rodada>`;
#                na rodada de fechamento (20h) também precisa ter saído o resumo do dia por e-mail.
# -Modo vigia  : só o freio, sem IA (PARAR, teto de gasto, fim do mandato). A cada 2 horas.
# ("diario" continua aceito como sinônimo de "rodada", para tarefas antigas.)
param([ValidateSet("rodada", "diario", "vigia")][string]$Modo = "rodada", [int]$Tentativas = 4)

$raiz = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $raiz
$py = Join-Path $raiz "venv\Scripts\python.exe"
$dados = Join-Path $PSScriptRoot "dados"
New-Item -ItemType Directory -Force $dados | Out-Null
$log = Join-Path $dados ("log_" + (Get-Date -Format "yyyy-MM-dd") + ".txt")
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Log($t) { Add-Content -Path $log -Value ("[" + (Get-Date -Format "HH:mm:ss") + "] " + $t) -Encoding utf8 }
# saída de programas vai para o log em UTF-8 (o `*>>` do PowerShell 5.1 grava UTF-16 e o log ficava ilegível)
function ParaLog { process { [System.IO.File]::AppendAllText($log, ([string]$_) + "`r`n", $utf8) } }

if ($Modo -eq "vigia") {
    Log "vigia"
    & $py -m agentes.vitrine.vitrine vigia 2>&1 | ParaLog
    exit $LASTEXITCODE
}

# rodada do horário mais recente (antes das 08h, vale a das 20h de ontem, se ainda não foi feita)
$horarios = @(8, 12, 16, 20)
$agora = Get-Date
$h = ($horarios | Where-Object { $_ -le $agora.Hour } | Select-Object -Last 1)
$dia = $agora.Date
if ($null -eq $h) { $h = 20; $dia = $dia.AddDays(-1) }
$rodada = $dia.ToString("yyyy-MM-dd") + "_" + ("{0:D2}h" -f $h)
$fechamento = ($h -eq 20)

$feitas = Join-Path $dados "rodadas_feitas.txt"
if ((Test-Path $feitas) -and ((Get-Content $feitas) -contains $rodada)) { exit 0 }
& $py -m agentes.vitrine.vitrine ativo | Out-Null
if ($LASTEXITCODE -ne 0) { & $py -m agentes.vitrine.vitrine vigia 2>&1 | ParaLog; exit 0 }   # fora da janela: só o freio
if (Test-Path (Join-Path $PSScriptRoot "PARAR")) { Log "PARAR presente: rodada não feita"; & $py -m agentes.vitrine.vitrine vigia 2>&1 | ParaLog; exit 0 }

function Concluida {
    $arq = Join-Path $dados "rodadas.jsonl"
    $ok = (Test-Path $arq) -and (Select-String -Path $arq -SimpleMatch ('"rodada": "' + $rodada + '"') -Quiet)
    if ($ok -and $fechamento) {
        $resumo = Join-Path $dados "resumo_enviado.txt"
        $ok = (Test-Path $resumo) -and ((Get-Content $resumo -Raw).Trim() -eq $dia.ToString("yyyy-MM-dd"))
    }
    return $ok
}

# --restricted ignora as permissões gerais do projeto: só vale a lista abaixo (testado em 25/11: criar arquivo fora de
# dados/, python solto e git commit ficam bloqueados; Edit cobre também a escrita de arquivos novos em dados/).
$permitidas = @(
    "Read", "Glob", "Grep",
    "Edit(agentes/vitrine/dados/**)",
    "PowerShell(.\venv\Scripts\python.exe -m agentes.vitrine.vitrine *)",
    "PowerShell(./venv/Scripts/python.exe -m agentes.vitrine.vitrine *)",
    "PowerShell(venv\Scripts\python.exe -m agentes.vitrine.vitrine *)",
    "PowerShell(& .\venv\Scripts\python.exe -m agentes.vitrine.vitrine *)"
) -join ","
$tipo = if ($fechamento) { "a rodada de FECHAMENTO do dia (inclui o relatório por e-mail)" } else { "uma rodada intermediária (sem e-mail)" }
$fim = 0
for ($t = 1; $t -le $Tentativas; $t++) {
    if (Concluida) { break }
    if ($t -eq 1) {
        $prompt = "Você é o agente Vitrine. Leia agentes/vitrine/INSTRUCOES.md e execute a rodada $rodada, que é $tipo, " +
                  "do começo ao fim. Use somente os comandos .\venv\Scripts\python.exe -m agentes.vitrine.vitrine descritos lá. " +
                  "Ao terminar, rode: .\venv\Scripts\python.exe -m agentes.vitrine.vitrine concluir $rodada `"<resumo curto>`""
    } else {
        $prompt = "Você é o agente Vitrine. A rodada $rodada ($tipo) foi interrompida antes de terminar (tentativa $t). " +
                  "Leia agentes/vitrine/INSTRUCOES.md, depois o fim de agentes/vitrine/dados/diario.jsonl, o log de hoje em " +
                  "agentes/vitrine/dados/ e dados/notas.md para ver o que JÁ foi feito nesta rodada. NÃO repita ação já " +
                  "confirmada; continue do ponto onde parou até terminar a rodada. Ao terminar, rode: " +
                  ".\venv\Scripts\python.exe -m agentes.vitrine.vitrine concluir $rodada `"<resumo curto>`""
    }
    Log "rodada $rodada : início (tentativa $t)"
    claude -p $prompt --restricted --strict-mcp-config --tools "Read,Glob,Grep,Edit,Write,PowerShell" `
        --allowedTools $permitidas --max-turns 120 2>&1 | ParaLog
    $fim = $LASTEXITCODE
    Log ("rodada $rodada : fim da tentativa $t (código " + $fim + ")")
}
if (Concluida) {
    Add-Content -Path $feitas -Value $rodada -Encoding utf8
    Log "rodada $rodada concluída"
} else {
    Log "rodada $rodada NÃO concluída após $Tentativas tentativas: fica pendente para o próximo disparo"
}
exit $fim
