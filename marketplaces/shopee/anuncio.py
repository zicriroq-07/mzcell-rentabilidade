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
    for p in peds:
        dia = painel._dia(p["criado"])
        if not (d0 <= dia <= d1):
            continue
        meus = [i for i in p["itens"] if i["item_id"] == item_id]
        if p["status"] in ("CANCELLED", "IN_CANCEL"):
            if (p.get("motivo_cancel") or "") != "Unpaid Order":      # cancelamento automático por falta de pagamento não é da loja
                canc += 1
            continue
        if p["status"] in FINAL_FORA:
            continue
        ped += 1
        for i in meus:
            un += _f(i["qtd"])
            rec += _f(i["preco"]) * _f(i["qtd"])
    return {"unidades": un, "receita": rec, "pedidos": ped, "cancelados": canc}


def _ads_periodo(c, item_id, d0, d1):
    r = c.execute("SELECT COALESCE(SUM(d.imp),0),COALESCE(SUM(d.cliques),0),COALESCE(SUM(d.gasto),0),COALESCE(SUM(d.gmv),0),COALESCE(SUM(d.pedidos),0),"
                  "COUNT(DISTINCT d.dia) FROM ads_dia d JOIN ads_camp a ON a.campaign_id=d.campaign_id WHERE a.item_id=? AND d.dia>=? AND d.dia<=?",
                  (item_id, d0, d1)).fetchone()
    return {"impressoes": r[0], "cliques": r[1], "ads_total": r[2], "receita_publi": r[3], "vads": r[4], "dias_com_dado": r[5]}


def _visitas(c, item_id, d0, d1):
    """Visitas no período = views (último retrato até d1) − views (último retrato antes de d0). None sem os dois retratos."""
    fim = c.execute("SELECT views FROM views_hist WHERE item_id=? AND dia<=? ORDER BY dia DESC LIMIT 1", (item_id, d1)).fetchone()
    ini = c.execute("SELECT views FROM views_hist WHERE item_id=? AND dia<? ORDER BY dia DESC LIMIT 1", (item_id, d0)).fetchone()
    return (fim[0] - ini[0]) if fim and ini else None


def _periodo(c, peds, item_id, d0, d1):
    n = (datetime.fromisoformat(d1) - datetime.fromisoformat(d0)).days + 1
    v, a = _vendas_periodo(peds, d0, d1, item_id), _ads_periodo(c, item_id, d0, d1)
    vis = _visitas(c, item_id, d0, d1)
    rec, g, rp = v["receita"], a["ads_total"], a["receita_publi"]
    o = {**v, **a, "de": d0, "ate": d1, "dias": n, "visitas": vis, "media_dia": v["unidades"] / n if n else 0,
         "tacos": g / rec if rec > 0 else 0, "acos": g / rp if rp > 0 else 0, "roas": rp / g if g > 0 else 0,
         "ctr_c": a["cliques"] / a["impressoes"] if a["impressoes"] else 0, "cpc_c": g / a["cliques"] if a["cliques"] else 0,
         "cvr_ads": a["vads"] / a["cliques"] if a["cliques"] else 0, "vendas_sem_publi": max(0, v["unidades"] - a["vads"]),
         "conv_c": (v["pedidos"] / vis) if vis else None, "preco_c": rec / v["unidades"] if v["unidades"] else 0}
    return o


def _margem_item(c, peds, item_id, d0, d1, base, aliq):
    """Margem do anúncio no período, pelas tarifas do escrow (só pedidos com escrow)."""
    esc = {}
    sns = [p["order_sn"] for p in peds if d0 <= painel._dia(p["criado"]) <= d1 and p["status"] not in FINAL_FORA]
    for k in range(0, len(sns), 400):
        lote = sns[k:k + 400]
        for sn, j in c.execute(f"SELECT order_sn,dados FROM escrow WHERE order_sn IN ({','.join('?' * len(lote))})", lote):
            esc[sn] = json.loads(j)
    t = dict(receita=0.0, comissao=0.0, servico=0.0, frete=0.0, extras=0.0, imposto=0.0, cmv=0.0, unid=0.0, pedidos=0)
    sem_cmv, sem_escrow = False, 0
    for p in peds:
        if p["order_sn"] not in sns:
            continue
        e = esc.get(p["order_sn"])
        if not e or _f(e.get("seller_return_refund")) < 0:
            sem_escrow += 1
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
    camps = [dict(zip(("campaign_id", "nome", "status", "local", "orcamento", "roas_alvo", "inicio", "fim"), r)) for r in
             c.execute("SELECT campaign_id,nome,status,local,orcamento,roas_alvo,inicio,fim FROM ads_camp WHERE item_id=? ORDER BY status='ongoing' DESC, campaign_id DESC", (item_id,))]
    ativas = sum(1 for x in camps if x["status"] == "ongoing")
    alvos = [x["roas_alvo"] for x in camps if x["status"] == "ongoing" and x["roas_alvo"]]
    sbs_all = sbs_estoque(sid)
    sbs = sbs_all.get(item_id)
    vel = 0.4 * P7_unid(c, peds, item_id, ontem) / 7 + 0.6 * P30["unidades"] / 30
    preco_atual = next((m["preco"] for m in cat["modelos"] if m.get("preco")), 0)
    sim = _custos_reais(c, peds, item_id, base, aliq, preco_atual)
    sim["tacos30"] = P30["tacos"]
    sim["roas_objetivo"] = alvos[0] if alvos else None
    mapa = [{"id": r[0], "ts": r[1], "autor": r[2], "texto": r[3], "dados": json.loads(r[4] or "{}"), "fav": bool(r[5])} for r in
            c.execute("SELECT id,ts,autor,texto,dados,fav FROM mapa WHERE item_id=? ORDER BY ts DESC LIMIT 100", (item_id,))]
    pos = c.execute("SELECT pagina,posicao,keyword,ts FROM posicao WHERE item_id=?", (item_id,)).fetchone()
    snaps = c.execute("SELECT MIN(dia),COUNT(*) FROM views_hist WHERE item_id=?", (item_id,)).fetchone()
    c.close()
    promos_fim = [p.get("end_time") for p in cat.get("promocoes", []) if p.get("promotion_staging") == "ongoing"]
    v30 = P30["unidades"]
    full = None
    if sbs:
        valido = sbs["vendavel"] + sbs["a_caminho"]
        full = {**sbs, "vel_nossa": round(vel, 2), "cobertura": round(valido / vel, 1) if vel > 0 else None, "eh_full": True,
                "sugestao": max(0, math.ceil(painel.META_DIAS * vel - valido)) if vel > 0 else 0, "meta_dias": painel.META_DIAS}
    return {"item": {k: cat.get(k) for k in ("item_id", "titulo", "foto", "full", "status", "nota", "comentarios", "views", "likes", "vendas_total", "modelos", "n_imagens", "video")},
            "link": f"{LOJA_URL}/{sid}/{item_id}", "sku": ", ".join(sorted({m["sku"] for m in cat["modelos"] if m["sku"]})) or "Sem SKU",
            "periodo": {"de": iso(de_d), "ate": iso(ate_d), "dias": n}, "A": A, "B": Bp, "P3": P3, "P4": P4, "p30": P30,
            "rot": {"A": [iso(de_d), iso(ate_d)], "B": [iso(B0), iso(B1)], "P3": [iso(C0), iso(C1)], "P4": [iso(D0), iso(D1_)]},
            "avisos": _avisos(cat, sbs, vel, P30, camps, promos_fim), "pivo": _pivo(cat, ativas), "sim": sim, "full": full,
            "promocoes": cat.get("promocoes", []), "campanhas": camps, "mapa": mapa,
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
