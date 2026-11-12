"""
marketplaces/consulta/atrasos.py — Envios em trânsito marcados como atrasados pelo Mercado Livre (somente leitura)
MZ App

Varre os pedidos com envio em andamento de uma conta, lê o prazo (/sla) e os atrasos (/delays) de cada envio e guarda o resultado por
15 minutos: a varredura de uma conta leva alguns minutos. Quem pergunta durante a varredura recebe o último resultado (ou um aviso).
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

MELIS = ("meli01", "meli02", "meli03")
VALIDADE_SEG = 15 * 60
CANAIS = {"flex": "self_service", "full": "fulfillment", "coleta": "xd_drop_off", "places": "drop_off"}
NOME_CANAL = {"self_service": "Flex", "fulfillment": "Full", "xd_drop_off": "Coleta", "drop_off": "Places"}
# do mais ligado à transportadora ao menos: o primeiro que aparecer no envio é o motivo mostrado
MOTIVOS = (("shipping_predicted", "previsão de atraso"), ("shipping_delayed", "entrega (transportadora)"), ("sla_delayed", "prazo de despacho vencido"),
           ("handling_delayed", "preparo do vendedor"))
ORDEM = ("shipping_delayed", "sla_delayed", "handling_delayed", "shipping_predicted")


def _motivo(tipos: list) -> str:
    for pref in ORDEM:
        if any(t.startswith(pref) for t in tipos):
            return dict(MOTIVOS)[pref]
    return "prazo vencido"


_cache = {}          # (conta, dias) -> {"gerado": epoch, "itens": [...]}
_varrendo = set()
_trava = threading.Lock()


def _varrer(conta: str, dias: int) -> list:
    from datetime import datetime, timedelta, timezone
    import ml_api
    from reposicao_full import ML
    ml = ML(conta)
    sid = ml_api.get_seller_id(conta)
    de = (datetime.now(timezone(timedelta(hours=-3))) - timedelta(days=dias)).strftime("%Y-%m-%dT00:00:00.000-03:00")
    pedidos = []
    for off in range(0, 3000, 50):
        st, r = ml.get("/orders/search", seller=sid, sort="date_asc", limit=50, offset=off,
                       **{"shipping.status": "shipped", "order.date_created.from": de})
        if st != 200:
            raise RuntimeError(f"{conta}: pedidos HTTP {st}")
        res = (r or {}).get("results") or []
        pedidos += [o for o in res if (o.get("shipping") or {}).get("id")]
        if len(res) < 50:
            break

    def um(o):
        env = o["shipping"]["id"]
        st, e = ml.get(f"/shipments/{env}")
        if st != 200 or e.get("status") != "shipped":
            return None
        st2, d = ml.get(f"/shipments/{env}/delays")
        _, sla = ml.get(f"/shipments/{env}/sla")
        sla_status = (sla or {}).get("status")
        if st2 != 200 and sla_status != "delayed":
            return None
        tipos = [str(x.get("type") or "").lower() for x in ((d or {}).get("delays") or [])] if st2 == 200 and isinstance(d, dict) else []
        a = e.get("receiver_address") or {}
        uf = str((a.get("state") or {}).get("id") or "").replace("BR-", "")
        return {"conta": conta, "pedido": o["id"], "envio": env, "data": str(o.get("date_created") or "")[:10], "canal": NOME_CANAL.get(e.get("logistic_type"), e.get("logistic_type")),
                "situacao": e.get("substatus") or "em trânsito", "prazo": (sla or {}).get("expected_date") or "", "sla": sla_status,
                "motivo": _motivo(tipos), "tipos_ml": sorted(set(tipos)),
                "cidade": f"{(a.get('city') or {}).get('name') or '-'}/{uf}", "cep": a.get("zip_code") or "-"}

    with ThreadPoolExecutor(max_workers=6) as ex:
        itens = [x for x in ex.map(um, pedidos) if x]
    _bling(conta, itens)
    return itens


def _bling(conta: str, itens: list) -> None:
    from marketplaces.bling import api as bling
    from marketplaces.consulta.executores import _bling_de
    b = _bling_de({"conta": conta})

    def um(x):
        x["bling"] = b
        try:
            st, r = bling.get(b, "/pedidos/vendas", **{"numerosLojas[]": [str(x["pedido"])]})
            achados = (r or {}).get("data") or [] if st == 200 else []
            if achados:
                x["bling_numero"] = achados[0].get("numero")
                x["bling_situacao"] = (achados[0].get("situacao") or {}).get("valor")
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(um, itens))


def _atualizar(conta: str, dias: int) -> None:
    try:
        itens = _varrer(conta, dias)
        with _trava:
            _cache[(conta, dias)] = {"gerado": time.time(), "itens": itens, "erro": None}
    except Exception as e:
        with _trava:
            antigo = _cache.get((conta, dias)) or {"gerado": 0, "itens": []}
            _cache[(conta, dias)] = dict(antigo, erro=f"{type(e).__name__}: {str(e)[:120]}", tentou=time.time())
    finally:
        with _trava:
            _varrendo.discard((conta, dias))


def obter(conta: str, dias: int, esperar_seg: int = 90):
    """(itens, idade_seg, estado). Resultado velho: devolve na hora e atualiza em segundo plano. Sem nenhum resultado: espera a primeira varredura."""
    chave = (conta, dias)
    with _trava:
        c = _cache.get(chave)
        velho = not c or time.time() - max(c["gerado"], c.get("tentou") or 0) > VALIDADE_SEG
        if velho and chave not in _varrendo:
            _varrendo.add(chave)
            threading.Thread(target=_atualizar, args=chave, daemon=True).start()
    fim = time.time() + esperar_seg
    while time.time() < fim:
        with _trava:
            c = _cache.get(chave)
            andando = chave in _varrendo
        if c and c["gerado"]:
            return c["itens"], int(time.time() - c["gerado"]), ("atualizando" if andando else "ok")
        if not andando:
            break
        time.sleep(1)
    with _trava:
        c = _cache.get(chave)
    if c and c.get("erro"):
        return None, None, "erro: " + c["erro"]
    return None, None, "carregando"
