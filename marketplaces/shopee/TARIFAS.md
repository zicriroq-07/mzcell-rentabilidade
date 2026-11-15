# Tarifas da Shopee: o que cada cobrança é (validado nos pedidos reais da Techseller)

A API (`payment/get_escrow_detail`) devolve cada tarifa por `rule_id` e valor, **sem o nome** (`rule_display_name` vem vazio).
Os significados abaixo foram deduzidos do comportamento nos pedidos (percentual constante ou valor fixo), em 14/11/2026,
com ~1.700 pedidos. Conferir os nomes no Seller Centre (Finanças → detalhes do pedido).

| Cobrança | Onde vem | Valor observado |
|---|---|---|
| Comissão | `commission_fee` (18,6%) + regra `1000074` (1,4%, lançada na lista de taxa de serviço) | **20%** do preço líquido até R$ 79,99; 14% de R$ 100 a R$ 199,99 (12,6% + 1,4%) |
| Taxa fixa por item | regra `1000075` | **R$ 4,50** até R$ 79,99 (era **R$ 4,00** até 29/09/2026; passou a R$ 4,50 em 30/09–01/10); **R$ 20,00** de R$ 100 a R$ 199,99 |
| Taxa de campanha (Destaque) | regra `1000069` | **3,5%** do preço, de 30/09 a 27/10/2026 (antes disso aparece sob outro `rule_id`) |
| Taxa de campanha | regra `1000091` | **5,0%** do preço, em **todos os pedidos desde 28/10/2026**; a de 3,5% deixa de aparecer no mesmo dia |

Preço líquido = preço cheio − desconto do vendedor (`order_selling_price`). As taxas são cobradas sobre ele.

## Pontos de atenção
- A virada de 3,5% para 5% é exata e vale para todos os pedidos de 28/10 em diante. Artigos de mercado também a citam
  ("taxa de serviço adicional de 5% desde 28/10/2026, sobre todas as vendas da loja durante a campanha").
  Se a loja não deveria estar na campanha, vale checar a inscrição em Campanhas de Destaque no Seller Centre.
- Pedido pago no cartão parcelado traz listas **líquidas** (comissão e serviço menores + taxa de cartão), mas o repasse
  (`escrow_amount`) desconta os valores brutos. O painel usa os brutos e prefere venda no Pix para estimar tarifas.
- Faixas de R$ 80 a R$ 99,99 (14% + R$ 16) e de R$ 200 em diante (14% + R$ 26) ainda não foram validadas (sem vendas).
- Constante de R$ 0,49 que sobra entre receita e repasse em quase todo pedido concluído: não identificada.
- Tabela oficial: não há API. A de `marketplaces/shopee/precificador.py` vem de artigos de mercado e segue como provisória.

Impacto da taxa de 5% (em vez de 3,5%): +1,5 ponto percentual sobre a receita líquida, cerca de R$ 1,9 mil por mês na loja
(R$ 130 mil de receita líquida em 30 dias).
