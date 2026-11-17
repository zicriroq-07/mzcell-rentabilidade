"""
marketplaces/sac/meli.py — SAC do Mercado Livre além das devoluções: reputação, perguntas, mensagens e reclamações (somente leitura)
MZ App

Endpoints já validados na sonda de 15/10/2026 (memória sac-sonda-ml-15-10): /users/{id} (seller_reputation), /questions/search,
/messages/unread, /post-purchase/v1/claims/search. Nada é respondido nem marcado como lido (mark_as_read nunca é usado).
As devoluções em andamento continuam em marketplaces/sac/devolucoes.py.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import ml_api
from reposicao_full import ML

BRT = timezone(timedelta(hours=-3))
CONTAS = ("meli01", "meli02", "meli03")
VALE_SEG = 300
_trava = threading.Lock()
_cache = {}

NIVEIS = {"5_green": "Verde", "4_light_green": "Verde claro", "3_yellow": "Amarelo", "2_orange": "Laranja", "1_red": "Vermelho"}
METRICAS = {"claims": "Reclamações", "delayed_handling_time": "Envio atrasado", "cancellations": "Cancelamentos", "sales": "Vendas"}


def _cached(chave, fn):
    with _trava:
        c = _cache.get(chave)
        if c and time.time() - c[0] < VALE_SEG:
            return c[1]
    v = fn()
    with _trava:
        _cache[chave] = (time.time(), v)
    return v


def _reputacao(ml, sid):
    st, u = ml.get(f"/users/{sid}")
    if st != 200:
        raise RuntimeError(f"/users HTTP {st}")
    rep = u.get("seller_reputation") or {}
    mets = rep.get("metrics") or {}
    saida = []
    for k, titulo in METRICAS.items():
        m = mets.get(k) or {}
        saida.append({"nome": k, "titulo": titulo, "taxa": m.get("rate"), "valor": m.get("value"), "periodo": m.get("period")})
    return {"nivel": rep.get("level_id"), "nivel_texto": NIVEIS.get(rep.get("level_id"), rep.get("level_id")), "mercado_lider": rep.get("power_seller_status"),
            "vendas_concluidas_60d": (rep.get("transactions") or {}).get("completed"), "cancelada_60d": (rep.get("transactions") or {}).get("canceled"),
            "avaliacoes": (rep.get("transactions") or {}).get("ratings"), "metricas": saida}


def _perguntas(ml, sid, limite=15):
    st, q = ml.get("/questions/search", seller_id=sid, status="UNANSWERED", limit=limite, sort_fields="date_created", sort_types="DESC")
    if st != 200:
        raise RuntimeError(f"/questions HTTP {st}")
    itens = [{"id": x.get("id"), "item_id": x.get("item_id"), "texto": (x.get("text") or "")[:300], "quando": x.get("date_created")} for x in q.get("questions") or []]
    return {"sem_resposta": q.get("total", len(itens)), "itens": itens}


def _mensagens_nao_lidas(ml):
    st, m = ml.get("/messages/unread", role="seller", tag="post_sale")
    if st != 200:
        raise RuntimeError(f"/messages/unread HTTP {st}")
    res = (m or {}).get("results") or []
    return {"conversas_nao_lidas": len(res), "mensagens_nao_lidas": sum(int(r.get("count") or 0) for r in res)}


def _reclamacoes(ml):
    from marketplaces.sac.devolucoes import _reclamacoes_abertas
    todas = []
    off = 0
    for _ in range(6):
        st, d = ml.get("/post-purchase/v1/claims/search", status="opened", limit=50, offset=off, sort="date_created:desc")
        if st != 200:
            raise RuntimeError(f"/claims HTTP {st}")
        lote = (d or {}).get("data") or []
        todas += lote
        off += len(lote)
        if len(lote) < 50 or off >= ((d or {}).get("paging") or {}).get("total", 0):
            break
    por_tipo, aguardam = {}, 0
    for c in todas:
        por_tipo[c.get("type")] = por_tipo.get(c.get("type"), 0) + 1
        vend = next((p for p in c.get("players") or [] if p.get("role") == "respondent"), {})
        aguardam += 1 if vend.get("available_actions") else 0
    return {"abertas": len(todas), "aguardam_voce": aguardam, "por_tipo": por_tipo}


def resumo(conta: str) -> dict:
    """Painel do SAC de uma conta: cada bloco falha sozinho (erro por bloco) sem derrubar os outros."""
    def montar():
        ml, sid = ML(conta), ml_api.get_seller_id(conta)
        out = {"conta": conta, "erros": {}}
        for nome, fn in (("reputacao", lambda: _reputacao(ml, sid)), ("perguntas", lambda: _perguntas(ml, sid)),
                         ("mensagens", lambda: _mensagens_nao_lidas(ml)), ("reclamacoes", lambda: _reclamacoes(ml))):
            try:
                out[nome] = fn()
            except Exception as e:
                out[nome] = None
                out["erros"][nome] = str(e)[:160]
        out["gerado_em"] = datetime.now(BRT).isoformat(timespec="seconds")
        return out
    return _cached(("resumo", conta), montar)
