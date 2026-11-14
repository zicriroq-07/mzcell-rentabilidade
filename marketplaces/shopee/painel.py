"""
marketplaces/shopee/painel.py — Motor do painel Shopee (cache local + agregação). SOMENTE LEITURA na Shopee.
MZ App

- Cache SQLite (shopee.db, gitignored): pedidos (sem dados de comprador) e escrow (order_income) por order_sn.
- sincronizar(): lista pedidos por janelas de 15 dias, busca detalhes em lotes de 50 e o escrow dos concluídos.
  Roda em thread; o painel mostra o progresso.
- dados(): agrega vendas, margem por SKU (CMV da base do Grupo, imposto da Techseller), Ads, Full e conta.
CMV vem de cmv_base.json (nunca do Bling). Imposto de impostos.json (shopee|unica).
"""

import json
import math
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from datetime import datetime, timedelta, timezone

from marketplaces.shopee import api, lojas
from marketplaces.shopee.lojas import ErroShopee

DB = os.path.join(lojas.BASE_DIR, "shopee.db")
BRT = timezone(timedelta(hours=-3))
DIA = 86400
META_DIAS = 20
FINAIS = {"COMPLETED", "CANCELLED"}
CAMPOS_PEDIDO = "item_list,pay_time,total_amount,actual_shipping_fee,estimated_shipping_fee,payment_method,cancel_by,cancel_reason,fulfillment_flag,package_list"

_estado = {"rodando": False, "fase": "", "feito": 0, "total": 0, "erro": "", "inicio": None, "fim": None}
_trava = threading.Lock()
_cache_vivo = {}


# ─── armazenamento ───────────────────────────────────────────────────────────

def _db():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS pedidos(order_sn TEXT PRIMARY KEY, criado INT, status TEXT, buscado INT, dados TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS escrow(order_sn TEXT PRIMARY KEY, buscado INT, dados TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_ped_criado ON pedidos(criado)")
    return c


def _enxuto_pedido(o):
    """Só o necessário; descarta comprador, endereço e observações."""
    itens = [{"item_id": i.get("item_id"), "model_id": i.get("model_id"), "item_sku": i.get("item_sku") or "",
              "model_sku": i.get("model_sku") or "", "nome": (i.get("item_name") or "")[:90],
              "qtd": i.get("model_quantity_purchased") or 0, "preco": i.get("model_discounted_price") or 0,
              "preco_cheio": i.get("model_original_price") or 0} for i in o.get("item_list", [])]
    return {"order_sn": o.get("order_sn"), "status": o.get("order_status"), "criado": o.get("create_time"),
            "pago": o.get("pay_time"), "total": o.get("total_amount"), "frete_real": o.get("actual_shipping_fee"),
            "cancelado_por": o.get("cancel_by"), "motivo_cancel": o.get("cancel_reason"),
            "full": o.get("fulfillment_flag"), "itens": itens}


def estado():
    with _trava:
        e = dict(_estado)
    c = _db()
    e["pedidos_cache"] = c.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0]
    e["escrow_cache"] = c.execute("SELECT COUNT(*) FROM escrow").fetchone()[0]
    c.close()
    return e


# ─── sincronização ───────────────────────────────────────────────────────────

def _set(**kw):
    with _trava:
        _estado.update(kw)


def sincronizar(dias: int = 7, loja: str = "techseller"):
    """Dispara a sincronização em segundo plano. Devolve False se já há uma rodando."""
    with _trava:
        if _estado["rodando"]:
            return False
        _estado.update(rodando=True, fase="iniciando", feito=0, total=0, erro="", inicio=time.time(), fim=None)
    threading.Thread(target=_rodar, args=(int(dias), loja), daemon=True).start()
    return True


def _rodar(dias, loja):
    try:
        sid = lojas.shop_id_da(loja)
        agora = datetime.now()
        # 1) lista de pedidos
        _set(fase="listando pedidos")
        sns = []
        for de, ate in api.janelas(agora - timedelta(days=dias), agora):
            cur = ""
            while True:
                r = api.chamar("/api/v2/order/get_order_list", sid, {"time_range_field": "create_time", "time_from": de,
                               "time_to": ate, "page_size": 100, "cursor": cur}).get("response", {})
                sns += [o["order_sn"] for o in r.get("order_list", [])]
                if not r.get("more"):
                    break
                cur = r.get("next_cursor", "")
        sns = list(dict.fromkeys(sns))
        # 2) detalhes dos que faltam (ou ainda não finais e velhos de cache)
        c = _db()
        conhecidos = {r[0]: (r[1], r[2]) for r in c.execute("SELECT order_sn,status,buscado FROM pedidos")}
        falta = [s for s in sns if s not in conhecidos or (conhecidos[s][0] not in FINAIS and time.time() - conhecidos[s][1] > 6 * 3600)]
        _set(fase="detalhando pedidos", feito=0, total=len(falta))
        for i in range(0, len(falta), 50):
            lote = falta[i:i + 50]
            r = api.chamar("/api/v2/order/get_order_detail", sid, {"order_sn_list": ",".join(lote),
                           "response_optional_fields": CAMPOS_PEDIDO}).get("response", {})
            for o in r.get("order_list", []):
                p = _enxuto_pedido(o)
                c.execute("INSERT OR REPLACE INTO pedidos VALUES(?,?,?,?,?)", (p["order_sn"], p["criado"], p["status"], int(time.time()),
                                                                              json.dumps(p, ensure_ascii=False)))
            c.commit()
            _set(feito=min(i + 50, len(falta)))
        # 3) escrow (tarifas e frete) de todo pedido pago e não cancelado. Enquanto não concluído é estimativa e é refeito.
        feitos = {r[0]: r[1] for r in c.execute("SELECT order_sn,buscado FROM escrow")}
        cand = [(r[0], r[1]) for r in c.execute("SELECT order_sn,status FROM pedidos WHERE criado>=? AND status NOT IN ('CANCELLED','IN_CANCEL','UNPAID')",
                                                (int(time.time()) - dias * DIA,))]
        pend = [sn for sn, st in cand if sn not in feitos or (st != "COMPLETED" and time.time() - feitos[sn] > 6 * 3600)]
        _set(fase="buscando tarifas e frete (escrow)", feito=0, total=len(pend))
        def _um(sn):
            try:
                r = api.chamar("/api/v2/payment/get_escrow_detail", sid, {"order_sn": sn}).get("response", {})
                return sn, json.dumps(r.get("order_income", {}), ensure_ascii=False)
            except ErroShopee:
                return sn, None
        with ThreadPoolExecutor(max_workers=6) as ex:
            for n, (sn, js) in enumerate(ex.map(_um, pend), 1):
                if js is not None:
                    c.execute("INSERT OR REPLACE INTO escrow VALUES(?,?,?)", (sn, int(time.time()), js))
                if n % 25 == 0:
                    c.commit()
                _set(feito=n)
        c.commit()
        c.close()
        _cache_vivo.clear()
        _set(fase="concluído", rodando=False, fim=time.time())
    except Exception as e:                      # nunca derruba o servidor; mostra o motivo mascarado
        _set(fase="erro", erro=api.mascarar(str(e))[:300], rodando=False, fim=time.time())


# ─── agregação ───────────────────────────────────────────────────────────────

def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _aliquota():
    try:
        import impostos
        return float(impostos.carregar()["itens"]["shopee|unica"]["aliquota"])
    except Exception:
        return 0.0


def _cmv(sku, base):
    x = base.get(str(sku or "").strip().upper())
    return x["cmv"] if x else None


def _base_cmv():
    try:
        import cmv_base
        return cmv_base.carregar().get("itens", {})
    except Exception:
        return {}


def _dia(ts):
    return datetime.fromtimestamp(ts, BRT).strftime("%Y-%m-%d")


def vendas(dias: int):
    """Margem de TODOS os pedidos pagos e não cancelados (tarifas do escrow, estimadas até o pedido concluir).
    Receita líquida = preço cheio − desconto do vendedor − cupom do vendedor. O repasse não entra: é do financeiro."""
    c = _db()
    corte = int(time.time()) - dias * DIA
    peds = [json.loads(r[0]) for r in c.execute("SELECT dados FROM pedidos WHERE criado>=?", (corte,))]
    esc = {r[0]: json.loads(r[1]) for r in c.execute("SELECT order_sn,dados FROM escrow")}
    c.close()
    aliq, base = _aliquota(), _base_cmv()
    por_status, por_dia, por_sku = {}, {}, {}
    z = dict(pedidos=0, unidades=0, preco_cheio=0.0, desc_vendedor=0.0, cupom_vendedor=0.0, receita=0.0, comissao=0.0, servico=0.0,
             frete=0.0, extras=0.0, repasse=0.0, receita_estimada=0.0)
    tot = {**z, "cancelados": 0, "em_devolucao": 0, "nao_pagos": 0, "sem_tarifas": 0, "total_pedidos": len(peds)}
    for p in peds:
        st = p["status"]
        por_status[st] = por_status.get(st, 0) + 1
        if st in ("CANCELLED", "IN_CANCEL"):
            tot["cancelados"] += 1
            continue
        if st == "UNPAID":
            tot["nao_pagos"] += 1
            continue
        if st in ("TO_RETURN", "RETURNED"):
            tot["em_devolucao"] += 1
            continue
        e = esc.get(p["order_sn"])
        if not e:
            tot["sem_tarifas"] += 1
            continue
        bruto_itens = sum(_f(i["preco"]) * _f(i["qtd"]) for i in p["itens"]) or 1
        cheio = _f(e.get("order_original_price")) or bruto_itens
        desc = _f(e.get("order_seller_discount"))
        cupom = _f(e.get("voucher_from_seller"))
        rl = _f(e.get("order_selling_price")) - cupom
        comissao = _f(e.get("commission_fee"))
        servico = (_f(e.get("net_service_fee")) if st == "COMPLETED" and e.get("net_service_fee") is not None else (_f(e.get("service_fee")) or _f(e.get("net_service_fee"))))   # concluído: líquida (o que foi cobrado); antes: estimativa
        frete = (_f(e.get("actual_shipping_fee")) or _f(e.get("estimated_shipping_fee"))) - _f(e.get("buyer_paid_shipping_fee")) - _f(e.get("shopee_shipping_rebate"))   # real só existe depois de concluir; antes, o estimado
        fbs, camp = _f(e.get("fbs_fee")), _f(e.get("campaign_fee"))
        repasse = _f(e.get("escrow_amount"))
        extras = fbs + camp + (rl - comissao - servico - frete - fbs - camp - repasse)   # taxa Full + campanha + outras/ajustes
        unid = sum(_f(i["qtd"]) for i in p["itens"])
        for k, v in (("preco_cheio", cheio), ("desc_vendedor", desc), ("cupom_vendedor", cupom), ("receita", rl), ("comissao", comissao),
                     ("servico", servico), ("frete", frete), ("extras", extras), ("repasse", repasse), ("unidades", unid)):
            tot[k] += v
        tot["pedidos"] += 1
        if st != "COMPLETED":
            tot["receita_estimada"] += rl
        d = por_dia.setdefault(_dia(p["criado"]), {"pedidos": 0, "receita": 0.0})
        d["pedidos"] += 1
        d["receita"] += rl
        for it in p["itens"]:
            sku = it["model_sku"] or it["item_sku"] or f"(sem SKU) {it['item_id']}"
            part = _f(it["preco"]) * _f(it["qtd"]) / bruto_itens
            x = por_sku.setdefault(sku, {"sku": sku, "nome": it["nome"], "unid": 0.0, "receita": 0.0, "comissao": 0.0, "servico": 0.0,
                                          "frete": 0.0, "extras": 0.0, "desc": 0.0})
            x["unid"] += _f(it["qtd"])
            x["receita"] += rl * part
            x["comissao"] += comissao * part
            x["servico"] += servico * part
            x["frete"] += frete * part
            x["extras"] += extras * part
            x["desc"] += (desc + cupom) * part
    cmv_t = imp_t = marg_t = rec_m = sem_cmv = 0.0
    linhas = []
    for x in por_sku.values():
        cm = _cmv(x["sku"], base)
        x["cmv_unit"] = cm
        x["imposto"] = aliq * x["receita"]
        if cm is None:
            x["cmv"], x["margem"], x["margem_pct"] = None, None, None
            sem_cmv += x["receita"]
        else:
            x["cmv"] = cm * x["unid"]
            x["margem"] = x["receita"] - x["comissao"] - x["servico"] - x["frete"] - x["extras"] - x["imposto"] - x["cmv"]
            x["margem_pct"] = x["margem"] / x["receita"] * 100 if x["receita"] else None
            cmv_t += x["cmv"]
            imp_t += x["imposto"]
            marg_t += x["margem"]
            rec_m += x["receita"]
        linhas.append(x)
    tot.update(cmv=cmv_t, imposto=imp_t, margem=marg_t, receita_com_cmv=rec_m, receita_sem_cmv=sem_cmv)
    linhas.sort(key=lambda x: -x["receita"])
    return {"dias": dias, "aliquota": aliq, "totais": tot, "por_status": por_status,
            "por_dia": [{"dia": k, **v} for k, v in sorted(por_dia.items())], "skus": linhas}


# ─── dados ao vivo (cache 10 min) ────────────────────────────────────────────

def _vivo(chave, fn, ttl=600):
    x = _cache_vivo.get(chave)
    if x and time.time() - x[0] < ttl:
        return x[1]
    v = fn()
    _cache_vivo[chave] = (time.time(), v)
    return v


def _safe(fn, padrao):
    try:
        return fn()
    except ErroShopee as e:
        return padrao if not isinstance(padrao, dict) else {**padrao, "erro": str(e)[:160]}


def _estoque_linhas(sid):
    """Uma linha por SKU (modelo) com estoque do vendedor, Full, reservado e a caminho."""
    r = api.chamar("/api/v2/product/get_item_list", sid, {"offset": 0, "page_size": 100, "item_status": "NORMAL"}).get("response", {})
    ids = [i["item_id"] for i in r.get("item", [])]
    linhas = []
    for k in range(0, len(ids), 50):
        lote = ids[k:k + 50]
        base = api.chamar("/api/v2/product/get_item_base_info", sid, {"item_id_list": ",".join(map(str, lote))}).get("response", {}).get("item_list", [])
        extra = {i["item_id"]: i for i in api.chamar("/api/v2/product/get_item_extra_info", sid,
                 {"item_id_list": ",".join(map(str, lote))}).get("response", {}).get("item_list", [])}
        for it in base:
            ex = extra.get(it["item_id"], {})
            comum = {"item_id": it["item_id"], "nome": it.get("item_name", "")[:90], "full": bool(it.get("is_fulfillment_by_shopee")),
                     "avaliacao": ex.get("rating_star"), "vendas_total": ex.get("sale"), "visualizacoes": ex.get("views"),
                     "comentarios": ex.get("comment_count")}
            fontes = []
            if it.get("has_model"):
                for m in api.chamar("/api/v2/product/get_model_list", sid, {"item_id": it["item_id"]}).get("response", {}).get("model", []):
                    fontes.append((m.get("model_id"), m.get("model_sku") or it.get("item_sku") or "", m.get("model_name", ""),
                                   m.get("price_info"), m.get("stock_info_v2")))
            else:
                fontes.append((0, it.get("item_sku") or "", "", it.get("price_info"), it.get("stock_info_v2")))
            for mid, sku, mnome, preco, est in fontes:
                est = est or {}
                resumo = est.get("summary_info") or {}
                full_est = sum(_f(x.get("stock")) for x in est.get("shopee_stock", []))
                adv = est.get("advance_stock") or {}
                p = (preco or [{}])[0] if isinstance(preco, list) else (preco or {})
                linhas.append({**comum, "model_id": mid, "sku": sku, "variacao": mnome,
                               "preco": p.get("current_price") or p.get("original_price"),
                               "est_vendedor": sum(_f(x.get("stock")) for x in est.get("seller_stock", [])),
                               "est_full": full_est, "reservado": _f(resumo.get("total_reserved_stock")),
                               "a_caminho": _f(adv.get("in_transit_advance_stock")),
                               "full_vendavel": _f(adv.get("sellable_advance_stock"))})
    return linhas


def estoque_e_previsao(sid, dias_hist=30):
    linhas = _vivo("estoque", lambda: _estoque_linhas(sid))
    c = _db()
    agora = int(time.time())
    v7, v30 = {}, {}
    for (dados,) in c.execute("SELECT dados FROM pedidos WHERE criado>=? AND status NOT IN ('CANCELLED','IN_CANCEL')", (agora - 30 * DIA,)):
        p = json.loads(dados)
        for i in p["itens"]:
            k = (i["item_id"], i["model_id"] or 0)
            v30[k] = v30.get(k, 0) + _f(i["qtd"])
            if p["criado"] >= agora - 7 * DIA:
                v7[k] = v7.get(k, 0) + _f(i["qtd"])
    c.close()
    out = []
    for l in linhas:
        k = (l["item_id"], l["model_id"] or 0)
        a7, a30 = v7.get(k, 0), v30.get(k, 0)
        vel = 0.4 * a7 / 7 + 0.6 * a30 / 30
        valido = l["est_full"] + l["a_caminho"]
        sug = max(0, math.ceil(META_DIAS * vel - valido)) if l["full"] else None
        out.append({**l, "v7": a7, "v30": a30, "vel_dia": round(vel, 2),
                    "cobertura_dias": round(valido / vel, 1) if (l["full"] and vel > 0) else None,
                    "sugestao_full": sug})
    return out


def ads(sid, dias):
    fmt = lambda t: time.strftime("%d-%m-%Y", time.localtime(t))
    agora = int(time.time())
    de, ate = fmt(agora - min(dias, 30) * DIA), fmt(agora - DIA)
    diario = _safe(lambda: api.chamar("/api/v2/ads/get_all_cpc_ads_daily_performance", sid,
                                      {"start_date": de, "end_date": ate}).get("response", []), [])
    saldo = _safe(lambda: api.chamar("/api/v2/ads/get_total_balance", sid).get("response", {}).get("total_balance"), None)
    camps = _safe(lambda: api.chamar("/api/v2/ads/get_product_level_campaign_id_list", sid,
                                     {"ad_type": "all", "offset": 0, "limit": 50}).get("response", {}).get("campaign_list", []), [])
    return {"periodo": [de, ate], "saldo": saldo, "diario": diario if isinstance(diario, list) else [],
            "campanhas": camps if isinstance(camps, list) else []}


def promocoes(sid):
    desc = []
    for st in ("ongoing", "upcoming"):
        r = _safe(lambda: api.chamar("/api/v2/discount/get_discount_list", sid, {"discount_status": st, "page_no": 1, "page_size": 20}).get("response", {}), {})
        for d in r.get("discount_list", []):
            desc.append({"id": d.get("discount_id"), "nome": d.get("discount_name"), "status": st,
                         "inicio": d.get("start_time"), "fim": d.get("end_time")})
    return desc


def conta(sid):
    perf = _safe(lambda: api.chamar("/api/v2/account_health/get_shop_performance", sid).get("response", {}), {})
    atrasados = _safe(lambda: api.chamar("/api/v2/account_health/get_late_orders", sid, {"page_no": 1, "page_size": 10}).get("response", {}), {})
    agora = int(time.time())
    devs = _safe(lambda: api.chamar("/api/v2/returns/get_return_list", sid, {"page_no": 0, "page_size": 50,
                 "create_time_from": agora - 30 * DIA, "create_time_to": agora}).get("response", {}), {})
    return {"geral": perf.get("overall_performance"), "metricas": perf.get("metric_list", [])[:20],
            "atrasados": atrasados.get("total_count", 0), "devolucoes_30d": len(devs.get("return", []))}


def _tacos(v, a):
    """TACoS = gasto com Ads ÷ receita líquida do mesmo período (dias do Ads, que terminam ontem)."""
    gasto, dias = 0.0, set()
    for d in a.get("diario", []):
        dd, mm, aa = str(d.get("date")).split("-")
        dias.add(f"{aa}-{mm}-{dd}")
        gasto += _f(d.get("expense"))
    rec = sum(x["receita"] for x in v["por_dia"] if x["dia"] in dias)
    return {"gasto": gasto, "receita_periodo": rec, "tacos_pct": gasto / rec * 100 if rec else None}


def _morno(chave, vazio):
    x = _cache_vivo.get(chave)
    return x[1] if x else vazio


def dados(dias: int = 7, loja: str = "techseller", vivo: bool = True):
    """vivo=False devolve só o que está em cache local (vendas) e o que já foi buscado antes na Shopee; resposta imediata."""
    sid = lojas.shop_id_da(loja)
    v = vendas(dias)
    a = _vivo(f"ads{dias}", lambda: ads(sid, dias)) if vivo else _morno(f"ads{dias}", {"diario": [], "campanhas": [], "saldo": None})
    t = _tacos(v, a)
    taxa = (t["tacos_pct"] or 0) / 100
    for x in v["skus"]:
        x["ads"] = taxa * x["receita"] if x["margem"] is not None else None
        x["margem_pos_ads"] = x["margem"] - x["ads"] if x["margem"] is not None else None
        x["margem_pos_ads_pct"] = x["margem_pos_ads"] / x["receita"] * 100 if x["margem_pos_ads"] is not None and x["receita"] else None
    v["totais"]["ads_alocado"] = sum(x["ads"] or 0 for x in v["skus"])
    v["totais"]["margem_pos_ads"] = v["totais"]["margem"] - v["totais"]["ads_alocado"]
    return {"loja": loja, "gerado_em": datetime.now(BRT).isoformat(timespec="seconds"), "sync": estado(),
            "vendas": v, "tacos": t,
            "estoque": _safe(lambda: estoque_e_previsao(sid), []) if vivo else (estoque_e_previsao(sid) if "estoque" in _cache_vivo else []),
            "ads": a,
            "promocoes": _vivo("promo", lambda: promocoes(sid)) if vivo else _morno("promo", []),
            "conta": _vivo("conta", lambda: conta(sid)) if vivo else _morno("conta", {}),
            "completo": vivo,
            "meta_dias": META_DIAS}
