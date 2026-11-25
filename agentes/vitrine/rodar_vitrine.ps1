# agentes\vitrine\rodar_vitrine.ps1 — rodada diária do Vitrine (chamada pelo Agendador do Windows)
# -Modo diario : 1 rodada por dia com o Claude (se já rodou hoje, sai). Roda também ao ligar o PC, se o horário foi perdido.
# -Modo vigia  : só o freio, sem IA (PARAR, teto de gasto, fim do mandato). A cada 2 horas.
param([ValidateSet("diario", "vigia")][string]$Modo = "diario")

$raiz = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $raiz
$py = Join-Path $raiz "venv\Scripts\python.exe"
$dados = Join-Path $PSScriptRoot "dados"
New-Item -ItemType Directory -Force $dados | Out-Null
$log = Join-Path $dados ("log_" + (Get-Date -Format "yyyy-MM-dd") + ".txt")

function Log($t) { Add-Content -Path $log -Value ("[" + (Get-Date -Format "HH:mm:ss") + "] " + $t) -Encoding utf8 }

if ($Modo -eq "vigia") {
    Log "vigia"
    & $py -m agentes.vitrine.vitrine vigia *>> $log
    exit $LASTEXITCODE
}

$marca = Join-Path $dados "ultima_rodada.txt"
$hoje = Get-Date -Format "yyyy-MM-dd"
if ((Test-Path $marca) -and ((Get-Content $marca -Raw).Trim() -eq $hoje)) { exit 0 }
& $py -m agentes.vitrine.vitrine ativo | Out-Null
if ($LASTEXITCODE -ne 0) { & $py -m agentes.vitrine.vitrine vigia *>> $log; exit 0 }   # fora da janela: só o freio
if (Test-Path (Join-Path $PSScriptRoot "PARAR")) { Log "PARAR presente: rodada não feita"; & $py -m agentes.vitrine.vitrine vigia *>> $log; exit 0 }

Log "rodada diária: início"
$prompt = "Você é o agente Vitrine. Leia agentes/vitrine/INSTRUCOES.md e execute a rodada de hoje ($hoje) do começo ao fim. " +
          "Use somente os comandos python -m agentes.vitrine.vitrine descritos lá."
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
claude -p $prompt --restricted --strict-mcp-config --tools "Read,Glob,Grep,Edit,Write,PowerShell" `
    --allowedTools $permitidas --max-turns 80 *>> $log
$fim = $LASTEXITCODE
Log ("rodada diária: fim (código " + $fim + ")")
$resumo = Join-Path $dados "resumo_enviado.txt"
if ((Test-Path $resumo) -and ((Get-Content $resumo -Raw).Trim() -eq $hoje)) {
    [System.IO.File]::WriteAllText($marca, $hoje, (New-Object System.Text.UTF8Encoding($false)))
} else {
    Log "resumo do dia não saiu: a rodada fica pendente e tenta de novo no próximo disparo"
}
exit $fim
