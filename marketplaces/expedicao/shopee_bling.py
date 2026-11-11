"""
marketplaces/expedicao/shopee_bling.py — Pedidos da Shopee e da Magalu para a Expedição, lidos do Bling (por enquanto)
MZ App

Somente leitura. Usa a lista de pedidos que a Expedição já busca no bling01 e só abre o detalhe de cada pedido uma vez,
para saber a modalidade (o serviço do volume). O Bling não guarda quando o pedido foi verificado: para contar os feitos do
dia, shopee_expedicao.json lembra quais estavam em aberto e registra o dia em que viraram Verificado.

Pedido pago depois do corte vai para o corte do dia seguinte. O Bling só guarda a data do pedido, sem hora; a entrada é
estimada pelo maior de dois sinais:
  - o número da Shopee começa com a data da Shopee (AAMMDD, horário UTC+8): data posterior à do Bling = criado depois das
    13h de Brasília, ou seja, depois dos dois cortes;
  - a hora em que o servidor viu o pedido no Bling pela primeira vez (o Bling importa o pedido quando é pago), só quando o
    servidor já estava lendo antes (sem isso, um reinício empurraria para amanhã todo pedido em aberto).
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from marketplaces.bling import api as bling
from marketplaces.expedicao import bling_checkout, cortes

BRT = timezone(timedelta(hours=-3))
CONTA_BLING = "bling01"
ABERTOS = (6, 15)
# Contagem começa em 10/11/2026 (pedido do usuário): os pedidos em aberto de antes ficam de fora da Expedição
INICIO = "2026-11-10"
# "Logistica Shopee" = Entrega Direta: leitura pelo prazo curto, a confirmar com o painel da Shopee
SERVICOS = {"shopee xpress": "Shopee Xpress", "logistica shopee": "Entrega Direta"}
# lojas do bling01 que entram, pelo nome do canal de venda: conta da Expedição e plataforma do corte (cortes.py)
LOJAS = (("shopee", "shopee", "Shopee"), ("magalu", "magalu", "Magalu"))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAMINHO = os.path.join(BASE_DIR, "shopee_expedicao.json")
LOJAS_SEG = 3600
# leitura anterior a no máximo isto: o servidor estava de olho e a hora em que viu o pedido vale como entrada
CONTINUA_SEG = 15 * 60
# o Bling demora alguns minutos para importar: pedido visto até esta folga depois do corte ainda conta no dia
FOLGA_MIN = 15
_lojas = {"ts": 0.0, "ids": {}}
_detalhes = {}


def _ids_lojas() -> dict:
    """{id do canal de venda: conta da Expedição (shopee ou magalu)}."""
    if time.time() - _lojas["ts"] >= LOJAS_SEG:
        st, r = bling.get(CONTA_BLING, "/canais-venda", limite=100)
        if st != 200:
            raise RuntimeError(f"canais de venda HTTP {st}")
        ids = {}
        for c in r.get("data") or []:
            conta = next((conta for termo, conta, _ in LOJAS if re.search(termo, str(c.get("descricao")), re.I)), None)
            if conta:
                ids[c.get("id")] = conta
        _lojas.update(ts=time.time(), ids=ids)
    return _lojas["ids"]


def _ler_estado() -> dict:
    try:
        with open(CAMINHO, encoding="utf-8") as f:
            d = json.load(f)
        return {"abertos": list(d.get("abertos") or []), "feitos": dict(d.get("feitos") or {}),
                "vistos": dict(d.get("vistos") or {}), "leitura": d.get("leitura")}
    except (OSError, ValueError, AttributeError):
        return {"abertos": [], "feitos": {}, "vistos": {}, "leitura": None}


def _gravar_estado(estado: dict):
    with open(CAMINHO + ".tmp", "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False)
    os.replace(CAMINHO + ".tmp", CAMINHO)


def _detalhe(pid, conta: str = "shopee") -> dict:
    """{canal, servico, itens}. A modalidade e os itens não mudam: uma leitura por pedido. Magalu tem uma modalidade só."""
    if pid not in _detalhes:
        st, r = bling.get(CONTA_BLING, f"/pedidos/vendas/{pid}")
        if st != 200:
            raise RuntimeError(f"pedido {pid} HTTP {st}")
        x = r.get("data") or {}
        vols = (x.get("transporte") or {}).get("volumes") or []
        servico = str((vols[0] if vols else {}).get("servico") or "").strip()
        _detalhes[pid] = {"servico": servico, "canal": "Magalu" if conta == "magalu" else SERVICOS.get(servico.lower()),
                          "itens": [{"mlb": None, "sku": i.get("codigo"), "titulo": i.get("descricao"), "qtd": i.get("quantidade")} for i in x.get("itens") or []]}
        time.sleep(0.35)
    return _detalhes[pid]


def _entrada_shopee(p: dict):
    """Menor hora possível de criação pelo número da Shopee: data da Shopee depois da do Bling = depois das 13h de Brasília."""
    m = re.match(r"(\d{2})(\d{2})(\d{2})", str(p.get("numeroLoja") or ""))
    try:
        dia_sh = datetime(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        dia_bl = datetime.strptime(str(p.get("data"))[:10], "%Y-%m-%d").date()
    except (AttributeError, ValueError):
        return None
    if dia_sh <= dia_bl:
        return None
    # meia-noite em UTC+8 do dia da Shopee, em Brasília
    return datetime(dia_sh.year, dia_sh.month, dia_sh.day, tzinfo=timezone(timedelta(hours=8))).astimezone(BRT)


def _meta(canal: str, hoje, plataforma: str = "Shopee", entrada: datetime = None) -> str:
    """Corte do dia; pedido que entrou depois do corte (com folga) vai para o corte do dia seguinte (domingo pula)."""
    hhmm = next((c["corte"] for c in cortes.carregar() if c.get("plataforma") == plataforma and c.get("canal") == canal), "12:00")
    h, m = (int(x) for x in hhmm.split(":"))
    dia = hoje
    if entrada:
        dia = entrada.date()
        if entrada > datetime(dia.year, dia.month, dia.day, h, m, tzinfo=BRT) + timedelta(minutes=FOLGA_MIN):
            dia += timedelta(days=2 if dia.weekday() == 5 else 1)
        dia = max(dia, hoje)
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=BRT).isoformat()


def _linha(p: dict, det: dict, hoje, conta: str = "shopee", entrada: datetime = None) -> dict:
    sit = p["situacao"]["id"]
    plataforma = next(pl for _, c, pl in LOJAS if c == conta)
    return {"conta": conta, "plataforma": plataforma, "canal": det["canal"], "logistica": det["servico"], "pedido": p.get("numeroLoja"), "pacote": None, "envio": p.get("numeroLoja"),
            "unidades": sum(i["qtd"] or 0 for i in det["itens"]), "status": "bling", "substatus": None, "situacao": bling_checkout.SITUACOES.get(sit, str(sit)),
            "meta": _meta(det["canal"], hoje, plataforma, entrada), "entrada": entrada.isoformat() if entrada else None, "prazo_ml": None, "itens": det["itens"], "bling_numero": p.get("numero"),
            "bling_situacao": bling_checkout.SITUACOES.get(sit, str(sit)), "bling_checkout": sit == bling_checkout.SITUACAO_VERIFICADO,
            "fonte": "Bling"}


def linhas(agora: datetime = None) -> tuple:
    """(linhas de hoje, erros): todo pedido em aberto, e os verificados que viraram Verificado hoje."""
    agora = agora or datetime.now(BRT)
    hoje = agora.date()
    erros = {}
    try:
        lojas = _ids_lojas()
        pedidos = [p for p in bling_checkout._pedidos(CONTA_BLING).values() if (p.get("loja") or {}).get("id") in lojas]
        # o cache do Bling é por numeroLoja; pedido de loja diferente com o mesmo número não acontece na Shopee nem na Magalu
    except Exception as e:
        return [], {"shopee": f"Bling: {type(e).__name__}: {str(e)[:100]}"}
    estado = _ler_estado()
    abertos_antes, feitos, vistos_em = set(estado["abertos"]), estado["feitos"], estado["vistos"]
    try:
        leitura = datetime.fromisoformat(estado["leitura"]) if estado["leitura"] else None
    except ValueError:
        leitura = None
    de_olho = leitura is not None and timedelta(0) <= agora - leitura <= timedelta(seconds=CONTINUA_SEG)
    abertos, saida, vistos = set(), [], set()
    for p in pedidos:
        pid, sit = str(p["id"]), p["situacao"]["id"]
        vistos.add(pid)
        if pid not in vistos_em:
            # primeira vez no Bling; sem leitura recente a hora não diz nada (servidor parado ou recém-ligado)
            vistos_em[pid] = agora.isoformat() if de_olho else None
        if str(p.get("data") or "") < INICIO:
            continue
        if sit in ABERTOS:
            abertos.add(pid)
        elif sit == bling_checkout.SITUACAO_VERIFICADO:
            if pid not in feitos:
                feitos[pid] = hoje.isoformat() if pid in abertos_antes else str(p.get("data"))
            if feitos[pid] != hoje.isoformat():
                continue
        else:
            continue
        conta = lojas[(p.get("loja") or {}).get("id")]
        try:
            det = _detalhe(p["id"], conta)
        except Exception as e:
            erros[conta] = f"Bling: {type(e).__name__}: {str(e)[:100]}"
            continue
        if not det["canal"]:
            erros[f"{conta}_modalidade"] = f"modalidade desconhecida: {det['servico'] or 'vazia'}"
            continue
        candidatas = [_entrada_shopee(p) if conta == "shopee" else None,
                      datetime.fromisoformat(vistos_em[pid]) if vistos_em.get(pid) else None]
        entrada = max((e for e in candidatas if e), default=None)
        saida.append(_linha(p, det, hoje, conta, entrada))
    novo = {"abertos": sorted(abertos), "feitos": {k: v for k, v in feitos.items() if k in vistos},
            "vistos": {k: v for k, v in vistos_em.items() if k in vistos}, "leitura": agora.isoformat()}
    try:
        _gravar_estado(novo)
    except OSError as e:
        erros["shopee_estado"] = f"{type(e).__name__}: {str(e)[:100]}"
    return saida, erros
