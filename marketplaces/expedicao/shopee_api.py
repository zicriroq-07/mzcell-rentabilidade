"""
marketplaces/expedicao/shopee_api.py — Pedidos da Shopee para a Expedição, lidos direto da API da Shopee (sem Bling)
MZ App

Somente leitura. Entram só os pedidos que o galpão despacha: transportadora "Shopee Xpress" (coleta) e "Entrega Direta"
(Flex). Pedido "Full" (FBS) sai do estoque da Shopee e não passa pelo galpão.

Estados da Shopee usados:
  READY_TO_SHIP  pedido pago, etiqueta ainda não gerada          -> pendente
  PROCESSED      etiqueta gerada, aguardando a coleta/entrega     -> pendente até a marcação de embalado/checkout
  SHIPPED        já com a transportadora (hoje)                   -> feito (checkout automático)
O prazo de despacho (ship_by_date) vai em `prazo_ml` (mesmo campo do ML, só informativo); o horário de corte do painel vem
de cortes.py (Shopee Entrega Direta 12:00, Shopee Xpress 13:00).
"""

import time
from datetime import datetime, timedelta, timezone

from marketplaces.expedicao import cortes
from marketplaces.shopee import api, lojas

BRT = timezone(timedelta(hours=-3))
LOJA = "techseller"
CANAIS = {"shopee xpress": "Shopee Xpress", "entrega direta": "Entrega Direta"}
JANELA_DIAS = 15          # pedidos em aberto mais antigos que isso já estão fora de prazo e fora desta tela
CAMPOS = "item_list,shipping_carrier,checkout_shipping_carrier,ship_by_date,days_to_ship,package_list,create_time,order_status,update_time"


def _meta(canal: str, hoje) -> str:
    hhmm = next((c["corte"] for c in cortes.carregar() if c.get("plataforma") == "Shopee" and c.get("canal") == canal), "12:00")
    h, m = hhmm.split(":")
    return datetime(hoje.year, hoje.month, hoje.day, int(h), int(m), tzinfo=BRT).isoformat()


def _listar(sid: int, status: str, campo: str, de: int, ate: int) -> list:
    sns, cursor = [], ""
    while True:
        p = {"time_range_field": campo, "time_from": de, "time_to": ate, "page_size": 100, "order_status": status}
        if cursor:
            p["cursor"] = cursor
        r = api.chamar("/api/v2/order/get_order_list", sid, p).get("response") or {}
        sns += [o["order_sn"] for o in r.get("order_list") or []]
        if not r.get("more"):
            return sns
        cursor = r.get("next_cursor") or ""


def _detalhes(sid: int, sns: list) -> list:
    out = []
    for i in range(0, len(sns), 50):
        r = api.chamar("/api/v2/order/get_order_detail", sid, {"order_sn_list": ",".join(sns[i:i + 50]), "response_optional_fields": CAMPOS})
        out += (r.get("response") or {}).get("order_list") or []
    return out


_bipes = {}          # order_sn -> epoch do bipe na coleta (não muda depois de feito): uma consulta por pedido


def _bipe_em(sid: int, o: dict):
    """Quando a transportadora bipou o pacote (PICKED_UP no rastreio). None se ainda não bipado ou se a Shopee não informar."""
    sn = o["order_sn"]
    if sn in _bipes:
        return _bipes[sn]
    quando = None
    try:
        pk = (o.get("package_list") or [{}])[0]
        r = api.chamar("/api/v2/logistics/get_tracking_info", sid, {"order_sn": sn, "package_number": pk.get("package_number")}).get("response") or {}
        ts = [e["update_time"] for e in r.get("tracking_info") or [] if e.get("logistics_status") == "PICKED_UP"]
        quando = min(ts) if ts else None
    except Exception:
        return None                      # falha de rede: tenta de novo na próxima leitura
    _bipes[sn] = quando or o.get("update_time")
    return _bipes[sn]


def _linha(o: dict, canal: str, hoje, bipe_ts=None, agora=None) -> dict:
    itens = [{"mlb": None, "sku": i.get("model_sku") or i.get("item_sku"), "titulo": i.get("item_name"), "qtd": i.get("model_quantity_purchased")}
             for i in o.get("item_list") or []]
    enviado = o.get("order_status") == "SHIPPED"
    ship_by = o.get("ship_by_date")
    agora_ts = (agora or datetime.now(BRT)).timestamp()
    # dois indicadores: checkout = nossas etapas (Bling Verificado ou marca do galpão); bipe = a coleta bipou o pacote
    fora = bool(ship_by and ((bipe_ts and bipe_ts > ship_by) or (not enviado and agora_ts > ship_by)))
    return {"conta": "shopee", "canal": canal, "logistica": o.get("shipping_carrier"), "pedido": o["order_sn"], "pacote": None, "envio": o["order_sn"],
            "unidades": sum(int(i["qtd"] or 0) for i in itens), "status": "shopee", "substatus": o.get("order_status"),
            "situacao": {"READY_TO_SHIP": "Etiqueta a gerar", "PROCESSED": "Etiqueta gerada", "SHIPPED": "Com a transportadora"}.get(o.get("order_status"), o.get("order_status")),
            "meta": _meta(canal, hoje), "prazo_ml": datetime.fromtimestamp(ship_by, BRT).isoformat() if ship_by else None,
            "itens": itens, "bling_numero": None, "bling_situacao": None, "bling_checkout": False, "bipado": enviado,
            "bipe_em": datetime.fromtimestamp(bipe_ts, BRT).isoformat() if bipe_ts else None, "bipe_fora_do_prazo": fora}


def linhas(agora: datetime = None, loja: str = LOJA) -> tuple:
    """(linhas de hoje, erros): todo pedido em aberto (etiqueta a gerar ou gerada) e os que foram à transportadora hoje."""
    agora = agora or datetime.now(BRT)
    hoje = agora.date()
    try:
        sid = lojas.shop_id_da(loja)
        t = int(time.time())
        inicio_dia = int(datetime(hoje.year, hoje.month, hoje.day, tzinfo=BRT).timestamp())
        abertos = _listar(sid, "READY_TO_SHIP", "create_time", t - JANELA_DIAS * 86400, t) + _listar(sid, "PROCESSED", "create_time", t - JANELA_DIAS * 86400, t)
        enviados = _listar(sid, "SHIPPED", "update_time", inicio_dia, t)
        pedidos = _detalhes(sid, list(dict.fromkeys(abertos + enviados)))
    except Exception as e:
        return [], {"shopee": f"{type(e).__name__}: {api.mascarar(str(e))[:120]}"}
    saida, outros = [], set()
    for o in pedidos:
        canal = CANAIS.get(str(o.get("shipping_carrier") or "").strip().lower())
        if not canal:
            outros.add(str(o.get("shipping_carrier")))      # "Full" e afins: fora do galpão
            continue
        if o.get("order_status") not in ("READY_TO_SHIP", "PROCESSED", "SHIPPED"):
            continue                                         # já entregue, em devolução ou cancelado: não é trabalho do galpão
        if o.get("order_status") == "SHIPPED" and datetime.fromtimestamp(o.get("update_time") or 0, BRT).date() != hoje:
            continue
        saida.append(_linha(o, canal, hoje, _bipe_em(sid, o) if o.get("order_status") == "SHIPPED" else None, agora))
    return saida, {}
