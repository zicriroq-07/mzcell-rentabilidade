"""
marketplaces/amazon/ads_painel.py — Rentabilidade da Amazon com Ads completo (campanhas, produtos, alvos, termos de busca)
MZ App

Junta duas fontes, as duas SOMENTE LEITURA:
  - Finances (cache do painel.py): receita, taxas, imposto, CMV e margem por SKU, consolidados por ASIN (FBA + DBA).
  - Advertising API (ads_api.py): Sponsored Products por dia, campanha e produto anunciado, alvos/palavras-chave e termos de busca.
A Amazon permite várias campanhas ativas por produto: o painel mostra o ASIN com a soma de todas e abre cada campanha com seus
indicadores. Margem após Ads = margem de contribuição (já descontados CMV, imposto, comissão e taxas) menos o gasto de Ads.
ACOS de equilíbrio = margem antes do Ads / receita líquida: acima disso, cada venda por Ads dá prejuízo.
Coleta em segundo plano -> amazon_ads_cache_<conta>.json (gitignored). Leitura do painel é instantânea (conta local).
"""

import json
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from marketplaces.amazon import ads_api, api

BASE = api.BASE_DIR
BRT = timezone(timedelta(hours=-3))
JANELA_DIAS = ads_api.JANELA_DIAS
_estado = {}
_trava = threading.Lock()

COL_CAMP = ["date", "campaignId", "campaignName", "campaignStatus", "campaignBudgetAmount", "impressions", "clicks", "cost",
            "purchases14d", "sales14d", "unitsSoldClicks14d", "topOfSearchImpressionShare"]
COL_PROD = ["date", "campaignId", "campaignName", "adGroupId", "adGroupName", "advertisedAsin", "advertisedSku", "impressions",
            "clicks", "cost", "purchases14d", "sales14d", "unitsSoldClicks14d"]
COL_ALVO = ["campaignId", "campaignName", "adGroupId", "adGroupName", "keywordId", "keyword", "keywordType", "matchType", "targeting",
            "impressions", "clicks", "cost", "purchases14d", "sales14d"]
COL_TERMO = ["campaignId", "campaignName", "adGroupId", "adGroupName", "searchTerm", "keyword", "matchType", "targeting",
             "impressions", "clicks", "cost", "purchases14d", "sales14d"]


def _arq(conta):
    return os.path.join(BASE, f"amazon_ads_cache_{conta}.json")


def carregar(conta):
    try:
        with open(_arq(conta), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def estado(conta):
    e = dict(_estado.get(conta) or {"rodando": False, "fase": "", "feito": 0, "total": 0, "erro": None})
    c = carregar(conta)
    e["cache"] = {"gerado_em": c["gerado_em"], "de": c["de"], "ate": c["ate"]} if c else None
    return e


def _set(conta, **kw):
    with _trava:
        _estado.setdefault(conta, {"rodando": False, "fase": "", "feito": 0, "total": 0, "erro": None}).update(kw)


def _blocos(de, ate):
    ini = date.fromisoformat(de)
    fim = date.fromisoformat(ate)
    while ini <= fim:
        f = min(ini + timedelta(days=ads_api.LOTE_DIAS - 1), fim)
        yield ini.isoformat(), f.isoformat()
        ini = f + timedelta(days=1)


def _coletar(conta, dias):
    try:
        ate = (datetime.now(BRT) - timedelta(days=1)).date()
        de = ate - timedelta(days=dias - 1)
        de30 = ate - timedelta(days=29)
        tarefas = []
        for a, b in _blocos(de.isoformat(), ate.isoformat()):
            tarefas.append(("camp_dia", "spCampaigns", a, b, COL_CAMP, ["campaign"], True))
            tarefas.append(("prod_dia", "spAdvertisedProduct", a, b, COL_PROD, ["advertiser"], True))
        tarefas.append(("alvos", "spTargeting", de30.isoformat(), ate.isoformat(), COL_ALVO, ["targeting"], False))
        tarefas.append(("termos", "spSearchTerm", de30.isoformat(), ate.isoformat(), COL_TERMO, ["searchTerm"], False))
        _set(conta, rodando=True, fase="campanhas", feito=0, total=len(tarefas) + 2, erro=None, inicio=time.time())
        saida = {"camp_dia": [], "prod_dia": [], "alvos": [], "termos": []}
        erros = []
        camps = ads_api.campanhas_sp(conta)
        _set(conta, feito=1, fase="relatórios de Ads")

        def uma(t):
            chave, tipo, a, b, cols, ag, diario = t
            try:
                linhas = ads_api.relatorio(conta, f"mz {tipo} {a}", tipo, a, b, cols, ag, diario=diario)
                return chave, linhas, None
            except Exception as e:   # noqa: BLE001 — um relatório com erro não derruba os outros
                return chave, [], f"{tipo} {a}..{b}: {str(e)[:160]}"

        with ThreadPoolExecutor(max_workers=4) as ex:
            for chave, linhas, erro in ex.map(uma, tarefas):
                saida[chave] += linhas
                if erro:
                    erros.append(erro)
                with _trava:
                    _estado[conta]["feito"] += 1
        _set(conta, fase="status dos anúncios")
        skus = sorted({r.get("advertisedSku") for r in saida["prod_dia"] if r.get("advertisedSku")})
        listagens = _status_anuncios(conta, skus)
        cache = {"gerado_em": datetime.now(BRT).isoformat(timespec="seconds"), "de": de.isoformat(), "ate": ate.isoformat(),
                 "campanhas": [{"id": str(c.get("campaignId")), "nome": c.get("name"), "estado": c.get("state"),
                                "tipo": c.get("targetingType"), "orcamento": (c.get("budget") or {}).get("budget"),
                                "inicio": c.get("startDate"), "fim": c.get("endDate"),
                                "estrategia": (c.get("dynamicBidding") or {}).get("strategy")} for c in camps],
                 **saida, "listagens": listagens, "erros": erros}
        ads_api.gravar_json(_arq(conta), cache)
        _set(conta, rodando=False, fase="concluído", feito=len(tarefas) + 2, erro=("Parcial: " + "; ".join(erros)[:300]) if erros else None)
    except Exception as e:   # noqa: BLE001
        _set(conta, rodando=False, fase="erro", erro=str(e)[:300])


def _status_anuncios(conta, skus):
    """Status de cada SKU anunciado (BUYABLE ou não) e unidades FBA aptas: gasto em anúncio não comprável é desperdício."""
    out = {}
    try:
        sid = api.conta_cfg(conta)["seller_id"]
    except Exception:   # noqa: BLE001
        return out
    for sku in skus:
        try:
            st, l = api.get(conta, f"/listings/2021-08-01/items/{sid}/{quote(sku, safe='')}", marketplaceIds=api.MARKETPLACE_BR,
                            includedData="summaries,attributes")
            if st != 200:
                out[sku] = {"status": "NAO_ENCONTRADO"}
                continue
            s = (l.get("summaries") or [{}])[0]
            fa = ((l.get("attributes") or {}).get("fulfillment_availability") or [{}])[0]
            out[sku] = {"status": ",".join(s.get("status") or []) or "?", "asin": s.get("asin"),
                        "canal": fa.get("fulfillment_channel_code"), "qtd": fa.get("quantity")}
        except Exception:   # noqa: BLE001
            out[sku] = {"status": "?"}
    return out


def iniciar_coleta(conta, dias=JANELA_DIAS):
    with _trava:
        if (_estado.get(conta) or {}).get("rodando"):
            return estado(conta)
    ads_api._cfg(conta)
    _set(conta, rodando=True, fase="iniciando", feito=0, total=0, erro=None)
    threading.Thread(target=_coletar, args=(conta, dias), daemon=True).start()
    return estado(conta)


# ───────────────────────── leitura / cálculo ─────────────────────────

def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _div(a, b):
    return round(a / b, 4) if b else None


def _recomendar(l):
    """Recomendação por regras fixas (só leitura: o painel nunca altera campanhas).
    Retorna {"acao", "tom" (ok|atencao|ruim|neutro), "motivo"}."""
    ad, sit = l["ads"], l["situacao"]
    gasto, acos, eq = ad["gasto"], ad["acos"], l["acos_equilibrio"]
    tacos, mpct = l["tacos"], l["margem_pct"]

    def r(acao, tom, motivo):
        return {"acao": acao, "tom": tom, "motivo": motivo}

    if sit == "inativo_gastando":
        return r("Pausar agora", "ruim", "O anúncio não está comprável e as campanhas continuam gastando.")
    if sit == "sem_ads":
        if l["comprave"] is not False and (l["receita"] or 0) >= 300:
            return r("Criar campanha automática pequena", "atencao",
                     "Vende sem Ads, tem margem e nenhuma campanha: teste com orçamento baixo.")
        return r("Sem ação", "neutro", "Sem campanha e com pouca venda.")
    if sit == "sem_venda":
        return r("Pausar e revisar", "ruim", "Gasto sem nenhuma venda pelo Ads no período.")
    if gasto < 5 and ad["vendas"] == 0:
        return r("Consolidar ou pausar", "neutro", "Gasto irrisório e sem venda: campanhas duplicadas ou paradas.")
    if sit == "prejuizo":
        if mpct is not None and mpct < 0.10:
            return r("Pausar o Ads e rever preço/custo", "ruim", "A margem é baixa demais para comportar anúncio.")
        return r("Reduzir lances e negativar termos", "ruim", "A margem após o Ads ficou negativa.")
    if sit == "acima_equilibrio":
        return r("Reduzir lances de 15 a 20%", "atencao",
                 "O ACOS passou do ACOS de equilíbrio: cada venda por Ads dá prejuízo. Meta: 75% do equilíbrio.")
    if acos is not None and eq:
        razao = acos / eq
        nota = " TACOS alto: o Ads pesa na receita total." if (tacos is not None and tacos >= 0.10) else ""
        if razao <= 0.55 and gasto >= 100 and (mpct or 0) >= 0.15:
            return r("Escalar: +20% de orçamento/lance", "ok",
                     f"Muita folga (ACOS {acos:.1%} contra equilíbrio {eq:.1%}). Conferir em 7 dias; teto de ACOS = 80% do equilíbrio.{nota}")
        if razao >= 0.85 or (tacos is not None and tacos >= 0.10):
            return r("Vigiar; não escalar", "atencao",
                     f"Folga pequena (ACOS {acos:.1%} contra equilíbrio {eq:.1%}). Baixar lances ~10% e negativar termos sem venda.{nota}")
        return r("Manter e negativar termos sem venda", "ok",
                 f"ACOS {acos:.1%} dentro do equilíbrio de {eq:.1%}.{nota}")
    return r("Sem ação", "neutro", "Dados insuficientes para recomendar.")


def _metricas(linhas):
    """Soma os indicadores de Ads de um conjunto de linhas e devolve os derivados (CTR, CPC, ACOS, ROAS)."""
    imp = sum(_f(r.get("impressions")) for r in linhas)
    cli = sum(_f(r.get("clicks")) for r in linhas)
    custo = sum(_f(r.get("cost")) for r in linhas)
    ped = sum(_f(r.get("purchases14d")) for r in linhas)
    ven = sum(_f(r.get("sales14d")) for r in linhas)
    un = sum(_f(r.get("unitsSoldClicks14d")) for r in linhas)
    return {"impressoes": int(imp), "cliques": int(cli), "gasto": round(custo, 2), "pedidos": int(ped), "vendas": round(ven, 2), "unidades": int(un),
            "ctr": _div(cli, imp), "cpc": round(custo / cli, 2) if cli else None, "acos": _div(custo, ven), "roas": round(ven / custo, 2) if custo else None,
            "conversao": _div(ped, cli)}


def _periodo_padrao(dias=30):
    ate = (datetime.now(BRT) - timedelta(days=1)).date()
    return (ate - timedelta(days=dias - 1)).isoformat(), ate.isoformat()


def _financas(conta, de, ate):
    """Linhas de margem por SKU, do cache do Finances. Sem cache (ou fora da janela), devolve (None, motivo)."""
    try:
        from marketplaces.amazon import painel as pf
        r = pf.painel(conta, de, ate)
        return r, None
    except Exception as e:   # noqa: BLE001
        return None, str(e)[:160]


def _mapa_asin(conta):
    try:
        with open(os.path.join(BASE, "amazon_asin_skus.json"), encoding="utf-8") as f:
            return (json.load(f).get(conta) or {}).get("sku_asin") or {}
    except (FileNotFoundError, ValueError):
        return {}


def dados(conta, de=None, ate=None):
    c = carregar(conta)
    if not c:
        raise ads_api.ErroAds(404, "Sem dados de Ads ainda: clique em Atualizar dados.")
    if not de or not ate:
        de, ate = _periodo_padrao(30)
    n = (date.fromisoformat(ate) - date.fromisoformat(de)).days + 1
    pa = (date.fromisoformat(de) - timedelta(days=1)).isoformat()
    pd_ = (date.fromisoformat(de) - timedelta(days=n)).isoformat()
    dentro = lambda r, a, b: a <= r.get("date", "") <= b
    prod = [r for r in c["prod_dia"] if dentro(r, de, ate)]
    prod_ant = [r for r in c["prod_dia"] if dentro(r, pd_, pa)]
    camp = [r for r in c["camp_dia"] if dentro(r, de, ate)]
    camp_ant = [r for r in c["camp_dia"] if dentro(r, pd_, pa)]
    fin, motivo_fin = _financas(conta, de, ate)
    mapa = _mapa_asin(conta)

    # Finances por ASIN
    fa = defaultdict(lambda: {"skus": [], "titulo": "", "receita": 0.0, "receita_liquida": 0.0, "un": 0, "margem": 0.0, "completo": True,
                              "comissao": 0.0, "taxa_fba": 0.0, "imposto": 0.0, "cmv": 0.0})
    if fin:
        for x in fin["skus"]:
            asin = x.get("asin") or mapa.get(x["sku"]) or ""
            a = fa[asin or "SEM_ASIN:" + x["sku"]]
            a["skus"].append(x["sku"])
            a["titulo"] = a["titulo"] or x.get("titulo", "")
            for k_src, k_dst in (("receita", "receita"), ("receita_liquida", "receita_liquida"), ("comissao", "comissao"), ("taxa_fba", "taxa_fba")):
                a[k_dst] += _f(x.get(k_src))
            a["un"] += int(x.get("un_liquidas") or 0)
            if x.get("completo") and x.get("margem") is not None:
                a["margem"] += _f(x["margem"])
                a["imposto"] += _f(x.get("imposto"))
                a["cmv"] += _f(x.get("cmv"))
            else:
                a["completo"] = False
                a["margem"] += _f(x.get("margem_apos_taxas"))   # sem custo: margem só até as taxas, marcada como incompleta

    # Ads por ASIN
    por_asin, camp_do_asin = defaultdict(list), defaultdict(set)
    skus_do_asin = defaultdict(set)
    for r in prod:
        asin = r.get("advertisedAsin") or ""
        por_asin[asin].append(r)
        camp_do_asin[asin].add(str(r.get("campaignId")))
        skus_do_asin[asin].add(r.get("advertisedSku"))
    ant_asin = defaultdict(list)
    for r in prod_ant:
        ant_asin[r.get("advertisedAsin") or ""].append(r)

    titulos = {}
    meta_camp = {k["id"]: k for k in c["campanhas"]}
    for k, v in (c.get("listagens") or {}).items():
        titulos[k] = v
    linhas = []
    for asin in sorted(set(por_asin) | {k for k in fa if not k.startswith("SEM_ASIN")}):
        m = _metricas(por_asin.get(asin, []))
        f = fa.get(asin)
        rec_liq = f["receita_liquida"] if f else None
        margem = round(f["margem"], 2) if f else None
        gasto = m["gasto"]
        apos = round(margem - gasto, 2) if margem is not None else None
        skus = sorted(set(f["skus"]) | {s for s in skus_do_asin.get(asin, []) if s}) if f else sorted(s for s in skus_do_asin.get(asin, []) if s)
        st = [titulos.get(s, {}).get("status") for s in skus if s in titulos]
        comprave = ("BUYABLE" in ",".join(x or "" for x in st)) if st else None
        acos_eq = round(margem / rec_liq, 4) if (f and rec_liq and rec_liq > 0 and margem is not None) else None
        tacos = round(gasto / f["receita"], 4) if (f and f["receita"] and gasto) else None
        ant = _metricas(ant_asin.get(asin, []))
        # situação
        if gasto > 0 and comprave is False:
            sit = "inativo_gastando"
        elif gasto >= 20 and m["vendas"] == 0:
            sit = "sem_venda"
        elif apos is not None and apos < 0 and gasto > 0:
            sit = "prejuizo"
        elif m["acos"] is not None and acos_eq is not None and m["acos"] > acos_eq:
            sit = "acima_equilibrio"
        elif gasto > 0:
            sit = "lucrando"
        else:
            sit = "sem_ads"
        t = next((titulos[s] for s in skus if s in titulos), {})
        linhas.append({"asin": asin, "skus": skus, "titulo": (f or {}).get("titulo") or "", "comprave": comprave,
                       "status": (st[0] if st else None), "canal": t.get("canal"),
                       "receita": round(f["receita"], 2) if f else None, "receita_liquida": round(rec_liq, 2) if rec_liq is not None else None,
                       "un": f["un"] if f else None, "margem": margem, "margem_completa": f["completo"] if f else None,
                       "margem_pct": round(margem / rec_liq, 4) if (f and rec_liq and rec_liq > 0 and margem is not None) else None,
                       "ads": m, "ads_anterior": {"gasto": ant["gasto"], "vendas": ant["vendas"], "acos": ant["acos"]},
                       "acos_equilibrio": acos_eq, "tacos": tacos, "margem_apos_ads": apos,
                       "margem_apos_ads_pct": round(apos / rec_liq, 4) if (apos is not None and rec_liq and rec_liq > 0) else None,
                       "n_campanhas": len(camp_do_asin.get(asin, [])),
                       "n_campanhas_ativas": sum(1 for k in camp_do_asin.get(asin, []) if (meta_camp.get(k) or {}).get("estado") == "ENABLED"),
                       "situacao": sit})
        linhas[-1]["recomendacao"] = _recomendar(linhas[-1])
    linhas.sort(key=lambda r: (-r["ads"]["gasto"], -(r["receita_liquida"] or 0)))

    # Campanhas
    por_camp = defaultdict(list)
    for r in camp:
        por_camp[str(r["campaignId"])].append(r)
    asins_da_camp = defaultdict(set)
    for r in prod:
        asins_da_camp[str(r.get("campaignId"))].add(r.get("advertisedAsin"))
    camps = []
    for k, meta in meta_camp.items():
        rows = por_camp.get(k, [])
        m = _metricas(rows)
        dias_com = len({r["date"] for r in rows})
        orc = _f(meta.get("orcamento")) or _f(rows[-1].get("campaignBudgetAmount")) if rows else _f(meta.get("orcamento"))
        tos = [r.get("topOfSearchImpressionShare") for r in rows if r.get("topOfSearchImpressionShare") is not None]
        if meta.get("estado") != "ENABLED" and not rows:
            continue
        camps.append({"id": k, "nome": meta.get("nome"), "estado": meta.get("estado"), "tipo": meta.get("tipo"), "orcamento": orc,
                      "estrategia": meta.get("estrategia"), "dias_ativos": dias_com,
                      "gasto_dia": round(m["gasto"] / n, 2), "uso_orcamento": round((m["gasto"] / n) / orc, 4) if orc else None,
                      "topo_busca": round(sum(tos) / len(tos), 4) if tos else None, **m,
                      "asins": sorted(a for a in asins_da_camp.get(k, []) if a)})
    camps.sort(key=lambda r: -r["gasto"])

    # Totais
    tot_ads = _metricas(camp)
    tot_ant = _metricas(camp_ant)
    com_fin = [l for l in linhas if l["margem"] is not None]
    rec_tot = sum(l["receita"] or 0 for l in linhas)
    rec_liq_tot = sum(l["receita_liquida"] or 0 for l in linhas)
    margem_tot = sum(l["margem"] for l in com_fin)
    totais = {"ads": tot_ads, "ads_anterior": tot_ant, "receita": round(rec_tot, 2), "receita_liquida": round(rec_liq_tot, 2),
              "margem": round(margem_tot, 2), "margem_apos_ads": round(margem_tot - tot_ads["gasto"], 2),
              "tacos": round(tot_ads["gasto"] / rec_tot, 4) if rec_tot else None,
              "acos_equilibrio": round(margem_tot / rec_liq_tot, 4) if rec_liq_tot else None,
              "campanhas_ativas": sum(1 for x in camps if x["estado"] == "ENABLED"), "campanhas_total": len(meta_camp),
              "orcamento_ativo": round(sum(x["orcamento"] or 0 for x in camps if x["estado"] == "ENABLED"), 2),
              "gasto_dia_medio": round(tot_ads["gasto"] / n, 2), "dias": n}
    # Alertas
    termos = [t for t in c["termos"]]
    alertas = {
        "inativo_gastando": [{"asin": l["asin"], "titulo": l["titulo"], "skus": l["skus"], "gasto": l["ads"]["gasto"], "status": l["status"]}
                             for l in linhas if l["situacao"] == "inativo_gastando"],
        "sem_venda": [{"campanha": x["nome"], "id": x["id"], "gasto": x["gasto"], "cliques": x["cliques"]} for x in camps
                      if x["gasto"] >= 20 and x["pedidos"] == 0 and x["estado"] == "ENABLED"],
        "acos_alto": [{"campanha": x["nome"], "id": x["id"], "gasto": x["gasto"], "acos": x["acos"], "vendas": x["vendas"]} for x in camps
                      if x["acos"] is not None and x["acos"] > 0.40 and x["gasto"] >= 20],
        "orcamento_estourado": [{"campanha": x["nome"], "id": x["id"], "uso": x["uso_orcamento"], "acos": x["acos"], "orcamento": x["orcamento"]}
                                for x in camps if x["estado"] == "ENABLED" and (x["uso_orcamento"] or 0) >= 0.9],
        "termos_sem_conversao": sorted([{"termo": t["searchTerm"], "campanha": t["campaignName"], "campanha_id": str(t["campaignId"]),
                                          "gasto": round(_f(t.get("cost")), 2), "cliques": int(_f(t.get("clicks")))}
                                         for t in termos if _f(t.get("cost")) >= 5 and _f(t.get("sales14d")) == 0 and _f(t.get("clicks")) >= 8],
                                        key=lambda x: -x["gasto"])[:40],
        "termos_campeoes": sorted([{"termo": t["searchTerm"], "campanha": t["campaignName"], "campanha_id": str(t["campaignId"]),
                                     "gasto": round(_f(t.get("cost")), 2), "vendas": round(_f(t.get("sales14d")), 2),
                                     "acos": round(_f(t.get("cost")) / _f(t.get("sales14d")), 4), "pedidos": int(_f(t.get("purchases14d")))}
                                    for t in termos if _f(t.get("sales14d")) > 0 and _f(t.get("purchases14d")) >= 2
                                    and _f(t.get("cost")) / _f(t.get("sales14d")) < 0.25 and (t.get("matchType") or "").startswith("TARGETING")],
                                   key=lambda x: -x["vendas"])[:40],
    }
    return {"conta": conta, "periodo": {"de": de, "ate": ate, "dias": n}, "anterior": {"de": pd_, "ate": pa},
            "gerado_em": c["gerado_em"], "cache": {"de": c["de"], "ate": c["ate"]}, "erros_coleta": c.get("erros") or [],
            "financas": {"disponivel": fin is not None, "motivo": motivo_fin, "gerado_em": (fin or {}).get("gerado_em")},
            "totais": totais, "asins": linhas, "campanhas": camps, "alertas": alertas}


def detalhe_asin(conta, asin, de=None, ate=None):
    c = carregar(conta)
    if not c:
        raise ads_api.ErroAds(404, "Sem dados de Ads ainda.")
    if not de or not ate:
        de, ate = _periodo_padrao(30)
    prod = [r for r in c["prod_dia"] if r.get("advertisedAsin") == asin and de <= r["date"] <= ate]
    meta = {k["id"]: k for k in c["campanhas"]}
    por_camp = defaultdict(list)
    for r in prod:
        por_camp[str(r["campaignId"])].append(r)
    campanhas = []
    for k, rows in por_camp.items():
        m = _metricas(rows)
        # a campanha pode anunciar outros ASINs: o total da campanha ajuda a ver a participação deste
        total_camp = _metricas([r for r in c["prod_dia"] if str(r["campaignId"]) == k and de <= r["date"] <= ate])
        campanhas.append({"id": k, "nome": (meta.get(k) or {}).get("nome") or rows[0].get("campaignName"), "estado": (meta.get(k) or {}).get("estado"),
                          "tipo": (meta.get(k) or {}).get("tipo"), "orcamento": (meta.get(k) or {}).get("orcamento"),
                          "grupos": sorted({r.get("adGroupName") for r in rows}), "skus": sorted({r.get("advertisedSku") for r in rows}),
                          "participacao_gasto": _div(m["gasto"], total_camp["gasto"]), **m})
    campanhas.sort(key=lambda x: -x["gasto"])
    dia = defaultdict(list)
    for r in prod:
        dia[r["date"]].append(r)
    serie = []
    d0 = date.fromisoformat(de)
    for i in range((date.fromisoformat(ate) - d0).days + 1):
        k = (d0 + timedelta(days=i)).isoformat()
        m = _metricas(dia.get(k, []))
        serie.append({"dia": k, "gasto": m["gasto"], "vendas": m["vendas"], "cliques": m["cliques"], "impressoes": m["impressoes"], "pedidos": m["pedidos"]})
    ids = set(por_camp)
    alvos = sorted([{"campanha": t["campaignName"], "grupo": t["adGroupName"], "alvo": t.get("keyword") or t.get("targeting"), "tipo": t.get("matchType"),
                     "impressoes": int(_f(t.get("impressions"))), "cliques": int(_f(t.get("clicks"))), "gasto": round(_f(t.get("cost")), 2),
                     "pedidos": int(_f(t.get("purchases14d"))), "vendas": round(_f(t.get("sales14d")), 2),
                     "acos": _div(_f(t.get("cost")), _f(t.get("sales14d")))}
                    for t in c["alvos"] if str(t["campaignId"]) in ids], key=lambda x: -x["gasto"])[:60]
    termos = sorted([{"campanha": t["campaignName"], "termo": t["searchTerm"], "alvo": t.get("keyword") or t.get("targeting"), "tipo": t.get("matchType"),
                      "impressoes": int(_f(t.get("impressions"))), "cliques": int(_f(t.get("clicks"))), "gasto": round(_f(t.get("cost")), 2),
                      "pedidos": int(_f(t.get("purchases14d"))), "vendas": round(_f(t.get("sales14d")), 2),
                      "acos": _div(_f(t.get("cost")), _f(t.get("sales14d")))}
                     for t in c["termos"] if str(t["campaignId"]) in ids], key=lambda x: -x["gasto"])[:60]
    return {"asin": asin, "periodo": {"de": de, "ate": ate}, "ads": _metricas(prod), "campanhas": campanhas, "serie": serie,
            "alvos": alvos, "termos": termos, "nota_alvos": "Alvos e termos são dos últimos 30 dias e agregados por campanha (uma campanha pode anunciar vários ASINs)."}
