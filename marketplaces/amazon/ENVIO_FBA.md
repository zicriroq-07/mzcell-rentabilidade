# Envio FBA da Amazon (Full da Amazon) — como operar

Guia para quem opera o servidor (inclusive o Claude Code do servidor). Complementa o cabeçalho de `envio_fba.py`.
**Este comando ESCREVE na Amazon.** Tudo simula por padrão; só cria, agenda, emite NF ou cancela com `--confirmar`.

## Ordem validada (por API, de ponta a ponta)

Validado na amazon01: `FBA19VTQKGBW` (31/10/2026) e `FBA19W1VZFVX` (04/11/2026: NF SUCCESS, transporte Total Express R$ 0, coleta reservada).

| # | Passo | Comando | O que faz |
|---|---|---|---|
| 1 | Simular | `simular` | Valida SKU, quantidades, origem e caixas. Não escreve nada. |
| 2 | Criar | `criar --confirmar` | Cria o plano e o envio, define as caixas (peso e dimensões), confirma o destino e lista as datas de coleta. **Não agenda.** |
| 3 | Agendar | `agendar --plano wf... --data AAAA-MM-DD --confirmar` (ou `--mais-cedo`) | Confirma o transporte (`confirmTransportationOptions`). Data só em dia útil. |
| 4 | NF-e de remessa | `nf --plano wf... --confirmar` | Pede a emissão pela Invoices API (`REMITTANCE` / `INBOUND_SHIPMENT`) e espera até 10 min. Sem `--confirmar` só mostra o status. |
| 5 | Seller Central (manual) | — | Conferir a reserva da coleta, imprimir o **DANFE** e as **etiquetas de caixa**. |
| 6 | Entregar | — | Entregar as caixas à transportadora. Só então "Marcar todos como enviados". |

```powershell
.\venv\Scripts\python.exe -m marketplaces.amazon.envio_fba simular amazon01 --sku KIT2MIC-FBA --caixas 3 --por-caixa 30 --peso 19 --dim 53x33x33
.\venv\Scripts\python.exe -m marketplaces.amazon.envio_fba criar   amazon01 --sku KIT2MIC-FBA --caixas 3 --por-caixa 30 --peso 19 --dim 53x33x33 --confirmar
.\venv\Scripts\python.exe -m marketplaces.amazon.envio_fba agendar amazon01 --plano wf... --data 2026-11-05 --confirmar
.\venv\Scripts\python.exe -m marketplaces.amazon.envio_fba nf      amazon01 --plano wf... --confirmar
.\venv\Scripts\python.exe -m marketplaces.amazon.envio_fba status  amazon01 --plano wf...
```

Caixa mista: `--itens SKU1=15,SKU2=10 --caixas 2 --peso 4.2 --dim 57x33x33` (cada caixa leva todos os SKUs).
Caixas de tipos diferentes no mesmo plano: `--caixa "3;53x33x33;19;SKU1=30" --caixa "1;40x30x30;8;SKU2=12"` (repita `--caixa`).

## Agendamento

- **Não existe passo de API para "reservar agendamento da transportadora".** A reserva aparece no Seller Central (responsável,
  data da coleta, botão "Cancelar agendamento") como consequência de `agendar` (transporte confirmado) mais a NF emitida.
- Coleta só em **dia útil**, sem fim de semana nem feriado nacional (lista `FERIADOS` em `envio_fba.py`; conferir todo ano).
- Depois de confirmar há uma janela de cerca de **2h16** para cancelar a remessa e anular cobranças.

## NF-e de remessa

- Endpoint: `POST /tax/invoices/2024-06-19/governmentInvoiceRequests` (e `GET` no mesmo caminho para o status).
- **Não clicar também em "Confirmar e gerar nota fiscal" no Seller Central:** é o mesmo pedido; faça um ou outro.
- Se o status der **429** (`QuotaExceeded`) logo depois do POST, é o limite de consultas: esperar. O comando já espera e repete.
- Resultado esperado: `SUCCESS` com o documento fiscal (chave da NF-e).

## Regras e cuidados

- SKU de envio = **SKU FBA** (`-FBA`), com `prepOwner NONE` e `labelOwner SELLER`. O estoque físico sai do **SKU irmão no mesmo
  ASIN**, que é o SKU do Bling e dos melis (mapa em `amazon_asin_skus.json`, chave `asin_skus`).
- Peso **real** por caixa (balança), no máximo **22 kg por volume**. Os pesos usados em 03/11 eram estimados.
- Origem e contato em `amazon_fba_origem.json` (gitignored; modelo `amazon_fba_origem.exemplo.json`). Há `amazon01` e `amazon02`.
- O estoque dos irmãos precisa existir antes de enviar. **O app não lança nem transfere estoque no Bling** (isso é manual, pela equipe).
- Um envio por vez, conferindo cada um; nunca em lote às cegas. Plano criado e não enviado pode ser desfeito com `cancelar --confirmar`.
- O estado dos planos criados por este comando fica em `amazon_fba_planos.json` (gitignored). `nf` e `agendar` só funcionam para
  planos criados por `criar` neste mesmo servidor.
- Depois de `SHIPPED`, o envio entra sozinho na fila de montagem do módulo Full (a cada 20 min).

## Ainda não validado / pendente

- **amazon02 nunca criou um envio.** Todos os envios reais foram na amazon01. Na primeira vez: `simular`, depois `criar` de um item só.
- **Etiquetas de caixa e de unidade em PDF pela API:** ainda não buscadas; hoje saem pelo Seller Central.
- Se a Amazon recusar a escrita (403) na amazon02, rode `python -m marketplaces.amazon.diagnostico amazon02 --salvar` e confira o papel.
