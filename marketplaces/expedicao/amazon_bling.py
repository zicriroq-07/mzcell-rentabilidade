"""
marketplaces/expedicao/amazon_bling.py — Checkout dos pedidos Amazon DBA pelo Bling (somente leitura)
MZ App

O pedido DBA vem da Amazon (amazon_dba.py) e chega ao Bling com o número da Amazon em numeroLoja
(Amazon 1 no bling01, Amazon 2 no bling02). Quando o Bling tem o pedido, o checkout é o do Bling (Verificado),
igual ao Mercado Livre: etiqueta gerada não basta; só sai da pendência com Verificado ou já no ponto de coleta.
Sem o pedido no Bling (ainda não importado) ou com o Bling fora, vale a regra da etiqueta.
Os itens (SKU e quantidade) vêm do Bling, uma leitura por pedido, para a lista de montagem.
"""

import time

from marketplaces.bling import api as bling
from marketplaces.expedicao import bling_checkout

BLING = {"amazon01": "bling01", "amazon02": "bling02"}
_itens = {}


def _itens_do_pedido(conta_bling: str, pid) -> list:
    """Itens não mudam: uma leitura por pedido."""
    if pid not in _itens:
        st, r = bling.get(conta_bling, f"/pedidos/vendas/{pid}")
        if st != 200:
            raise RuntimeError(f"pedido {pid} HTTP {st}")
        _itens[pid] = [{"mlb": None, "sku": i.get("codigo"), "titulo": i.get("descricao"), "qtd": i.get("quantidade")}
                       for i in (r.get("data") or {}).get("itens") or []]
        time.sleep(0.35)
    return _itens[pid]


def enriquecer(linhas: list) -> tuple:
    """(linhas com a situação do Bling, erros por conta). Não altera a lista recebida."""
    erros, por_conta = {}, {}
    for conta in {l["conta"] for l in linhas} & set(BLING):
        try:
            por_conta[conta] = bling_checkout._pedidos(BLING[conta])
        except Exception as e:
            erros[f"bling:{conta}"] = f"Bling: {type(e).__name__}: {str(e)[:100]}"
    saida = []
    for l in linhas:
        nova = dict(l, bling_numero=None, bling_situacao=None, bling_checkout=False)
        p = (por_conta.get(l["conta"]) or {}).get(str(l.get("pedido")))
        if p:
            sit = p["situacao"]["id"]
            nova.update(bling_numero=p.get("numero"), bling_situacao=bling_checkout.SITUACOES.get(sit, str(sit)),
                        bling_checkout=sit == bling_checkout.SITUACAO_VERIFICADO, checkout_auto=bool(l.get("no_ponto")))
            if not nova.get("itens"):
                try:
                    nova["itens"] = _itens_do_pedido(BLING[l["conta"]], p["id"])
                except Exception as e:
                    erros[f"bling:{l['conta']}"] = f"Bling: {type(e).__name__}: {str(e)[:100]}"
        saida.append(nova)
    return saida, erros
