"""
marketplaces/sac/amazon.py — SAC da Amazon: devoluções, avaliações do vendedor e saúde da conta (somente leitura)
MZ App

A SP-API NÃO permite ler a caixa de mensagens dos compradores (só enviar mensagens permitidas por pedido; hoje a única ação é a nota fiscal),
nem reclamações A-to-z com endpoint próprio: elas aparecem na coluna "A-to-Z Claim" do relatório de devoluções MFN. Por isso a tela de
mensagens da Amazon fica indicada como indisponível. Tudo vem de relatórios assíncronos (30 a 90 s) guardados em cache por 1 hora:
  GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA        devoluções de pedidos FBA
  GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE        devoluções de pedidos MFN (RMA, A-to-Z, SafeT, resolução)
  GET_SELLER_FEEDBACK_DATA                         avaliações do vendedor (o e-mail do avaliador é descartado)
  GET_V2_SELLER_PERFORMANCE_REPORT                 saúde da conta (status, ODR, atraso, políticas, AHR)
Nada é respondido ou solicitado na Amazon.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from marketplaces.amazon import api, relatorios

BRT = timezone(timedelta(hours=-3))
CONTAS = ("amazon01", "amazon02")
VALE_SEG = 3600
_trava = threading.Lock()
_cache = {}
_locks = {}


def _fmt(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _relatorio(conta: str, tipo: str, dias: int) -> str:
    chave = (conta, tipo, dias)
    with _trava:
        lk = _locks.setdefault(chave, threading.Lock())
    with lk:
        c = _cache.get(chave)
        if c and time.time() - c[0] < VALE_SEG:
            return c[1]
        fim = datetime.now(timezone.utc) - timedelta(minutes=5)
        rid = relatorios.criar(conta, tipo, _fmt(fim - timedelta(days=dias)), _fmt(fim))
        texto = relatorios.baixar_texto(conta, relatorios.aguardar(conta, rid))
        _cache[chave] = (time.time(), texto)
        return texto


def _f(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def devolucoes(conta: str, dias: int = 30) -> dict:
    itens, erros = [], {}
    try:
        for r in relatorios.ler_tsv(_relatorio(conta, "GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA", dias)):
            itens.append({"origem": "FBA", "data": (r.get("return-date") or "")[:10], "pedido": r.get("order-id"), "sku": r.get("sku"), "asin": r.get("asin"),
                          "produto": r.get("product-name"), "qtd": int(_f(r.get("quantity")) or 1), "motivo": r.get("reason"), "disposicao": r.get("detailed-disposition"),
                          "status": r.get("detailed-disposition"), "comentario": (r.get("customer-comments") or "")[:300], "a_to_z": False, "resolucao": None,
                          "rma": r.get("license-plate-number")})
    except Exception as e:
        erros["fba"] = api.mascarar(str(e))[:160]
    try:
        for r in relatorios.ler_tsv(_relatorio(conta, "GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE", dias)):
            a2z = str(r.get("A-to-Z Claim") or "").strip().lower() in ("yes", "sim", "true", "y")
            itens.append({"origem": "MFN", "data": (r.get("Return request date") or "")[:10], "pedido": r.get("Order ID"), "sku": r.get("Merchant SKU"), "asin": r.get("ASIN"),
                          "produto": r.get("Item Name"), "qtd": int(_f(r.get("Return quantity")) or 1), "motivo": r.get("Return Reason"), "disposicao": None,
                          "status": r.get("Return request status"), "comentario": "", "a_to_z": a2z, "resolucao": r.get("Resolution"), "rma": r.get("Amazon RMA ID"),
                          "valor_reembolsado": _f(r.get("Refunded Amount")), "valor_pedido": _f(r.get("Order Amount")), "safet": r.get("SafeT claim state") or None,
                          "entrega_devolucao": (r.get("Return delivery date") or "")[:10] or None, "rastreio": r.get("Tracking ID")})
    except Exception as e:
        erros["mfn"] = api.mascarar(str(e))[:160]
    itens.sort(key=lambda i: i["data"], reverse=True)
    motivos = {}
    for i in itens:
        motivos[i["motivo"] or "—"] = motivos.get(i["motivo"] or "—", 0) + 1
    return {"conta": conta, "dias": dias, "itens": itens, "erros": erros,
            "resumo": {"total": len(itens), "fba": sum(1 for i in itens if i["origem"] == "FBA"), "mfn": sum(1 for i in itens if i["origem"] == "MFN"),
                       "a_to_z": sum(1 for i in itens if i["a_to_z"]), "avariados": sum(1 for i in itens if (i.get("disposicao") or "").upper().startswith(("DAMAGED", "DEFECTIVE", "CUSTOMER_DAMAGED", "CARRIER_DAMAGED"))),
                       "motivos": dict(sorted(motivos.items(), key=lambda kv: -kv[1]))},
            "gerado_em": datetime.now(BRT).isoformat(timespec="seconds")}


def avaliacoes(conta: str, dias: int = 90) -> dict:
    itens = []
    for r in relatorios.ler_tsv(_relatorio(conta, "GET_SELLER_FEEDBACK_DATA", dias)):
        chaves = list(r.keys())          # cabeçalho em português: Data, Avaliação, Comentários, Sua resposta, ID do pedido, E-mail do avaliador
        pega = lambda i: r.get(chaves[i]) if len(chaves) > i else None  # noqa: E731
        nota = int(_f(pega(1)) or 0)
        itens.append({"data": (pega(0) or "")[:10], "estrelas": nota, "comentario": (pega(2) or "")[:400], "resposta": pega(3) or None, "pedido": pega(4)})
    ruins = sum(1 for i in itens if 0 < i["estrelas"] <= 3)
    media = round(sum(i["estrelas"] for i in itens) / len(itens), 2) if itens else None
    return {"conta": conta, "dias": dias, "total": len(itens), "media": media, "ruins": ruins, "itens": sorted(itens, key=lambda i: (i["estrelas"], i["data"])), "gerado_em": datetime.now(BRT).isoformat(timespec="seconds")}


NOMES = {"lateShipmentRate": "Taxa de envio atrasado", "invoiceDefectRate": "Defeito de nota fiscal", "orderDefectRate": "Taxa de defeito de pedido (ODR)",
         "onTimeDeliveryRate": "Entregas no prazo", "validTrackingRate": "Rastreio válido", "preFulfillmentCancellationRate": "Cancelamento antes do envio",
         "listingPolicyViolations": "Violações de política de anúncio", "productAuthenticityCustomerComplaints": "Reclamações de autenticidade",
         "productConditionCustomerComplaints": "Reclamações de condição do produto", "productSafetyCustomerComplaints": "Reclamações de segurança do produto",
         "receivedIntellectualPropertyComplaints": "Reclamações de propriedade intelectual", "restrictedProductPolicyViolations": "Produtos restritos",
         "suspectedIntellectualPropertyViolations": "Suspeita de violação de propriedade intelectual", "foodAndProductSafetyIssues": "Segurança de alimentos e produtos",
         "customerProductReviewsPolicyViolations": "Política de avaliações de clientes", "otherPolicyViolations": "Outras violações de política", "documentRequests": "Solicitações de documentos"}
COND = {"LESS_THAN": "menor que", "GREATER_THAN": "maior que", "EQUALS": "igual a"}


def _metrica(nome, m, sub=None):
    valor = m.get("rate") if m.get("rate") is not None else m.get("defectsCount")
    e = {"nome": nome + (f".{sub}" if sub else ""), "titulo": NOMES.get(nome, nome) + (f" ({sub.upper()})" if sub else ""), "valor": valor,
         "eh_taxa": m.get("rate") is not None, "meta": m.get("targetValue"), "condicao": COND.get(m.get("targetCondition"), m.get("targetCondition")), "status": m.get("status")}
    for k in ("orderCount", "lateShipmentCount", "cancellationCount", "onTimeDeliveryCount", "shipmentCount"):
        if k in m:
            e[k] = m[k]
    return e


def saude(conta: str) -> dict:
    j = json.loads(_relatorio(conta, "GET_V2_SELLER_PERFORMANCE_REPORT", 30))
    pm = (j.get("performanceMetrics") or [{}])[0]
    mets = []
    for nome, v in pm.items():
        if nome.endswith("List") or not isinstance(v, dict) or nome in ("accountHealthRating",):
            continue
        if nome == "orderDefectRate":
            for canal in ("afn", "mfn"):
                if isinstance(v.get(canal), dict) and v[canal].get("status"):
                    mets.append(_metrica(nome, dict(v[canal], rate=v[canal].get("rate")), canal))
            continue
        mets.append(_metrica(nome, v))
    ahr = pm.get("accountHealthRating") or {}
    status = (j.get("accountStatuses") or [{}])[0].get("status")
    ordem = {"BAD": 0, "FAIR": 1, "GOOD": 2, "NONE": 3}
    mets.sort(key=lambda m: ordem.get(m["status"], 4))
    return {"conta": conta, "status_conta": status, "ahr": {"status": ahr.get("ahrStatus"), "pontos": ahr.get("ahrScore")}, "metricas": mets,
            "avisos": pm.get("warningStates") or [], "ruins": [m["titulo"] for m in mets if m["status"] in ("BAD", "FAIR")], "gerado_em": datetime.now(BRT).isoformat(timespec="seconds")}


def mensagens(conta: str) -> dict:
    return {"conta": conta, "disponivel": False,
            "motivo": "A SP-API da Amazon não permite ler a caixa de mensagens dos compradores. Só é possível enviar mensagens permitidas por pedido (hoje, apenas a nota fiscal). Responda no Seller Central."}
