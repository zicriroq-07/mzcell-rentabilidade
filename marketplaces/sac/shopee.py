"""
marketplaces/sac/shopee.py — SAC da Shopee: devoluções, mensagens, avaliações, reputação e saúde da conta (somente leitura)
MZ App

Nada é respondido, aceito, contestado nem marcado como lido na Shopee. Endpoints (sonda de 16/11/2026, ver memória sac-sonda-shopee-amazon-16-11):
  returns/get_return_list (janela máx. 15 dias) e get_return_detail   sellerchat/get_conversation_list, get_message, get_unread_conversation_count
  product/get_comment (item_id=0 traz todas)                          account_health/get_shop_performance, get_penalty_point_history,
  get_punishment_history, get_listings_with_issues, get_late_orders   shop/get_shop_notification
Dados pessoais do comprador (nome, endereço, telefone) não entram nas respostas; o apelido do chat é mostrado só porque a conversa
é do SAC. Cada leitura fica em cache de memória por alguns minutos para a tela responder na hora.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from marketplaces.shopee import api, lojas

BRT = timezone(timedelta(hours=-3))
LOJA = "techseller"
DIA = 86400
_trava = threading.Lock()
_cache = {}


def _com_cache(chave, seg, fn):
    agora = time.time()
    with _trava:
        c = _cache.get(chave)
        if c and agora - c[0] < seg:
            return c[1]
    v = fn()
    with _trava:
        _cache[chave] = (time.time(), v)
    return v


def _sid(loja):
    return lojas.shop_id_da(loja)


def _chamar(sid, caminho, params=None):
    return api.chamar(caminho, sid, params or {}).get("response", {}) or {}


def _iso(ts):
    return datetime.fromtimestamp(ts, BRT).isoformat(timespec="seconds") if ts else None


# ─── devoluções ──────────────────────────────────────────────────────────────

STATUS = {"REQUESTED": "Solicitada: aguarda a sua resposta", "ACCEPTED": "Aceita", "CANCELLED": "Cancelada", "JUDGING": "Em análise pela Shopee",
          "CLOSED": "Encerrada", "PROCESSING": "Reembolso em processamento", "SELLER_DISPUTE": "Em disputa (você contestou)"}
MOTIVOS = {"WRONG_ITEM": "Produto errado", "ITEM_NOT_FIT": "Não serve / não corresponde", "DAMAGED": "Produto danificado", "NOT_RECEIVED": "Não recebido",
           "DIFFERENT_DESCRIPTION": "Diferente da descrição", "MISSING_ITEMS": "Itens faltando", "FAKE": "Falsificado", "CHANGE_MIND": "Desistência",
           "FUNCTIONAL_DAMAGE": "Defeito de funcionamento", "PHYSICAL_DAMAGE": "Dano físico", "NONRECEIPT": "Não recebido", "WRONG_SIZE": "Tamanho errado",
           "FUNCTIONAL_DMG": "Defeito de funcionamento", "DAMAGED_OTHERS": "Danificado (outros)", "NOT_RECEIPT": "Não recebido", "ITEM_MISSING": "Item faltando",
           "SUSPICIOUS_PARCEL": "Pacote suspeito", "CHANGE_OF_MIND": "Desistência", "USED": "Produto usado", "INCORRECT_ITEM": "Produto errado"}


def fase(r: dict, det: dict) -> str:
    """responder | disputa | chegou | a_caminho | reembolso | encerrada | aceita"""
    st = r.get("status")
    if st == "REQUESTED":
        return "responder"
    if st in ("SELLER_DISPUTE", "JUDGING"):
        return "disputa"
    if st in ("CANCELLED", "CLOSED"):
        return "encerrada"
    logi = str((det or {}).get("logistics_status") or r.get("logistics_status") or "")
    if st == "PROCESSING" and not logi.endswith("DELIVERY_DONE"):
        return "reembolso"
    if logi.endswith("DELIVERY_DONE") or r.get("is_arrived_at_warehouse"):
        return "chegou"
    if logi:
        return "a_caminho"
    return "aceita"


FASES = {"responder": "Responder (aguarda você)", "disputa": "Em disputa/análise", "chegou": "Chegou ao vendedor", "a_caminho": "A caminho do vendedor",
         "reembolso": "Reembolso em processamento", "aceita": "Aceita", "encerrada": "Encerrada"}
ORDEM = {"responder": 0, "disputa": 1, "chegou": 2, "a_caminho": 3, "reembolso": 4, "aceita": 5, "encerrada": 6}


def acao(f: str, prazo, vencido: bool = False) -> str:
    quando = f" Prazo: {prazo[:10]}." if prazo else ""
    if f == "chegou" and vencido:
        return "Prazo de contestação encerrado (" + prazo[:10] + "). Só conferir o produto e dar entrada no estoque se for apto."
    return {"responder": "RESPONDER no Seller Centre (aceitar, propor ou contestar)." + quando,
            "disputa": "Aguardar a decisão da Shopee e acompanhar as mensagens da disputa." + quando,
            "chegou": "Conferir o produto no galpão (apto ou inapto). Contestar só se houver dano ou item diferente." + quando,
            "a_caminho": "Acompanhar o rastreio da devolução. Dar entrada no estoque só quando chegar.",
            "reembolso": "Nada a fazer: a Shopee está processando o reembolso ao comprador.",
            "aceita": "Aguardar a postagem da devolução pelo comprador.", "encerrada": "Encerrada."}[f]


def _item_resumo(r: dict) -> dict:
    it = (r.get("item") or [{}])[0]
    return {"item_id": it.get("item_id"), "nome": it.get("name"), "sku": it.get("variation_sku") or it.get("item_sku"),
            "qtd": it.get("amount"), "foto": (it.get("images") or [None])[0]}


def _devolucoes_brutas(sid, dias):
    agora = int(time.time())
    saida, vistos = [], set()
    for k in range(0, (dias + 13) // 14):
        de, ate = agora - (k + 1) * 14 * DIA, agora - k * 14 * DIA
        pag = 0
        while True:
            r = _chamar(sid, "/api/v2/returns/get_return_list", {"page_no": pag, "page_size": 50, "create_time_from": de, "create_time_to": ate})
            for x in r.get("return") or []:
                if x["return_sn"] not in vistos:
                    vistos.add(x["return_sn"])
                    saida.append(x)
            if not r.get("more"):
                break
            pag += 1
    return saida


def devolucoes(dias: int = 30, loja: str = LOJA) -> dict:
    sid = _sid(loja)

    def montar():
        brutas = _devolucoes_brutas(sid, dias)
        abertas = [x for x in brutas if x.get("status") in ("REQUESTED", "ACCEPTED", "PROCESSING", "JUDGING", "SELLER_DISPUTE")]

        def det(x):
            try:
                return x["return_sn"], _chamar(sid, "/api/v2/returns/get_return_detail", {"return_sn": x["return_sn"]})
            except Exception:
                return x["return_sn"], {}
        with ThreadPoolExecutor(4) as ex:
            detalhes = dict(ex.map(det, abertas))
        itens = []
        agora_iso = datetime.now(BRT).isoformat(timespec="seconds")
        for x in brutas:
            d = detalhes.get(x["return_sn"], {})
            f = fase(x, d)
            prazo = _iso(x.get("due_date") if f == "responder" else (x.get("return_seller_due_date") or x.get("due_date")))
            vencido = bool(prazo and prazo < agora_iso)
            itens.append({"loja": loja, "return_sn": x["return_sn"], "pedido": x.get("order_sn"), "status": x.get("status"), "status_texto": STATUS.get(x.get("status"), x.get("status")),
                          "fase": f, "fase_texto": FASES[f], "motivo": x.get("reason"), "motivo_texto": MOTIVOS.get(x.get("reason"), x.get("reason")),
                          "comentario": (x.get("text_reason") or "")[:300], "valor": x.get("refund_amount"), "criada": _iso(x.get("create_time")),
                          "atualizada": _iso(x.get("update_time")), "prazo": prazo, "rastreio": x.get("tracking_number"),
                          "logistica": d.get("logistics_status"), "tem_fotos": bool(x.get("image") or x.get("buyer_videos")), "item": _item_resumo(x),
                          "acao": acao(f, prazo, vencido), "tipo": x.get("return_refund_type"), "prazo_vencido": vencido,
                          "contestavel": f in ("chegou", "responder") and not vencido})
        itens.sort(key=lambda i: i["criada"] or "", reverse=True)                      # mais recentes primeiro
        itens.sort(key=lambda i: (ORDEM[i["fase"]], not i["contestavel"]))               # por fase; dentro dela, as ainda contestáveis antes
        return itens
    itens = _com_cache(("dev", loja, dias), 600, montar)
    por = {}
    for i in itens:
        por[i["fase"]] = por.get(i["fase"], 0) + 1
    motivos = {}
    for i in itens:
        if i["fase"] != "encerrada":
            motivos[i["motivo_texto"]] = motivos.get(i["motivo_texto"], 0) + 1
    return {"loja": loja, "dias": dias, "itens": itens, "resumo": {"total": len(itens), "por_fase": por, "valor_em_aberto": round(sum(i["valor"] or 0 for i in itens if i["fase"] not in ("encerrada", "reembolso")), 2),
                                                                    "contestaveis": sum(1 for i in itens if i["contestavel"]), "valor_contestavel": round(sum(i["valor"] or 0 for i in itens if i["contestavel"]), 2),
                                                                    "motivos": dict(sorted(motivos.items(), key=lambda kv: -kv[1]))}, "gerado_em": datetime.now(BRT).isoformat(timespec="seconds")}


# ─── mensagens (Seller Chat) ────────────────────────────────────────────────

TIPOS_TEXTO = {"text", "TEXT"}


def mensagens(limite: int = 30, loja: str = LOJA) -> dict:
    sid = _sid(loja)

    def montar():
        nao_lidas = _chamar(sid, "/api/v2/sellerchat/get_unread_conversation_count").get("total_unread_count", 0)
        # neste endpoint direction=older devolve as conversas MAIS RECENTES (latest devolve as mais antigas); "unread" filtra as não lidas
        brutas = {}
        for tipo in ("unread", "all"):
            r = _chamar(sid, "/api/v2/sellerchat/get_conversation_list", {"direction": "older", "type": tipo, "page_size": min(limite, 60)})
            for c in r.get("conversations") or []:
                brutas.setdefault(c.get("conversation_id"), c)
        convs = []
        for c in sorted(brutas.values(), key=lambda c: -(c.get("last_message_timestamp") or 0)):
            ult = c.get("latest_message_content") or {}
            texto = ult.get("text") if isinstance(ult, dict) else None
            do_comprador = c.get("latest_message_from_id") == c.get("to_id")
            convs.append({"conversa": str(c.get("conversation_id")), "comprador": c.get("to_name"), "nao_lidas": c.get("unread_count") or 0,
                          "ultima_em": _iso((c.get("last_message_timestamp") or 0) // 1_000_000_000 if (c.get("last_message_timestamp") or 0) > 1e12 else c.get("last_message_timestamp")),
                          "ultima_de": "comprador" if do_comprador else "loja", "ultima_tipo": c.get("latest_message_type"),
                          "ultima_texto": (texto or "")[:200] if texto else None, "aguarda_resposta": bool(do_comprador)})
        convs.sort(key=lambda c: (not c["aguarda_resposta"], -c["nao_lidas"], c["ultima_em"] and "" or "", ), reverse=False)
        convs.sort(key=lambda c: c["ultima_em"] or "", reverse=True)
        convs.sort(key=lambda c: (not c["aguarda_resposta"], c["nao_lidas"] == 0))
        return {"nao_lidas_total": nao_lidas, "conversas": convs}
    d = _com_cache(("msg", loja, limite), 120, montar)
    return {"loja": loja, **d, "aguardando_resposta": sum(1 for c in d["conversas"] if c["aguarda_resposta"]), "gerado_em": datetime.now(BRT).isoformat(timespec="seconds")}


def conversa(conversa_id: str, loja: str = LOJA, limite: int = 30) -> dict:
    """Últimas mensagens de uma conversa. Só leitura: não marca como lida (get_message não altera o estado)."""
    sid = _sid(loja)
    r = _chamar(sid, "/api/v2/sellerchat/get_message", {"conversation_id": str(conversa_id), "page_size": min(limite, 50)})
    msgs = []
    for m in r.get("messages") or []:
        c = m.get("content") or {}
        msgs.append({"de": "loja" if m.get("from_shop_id") else "comprador", "quando": _iso(m.get("created_timestamp")), "tipo": m.get("message_type"),
                     "texto": c.get("text") if isinstance(c, dict) else None})
    msgs.sort(key=lambda x: x["quando"] or "")
    return {"conversa": str(conversa_id), "mensagens": msgs}


# ─── avaliações ─────────────────────────────────────────────────────────────

def avaliacoes(dias: int = 30, loja: str = LOJA) -> dict:
    sid = _sid(loja)

    def montar():
        agora = int(time.time())
        saida, cursor = [], ""
        for _ in range(6):
            r = _chamar(sid, "/api/v2/product/get_comment", {"item_id": 0, "cursor": cursor, "page_size": 50})
            lote = r.get("item_comment_list") or []
            saida += lote
            if not r.get("more") or (lote and min(x.get("create_time", agora) for x in lote) < agora - dias * DIA):
                break
            cursor = r.get("next_cursor") or ""
        return [x for x in saida if x.get("create_time", 0) >= agora - dias * DIA]
    lista = _com_cache(("aval", loja, dias), 900, montar)
    por = {}
    for x in lista:
        por[x.get("rating_star")] = por.get(x.get("rating_star"), 0) + 1
    itens = [{"avaliacao": x.get("comment_id"), "pedido": x.get("order_sn"), "item_id": x.get("item_id"), "estrelas": x.get("rating_star"),
              "comentario": (x.get("comment") or "")[:400], "quando": _iso(x.get("create_time")), "respondida": bool((x.get("comment_reply") or {}).get("reply")),
              "pode_responder": x.get("editable") == "EDITABLE"} for x in sorted(lista, key=lambda y: (y.get("rating_star", 5), -y.get("create_time", 0)))]
    media = round(sum((x.get("rating_star") or 0) for x in lista) / len(lista), 2) if lista else None
    return {"loja": loja, "dias": dias, "total": len(lista), "media": media, "por_estrela": {str(k): por[k] for k in sorted(por)},
            "ruins": sum(1 for x in lista if (x.get("rating_star") or 5) <= 3), "itens": itens}


# ─── reputação e saúde da conta ─────────────────────────────────────────────

NOMES = {"late_shipment_rate": "Taxa de envio atrasado", "non_fulfillment_rate": "Taxa de não cumprimento", "response_rate": "Taxa de resposta (chat)",
         "pre_order_listing_rate": "Anúncios sob encomenda (%)", "the_amount_of_pre_order_listing": "Quantidade de anúncios sob encomenda",
         "shop_rating": "Nota da loja", "cancellation_rate": "Taxa de cancelamento", "return_refund_rate": "Taxa de devolução/reembolso",
         "severe_listing_violations": "Violações graves de anúncio", "other_listing_violations": "Outras violações de anúncio",
         "prohibited_listings": "Anúncios proibidos", "counterfeit_ip_infringement": "Falsificação / propriedade intelectual", "spam_listings": "Anúncios spam",
         "pqr_products": "Produtos com baixa qualidade (PQR)", "saturday_shipment_rate": "Envio aos sábados", "avg_preparation_time_ps": "Tempo médio de preparação (dias)",
         "otdr_dd_rate": "Entregas no prazo (Entrega Direta)"}
MAIOR_E_MELHOR = {"response_rate", "shop_rating", "saturday_shipment_rate", "otdr_dd_rate"}


def _estado_metrica(nome, atual, meta):
    if atual is None or meta is None:
        return "sem_dado"
    if nome in MAIOR_E_MELHOR:
        return "ok" if atual >= meta else "ruim"
    return "ok" if atual <= meta else "ruim"


def saude(loja: str = LOJA) -> dict:
    sid = _sid(loja)

    def montar():
        perf = _chamar(sid, "/api/v2/account_health/get_shop_performance")
        pontos = _chamar(sid, "/api/v2/account_health/get_penalty_point_history", {"page_no": 1, "page_size": 20})
        punicoes = _chamar(sid, "/api/v2/account_health/get_punishment_history", {"punishment_status": 1, "page_no": 1, "page_size": 20})
        problemas = _chamar(sid, "/api/v2/account_health/get_listings_with_issues", {"page_no": 1, "page_size": 50})
        atrasos = _chamar(sid, "/api/v2/account_health/get_late_orders", {"page_no": 1, "page_size": 20})
        avisos = _chamar(sid, "/api/v2/shop/get_shop_notification", {"cursor": 0, "page_size": 10})
        mets = []
        for m in perf.get("metric_list") or []:
            nome = m.get("metric_name")
            alvo = m.get("target") if isinstance(m.get("target"), dict) else {}
            atual, meta = m.get("current_period"), alvo.get("value")
            mets.append({"nome": nome, "titulo": NOMES.get(nome, nome), "atual": atual, "meta": meta, "unidade": m.get("unit"),
                         "anterior": m.get("last_period"), "estado": _estado_metrica(nome, atual, meta)})
        geral = perf.get("overall_performance") or {}
        return {"geral": {"nivel": geral.get("rating"), "falhas_envio": geral.get("fulfillment_failed"), "falhas_anuncio": geral.get("listing_failed"),
                          "falhas_atendimento": geral.get("custom_service_failed")}, "metricas": mets,
                "pontos_penalidade": pontos.get("total_count", 0), "punicoes_ativas": punicoes.get("total_count", 0),
                "anuncios_com_problema": [{"item_id": x.get("item_id"), "motivo": x.get("reason")} for x in problemas.get("listing_list") or []],
                "pedidos_atrasados": atrasos.get("total_count", 0),
                "avisos": [{"quando": _iso(a.get("create_time")), "titulo": a.get("title"), "texto": a.get("content")} for a in (avisos.get("data") or [])[:10]]}
    d = _com_cache(("saude", loja), 600, montar)
    return {"loja": loja, **d, "fora_da_meta": [m["titulo"] for m in d["metricas"] if m["estado"] == "ruim"], "gerado_em": datetime.now(BRT).isoformat(timespec="seconds")}
