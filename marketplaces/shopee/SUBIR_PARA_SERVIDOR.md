# Subir o módulo Shopee para o servidor (roteiro para o Claude Code do servidor)

Quem executa: o Claude Code do servidor (192.168.15.200), a pedido da Zicri, que acessa por Remote Control.
**Nunca** copiar, imprimir, commitar ou enviar por zip: `shopee.env`, `.shopee_tokens.json`, `shopee_lojas.json`, `shopee.db`
(todos no `.gitignore`). Não rodar a Shopee em dois computadores ao mesmo tempo (o refresh token muda a cada renovação).

## 1. Código
1. `git status` na raiz do projeto. Se houver alteração local não commitada ou conflito, **parar e avisar a Zicri**.
2. `git pull https://github.com/zicriroq-07/mzcell-rentabilidade.git main` (repositório público, sem login).
3. `.\venv\Scripts\python.exe -m unittest tests.test_shopee tests.test_shopee_validar tests.test_shopee_anuncio -v` deve passar (sem rede).

## 2. Credenciais (única informação secreta: a Partner Key)
Criar `shopee.env` na raiz com este conteúdo. **Pedir à Zicri o valor de `SHOPEE_PROD_PARTNER_KEY`** (open.shopee.com → Console →
app Live → Key). Não repetir a key em nenhuma resposta, log ou commit.
```
SHOPEE_ENV=prod
SHOPEE_PROD_BASE_URL=https://partner.shopeemobile.com
SHOPEE_PROD_PARTNER_ID=2048927
SHOPEE_PROD_PARTNER_KEY=<pedir à Zicri>
SHOPEE_PROD_SHOP_ID=
SHOPEE_REDIRECT_URL=https://shopee-mz.onrender.com/shopee/coletar
```
Validar: `.\venv\Scripts\python.exe -m marketplaces.shopee.validar` deve mostrar ambiente prod, formato OK e "assinatura aceita pela Shopee".

## 3. Autorizar a loja Techseller (gera token novo no servidor)
1. Gerar o link: `.\venv\Scripts\python.exe -c "from marketplaces.shopee import api; print(api.url_autorizacao())"` e mostrar à Zicri (o link vale poucos minutos e não leva a key).
2. A Zicri abre o link logada como dona da loja e autoriza. A página `shopee-mz.onrender.com/shopee/coletar` mostra `shop_id` e `code`; ela cola os dois aqui (o `code` vale ~10 min e é de uso único).
3. Cadastrar e trocar o código:
   `.\venv\Scripts\python.exe -c "from marketplaces.shopee import api, lojas; lojas.cadastrar('techseller','Techseller',<shop_id>); print(api.trocar_codigo('<code>',<shop_id>))"`
4. Testar: `.\venv\Scripts\python.exe -m marketplaces.shopee.validar --teste techseller` (deve mostrar shop_name Techseller, região BR, status NORMAL).
   A autorização vale 365 dias; `/shopee/alertas` avisa quando estiver perto de vencer.

## 4. Subir e sincronizar
1. Reiniciar a tarefa "MZ Servidor" (o `main.py` já monta `/shopee/*` dentro de `try/except ImportError`).
2. O painel só abre no navegador do próprio servidor (regra de rede): `http://127.0.0.1:8001/shopee/painel`.
3. Primeira carga (120 dias de pedidos, ~10 a 15 min), pelo botão do painel ou por comando:
   `.\venv\Scripts\python.exe -m marketplaces.shopee.painel sincronizar 120`
4. Agendar a sincronização diária no Agendador de Tarefas do Windows (confirmar horário com a Zicri; sugestão 06:00 e 22:00, como a carga do servidor):
   `.\venv\Scripts\python.exe -m marketplaces.shopee.painel sincronizar 30`
   Cada execução também grava o retrato diário de visualizações (a Shopee só informa o total acumulado; visitas e conversão dependem disso).

## 5. Depois de funcionar
- No computador de desenvolvimento (LAPTOP-ZICRI) parar o servidor de teste da Shopee e **não usar mais o `.shopee_tokens.json` de lá**.
- Opcional: gerar uma Partner Key nova no open.shopee e atualizar só o `shopee.env` do servidor (a key passou pela conversa).
- Tarifas e como ler o escrow: `marketplaces/shopee/TARIFAS.md`. Modal e plano de Full: `marketplaces/shopee/anuncio.py` e `painel.py`.

## 6. O que mais vai junto neste pull (já validado no clone `C:\mz-clone`)
- **Aba Shopee nativa no dashboard** (lista + modal iguais ao Meli; cards DRE e Full desligados por `SHP_CARD_DRE`/`SHP_CARD_FULL`). Estoque do Bling ao lado do Full (Shopee usa o bling01; tooltip com os demais).
- **Expedição** atualiza ML e checkout do Bling a cada 30 s (Amazon 60 s pelo limite da SP-API).
- **Banco dos agendamentos de Full** (`full_agendamentos.db`, criado sozinho ao lado do `montagem_envios.json`, fora do git): `/montagem/historico`, `/montagem/historico/{chave}/eventos`, `/montagem/programado`. Na primeira subida, `historico.carregar_do_arquivo()` importa os envios que já estão na fila.
- Vendas orgânicas da Shopee seguem a regra do Meli (unidades pagas sem cancelados menos as atribuídas a Ads).
