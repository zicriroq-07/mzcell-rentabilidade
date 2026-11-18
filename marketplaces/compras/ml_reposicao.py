"""
marketplaces/compras/ml_reposicao.py — Recomendação de reposição do próprio Mercado Livre (somente leitura)
MZ App

GET /marketplace/fbm/sellers/{seller_id}/user-products/replenishment?country=BR (paginado, 20 por página): para cada produto no Full
devolve estoque total, urgência de envio, sugestão de reposição, vendas semanais do Full e o SKU do vendedor. Serve para comparar com a
nossa sugestão (marketplaces/compras/previsao.py). O ML recalcula toda semana (domingo); cache de 30 min por conta.

`total_stock` do ML inclui o disponível, o que está aguardando chegada, o reservado para transferência e o que depende de documento
fiscal. Por isso `em_chegada` = total_stock - disponível - transferência (o que ainda não está vendável): é o "a caminho" que a API deixa ver.
Não escreve em nenhuma API (o POST de inbound é só do modelo China: 403 no teste de 18/11/2026).
"""

import threading
import time

import ml_api
import reposicao_full

CACHE_SEG = 1800
_cache = {}      # conta -> (ts, {inventory_id: dados})
_trava = threading.Lock()


def _resumo(x: dict) -> dict:
    i, s, r = x.get("identifiers") or {}, x.get("stock") or {}, x.get("recommendation") or {}
    q = r.get("suggested_quantity") or {}
    vendas = (x.get("sales") or {}).get("sales_totals") or {}
    return {"user_product_id": i.get("user_product_id"), "inventory_id": i.get("inventory_id"), "sku": i.get("seller_sku"),
            "total_stock": s.get("total_stock"), "urgencia": s.get("shipping_urgency"), "minimo_distribuivel": s.get("minimum_distributable_stock"),
            "tipo": r.get("recommendation_type"), "sugestao": q.get("value") if q.get("type") == "exact" else None,
            "sugestao_min": q.get("min"), "sugestao_max": q.get("max"), "prazo": r.get("replenishment_deadline"),
            "frequencia_semanas": r.get("replenishment_frequency"), "beneficios": x.get("eligibility_benefits") or [],
            "un_30d_full": next((u.get("full") for u in vendas.get("units_sold") or []), None),
            "tags": (x.get("product") or {}).get("tags") or []}


def _ler(conta: str) -> dict:
    seller_id = str(ml_api.load_config()["contas"][conta]["seller_id"])
    ml = reposicao_full.ML(conta)
    out, offset = {}, 0
    while True:
        st, b = ml.get(f"/marketplace/fbm/sellers/{seller_id}/user-products/replenishment", country="BR", limit=20, offset=offset)
        if st not in (200, 206) or not isinstance(b, dict):
            raise RuntimeError(f"{conta}: reposição do ML HTTP {st}")
        for x in b.get("results") or []:
            d = _resumo(x)
            if d["inventory_id"]:
                out[d["inventory_id"]] = d
        offset += 20
        if offset >= ((b.get("paging") or {}).get("total") or 0):
            break
        time.sleep(0.3)
    return out


def por_conta(conta: str, forcar: bool = False) -> dict:
    """{inventory_id: dados da recomendação do ML}. Falha → levanta (quem chama registra o erro sem derrubar a previsão)."""
    with _trava:
        c = _cache.get(conta)
        if c and not forcar and time.time() - c[0] < CACHE_SEG:
            return c[1]
        dados = _ler(conta)
        _cache[conta] = (time.time(), dados)
        return dados
