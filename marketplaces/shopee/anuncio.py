"""
marketplaces/shopee/anuncio.py — Dados do modal de anúncio da Shopee (equivalente ao modal do Meli). SOMENTE LEITURA na Shopee.
MZ App

Fontes:
  pedidos/escrow (cache shopee.db)        vendas, margem por anúncio, custos reais da última venda
  Ads: campanhas manuais, 1 anúncio cada  gasto, GMV, pedidos, cliques, CTR, CPC por anúncio e por dia (janelas de 30 dias)
  views_hist                              retrato diário de visualizações (a API só dá o total acumulado do anúncio)
  SBS (Full)                              vendável, reservado, não apto, a caminho, por armazém
  catálogo (base_info)                    título, imagens, vídeo, descrição, promoção, status (PIVO da Shopee)
  mapa de alterações e posição            gravados localmente (a Shopee não tem API de posição de busca)
Visitas e conversão só existem a partir do primeiro retrato diário (a Shopee não informa visitas por dia).
"""

import json
import math
import time
from datetime import datetime, timedelta

from marketplaces.shopee import api, lojas, painel
from marketplaces.shopee.lojas import ErroShopee

DIA = 86400
LOJA_URL = "https://shopee.com.br/product"
FINAL_FORA = ("CANCELLED", "IN_CANCEL", "UNPAID", "TO_RETURN", "RETURNED")   # fora das vendas do anúncio


def _tabelas(c):
    c.execute("CREATE TABLE IF NOT EXISTS views_hist(item_id INT, dia TEXT, views INT, likes INT, vendas INT, nota REAL, comentarios INT, PRIMARY KEY(item_id,dia))")
    c.execute("CREATE TABLE IF NOT EXISTS ads_camp(campaign_id INT PRIMARY KEY, item_id INT, nome TEXT, status TEXT, tipo TEXT, local TEXT, orcamento REAL, roas_alvo REAL, inicio INT, fim INT)")
    c.execute("CREATE TABLE IF NOT EXISTS ads_dia(campaign_id INT, dia TEXT, imp INT, cliques INT, gasto REAL, gmv REAL, pedidos INT, gmv_dir REAL, ped_dir INT, PRIMARY KEY(campaign_id,dia))")
    c.execute("CREATE TABLE IF NOT EXISTS mapa(id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, ts INT, autor TEXT, texto TEXT, dados TEXT, fav INT DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS config(chave TEXT PRIMARY KEY, valor TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS posicao(item_id INT PRIMARY KEY, pagina INT, posicao INT, keyword TEXT, ts INT)")


def _db():
    c = painel._db()
    _tabelas(c)
    return c


def _f(x):
    return painel._f(x)


def _hoje():
    return datetime.now(painel.BRT).date()


def _iso_ads(s):                      # "dd-mm-yyyy" -> "yyyy-mm-dd"
    d, m, a = str(s).split("-")
    return f"{a}-{m}-{d}"


# ─── coleta (chamada pela sincronização) ─────────────────────────────────────

def ids_ativos(sid):
    ids, off = [], 0
    while True:
        r = api.chamar("/api/v2/product/get_item_list", sid, {"offset": off, "page_size": 100, "item_status": "NORMAL"}).get("response", {})
        ids += [i["item_id"] for i in r.get("item", [])]
        if not r.get("has_next_page"):
            break
        off = r.get("next_offset", off + 100)
    return ids


def coletar_views(sid):
    """Retrato diário (último do dia vale) de visualizações, curtidas, vendas e nota de cada anúncio."""
    ids = ids_ativos(sid)
    c = _db()
    dia = _hoje().isoformat()
    n = 0
    for k in range(0, len(ids), 50):
        r = api.chamar("/api/v2/product/get_item_extra_info", sid, {"item_id_list": ",".join(map(str, ids[k:k + 50]))}).get("response", {})
        for x in r.get("item_list", []):
            c.execute("INSERT OR REPLACE INTO views_hist VALUES(?,?,?,?,?,?,?)", (x["item_id"], dia, x.get("views") or 0, x.get("likes") or 0,
                      x.get("sale") or 0, x.get("rating_star") or 0, x.get("comment_count") or 0))
            n += 1
    c.commit()
    c.close()
    return n


def coletar_ads(sid, dias=120):
    """Campanhas (todas, 1 anúncio cada) e desempenho diário em janelas de 30 dias (limite da API)."""
    cl, off = [], 0
    while True:
        r = api.chamar("/api/v2/ads/get_product_level_campaign_id_list", sid, {"ad_type": "all", "offset": off, "limit": 100}).get("response", {})
        lst = r.get("campaign_list", [])
        cl += lst
        if len(lst) < 100:
            break
        off += 100
    ids = [x["campaign_id"] for x in cl]
    c = _db()
    for k in range(0, len(ids), 100):
        r = api.chamar("/api/v2/ads/get_product_level_campaign_setting_info", sid, {"info_type_list": "1,2,3,4",
                       "campaign_id_list": ",".join(map(str, ids[k:k + 100]))}).get("response", {})
        for s in r.get("campaign_list", []):
            ci, ab = s.get("common_info") or {}, s.get("auto_bidding_info") or {}
            itens = ci.get("item_id_list") or [0]
            dur = ci.get("campaign_duration") or {}
            c.execute("INSERT OR REPLACE INTO ads_camp VALUES(?,?,?,?,?,?,?,?,?,?)", (s["campaign_id"], itens[0], ci.get("ad_name"), ci.get("campaign_status"),
                      ci.get("ad_type"), ci.get("campaign_placement"), _f(ci.get("campaign_budget")), _f(ab.get("roas_target")),
                      dur.get("start_time"), dur.get("end_time")))
    c.commit()
    agora = int(time.time())
    for w in range(max(1, math.ceil(min(dias, 120) / 30))):
        fim, ini = agora - (w * 30 + 1) * DIA, agora - (w * 30 + 30) * DIA
        f = lambda t: time.strftime("%d-%m-%Y", time.localtime(t))
        for k in range(0, len(ids), 50):
            r = api.chamar("/api/v2/ads/get_product_campaign_daily_performance", sid, {"start_date": f(ini), "end_date": f(fim),
                           "campaign_id_list": ",".join(map(str, ids[k:k + 50]))}).get("response", {})
            for cp in r.get("campaign_list", []):
                for m in cp.get("metrics_list", []):
                    c.execute("INSERT OR REPLACE INTO ads_dia VALUES(?,?,?,?,?,?,?,?,?)", (cp["campaign_id"], _iso_ads(m["date"]), m.get("impression") or 0,
                              m.get("clicks") or 0, _f(m.get("expense")), _f(m.get("broad_gmv")), m.get("broad_order") or 0,
                              _f(m.get("direct_gmv")), m.get("direct_order") or 0))
        c.commit()
    c.close()
    return len(ids)


# ─── Full (SBS) ──────────────────────────────────────────────────────────────

def sbs_estoque(sid):
    """{item_id: {...}} somando modelos e armazéns. Vazio se o SBS não responder."""
    def buscar():
        itens, pg = [], 1
        while True:
            r = api.chamar("/api/v2/sbs/get_current_inventory", sid, {"whs_region": "BR", "page_no": pg, "page_size": 100}).get("response", {})
            itens += r.get("item_list", [])
            if len(r.get("item_list", [])) < 100 or pg > 20:
                break
            pg += 1
        out = {}
        for it in itens:
            for s in it.get("sku_list", []):
                for sk in s.get("shop_sku_list", []):
                    iid = int(sk["shop_item_id"])
                    o = out.setdefault(iid, {"vendavel": 0, "reservado": 0, "nao_apto": 0, "a_caminho": 0, "aprovacao": 0, "excesso": 0, "vel_shopee": 0.0,
                                             "v7": 0, "v15": 0, "v30": 0, "v60": 0, "v90": 0, "armazens": []})
                    for w in s.get("whs_list", []):
                        o["vendavel"] += w.get("sellable_qty") or 0
                        o["reservado"] += w.get("reserved_qty") or 0
                        o["nao_apto"] += w.get("unsellable_qty") or 0
                        o["a_caminho"] += w.get("in_transit_pending_putaway_qty") or 0
                        o["aprovacao"] += w.get("ir_approval_qty") or 0
                        o["excesso"] += w.get("excess_stock") or 0
                        o["vel_shopee"] += w.get("selling_speed") or 0
                        for a, b in (("v7", "last_7_sold"), ("v15", "last_15_sold"), ("v30", "last_30_sold"), ("v60", "last_60_sold"), ("v90", "last_90_sold")):
                            o[a] += w.get(b) or 0
                        if any((w.get(k) or 0) for k in ("sellable_qty", "reserved_qty", "unsellable_qty", "in_transit_pending_putaway_qty", "ir_approval_qty")):
                            o["armazens"].append({"whs": w.get("whs_id"), "vendavel": w.get("sellable_qty") or 0, "reservado": w.get("reserved_qty") or 0,
                                                  "nao_apto": w.get("unsellable_qty") or 0, "a_caminho": w.get("in_transit_pending_putaway_qty") or 0,
                                                  "aprovacao": w.get("ir_approval_qty") or 0, "cobertura": w.get("coverage_days")})
        return out
    try:
        return painel._vivo("sbs", buscar)
    except ErroShopee:
        return {}


# ─── catálogo do anúncio ─────────────────────────────────────────────────────

def _catalogo(sid, item_id):
    def buscar():
        b = api.chamar("/api/v2/product/get_item_base_info", sid, {"item_id_list": str(item_id)}).get("response", {}).get("item_list", [])
        if not b:
            return {}
        it = b[0]
        ex = (api.chamar("/api/v2/product/get_item_extra_info", sid, {"item_id_list": str(item_id)}).get("response", {}).get("item_list") or [{}])[0]
        modelos = []
        if it.get("has_model"):
            for m in api.chamar("/api/v2/product/get_model_list", sid, {"item_id": item_id}).get("response", {}).get("model", []):
                est = m.get("stock_info_v2") or {}
                modelos.append({"model_id": m.get("model_id"), "sku": m.get("model_sku") or "", "nome": m.get("model_name") or "",
                                "preco": ((m.get("price_info") or [{}])[0]).get("current_price"),
                                "est_vendedor": sum(_f(x.get("stock")) for x in est.get("seller_stock", []))})
        else:
            est = it.get("stock_info_v2") or {}
            modelos.append({"model_id": 0, "sku": it.get("item_sku") or "", "nome": "", "preco": ((it.get("price_info") or [{}])[0]).get("current_price"),
                            "est_vendedor": sum(_f(x.get("stock")) for x in est.get("seller_stock", []))})
        promo = api.chamar("/api/v2/product/get_item_promotion", sid, {"item_id_list": str(item_id)}).get("response", {}).get("success_list", [])
        imgs = (it.get("image") or {}).get("image_url_list", [])
        return {"item_id": item_id, "titulo": it.get("item_name", ""), "descricao": len(it.get("description") or ""), "n_imagens": len(imgs),
                "foto": imgs[0] if imgs else None, "video": bool(it.get("video_info")), "full": bool(it.get("is_fulfillment_by_shopee")),
                "status": it.get("item_status"), "tem_promocao": bool(it.get("has_promotion")), "modelos": modelos, "views": ex.get("views"),
                "likes": ex.get("likes"), "vendas_total": ex.get("sale"), "nota": ex.get("rating_star"), "comentarios": ex.get("comment_count"),
                "promocoes": (promo[0].get("promotion") if promo else []) or [], "categoria": it.get("category_id"), "criado": it.get("create_time")}
    return painel._vivo(f"cat{item_id}", buscar, 900)


def fotos(item_ids, loja="techseller"):
    """Foto de capa de vários anúncios (lotes de 50 em get_item_base_info), com cache de 1 hora: {item_id: url}."""
    sid = lojas.shop_id_da(loja)
    ids = [int(i) for i in item_ids if str(i).isdigit()]
    saida = {}
    for k in range(0, len(ids), 50):
        lote = ids[k:k + 50]

        def buscar(lote=lote):
            r = api.chamar("/api/v2/product/get_item_base_info", sid, {"item_id_list": ",".join(str(i) for i in lote), "need_complaint_policy": "false"})
            return {str(it.get("item_id")): ((it.get("image") or {}).get("image_url_list") or [None])[0] for it in (r.get("response") or {}).get("item_list", [])}
        saida.update(painel._vivo("fotos" + ",".join(str(i) for i in lote), buscar, 3600))
    return saida


def cobertura(dias=30, loja="techseller"):
    """Confere (sem rede) se o cache tem pelo menos `dias` de vendas e de Ads para TODOS os anúncios: lista o que falta.
    Vendas: pedidos por dia e escrow dos pedidos válidos. Ads: um registro por dia desde o início da campanha até o último dia fechado."""
    c = _db()
    fim = _hoje() - timedelta(days=1)
    ini = fim - timedelta(days=dias - 1)
    i0, i1 = ini.isoformat(), fim.isoformat()
    desde = int(time.mktime((ini - timedelta(days=1)).timetuple()))
    por_dia, itens, validos_sn = {}, {}, []
    for (j,) in c.execute("SELECT dados FROM pedidos WHERE criado>=?", (desde,)):
        p = json.loads(j)
        dia = painel._dia(p["criado"])
        if not (i0 <= dia <= i1):
            continue
        por_dia[dia] = por_dia.get(dia, 0) + 1
        ok = p["status"] not in FINAL_FORA
        if ok:
            validos_sn.append(p["order_sn"])
        for it in p["itens"]:
            x = itens.setdefault(it["item_id"], {"pedidos": 0, "unidades": 0.0})
            x["pedidos"] += 1
            if ok:
                x["unidades"] += _f(it["qtd"])
    dias_lista = [(ini + timedelta(days=k)).isoformat() for k in range(dias)]
    sem_pedido = [d for d in dias_lista if d not in por_dia]
    cache = c.execute("SELECT MIN(criado), MAX(criado) FROM pedidos").fetchone()
    escrow = {r[0] for r in c.execute("SELECT order_sn FROM escrow")}
    sem_escrow = [sn for sn in validos_sn if sn not in escrow]
    camps = c.execute("SELECT campaign_id, item_id, inicio, fim FROM ads_camp").fetchall()
    tem = {}
    for cid, d in c.execute("SELECT campaign_id, COUNT(DISTINCT dia) FROM ads_dia WHERE dia>=? AND dia<=? GROUP BY campaign_id", (i0, i1)):
        tem[cid] = d
    ultimo = c.execute("SELECT MAX(dia) FROM ads_dia").fetchone()[0]
    lacunas, com_ads = [], set()
    for cid, item, a_ini, a_fim in camps:
        com_ads.add(item)
        d_ini = max(ini, datetime.fromtimestamp(a_ini).date()) if a_ini else ini
        d_fim = min(fim, datetime.fromtimestamp(a_fim).date()) if a_fim and datetime.fromtimestamp(a_fim).date() < fim else fim
        esperado = max(0, (d_fim - d_ini).days + 1)
        if esperado and tem.get(cid, 0) < esperado:
            lacunas.append({"campanha": cid, "item_id": item, "esperado": esperado, "tem": tem.get(cid, 0), "faltam": esperado - tem.get(cid, 0)})
    c.close()
    com_venda = {i for i, x in itens.items() if x["unidades"] > 0}
    saida = {"periodo": {"de": i0, "ate": i1, "dias": dias},
             "vendas": {"anuncios_com_venda": len(com_venda), "pedidos": sum(por_dia.values()), "dias_sem_pedido": sem_pedido,
                        "cache_de": datetime.fromtimestamp(cache[0]).date().isoformat() if cache[0] else None,
                        "cache_ate": datetime.fromtimestamp(cache[1]).date().isoformat() if cache[1] else None},
             "escrow": {"pedidos_validos": len(validos_sn), "sem_escrow": len(sem_escrow)},
             "ads": {"campanhas": len(camps), "anuncios_com_campanha": len(com_ads), "anuncios_com_venda_sem_campanha": len(com_venda - com_ads),
                     "ultimo_dia_no_cache": ultimo, "lacunas": lacunas}}
    saida["ok"] = (not sem_pedido and not lacunas and not sem_escrow and ultimo == i1 and (saida["vendas"]["cache_de"] or "9") <= i0)
    return saida


# ─── agregações ──────────────────────────────────────────────────────────────

def _pedidos_item(c, item_id, desde_ts):
    """[(dia, status, order_sn, itens_do_pedido)] só com pedidos que têm o anúncio."""
    padrao = f'%"item_id": {item_id},%'
    out = []
    for (j,) in c.execute("SELECT dados FROM pedidos WHERE criado>=? AND dados LIKE ?", (desde_ts, padrao)):
        p = json.loads(j)
        out.append(p)
    return out


def _vendas_periodo(peds, d0, d1, item_id):
    un = rec = ped = canc = 0
    por_dia = {}          # dia -> {"todos": pedidos em qualquer status, "un": unidades de pedidos válidos}: base para separar vendas por Ads e orgânicas
    for p in peds:
        dia = painel._dia(p["criado"])
        if not (d0 <= dia <= d1):
            continue
        meus = [i for i in p["itens"] if i["item_id"] == item_id]
        pd = por_dia.setdefault(dia, {"todos": 0, "un": 0.0})
        pd["todos"] += 1
        if p["status"] in ("CANCELLED", "IN_CANCEL"):
            if (p.get("motivo_cancel") or "") != "Unpaid Order":      # cancelamento automático por falta de pagamento não é da loja
                canc += 1
            continue
        if p["status"] in FINAL_FORA:
            continue
        ped += 1
        for i in meus:
            un += _f(i["qtd"])
            pd["un"] += _f(i["qtd"])
            rec += _f(i["preco"]) * _f(i["qtd"])
    return {"unidades": un, "receita": rec, "pedidos": ped, "cancelados": canc, "por_dia": por_dia}


def _ads_periodo(c, item_id, d0, d1):
    r = c.execute("SELECT COALESCE(SUM(d.imp),0),COALESCE(SUM(d.cliques),0),COALESCE(SUM(d.gasto),0),COALESCE(SUM(d.gmv),0),COALESCE(SUM(d.pedidos),0),"
                  "COUNT(DISTINCT d.dia) FROM ads_dia d JOIN ads_camp a ON a.campaign_id=d.campaign_id WHERE a.item_id=? AND d.dia>=? AND d.dia<=?",
                  (item_id, d0, d1)).fetchone()
    dias = {dia: p for dia, p in c.execute("SELECT d.dia, SUM(d.pedidos) FROM ads_dia d JOIN ads_camp a ON a.campaign_id=d.campaign_id "
                                           "WHERE a.item_id=? AND d.dia>=? AND d.dia<=? GROUP BY d.dia", (item_id, d0, d1))}
    return {"impressoes": r[0], "cliques": r[1], "ads_total": r[2], "receita_publi": r[3], "vads": r[4], "dias_com_dado": r[5], "ads_por_dia": dias}


def ads_recentes(sid, item_id, dias=4):
    """Reconsulta na Shopee os últimos dias de Ads do anúncio (o dia fechado só aparece na API no dia seguinte e os números ainda se ajustam).
    Cache de 30 min; sem isso o último dia fica zerado e os períodos ficam menores que a realidade."""
    def buscar():
        c = _db()
        ids = [r[0] for r in c.execute("SELECT campaign_id FROM ads_camp WHERE item_id=?", (item_id,))]
        if not ids:
            c.close()
            return 0
        fim = _hoje() - timedelta(days=1)
        ini = fim - timedelta(days=dias - 1)
        f = lambda d: d.strftime("%d-%m-%Y")
        r = api.chamar("/api/v2/ads/get_product_campaign_daily_performance", sid, {"start_date": f(ini), "end_date": f(fim),
                       "campaign_id_list": ",".join(map(str, ids))}).get("response", {})
        n = 0
        for cp in r.get("campaign_list", []):
            for m in cp.get("metrics_list", []):
                c.execute("INSERT OR REPLACE INTO ads_dia VALUES(?,?,?,?,?,?,?,?,?)", (cp["campaign_id"], _iso_ads(m["date"]), m.get("impression") or 0,
                          m.get("clicks") or 0, _f(m.get("expense")), _f(m.get("broad_gmv")), m.get("broad_order") or 0,
                          _f(m.get("direct_gmv")), m.get("direct_order") or 0))
                n += 1
        c.commit()
        c.close()
        return n
    try:
        return painel._vivo(f"adsrec{item_id}", buscar, 1800)
    except Exception:
        return 0


def _visitas(c, item_id, d0, d1):
    """Visitas no período = views (último retrato até d1) − views (último retrato antes de d0). None sem os dois retratos."""
    fim = c.execute("SELECT views FROM views_hist WHERE item_id=? AND dia<=? ORDER BY dia DESC LIMIT 1", (item_id, d1)).fetchone()
    ini = c.execute("SELECT views FROM views_hist WHERE item_id=? AND dia<? ORDER BY dia DESC LIMIT 1", (item_id, d0)).fetchone()
    return (fim[0] - ini[0]) if fim and ini else None


def ads_em_unidades(vendas_dia, ads_pedidos_dia):
    """Unidades válidas atribuídas ao Ads, dia a dia: pedidos de Ads do dia (limitados ao total de pedidos do dia) × unidades válidas por pedido do dia.
    Cancelados e não pagos ficam de fora, como no Meli. É uma estimativa: a Shopee não diz quais pedidos cancelados vieram do Ads."""
    total = 0.0
    for dia, pd in vendas_dia.items():
        if pd["todos"] > 0:
            total += min(pd["un"], min(ads_pedidos_dia.get(dia, 0), pd["todos"]) * pd["un"] / pd["todos"])
    return round(total)


def _periodo(c, peds, item_id, d0, d1):
    n = (datetime.fromisoformat(d1) - datetime.fromisoformat(d0)).days + 1
    v, a = _vendas_periodo(peds, d0, d1, item_id), _ads_periodo(c, item_id, d0, d1)
    # A Shopee informa PEDIDOS atribuídos ao Ads e essa contagem inclui cancelados e não pagos (nunca passa do total de pedidos do dia, mas passa dos válidos).
    # No Meli "vendas por Ads" são unidades vendidas e "sem Ads" = unidades pagas − unidades do Ads. Aqui converte dia a dia: parte válida do dia × pedidos de Ads do dia.
    a["ads_pedidos_api"] = a["vads"]
    a["vads"] = ads_em_unidades(v["por_dia"], a["ads_por_dia"])
    vis = _visitas(c, item_id, d0, d1)
    rec, g, rp = v["receita"], a["ads_total"], a["receita_publi"]
    o = {**v, **a, "de": d0, "ate": d1, "dias": n, "visitas": vis, "media_dia": v["unidades"] / n if n else 0,
         "tacos": g / rec if rec > 0 else 0, "acos": g / rp if rp > 0 else 0, "roas": rp / g if g > 0 else 0,
         "ctr_c": a["cliques"] / a["impressoes"] if a["impressoes"] else 0, "cpc_c": g / a["cliques"] if a["cliques"] else 0,
         "cvr_ads": a["ads_pedidos_api"] / a["cliques"] if a["cliques"] else 0, "vendas_sem_publi": max(0, v["unidades"] - a["vads"]),
         "conv_c": (v["pedidos"] / vis) if vis else None, "preco_c": rec / v["unidades"] if v["unidades"] else 0}
    o.pop("por_dia", None)
    o.pop("ads_por_dia", None)
    return o


def _margem_item(c, peds, item_id, d0, d1, base, aliq):
    """Margem do anúncio no período, pelas tarifas do escrow (só pedidos com escrow)."""
    esc = {}
    sns = [p["order_sn"] for p in peds if d0 <= painel._dia(p["criado"]) <= d1 and p["status"] not in FINAL_FORA]
    for k in range(0, len(sns), 400):
        lote = sns[k:k + 400]
        for sn, j in c.execute(f"SELECT order_sn,dados FROM escrow WHERE order_sn IN ({','.join('?' * len(lote))})", lote):
            esc[sn] = json.loads(j)
    t = dict(receita=0.0, comissao=0.0, servico=0.0, frete=0.0, extras=0.0, imposto=0.0, cmv=0.0, unid=0.0, pedidos=0, cupom=0.0, reembolsado=0.0,
             sem_escrow_valor=0.0, s_comissao_extra=0.0, s_fixo=0.0, s_destaque=0.0, s_outras=0.0)
    sem_cmv, sem_escrow = False, 0
    for p in peds:
        if p["order_sn"] not in sns:
            continue
        e = esc.get(p["order_sn"])
        meu_valor = sum(_f(i["preco"]) * _f(i["qtd"]) for i in p["itens"] if i["item_id"] == item_id)
        if e and _f(e.get("seller_return_refund")) < 0:       # estorno ao comprador já processado
            t["reembolsado"] += meu_valor
            continue
        if not e:
            sem_escrow += 1
            t["sem_escrow_valor"] += meu_valor
            continue
        tot_itens = sum(_f(i["preco"]) * _f(i["qtd"]) for i in p["itens"]) or 1
        rl = _f(e.get("order_selling_price")) - _f(e.get("voucher_from_seller"))
        servico = _f(e.get("service_fee")) or _f(e.get("net_service_fee"))
        frete = (_f(e.get("actual_shipping_fee")) or _f(e.get("estimated_shipping_fee"))) - _f(e.get("buyer_paid_shipping_fee")) - _f(e.get("shopee_shipping_rebate"))
        extras = _f(e.get("fbs_fee")) + _f(e.get("campaign_fee")) + sum(_f(e.get(k)) for k in painel.OUTRAS_TARIFAS)
        for it in p["itens"]:
            if it["item_id"] != item_id:
                continue
            part = _f(it["preco"]) * _f(it["qtd"]) / tot_itens
            sku = it["model_sku"] or it["item_sku"]
            cm = painel._cmv(sku, base)
            if cm is None:
                sem_cmv = True
            t["receita"] += rl * part
            t["cupom"] += _f(e.get("voucher_from_seller")) * part
            dec = decompor_tarifas(e, part, 1.0, 1.0)            # rl=1 e qtd=1: devolve valores em R$ já rateados
            t["s_comissao_extra"] += dec["comissao_pct"] - _f(e.get("commission_fee")) * part
            t["s_fixo"] += dec["fixo"]
            t["s_destaque"] += dec["destaque_pct"]
            t["s_outras"] += dec["outras_un"]
            t["comissao"] += _f(e.get("commission_fee")) * part
            t["servico"] += servico * part
            t["frete"] += frete * part
            t["extras"] += extras * part
            t["unid"] += _f(it["qtd"])
            t["cmv"] += (cm or 0) * _f(it["qtd"])
        t["pedidos"] += 1
    t["imposto"] = aliq * t["receita"]
    t["margem"] = None if (sem_cmv or not t["receita"]) else t["receita"] - t["comissao"] - t["servico"] - t["frete"] - t["extras"] - t["imposto"] - t["cmv"]
    t["margem_pct"] = (t["margem"] / t["receita"]) if t["margem"] is not None and t["receita"] else None
    t["sem_cmv"] = sem_cmv
    t["pedidos_sem_escrow"] = sem_escrow
    return t



# A Shopee devolve cada tarifa por rule_id e valor, sem o nome (rule_display_name vem vazio). Significados inferidos dos dados reais
# (Pix, 1 unidade, preço líquido abaixo de R$ 80; conferir no Seller Centre): ver marketplaces/shopee/TARIFAS.md
REGRAS = {
    1000074: ("comissao_extra", "Parte da comissão (1,4% do preço; com commission_fee fecha 20%)"),
    1000075: ("fixo", "Taxa fixa por item (R$ 4,50 até R$ 79,99; R$ 20,00 de R$ 100 a R$ 199,99)"),
    1000069: ("destaque", "Campanha Destaque a 3,5% (vendas de 22/09 a 27/10/2026)"),
    1000091: ("destaque", "Campanha Destaque a 5% (vendas desde 28/10/2026; substituiu a de 3,5%, nome não informado pela Shopee)"),
}


def decompor_tarifas(e, part, qtd, rl):
    """Tarifas do escrow de um pedido, rateadas para o anúncio (part) e por unidade (qtd). Devolve percentuais e R$/un."""
    out = {"comissao_pct": _f(e.get("commission_fee")) * part / rl, "fixo": 0.0, "destaque_pct": 0.0, "outras_un": 0.0, "regras": []}
    for r in e.get("net_service_fee_info_list") or []:
        rid, val = r.get("rule_id"), _f(r.get("fee_amount")) * part
        tipo, nome = REGRAS.get(rid, ("outra", f"Regra {rid} (sem nome na API)"))
        if tipo == "comissao_extra":
            out["comissao_pct"] += val / rl
        elif tipo == "fixo":
            out["fixo"] += val / qtd
        elif tipo == "destaque":
            out["destaque_pct"] += val / rl
        else:
            out["outras_un"] += val / qtd
        out["regras"].append({"rule_id": rid, "nome": nome, "valor_un": val / qtd, "pct": val / rl})
    out["regras"].insert(0, {"rule_id": "commission_fee", "nome": "Comissão (commission_fee)", "valor_un": _f(e.get("commission_fee")) * part / qtd,
                             "pct": _f(e.get("commission_fee")) * part / rl})
    return out


def _custos_reais(c, peds, item_id, base, aliq, preco_atual):
    """Parâmetros do simulador pela última venda com escrow: comissão, taxa fixa, Destaque e demais regras separadas.
    Prefere venda paga no Pix, em que as tarifas não têm ajuste de parcelamento."""
    candidatos = []
    for p in sorted(peds, key=lambda x: -x["criado"]):
        if p["status"] in FINAL_FORA:
            continue
        r = c.execute("SELECT dados FROM escrow WHERE order_sn=?", (p["order_sn"],)).fetchone()
        if not r:
            continue
        e = json.loads(r[0])
        if _f(e.get("seller_return_refund")) < 0:
            continue
        meus = [i for i in p["itens"] if i["item_id"] == item_id]
        if not meus:
            continue
        candidatos.append((p, e, meus))
        if e.get("buyer_payment_method") == "Pix" and len(candidatos) >= 1:
            break
    pix = [x for x in candidatos if x[1].get("buyer_payment_method") == "Pix"]
    for p, e, meus in (pix or candidatos):
        tot_itens = sum(_f(i["preco"]) * _f(i["qtd"]) for i in p["itens"]) or 1
        qtd = sum(_f(i["qtd"]) for i in meus)
        rec = sum(_f(i["preco"]) * _f(i["qtd"]) for i in meus)
        part = rec / tot_itens
        rl = (_f(e.get("order_selling_price")) - _f(e.get("voucher_from_seller"))) * part
        if rl <= 0 or qtd <= 0:
            continue
        frete = ((_f(e.get("actual_shipping_fee")) or _f(e.get("estimated_shipping_fee"))) - _f(e.get("buyer_paid_shipping_fee")) - _f(e.get("shopee_shipping_rebate"))) * part
        extras = (_f(e.get("fbs_fee")) + _f(e.get("campaign_fee")) + sum(_f(e.get(k)) for k in painel.OUTRAS_TARIFAS)) * part
        sku = meus[0]["model_sku"] or meus[0]["item_sku"]
        t = decompor_tarifas(e, part, qtd, rl)
        return {"origem": f"última venda em {painel._dia(p['criado'])} (pedido {p['order_sn']}, {e.get('buyer_payment_method') or 'pagamento n/d'})",
                "preco": rl / qtd, "preco_cheio": _f(e.get("order_original_price")) * part / qtd, **t,
                "frete": frete / qtd, "extras": extras / qtd, "cupom": _f(e.get("voucher_from_seller")) * part / qtd,
                "cmv": painel._cmv(sku, base), "sku": sku, "imposto_pct": aliq}
    return {"origem": "sem venda com escrow no cache", "preco": preco_atual, "comissao_pct": 0.20, "fixo": 4.5, "destaque_pct": 0.05,
            "outras_un": 0.0, "regras": [], "frete": 0.0, "extras": 0.0, "cupom": 0.0, "cmv": None, "sku": "", "imposto_pct": aliq}


REGRAS_CAMPANHA = (1000069, 1000091)        # taxa de campanha (Destaque): 3,5% até 27/10/2026 e 5% desde 28/10/2026


def taxa_campanha(e):
    """% de taxa de campanha cobrada no pedido (None se o pedido não pagou)."""
    rl = _f(e.get("order_selling_price"))
    if rl <= 0:
        return None
    v = sum(_f(r.get("fee_amount")) for r in e.get("net_service_fee_info_list") or [] if r.get("rule_id") in REGRAS_CAMPANHA)
    return v / rl if v > 0 else None


def _arredonda(x):
    return round(x * 200) / 200            # 0,5 p.p.: 3,5% e 5,0% sem ruído de centavos


def destaque_loja():
    """Linha do tempo da taxa de campanha da loja, pelos pedidos: [{de, ate, pct, pedidos, com_taxa}] (a Shopee não informa a inscrição)."""
    def montar():
        c = _db()
        dias = {}
        for (j, criado) in c.execute("SELECT e.dados,p.criado FROM escrow e JOIN pedidos p ON p.order_sn=e.order_sn WHERE p.status NOT IN ('CANCELLED','IN_CANCEL','UNPAID')"):
            e = json.loads(j)
            if _f(e.get("seller_return_refund")) < 0 or _f(e.get("order_selling_price")) <= 0:
                continue
            d = dias.setdefault(painel._dia(criado), {"n": 0, "com": 0, "pcts": []})
            d["n"] += 1
            t = taxa_campanha(e)
            if t:
                d["com"] += 1
                d["pcts"].append(_arredonda(t))
        c.close()
        seg = []
        for dia in sorted(dias):
            d = dias[dia]
            if d["n"] < 1:
                continue
            pct = max(set(d["pcts"]), key=d["pcts"].count) if d["com"] / d["n"] >= 0.5 else 0.0
            if seg and seg[-1]["pct"] == pct:
                seg[-1]["ate"] = dia
                seg[-1]["pedidos"] += d["n"]
                seg[-1]["com_taxa"] += d["com"]
            else:
                seg.append({"de": dia, "ate": dia, "pct": pct, "pedidos": d["n"], "com_taxa": d["com"]})
        return seg
    return painel._vivo("destaque_loja", montar, 600)


def destaque_item(c, peds, item_id, hist):
    """Situação da taxa de campanha para o anúncio: ativa se a maioria dos pedidos dos últimos 7 dias pagou a taxa."""
    corte = int(time.time()) - 7 * DIA
    tot = com = 0
    ult_com = ult_sem = None
    taxas = []
    for p in sorted(peds, key=lambda x: -x["criado"]):
        if p["status"] in FINAL_FORA:
            continue
        r = c.execute("SELECT dados FROM escrow WHERE order_sn=?", (p["order_sn"],)).fetchone()
        if not r:
            continue
        e = json.loads(r[0])
        if _f(e.get("seller_return_refund")) < 0 or _f(e.get("order_selling_price")) <= 0:
            continue
        t = taxa_campanha(e)
        if t and ult_com is None:
            ult_com = painel._dia(p["criado"])
        if not t and ult_sem is None:
            ult_sem = painel._dia(p["criado"])
        if p["criado"] >= corte:
            tot += 1
            if t:
                com += 1
                taxas.append(_arredonda(t))
    atual = hist[-1] if hist else None
    if tot:
        ativa = com / tot >= 0.5
        base = "pedidos do anúncio nos últimos 7 dias"
    else:                                     # sem venda recente do anúncio: vale o que a loja está pagando
        ativa = bool(atual and atual["pct"] > 0)
        base = "pedidos da loja (o anúncio não vendeu nos últimos 7 dias)"
    taxa = max(set(taxas), key=taxas.count) if taxas else (atual["pct"] if atual else 0.0)
    desde = None
    for sgm in reversed(hist):
        if sgm["pct"] == (atual["pct"] if atual else 0):
            desde = sgm["de"]
        else:
            break
    return {"ativa": ativa, "taxa": taxa if ativa else 0.0, "desde": desde, "pedidos_7d": tot, "com_taxa_7d": com, "base": base,
            "ultimo_pedido_com_taxa": ult_com, "ultimo_pedido_sem_taxa": ult_sem,
            "historico": [x for x in hist if x["pct"] > 0 or True], "inferido": True}


# ─── validador da comissão da última venda e desativação do Destaque ─────────

def _config_get(c, chave):
    r = c.execute("SELECT valor FROM config WHERE chave=?", (chave,)).fetchone()
    return json.loads(r[0]) if r else None


def registrar_desativacao(solicitada_em, efeito_previsto, obs=""):
    """Guarda a solicitação de desativação da campanha Destaque feita no Seller Centre (a Shopee não informa isso por API)."""
    c = _db()
    c.execute("INSERT OR REPLACE INTO config VALUES('destaque_desativacao',?)", (json.dumps({"solicitada_em": solicitada_em, "efeito_previsto": efeito_previsto, "obs": obs}, ensure_ascii=False),))
    c.commit()
    c.close()


def limpar_desativacao():
    c = _db()
    c.execute("DELETE FROM config WHERE chave='destaque_desativacao'")
    c.commit()
    c.close()


def _ultima_venda(c, peds=None, item_id=None):
    """Venda mais recente (da loja ou do anúncio) com escrow, já decomposta nas tarifas."""
    if peds is None:
        fonte = (json.loads(j) for (j,) in c.execute("SELECT dados FROM pedidos WHERE status NOT IN ('CANCELLED','IN_CANCEL','UNPAID','TO_RETURN','RETURNED') ORDER BY criado DESC LIMIT 300"))
    else:
        fonte = iter(sorted(peds, key=lambda x: -x["criado"]))
    for p in fonte:
        if p["status"] in FINAL_FORA:
            continue
        r = c.execute("SELECT dados FROM escrow WHERE order_sn=?", (p["order_sn"],)).fetchone()
        if not r:
            continue
        e = json.loads(r[0])
        if _f(e.get("seller_return_refund")) < 0:
            continue
        meus = [i for i in p["itens"] if item_id is None or i["item_id"] == item_id]
        if not meus:
            continue
        tot_itens = sum(_f(i["preco"]) * _f(i["qtd"]) for i in p["itens"]) or 1
        qtd = sum(_f(i["qtd"]) for i in meus)
        rec = sum(_f(i["preco"]) * _f(i["qtd"]) for i in meus)
        part = rec / tot_itens
        rl = (_f(e.get("order_selling_price")) - _f(e.get("voucher_from_seller"))) * part
        if rl <= 0 or qtd <= 0:
            continue
        t = decompor_tarifas(e, part, qtd, rl)
        return {"order_sn": p["order_sn"], "dia": painel._dia(p["criado"]), "hora": datetime.fromtimestamp(p["criado"], painel.BRT).strftime("%H:%M"),
                "pagamento": e.get("buyer_payment_method") or "n/d", "parcelas": e.get("instalment_plan"), "status": p["status"],
                "preco_un": rl / qtd, "qtd": qtd, "itens_no_pedido": len(p["itens"]), "sku": (meus[0]["model_sku"] or meus[0]["item_sku"]),
                "comissao_pct": t["comissao_pct"], "fixo": t["fixo"], "destaque_pct": t["destaque_pct"], "cobrou_destaque": t["destaque_pct"] > 0,
                "outras_un": t["outras_un"], "regras": t["regras"], "criado": p["criado"]}
    return None


def validar_comissao(v):
    """Compara a última venda com a tabela de comissão (precificador): comissão total, taxa fixa e taxa de campanha."""
    if not v:
        return None
    pct_t, fixa_t = painel_precificador().faixa(v["preco_un"])
    dif_pct = v["comissao_pct"] - pct_t
    dif_fixo = v["fixo"] - fixa_t
    itens = [
        {"campo": "Comissão total", "esperado": f"{pct_t * 100:.1f}%".replace(".", ","), "cobrado": f"{v['comissao_pct'] * 100:.2f}%".replace(".", ","), "ok": abs(dif_pct) <= 0.003},
        {"campo": "Taxa fixa por item", "esperado": f"R$ {fixa_t:.2f}".replace(".", ","), "cobrado": f"R$ {v['fixo']:.2f}".replace(".", ","), "ok": abs(dif_fixo) <= 0.2 or v["itens_no_pedido"] > 1},
    ]
    obs = []
    if v["pagamento"] != "Pix":
        obs.append("Pedido pago no cartão: a Shopee lista tarifas líquidas (comissão e serviço menores + taxa de cartão), mas o repasse usa as brutas. Prefira conferir com uma venda no Pix.")
    if v["itens_no_pedido"] > 1:
        obs.append("Pedido com mais de um item: taxa fixa não conferida.")
    if v["outras_un"] > 0.05:
        obs.append(f"Há taxas de serviço de regra desconhecida (R$ {v['outras_un']:.2f} por unidade).".replace(".", ","))
    return {"itens": itens, "ok": all(i["ok"] for i in itens), "obs": obs}


def painel_precificador():
    from marketplaces.shopee import precificador
    return precificador


def estado_destaque(ult_loja, cfg, hoje):
    """ativa | inativa | ativa_aguardando | cobrando_apos_previsto | desativada, pela última venda da loja e pela solicitação registrada."""
    cobrou = bool(ult_loja and ult_loja["cobrou_destaque"])
    if not cfg:
        return "ativa" if cobrou else "inativa", None
    prev = datetime.fromisoformat(cfg["efeito_previsto"]).date() if cfg.get("efeito_previsto") else None
    sol = cfg.get("solicitada_em") or ""
    if not cobrou and ult_loja and ult_loja["dia"] >= sol:
        return "desativada", None
    if cobrou and prev and hoje >= prev:
        return "cobrando_apos_previsto", (hoje - prev).days
    return "ativa_aguardando", ((prev - hoje).days if prev else None)


def _dre(c, peds, item_id, d0, d1, base, aliq, ads_gasto):
    """DRE do anúncio: do valor dos pedidos feitos até a margem após Ads (Ads real das campanhas do anúncio)."""
    pedidos = [p for p in peds if d0 <= painel._dia(p["criado"]) <= d1 and any(i["item_id"] == item_id for i in p["itens"])]
    cheio = venda = nao_pago = cancelado = devolucao = 0.0
    n = dict(pedidos=0, nao_pago=0, cancelado=0, devolucao=0)
    for p in pedidos:
        meus = [i for i in p["itens"] if i["item_id"] == item_id]
        val = sum(_f(i["preco"]) * _f(i["qtd"]) for i in meus)
        cheio += sum(_f(i["preco_cheio"]) * _f(i["qtd"]) for i in meus) or val
        venda += val
        n["pedidos"] += 1
        st = p["status"]
        if st == "UNPAID" or (st in ("CANCELLED", "IN_CANCEL") and (p.get("motivo_cancel") or "") == "Unpaid Order"):
            nao_pago += val
            n["nao_pago"] += 1
        elif st in ("CANCELLED", "IN_CANCEL"):
            cancelado += val
            n["cancelado"] += 1
        elif st in ("TO_RETURN", "RETURNED"):
            devolucao += val
            n["devolucao"] += 1
    m = _margem_item(c, peds, item_id, d0, d1, base, aliq)
    validas = venda - nao_pago - cancelado - devolucao - m["reembolsado"]
    rl = m["receita"]
    margem = m["margem"]
    ads = ads_gasto
    return {"cheio": cheio, "desconto": cheio - venda, "feitos": venda, "nao_pagos": nao_pago, "cancelados": cancelado, "em_devolucao": devolucao,
            "reembolsados": m["reembolsado"], "validas": validas, "cupom": m["cupom"], "sem_escrow": m["sem_escrow_valor"], "receita_liquida": rl,
            "comissao": m["comissao"], "comissao_extra": m["s_comissao_extra"], "fixo": m["s_fixo"], "destaque": m["s_destaque"], "outras": m["s_outras"],
            "servico_ajuste": m["servico"] - (m["s_comissao_extra"] + m["s_fixo"] + m["s_destaque"] + m["s_outras"]),
            "frete": m["frete"], "extras": m["extras"], "imposto": m["imposto"], "cmv": m["cmv"], "margem": margem, "margem_pct": (margem / rl if margem is not None and rl else None),
            "ads": ads, "margem_apos_ads": (margem - ads) if margem is not None else None,
            "margem_apos_ads_pct": ((margem - ads) / rl if margem is not None and rl else None), "pedidos_sem_escrow": m["pedidos_sem_escrow"],
            "sem_cmv": m["sem_cmv"], "n": n, "unidades": m["unid"]}


# ─── avisos e PIVO ───────────────────────────────────────────────────────────

def _pivo(cat, camp_ativas):
    g = []
    t_ok, d_ok = len(cat.get("titulo", "")) >= 60, cat.get("descricao", 0) >= 150
    g.append({"id": "P", "nome": "P: Palavras-chave indexadas", "ok": t_ok and d_ok, "partes": [
        {"nome": "Título com 60+ caracteres", "ok": t_ok, "detalhe": f"{len(cat.get('titulo', ''))} caracteres"},
        {"nome": "Descrição com 150+ caracteres", "ok": d_ok, "detalhe": f"{cat.get('descricao', 0)} caracteres"}]})
    i_ok = cat.get("n_imagens", 0) >= 5
    g.append({"id": "I", "nome": "I: Imagens com quebra de objeções", "ok": i_ok, "partes": [
        {"nome": "5 imagens ou mais", "ok": i_ok, "detalhe": f"{cat.get('n_imagens', 0)} imagens"}]})
    g.append({"id": "V", "nome": "V: Vídeo", "ok": bool(cat.get("video")), "partes": [
        {"nome": "Vídeo no anúncio", "ok": bool(cat.get("video")), "detalhe": "tem vídeo" if cat.get("video") else "sem vídeo"}]})
    promo = any(p.get("promotion_staging") == "ongoing" for p in cat.get("promocoes", [])) or cat.get("tem_promocao")
    g.append({"id": "O", "nome": "O: Otimização (promoção e Ads)", "ok": bool(promo) and camp_ativas > 0, "partes": [
        {"nome": "Promoção ativa", "ok": bool(promo), "detalhe": "em promoção" if promo else "sem promoção"},
        {"nome": "Ads ativo", "ok": camp_ativas > 0, "detalhe": f"{camp_ativas} campanha(s) ativa(s)"}]})
    pontos = sum(1 for x in g if x["ok"])
    return {"grupos": g, "pontos": pontos, "total": 4, "passa": pontos == 4,
            "atencao": [x["nome"].split(":")[0] + ": " + ", ".join(p["nome"] for p in x["partes"] if not p["ok"]) for x in g if not x["ok"]]}


def _avisos(cat, sbs, vel, per30, camps, promos_fim):
    av = []
    if cat.get("full") or sbs:
        if not sbs:
            av.append({"tipo": "full", "nivel": "atencao", "texto": "Anúncio Full sem dados no SBS"})
        else:
            valido = sbs["vendavel"] + sbs["a_caminho"]
            if valido <= 0 and vel > 0:
                av.append({"tipo": "full", "nivel": "critico", "texto": "Sem estoque no Full"})
            elif vel > 0:
                dias = valido / vel
                if dias < 10:
                    av.append({"tipo": "full", "nivel": "critico", "texto": f"Full acaba em {dias:.1f} dias (crítico)"})
                elif dias <= 15:
                    av.append({"tipo": "full", "nivel": "atencao", "texto": f"Full acaba em {dias:.1f} dias"})
            if sbs["nao_apto"] > 0:
                av.append({"tipo": "full", "nivel": "atencao", "texto": f"{sbs['nao_apto']} un. não aptas no Full"})
            if sbs["a_caminho"] > 0:
                av.append({"tipo": "full", "nivel": "info", "texto": f"{sbs['a_caminho']} un. a caminho do Full"})
            if sbs["excesso"] > 0:
                av.append({"tipo": "full", "nivel": "info", "texto": f"{sbs['excesso']} un. em excesso de estoque no Full"})
    for fim in promos_fim:
        if fim:
            d = (datetime.fromtimestamp(fim) - datetime.now()).days
            if 0 <= d <= 7:
                av.append({"tipo": "promo", "nivel": "atencao", "texto": f"Promoção termina em {d} dia(s)"})
    if per30["ads_total"] > 0 and per30["vads"] == 0:
        av.append({"tipo": "ads", "nivel": "critico", "texto": "Ads gastou e não vendeu nos últimos 30 dias"})
    if per30["receita"] > 0 and per30["tacos"] > 0.15:
        av.append({"tipo": "ads", "nivel": "atencao", "texto": f"TACOS de 30 dias em {per30['tacos'] * 100:.1f}%".replace(".", ",")})
    if camps and not any(x["status"] == "ongoing" for x in camps) and per30["unidades"] > 0:
        av.append({"tipo": "ads", "nivel": "info", "texto": "Nenhuma campanha de Ads ativa neste anúncio"})
    if cat.get("nota") and 0 < cat["nota"] < 4.5:
        av.append({"tipo": "conta", "nivel": "atencao", "texto": f"Nota do anúncio {cat['nota']:.1f}".replace(".", ",")})
    if per30["cancelados"] and per30["pedidos"] and per30["cancelados"] / (per30["pedidos"] + per30["cancelados"]) > 0.08:
        av.append({"tipo": "conta", "nivel": "atencao", "texto": f"{per30['cancelados']} cancelamentos em 30 dias"})
    return av


# ─── entrada principal ───────────────────────────────────────────────────────

def anuncio(item_id: int, de: str = None, ate: str = None, loja: str = "techseller"):
    sid = lojas.shop_id_da(loja)
    hoje = _hoje()
    ontem = hoje - timedelta(days=1)
    ate_d = datetime.fromisoformat(ate).date() if ate else ontem
    de_d = datetime.fromisoformat(de).date() if de else ate_d - timedelta(days=6)
    n = (ate_d - de_d).days + 1
    iso = lambda d: d.isoformat()
    B1 = de_d - timedelta(days=1)
    B0 = B1 - timedelta(days=n - 1)
    C1 = B0 - timedelta(days=1)
    C0 = C1 - timedelta(days=n - 1)
    D1_ = C0 - timedelta(days=1)
    D0 = D1_ - timedelta(days=n - 1)
    cat = _catalogo(sid, item_id)
    if not cat:
        return {"erro": "Anúncio não encontrado na Shopee."}
    ads_recentes(sid, item_id)
    c = _db()
    peds = _pedidos_item(c, item_id, int(time.time()) - 130 * DIA)
    base, aliq = painel._base_cmv(), painel._aliquota()
    A = _periodo(c, peds, item_id, iso(de_d), iso(ate_d))
    Bp = _periodo(c, peds, item_id, iso(B0), iso(B1))
    P3 = _periodo(c, peds, item_id, iso(C0), iso(C1))
    P4 = _periodo(c, peds, item_id, iso(D0), iso(D1_))
    de30, ate30 = ontem - timedelta(days=29), ontem
    P30 = _periodo(c, peds, item_id, iso(de30), iso(ate30))
    for o, d0, d1 in ((A, de_d, ate_d), (Bp, B0, B1)):
        o["margem"] = _margem_item(c, peds, item_id, iso(d0), iso(d1), base, aliq)
        o["dre"] = _dre(c, peds, item_id, iso(d0), iso(d1), base, aliq, o["ads_total"])
    camps = [dict(zip(("campaign_id", "nome", "status", "local", "orcamento", "roas_alvo", "inicio", "fim"), r)) for r in
             c.execute("SELECT campaign_id,nome,status,local,orcamento,roas_alvo,inicio,fim FROM ads_camp WHERE item_id=? ORDER BY status='ongoing' DESC, campaign_id DESC", (item_id,))]
    ativas = sum(1 for x in camps if x["status"] == "ongoing")
    alvos = [x["roas_alvo"] for x in camps if x["status"] == "ongoing" and x["roas_alvo"]]
    sbs_all = sbs_estoque(sid)
    sbs = sbs_all.get(item_id)
    u7 = P7_unid(c, peds, item_id, ontem)
    vel = painel.PESO_7D * u7 / 7 + painel.PESO_30D * P30["unidades"] / 30
    # sem venda em 7 dias e sem estoque válido: a média de 120 dias mostra se era ruptura (mesma regra do plano de envio)
    u120 = _vendas_periodo(peds, (ontem - timedelta(days=painel.JANELA_RUPTURA - 1)).isoformat(), ontem.isoformat(), item_id)["unidades"]
    primeiro = min((painel._dia(p["criado"]) for p in peds), default=iso(ontem))
    dias_base = max(1, min(painel.JANELA_RUPTURA, (ontem - datetime.fromisoformat(primeiro).date()).days + 1))
    ruptura = False
    if sbs and u7 == 0 and u120 > 0 and (sbs["vendavel"] + sbs["a_caminho"]) <= 0:
        ruptura, vel = True, max(vel, u120 / dias_base)
    preco_atual = next((m["preco"] for m in cat["modelos"] if m.get("preco")), 0)
    sim = _custos_reais(c, peds, item_id, base, aliq, preco_atual)
    sim["tacos30"] = P30["tacos"]
    sim["roas_objetivo"] = alvos[0] if alvos else None
    mapa = [{"id": r[0], "ts": r[1], "autor": r[2], "texto": r[3], "dados": json.loads(r[4] or "{}"), "fav": bool(r[5])} for r in
            c.execute("SELECT id,ts,autor,texto,dados,fav FROM mapa WHERE item_id=? ORDER BY ts DESC LIMIT 100", (item_id,))]
    pos = c.execute("SELECT pagina,posicao,keyword,ts FROM posicao WHERE item_id=?", (item_id,)).fetchone()
    snaps = c.execute("SELECT MIN(dia),COUNT(*) FROM views_hist WHERE item_id=?", (item_id,)).fetchone()
    dest = destaque_item(c, peds, item_id, destaque_loja())
    ult_loja, ult_item = _ultima_venda(c), _ultima_venda(c, peds, item_id)
    cfg_d = _config_get(c, "destaque_desativacao")
    c.close()
    est, dias_est = estado_destaque(ult_loja, cfg_d, hoje)
    dest.update({"estado": est, "dias_estado": dias_est, "desativacao": cfg_d, "ultima_loja": ult_loja, "ultima_item": ult_item,
                 "validacao": validar_comissao(ult_item or ult_loja), "validacao_de": "anúncio" if ult_item else "loja",
                 "ativa": est in ("ativa", "ativa_aguardando", "cobrando_apos_previsto")})
    promos_fim = [p.get("end_time") for p in cat.get("promocoes", []) if p.get("promotion_staging") == "ongoing"]
    v30 = P30["unidades"]
    full = None
    if sbs:
        valido = sbs["vendavel"] + sbs["a_caminho"]
        full = {**sbs, "vel_nossa": round(vel, 2), "ruptura_provavel": ruptura, "media_120d": round(u120 / dias_base, 2), "cobertura": round(valido / vel, 1) if vel > 0 else None, "eh_full": True,
                "sugestao": max(0, math.ceil(painel.META_DIAS * vel - valido)) if vel > 0 else 0, "meta_dias": painel.META_DIAS}
    return {"item": {k: cat.get(k) for k in ("item_id", "titulo", "foto", "full", "status", "nota", "comentarios", "views", "likes", "vendas_total", "modelos", "n_imagens", "video")},
            "link": f"{LOJA_URL}/{sid}/{item_id}", "sku": ", ".join(sorted({m["sku"] for m in cat["modelos"] if m["sku"]})) or "Sem SKU",
            "periodo": {"de": iso(de_d), "ate": iso(ate_d), "dias": n}, "A": A, "B": Bp, "P3": P3, "P4": P4, "p30": P30,
            "rot": {"A": [iso(de_d), iso(ate_d)], "B": [iso(B0), iso(B1)], "P3": [iso(C0), iso(C1)], "P4": [iso(D0), iso(D1_)]},
            "avisos": _avisos(cat, sbs, vel, P30, camps, promos_fim), "pivo": _pivo(cat, ativas), "sim": sim, "full": full,
            "promocoes": cat.get("promocoes", []), "campanhas": camps, "mapa": mapa, "destaque": dest,
            "posicao": {"pagina": pos[0], "posicao": pos[1], "keyword": pos[2], "ts": pos[3]} if pos else None,
            "visitas_desde": snaps[0], "retratos": snaps[1], "hoje": iso(hoje)}


def P7_unid(c, peds, item_id, ontem):
    return _vendas_periodo(peds, (ontem - timedelta(days=6)).isoformat(), ontem.isoformat(), item_id)["unidades"]


# ─── mapa de alterações e posição (gravação local) ───────────────────────────

def registrar_mapa(item_id, autor, texto, dados):
    c = _db()
    c.execute("INSERT INTO mapa(item_id,ts,autor,texto,dados) VALUES(?,?,?,?,?)", (item_id, int(time.time()), autor or "Usuário", texto.strip(), json.dumps(dados or {}, ensure_ascii=False)))
    c.commit()
    c.close()


def favoritar_mapa(mid):
    c = _db()
    c.execute("UPDATE mapa SET fav=1-fav WHERE id=?", (mid,))
    c.commit()
    c.close()


def salvar_posicao(item_id, pagina, posicao, keyword):
    c = _db()
    c.execute("INSERT OR REPLACE INTO posicao VALUES(?,?,?,?,?)", (item_id, pagina, posicao, keyword or "", int(time.time())))
    c.commit()
    c.close()
