"""
agentes/vitrine/coleta.py — Fotografia diária dos produtos do mandato (SOMENTE LEITURA)
MZ App

Junta, por produto: status e problemas do anúncio, preço, Buy Box, estoque FBA, vendas por dia (orderMetrics),
sessões e conversão (relatório Sales and Traffic) e Ads (relatório por produto e por termo de busca).
Grava dados/estado.json (o último) e dados/fotos/AAAA-MM-DD.json (histórico, para medir antes × depois).
"""

import json
import os
import time
from datetime import timedelta

from agentes.vitrine import ads_cliente as ads
from agentes.vitrine import cerca
from agentes.vitrine.escrita import PASTA, campanhas_vitrine, carregar_mandato, gravar_estado, hoje, ler_estado
from marketplaces.amazon import api, calculos, relatorios

FOTOS = os.path.join(PASTA, "dados", "fotos")


def _anuncio(conta, sid, sku):
    from urllib.parse import quote
    st, b = api.get(conta, f"/listings/2021-08-01/items/{sid}/{quote(sku, safe='')}", marketplaceIds=api.MARKETPLACE_BR,
                    includedData="summaries,issues,offers,fulfillmentAvailability")
    if st != 200:
        return {"erro": st}
    preco = None
    for o in b.get("offers") or []:
        preco = float((o.get("price") or {}).get("amount") or 0) or preco
    return {"status": [s.get("status") for s in b.get("summaries") or []], "preco": preco,
            "fulfillment": b.get("fulfillmentAvailability"),
            "problemas": [{"code": i.get("code"), "severity": i.get("severity"), "msg": (i.get("message") or "")[:220]}
                          for i in b.get("issues") or []]}


def _ofertas(conta, sid, asin):
    st, b = api.get(conta, f"/products/pricing/v0/items/{asin}/offers", MarketplaceId=api.MARKETPLACE_BR, ItemCondition="New")
    if st != 200:
        return {"erro": st}
    p = b.get("payload", {})
    return {"n_ofertas": (p.get("Summary") or {}).get("TotalOfferCount"),
            "ranks": (p.get("Summary") or {}).get("SalesRankings"),
            "ofertas": [{"nosso": o.get("SellerId") == sid, "preco": (o.get("ListingPrice") or {}).get("Amount"),
                         "fba": o.get("IsFulfilledByAmazon"), "buybox": o.get("IsBuyBoxWinner")} for o in p.get("Offers", [])]}


def _trafego(conta, de, ate):
    """Sessões, visualizações, unidades e conversão por ASIN filho no período (relatório Sales and Traffic)."""
    rid = relatorios.criar(conta, "GET_SALES_AND_TRAFFIC_REPORT", f"{de}T00:00:00-03:00", f"{ate}T23:59:59-03:00",
                           {"dateGranularity": "DAY", "asinGranularity": "CHILD"})
    doc = relatorios.aguardar(conta, rid, None, intervalo=15)
    d = json.loads(relatorios.baixar_texto(conta, doc))
    out = {}
    for r in d.get("salesAndTrafficByAsin", []):
        t, s = r.get("trafficByAsin", {}), r.get("salesByAsin", {})
        out[r.get("childAsin")] = {"sessoes": t.get("sessions"), "visualizacoes": t.get("pageViews"),
                                   "buybox_pct": t.get("buyBoxPercentage"), "conversao_pct": t.get("unitSessionPercentage"),
                                   "unidades": s.get("unitsOrdered"), "vendas": (s.get("orderedProductSales") or {}).get("amount")}
    return out


def _ads(conta, asins, de, ate):
    linhas = ads.relatorio(conta, f"vitrine-prod-{ate}", "spAdvertisedProduct", de, ate,
                           ["date", "campaignId", "campaignName", "advertisedAsin", "advertisedSku", "impressions", "clicks",
                            "cost", "sales14d", "unitsSoldClicks14d"], ["advertiser"])
    por = {}
    for l in linhas:
        if l.get("advertisedAsin") not in asins:
            continue
        g = por.setdefault(l["advertisedAsin"], {"impressoes": 0, "cliques": 0, "gasto": 0.0, "vendas_ads": 0.0, "un_ads": 0, "por_dia": {}})
        for k, c in (("impressoes", "impressions"), ("cliques", "clicks"), ("un_ads", "unitsSoldClicks14d")):
            g[k] += int(l.get(c) or 0)
        g["gasto"] += float(l.get("cost") or 0)
        g["vendas_ads"] += float(l.get("sales14d") or 0)
        dia = g["por_dia"].setdefault(l["date"], {"gasto": 0.0, "cliques": 0})
        dia["gasto"] += float(l.get("cost") or 0)
        dia["cliques"] += int(l.get("clicks") or 0)
    return por


def _termos(conta, ids_vitrine, de, ate):
    if not ids_vitrine:
        return []
    linhas = ads.relatorio(conta, f"vitrine-termos-{ate}", "spSearchTerm", de, ate,
                           ["campaignId", "searchTerm", "keyword", "matchType", "impressions", "clicks", "cost",
                            "sales14d", "unitsSoldClicks14d"], ["searchTerm"], diario=False)
    sel = [l for l in linhas if str(l.get("campaignId")) in ids_vitrine]
    return sorted(sel, key=lambda l: -float(l.get("cost") or 0))[:80]


def coletar(com_ads: bool = True, com_trafego: bool = True) -> dict:
    m = carregar_mandato()
    conta, sid = m["conta"], api.conta_cfg(m["conta"])["seller_id"]
    d0, ontem = m["inicio"], (hoje() - timedelta(days=1)).isoformat()
    base_de = (hoje() - timedelta(days=30)).isoformat()
    estoque = {e["asin"]: e for e in api.estoque_fba(conta)}
    foto = {"data": hoje().isoformat(), "produtos": {}, "erros": []}
    for p in m["produtos"]:
        asin = p["asin"]
        r = foto["produtos"][asin] = {"nome": p.get("nome"), "anuncios": {}, "estoque_fba": estoque.get(asin, {})}
        for s in p["skus"]:
            r["anuncios"][s] = _anuncio(conta, sid, s)
            time.sleep(0.6)
        r["buybox"] = _ofertas(conta, sid, asin)
        time.sleep(2.1)
        try:
            v = api.vendas_asin(conta, asin)
            r["vendas"] = {k: v.get(k) for k in ("vendido_7d", "vendido_30d", "velocidade") if k in v}
        except api.ErroAmazon as e:
            foto["erros"].append(f"vendas {asin}: {e}")
        preco = next((a.get("preco") for s, a in r["anuncios"].items() if s.endswith("FBA") and a.get("preco")), None)
        if preco:
            r["margem_preco_atual"] = cerca.margem(p, preco)
    asins = {p["asin"] for p in m["produtos"]}
    if com_trafego:
        try:
            t = _trafego(conta, base_de, ontem)
            for a in asins:
                foto["produtos"][a]["trafego_30d"] = t.get(a, {"sessoes": 0})
        except Exception as e:          # relatório lento ou recusado: a foto segue sem tráfego
            foto["erros"].append(f"trafego: {type(e).__name__}: {str(e)[:200]}")
    if com_ads:
        try:
            camp = campanhas_vitrine(m)
            foto["campanhas"] = [{"campaignId": c["campaignId"], "nome": c["name"], "estado": c["state"],
                                  "orcamento": (c.get("budget") or {}).get("budget"), "segmentacao": c.get("targetingType")}
                                 for c in camp]
            de_ads = min(d0, ontem) if d0 <= ontem else ontem
            pa = _ads(conta, asins, de_ads, ontem) if d0 <= ontem else {}
            gasto = round(sum(g["gasto"] for g in pa.values()), 2)
            for a in asins:
                foto["produtos"][a]["ads_desde_inicio"] = pa.get(a, {})
            foto["gasto_acumulado"] = gasto
            foto["termos"] = _termos(conta, {str(c["campaignId"]) for c in camp}, de_ads, ontem) if d0 <= ontem else []
        except Exception as e:
            foto["erros"].append(f"ads: {type(e).__name__}: {str(e)[:200]}")
    est = ler_estado()
    est.update({"ultima_foto": foto["data"], "foto": foto})
    if "gasto_acumulado" in foto:
        est["gasto_acumulado"] = foto["gasto_acumulado"]
    gravar_estado(est)
    os.makedirs(FOTOS, exist_ok=True)
    with open(os.path.join(FOTOS, foto["data"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(foto, f, ensure_ascii=False, indent=1)
    return foto
