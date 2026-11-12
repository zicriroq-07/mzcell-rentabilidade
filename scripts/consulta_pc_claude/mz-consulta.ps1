<#
  mz-consulta.ps1 — Consulta o MZ App (somente leitura) de qualquer PC da rede, pelo PowerShell.
  Sem senha, de qualquer PC da rede interna: consultas da Expedição e do SAC. A senha fica só no app; o comando "entrar" é opcional
  (usuário e senha do app) e libera a leitura direta do Bling/ML e as estatísticas a quem tem acesso total. Nada é gravado no Bling nem no Mercado Livre.

  Copie só este arquivo para o PC e já pode perguntar:
  Depois:
    .\mz-consulta.ps1 interativo                                         (tela de perguntas; é o que o Consulta-MZ.bat abre)
    .\mz-consulta.ps1 perguntar "quantos checkouts tenho em aberto?"     (pergunta em português, sem IA)
    .\mz-consulta.ps1 perguntar "qual a reclamação do pedido 2000012345678901?"
    .\mz-consulta.ps1 consultas                                          (o que o seu acesso permite)
    .\mz-consulta.ps1 executar checkout_abertos -Parametros @{plataforma="shopee"}
    .\mz-consulta.ps1 bling bling01 pedidos/vendas -Parametros @{numero="12345"}   (leitura direta)
    .\mz-consulta.ps1 ml meli01 orders/2000012345678901
    .\mz-consulta.ps1 estatisticas                                       (só quem tem acesso a todos os módulos)
    .\mz-consulta.ps1 sair
  Acrescente -Json para ver os dados completos em vez do resumo. Servidor: variável de ambiente MZ_SERVIDOR
  (padrão http://192.168.15.200:8001). O token fica só na sessão do PowerShell (arquivo temporário do seu usuário, apagado ao sair).
#>
param(
    [Parameter(Position = 0)] [string]$Comando = "ajuda",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)] [string[]]$Resto,
    [hashtable]$Parametros = @{},
    [switch]$Json
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Servidor = if ($env:MZ_SERVIDOR) { $env:MZ_SERVIDOR.TrimEnd("/") } else { "http://192.168.15.200:8001" }
$ArqToken = Join-Path $env:TEMP "mz-consulta-token.txt"

function Cabecalho {
    # sem login pela rede interna: só as consultas da Expedição e do SAC. O login (opcional) libera a leitura direta e as estatísticas
    if (Test-Path $ArqToken) { return @{ Authorization = "Bearer $((Get-Content $ArqToken -Raw).Trim())" } }
    return @{}
}

function Chamar($Caminho, $Consulta = @{}) {
    $qs = ($Consulta.GetEnumerator() | ForEach-Object { "$([uri]::EscapeDataString([string]$_.Key))=$([uri]::EscapeDataString([string]$_.Value))" }) -join "&"
    $url = "$Servidor$Caminho" + $(if ($qs) { "?$qs" } else { "" })
    try {
        # o servidor responde em UTF-8 sem declarar o charset: o PowerShell 5.1 leria como Latin-1 e quebraria os acentos
        $r = Invoke-WebRequest -Uri $url -Headers (Cabecalho) -TimeoutSec 120 -UseBasicParsing
        [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
    } catch {
        $resp = $_.Exception.Response
        $msg = $_.ErrorDetails.Message
        if ($resp -and [int]$resp.StatusCode -eq 401) { Remove-Item $ArqToken -ErrorAction SilentlyContinue; throw "Esta consulta pede login (ou a sessão venceu). Rode:  .\mz-consulta.ps1 entrar" }
        if ($msg) { try { $msg = ($msg | ConvertFrom-Json).detail } catch {} ; throw $msg }
        throw
    }
}

function Responder($texto) {
    # primeiro as consultas cadastradas (instantâneo); se não entender, a pergunta livre (alguns segundos)
    $r = Chamar "/consulta/perguntar" @{ texto = $texto }
    if ($r.entendi) { return $r }
    Write-Host "Pensando... pode levar alguns segundos." -ForegroundColor DarkGray
    return Chamar "/consulta/livre" @{ texto = $texto }
}
function Mostrar($r) {
    if ($Json) { $r | ConvertTo-Json -Depth 12; return }
    if ($r.texto) { $r.texto | ForEach-Object { Write-Host $_ } } else { $r | ConvertTo-Json -Depth 6 }
}

# ── Tela retrô (menu numerado, logo MZ) ─────────────────────────────────────────────────────────
$script:LARG = 78
function Linha($ch) { return ([string][char]$ch) * ($script:LARG - 2) }
function Caixa-Topo { Write-Host (([string][char]0x2554) + (Linha 0x2550) + ([string][char]0x2557)) -ForegroundColor DarkGreen }
function Caixa-Base { Write-Host (([string][char]0x255A) + (Linha 0x2550) + ([string][char]0x255D)) -ForegroundColor DarkGreen }
function Caixa-Meio { Write-Host (([string][char]0x2560) + (Linha 0x2550) + ([string][char]0x2563)) -ForegroundColor DarkGreen }
function Caixa-Linha($txt, $cor = "Green") {
    $v = [string][char]0x2551
    $t = [string]$txt; if ($t.Length -gt $script:LARG - 4) { $t = $t.Substring(0, $script:LARG - 7) + "..." }
    Write-Host $v -NoNewline -ForegroundColor DarkGreen
    Write-Host (" " + $t.PadRight($script:LARG - 3)) -NoNewline -ForegroundColor $cor
    Write-Host $v -ForegroundColor DarkGreen
}
function Logo {
    $M = @("##     ##", "###   ###", "#### ####", "## ### ##", "##  #  ##", "##     ##", "##     ##")
    $Z = @("#########", "      ###", "     ### ", "    ###  ", "   ###   ", "  ###    ", "#########")
    $cores = "Cyan", "Cyan", "Green", "Green", "DarkGreen", "DarkGreen", "DarkGray"
    $bloco = [string][char]0x2588
    for ($i = 0; $i -lt 7; $i++) {
        $l = ($M[$i] + "   " + $Z[$i]).ToCharArray() | ForEach-Object { if ($_ -eq "#") { $bloco + $bloco } else { "  " } }
        Caixa-Linha ("      " + ($l -join "")) $cores[$i]
    }
}
function Cabecalho-Tela($sub) {
    $Host.UI.RawUI.BackgroundColor = "Black"; Clear-Host
    Caixa-Topo; Caixa-Linha ""; Logo; Caixa-Linha ""
    Caixa-Linha ("  C O N S U L T A   M Z     " + (Get-Date -Format "dd/MM/yyyy HH:mm")) "White"
    Caixa-Meio
    if ($sub) { Caixa-Linha $sub "Yellow"; Caixa-Meio }
}
function Menu-Tela {
    Cabecalho-Tela "MENU PRINCIPAL"
    foreach ($i in @("1  Pedidos sem checkout", "2  Resumo da Expedição de hoje", "3  Envios Full / coletas", "4  Cancelados ainda ativos no Bling",
                     "5  Consultar um pedido", "6  Estoque de um SKU", "7  Reclamação de um pedido", "8  Pendências do SAC", "9  Pedir uma consulta nova", "A  Consulta avançada (pede login)", "",
                     "P  Perguntar em português", "S  Sair")) { Caixa-Linha ("   " + $i) }
    Caixa-Meio; Caixa-Linha "MZ App  -  somente consulta  -  rede interna" "DarkGray"; Caixa-Base
}
function Resultado($titulo, $linhas) {
    Cabecalho-Tela $titulo
    $pagina = 18; $n = 0
    foreach ($l in $linhas) {
        Write-Host $l
        $n++
        if ($n % $pagina -eq 0 -and $n -lt @($linhas).Count) { Read-Host "-- Enter para continuar --" | Out-Null }
    }
    Write-Host ""; Read-Host "Enter para voltar ao menu" | Out-Null
}
function Executar-Tela($id, $titulo, $parametros = @{}) {
    try {
        $r = Chamar "/consulta/executar/$id" $parametros
        Resultado $titulo $(if ($r.texto) { $r.texto } else { @("(sem resposta)") })
    } catch { Resultado $titulo @("Não foi possível consultar:", "  $_") }
}
function Pedir($rotulo) { return (Read-Host $rotulo).Trim() }
function Enviar($Caminho, $Corpo) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Corpo | ConvertTo-Json))
    try {
        $r = Invoke-WebRequest -Uri "$Servidor$Caminho" -Method Post -Body $bytes -ContentType "application/json; charset=utf-8" -Headers (Cabecalho) -TimeoutSec 60 -UseBasicParsing
        [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
    } catch {
        $msg = $_.ErrorDetails.Message
        if ($msg) { try { $msg = ($msg | ConvertFrom-Json).detail } catch {} ; throw $msg }
        throw
    }
}
function Parse-Params($texto) {
    $h = @{}
    foreach ($par in ($texto -split "[\s&]+")) { if ($par -match "^([^=]+)=(.*)$") { $h[$Matches[1]] = $Matches[2] } }
    return $h
}
function Entrar-Tela {
    Cabecalho-Tela "LOGIN DO MZ APP (acesso total)"
    $login = Pedir "Usuário"
    $senha = Read-Host "Senha" -AsSecureString
    $plano = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($senha))
    try {
        $r = Invoke-RestMethod -Method Post -Uri "$Servidor/consulta/login" -ContentType "application/json; charset=utf-8" `
            -Body ([Text.Encoding]::UTF8.GetBytes((@{ login = $login; senha = $plano } | ConvertTo-Json)))
        Set-Content -Path $ArqToken -Value $r.token -Encoding ascii
        return $true
    } catch {
        $m = $_.ErrorDetails.Message; if ($m) { try { $m = ($m | ConvertFrom-Json).detail } catch {} }
        Resultado "LOGIN" @("Não foi possível entrar:", "  $m")
        return $false
    } finally { $plano = $null }
}
function Json-Tela($titulo, $caminho, $params) {
    try {
        $r = Chamar $caminho $params
        Resultado $titulo (@(($r | ConvertTo-Json -Depth 8) -split "`r?`n") | Select-Object -First 400)
    } catch { Resultado $titulo @("Não foi possível consultar:", "  $_") }
}
function Estat-Tela {
    try {
        $e = Chamar "/consulta/estatisticas"
        $l = @("PEDIDOS DE CONSULTA NOVA (abertos):")
        $l += @($e.solicitacoes_abertas | ForEach-Object { "  #$($_.id) $($_.ts.Substring(5,5)) $($_.nome) [$($_.origem)]: $($_.texto)" })
        $l += "", "MAIS USADAS (30 dias):"
        $l += @($e.mais_usadas | ForEach-Object { "  $($_.consulta): $($_.vezes) vezes ($($_.erros) com erro)" })
        $l += "", "PERGUNTAS QUE O SISTEMA NÃO ENTENDEU:"
        $l += @($e.sem_resposta | ForEach-Object { "  $($_.vezes)x  $($_.texto)" })
        Resultado "USO E PEDIDOS" $l
    } catch { Resultado "USO E PEDIDOS" @("Não foi possível consultar:", "  $_") }
}
function Avancada-Tela {
    if (-not (Test-Path $ArqToken)) { if (-not (Entrar-Tela)) { return } }
    while ($true) {
        Cabecalho-Tela "CONSULTA AVANÇADA (leitura direta, acesso total)"
        foreach ($i in @("1  Ler o Bling", "2  Ler o Mercado Livre", "3  Pedidos de consulta nova e uso", "", "S  Voltar ao menu")) { Caixa-Linha ("   " + $i) }
        Caixa-Base
        $o = (Read-Host "`nOpção").Trim().ToUpper()
        switch ($o) {
            "1" {
                $c = Pedir "Conta (bling01, bling02 ou bling03)"
                $r = Pedir "Recurso (ex.: pedidos/vendas, produtos, estoques/saldos, nfe)"
                $q = Pedir "Filtros (ex.: numero=12345 limite=10; Enter = nenhum)"
                if ($c -and $r) { Json-Tela "BLING $c/$r" "/consulta/bling/$c/$r" (Parse-Params $q) }
            }
            "2" {
                $c = Pedir "Conta (meli01, meli02 ou meli03)"
                $r = Pedir "Recurso (ex.: orders/2000012345678901, shipments/123, items/MLB123)"
                $q = Pedir "Filtros (ex.: limit=10; Enter = nenhum)"
                if ($c -and $r) { Json-Tela "MERCADO LIVRE $c/$r" "/consulta/ml/$c/$r" (Parse-Params $q) }
            }
            "3" { Estat-Tela }
            { $_ -in "S", "0", "SAIR" } { return }
        }
    }
}
function Tela-Principal {
    [Console]::InputEncoding = [Text.Encoding]::UTF8
    $Host.UI.RawUI.WindowTitle = "Consulta MZ"
    try { Invoke-RestMethod "$Servidor/sistema/versao" -TimeoutSec 5 | Out-Null }
    catch { Write-Host "Não consegui falar com o servidor ($Servidor). Confira se o PC servidor está ligado e se este PC está na rede da empresa." -ForegroundColor Red; Read-Host "Enter para fechar" | Out-Null; return }
    while ($true) {
        Menu-Tela
        $op = (Read-Host "`nOpção").Trim().ToUpper()
        switch ($op) {
            "1" { $p = Pedir "Plataforma (Enter = todas; shopee, magalu, amazon ou meli)"; Executar-Tela "checkout_abertos" "PEDIDOS SEM CHECKOUT" $(if ($p) { @{ plataforma = $p.ToLower() } } else { @{} }) }
            "2" { Executar-Tela "expedicao_resumo" "RESUMO DA EXPEDIÇÃO" }
            "3" { Executar-Tela "full_envios" "ENVIOS FULL / COLETAS" }
            "4" { Executar-Tela "cancelamentos" "CANCELADOS AINDA ATIVOS NO BLING" }
            "5" { $n = Pedir "Número do pedido"; if ($n) { Executar-Tela "pedido" "PEDIDO $n" @{ numero = $n } } }
            "6" { $s = Pedir "SKU (ou o começo dele)"; if ($s) { Executar-Tela "estoque" "ESTOQUE $($s.ToUpper())" @{ sku = $s } } }
            "7" { $n = Pedir "Número do pedido do Mercado Livre"; if ($n) { Executar-Tela "reclamacao" "RECLAMAÇÃO DO PEDIDO $n" @{ numero = $n } } }
            "8" { Executar-Tela "sac_resumo" "PENDÊNCIAS DO SAC" }
            "9" {
                $n = Pedir "Seu nome"
                $t = Pedir "Descreva a consulta que você precisa (o que quer saber, de qual pedido, SKU ou plataforma)"
                if ($t) { try { $r = Enviar "/consulta/solicitar" @{ nome = $n; texto = $t }; Resultado "PEDIDO DE CONSULTA" $r.texto } catch { Resultado "PEDIDO DE CONSULTA" @("Não consegui registrar:", "  $_") } }
            }
            "A" { Avancada-Tela }
            "P" {
                $t = Pedir "Sua pergunta"
                if ($t) { try { $r = Responder $t; Resultado "RESPOSTA" $r.texto } catch { Resultado "RESPOSTA" @("Não consegui responder:", "  $_") } }
            }
            { $_ -in "S", "0", "SAIR" } { Clear-Host; return }
        }
    }
}

switch ($Comando.ToLower()) {
    "entrar" {
        $login = Read-Host "Usuário do MZ App"
        $senha = Read-Host "Senha" -AsSecureString
        $plano = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($senha))
        try {
            $r = Invoke-RestMethod -Method Post -Uri "$Servidor/consulta/login" -ContentType "application/json; charset=utf-8" `
                -Body ([Text.Encoding]::UTF8.GetBytes((@{ login = $login; senha = $plano } | ConvertTo-Json)))
        } catch {
            $m = $_.ErrorDetails.Message; if ($m) { try { $m = ($m | ConvertFrom-Json).detail } catch {} ; throw $m }; throw
        } finally { $plano = $null }
        Set-Content -Path $ArqToken -Value $r.token -Encoding ascii
        Write-Host "Olá, $($r.nome). Acesso: $($r.modulos -join ', '). Válido por $($r.validade_horas) h."
    }
    "sair" { Remove-Item $ArqToken -ErrorAction SilentlyContinue; Write-Host "Sessão encerrada." }
    "consultas" { $lista = Chamar "/consulta/consultas"; if ($lista.Count -eq 1 -and $lista[0] -is [array]) { $lista = $lista[0] }; $lista | ForEach-Object { Write-Host ("{0,-18} {1}`n{2,-18} ex.: {3}" -f $_.id, $_.titulo, "", $_.exemplo) } }
    "tela" { Tela-Principal }
    "interativo" {
        # tela da Consulta MZ: o funcionário digita a pergunta, sem comandos
        [Console]::InputEncoding = [Text.Encoding]::UTF8
        $Host.UI.RawUI.WindowTitle = "Consulta MZ"
        try { Invoke-RestMethod "$Servidor/sistema/versao" -TimeoutSec 5 | Out-Null }
        catch { Write-Host "Não consegui falar com o servidor ($Servidor). Confira se o PC servidor está ligado e se este PC está na rede da empresa." -ForegroundColor Red; Read-Host "Enter para fechar" | Out-Null; return }
        Write-Host "CONSULTA MZ" -ForegroundColor Green
        Write-Host "Digite sua pergunta e aperte Enter. Exemplos: quantos checkouts tenho em aberto? | situação do pedido 2000012345678901"
        Write-Host "Digite 'ajuda' para ver tudo que dá para consultar e 'sair' para fechar."
        while ($true) {
            $t = Read-Host "`nPergunta"
            if ($null -eq $t) { break }
            $t = $t.Trim()
            if (-not $t) { continue }
            if ($t -in "sair", "fechar", "exit") { break }
            try { Mostrar (Responder $t) } catch { Write-Host "Não consegui responder: $_" -ForegroundColor Red }
        }
    }
    "ajuda" { Mostrar (Chamar "/consulta/executar/ajuda") }
    "perguntar" { Mostrar (Responder ($Resto -join " ")) }
    "executar" { Mostrar (Chamar "/consulta/executar/$($Resto[0])" $Parametros) }
    "bling" { $r = Chamar "/consulta/bling/$($Resto[0])/$($Resto[1])" $Parametros; $r | ConvertTo-Json -Depth 12 }
    "ml" { $r = Chamar "/consulta/ml/$($Resto[0])/$($Resto[1])" $Parametros; $r | ConvertTo-Json -Depth 12 }
    "estatisticas" { Chamar "/consulta/estatisticas" | ConvertTo-Json -Depth 6 }
    default {
        # qualquer outra coisa é tratada como pergunta: .\mz-consulta.ps1 quantos checkouts em aberto
        Mostrar (Responder ((@($Comando) + $Resto) -join " "))
    }
}
