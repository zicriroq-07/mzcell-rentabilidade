"""
marketplaces/expedicao/amazon_flash.py — Amazon Flash (entrega no mesmo dia / dia seguinte pelo vendedor) para a Expedição
MZ App

PREPARADO, MAS DESLIGADO: o módulo "amazon_flash" em modulos_liberados.py vem False. Abrir só na máquina que for operar
(modulos_locais.json {"amazon_flash": true}) depois de a Amazon ativar a modalidade para a conta.

Somente leitura. Mesma ideia do Flex do Mercado Livre e da Entrega Direta da Shopee: o pedido é do vendedor (MFN), o galpão
separa e embala, e a Amazon cobra a entrega dentro do prazo curto (LatestShipDate). Não é Easy Ship/DBA (que tem coleta).

COMO RECONHECER UM PEDIDO FLASH — AINDA NÃO CONFIRMADO (em 16/11/2026 não havia nenhum pedido Flash; todos os MFN eram
"Std EZ Local/Regional/National", ou seja, DBA). A regra abaixo é provisória e fica em um lugar só:
  - ShipServiceLevel contém um dos TERMOS (sem diferenciar maiúsculas), ou
  - ShipmentServiceLevelCategory é uma das CATEGORIAS (a Amazon usa SameDay/NextDay/Expedited para entrega acelerada).
Quando o primeiro pedido Flash existir: `python -m marketplaces.expedicao.amazon_flash descobrir` lista os valores reais
de ShipServiceLevel/Category dos últimos 30 dias; ajustar TERMOS/CATEGORIAS conforme o que aparecer e conferir no Seller Central.

CAMPOS DA LINHA (iguais aos do DBA e da Shopee, para os cartões e a bipagem reaproveitarem tudo):
  canal "Flash", conta amazon01/amazon02, meta = corte do dia (cortes.py, plataforma "Amazon" canal "Flash"),
  prazo_ml = LatestShipDate (prazo da Amazon), bipado = pedido já com status Shipped (provisório: a confirmar se a Amazon
  expõe a coleta/entrega do Flash), checkout vem do Bling (numeroLoja = AmazonOrderId), como nas outras plataformas.
"""

import sys
import time
from datetime import datetime, timedelta, timezone

from marketplaces.amazon import api
from marketplaces.expedicao import cortes

BRT = timezone(timedelta(hours=-3))
CONTAS = ("amazon01", "amazon02")
TERMOS = ("flash", "same day", "sameday", "next day", "nextday")
CATEGORIAS = {"SameDay", "NextDay", "Expedited", "ScheduledDelivery"}
DIAS_BUSCA = 5
CACHE_SEG = 60
ABERTOS = ("Unshipped", "PartiallyShipped")
_cache = {}


def ativo() -> bool:
    try:
        from modulos_liberados import liberado
        return liberado("amazon_flash")
    except ImportError:
        return False


def eh_flash(o: dict) -> bool:
    nivel = str(o.get("ShipServiceLevel") or "").lower()
    return any(t in nivel for t in TERMOS) or o.get("ShipmentServiceLevelCategory") in CATEGORIAS


def _dt(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(BRT)
    except Exception:
        return None


def _meta(hoje) -> str:
    hhmm = next((c["corte"] for c in cortes.carregar() if c.get("plataforma") == "Amazon" and c.get("canal") == "Flash"), "12:00")
    h, m = hhmm.split(":")
    return datetime(hoje.year, hoje.month, hoje.day, int(h), int(m), tzinfo=BRT).isoformat()


def linha(conta: str, o: dict, hoje):
    """Linha do painel para um pedido Flash; None se não for Flash ou se estiver cancelado/pendente de pagamento."""
    if o.get("FulfillmentChannel") != "MFN" or o.get("OrderStatus") in ("Canceled", "Pending", "Unfulfillable") or not eh_flash(o):
        return None
    limite = _dt(o.get("LatestShipDate"))
    enviado = o.get("OrderStatus") == "Shipped"
    un = (o.get("NumberOfItemsUnshipped") or 0) + (o.get("NumberOfItemsShipped") or 0)
    return {"conta": conta, "canal": "Flash", "logistica": o.get("ShipServiceLevel"), "pedido": o.get("AmazonOrderId"), "pacote": None,
            "envio": o.get("AmazonOrderId"), "unidades": un or 1, "status": "amazon", "substatus": o.get("OrderStatus"),
            "situacao": "Enviado" if enviado else "A separar e enviar", "meta": _meta(hoje), "prazo_ml": limite.isoformat() if limite else None,
            "itens": [], "bling_numero": None, "bling_situacao": None, "bling_checkout": False, "bipado": enviado,
            "bipe_em": None, "bipe_fora_do_prazo": bool(limite and not enviado and datetime.now(BRT) > limite)}


def _pedidos(conta: str):
    desde = (datetime.now(timezone.utc) - timedelta(days=DIAS_BUSCA)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"MarketplaceIds": api.MARKETPLACE_BR, "FulfillmentChannels": "MFN", "MaxResultsPerPage": "100", "CreatedAfter": desde}
    for _ in range(8):
        status, corpo = api.get(conta, "/orders/v0/orders", **params)
        if status != 200:
            raise api.falha(status, corpo)
        payload = corpo.get("payload") or {}
        yield from payload.get("Orders") or []
        if not payload.get("NextToken"):
            return
        params = {"MarketplaceIds": api.MARKETPLACE_BR, "NextToken": payload["NextToken"]}
        time.sleep(2)


def linhas(agora: datetime = None, contas=CONTAS) -> tuple:
    """(linhas, erros). Desligado (módulo fechado): devolve vazio sem chamar a Amazon. Cache de 60 s por conta."""
    if not ativo():
        return [], {}
    hoje = (agora or datetime.now(BRT)).date()
    saida, erros = [], {}
    for c in contas:
        t = time.time()
        if c in _cache and t - _cache[c][0] < CACHE_SEG:
            saida += _cache[c][1]
            continue
        try:
            if not api.autenticado(c):
                raise RuntimeError("sem credenciais completas")
            ls = [x for x in (linha(c, o, hoje) for o in _pedidos(c)) if x]
            _cache[c] = (t, ls)
            saida += ls
        except Exception as e:
            erros[f"flash:{c}"] = f"Amazon Flash: {type(e).__name__}: {api.mascarar(str(e))[:100]}"
    return saida, erros


def descobrir(dias: int = 30):
    """Mostra os valores reais de ShipServiceLevel/Category dos pedidos MFN (para confirmar a regra do Flash)."""
    import collections
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for conta in CONTAS:
        if not api.autenticado(conta):
            continue
        c = collections.Counter()
        params = {"MarketplaceIds": api.MARKETPLACE_BR, "FulfillmentChannels": "MFN", "MaxResultsPerPage": "100", "CreatedAfter": desde}
        for _ in range(8):
            st, b = api.get(conta, "/orders/v0/orders", **params)
            if st != 200:
                print(conta, "HTTP", st)
                break
            pl = b.get("payload") or {}
            for o in pl.get("Orders") or []:
                c[(o.get("ShipServiceLevel"), o.get("ShipmentServiceLevelCategory"), "FLASH" if eh_flash(o) else "-")] += 1
            if not pl.get("NextToken"):
                break
            params = {"MarketplaceIds": api.MARKETPLACE_BR, "NextToken": pl["NextToken"]}
            time.sleep(2)
        print(f"== {conta}: {sum(c.values())} pedidos MFN em {dias} dias")
        for (nivel, cat, marca), n in c.most_common():
            print(f"   {n:4}  ShipServiceLevel={nivel!r}  Category={cat!r}  {marca}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "descobrir":
        descobrir(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    else:
        print("Uso: python -m marketplaces.expedicao.amazon_flash descobrir [dias]")
