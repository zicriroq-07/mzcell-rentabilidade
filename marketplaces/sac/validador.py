"""
marketplaces/sac/validador.py — Validador de operação do SAC (somente leitura no Mercado Pago)
MZ App

O analista confere um ressarcimento de contestação:
- pela operação do Mercado Pago (número que o atendente ou o mediador informa): data e hora, descrição, tipo, valor e,
  quando é o pagamento da venda, os estornos e quem pagou cada um;
- pela venda (pedido ou pacote do ML): todas as entradas e saídas no Mercado Pago (venda, tarifas, frete, estornos,
  tarifas devolvidas e os ressarcimentos ligados às contestações do pedido) e o saldo final.

Só GET em api.mercadopago.com com o token do ML da conta (o mesmo OAuth vale no Mercado Pago). Nada é escrito.
O crédito de ressarcimento ("Entrada de dinheiro correspondente a sua reclamação") não traz a venda: a ligação
vem das contestações já lidas pelo SAC (contestacoes_compartilhadas.json, campo `operacao` por pedido).
"""

import json
import os

import httpx

import ml_api

CONTAS = ("meli01", "meli02", "meli03")
MP = "https://api.mercadopago.com"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONTESTACOES = os.path.join(BASE_DIR, "contestacoes_compartilhadas.json")

# Nomes das cobranças do pagamento (charges_details) como aparecem para o analista
COBRANCAS = {"ml_sale_fee": "Tarifa de venda do ML", "mp_processing_fee": "Taxa de processamento do Mercado Pago",
             "mp_financing_1x_fee": "Taxa de parcelamento do Mercado Pago"}
TIPOS = {"regular_payment": "Pagamento da venda", "money_transfer": "Transferência / crédito", "recurring_payment": "Pagamento recorrente",
         "account_fund": "Entrada de dinheiro", "payment_addition": "Pagamento adicional"}


def _contas(conta):
    if conta and conta not in CONTAS:
        raise ValueError("conta inválida")
    return [conta] if conta else list(CONTAS)


def _get(conta: str, caminho: str, **params):
    tok = ml_api.get_token(conta)
    if not tok:
        return 0, {}
    r = httpx.get(MP + caminho, params=params, headers={"Authorization": f"Bearer {tok}"}, timeout=30, verify=False)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def _nome_cobranca(nome: str, tipo: str) -> str:
    if nome in COBRANCAS:
        return COBRANCAS[nome]
    if tipo == "shipping" or str(nome).startswith("shp_"):
        return "Frete cobrado do vendedor"
    return f"Tarifa ({nome})"


def _estornos(p: dict) -> list:
    """Estornos do pagamento. `bpp_covered` no pagamento = o ML cobriu a devolução ao comprador (vendedor não paga)."""
    coberto = p.get("status_detail") == "bpp_covered"
    saida = []
    for r in p.get("refunds") or []:
        saida.append({"id": r.get("id"), "data": r.get("date_created"), "valor": r.get("amount"),
                      "coberto_pelo_ml": coberto, "origem": (r.get("source") or {}).get("type"),
                      "detalhe": (r.get("metadata") or {}).get("status_detail")})
    return saida


def _resumo_operacao(conta: str, p: dict) -> dict:
    tipo = p.get("operation_type")
    estornos = _estornos(p)
    credito_reclamacao = tipo == "money_transfer" and "reclama" in str(p.get("description") or "").lower()
    if tipo == "money_transfer" and p.get("status") == "approved":
        ressarcido = p.get("transaction_amount")
    elif estornos and estornos[0]["coberto_pelo_ml"]:
        ressarcido = sum(e["valor"] or 0 for e in estornos)
    else:
        ressarcido = 0.0
    venda = (p.get("order") or {}).get("id") or (p.get("external_reference") if str(p.get("external_reference") or "").isdigit() else None)
    aviso = None
    if tipo == "regular_payment":
        aviso = ("Este número é o pagamento da venda, não um crédito de ressarcimento. Pesquise pela venda para ver as "
                 "entradas e saídas e procurar o crédito da reclamação.")
    elif tipo == "money_transfer" and not credito_reclamacao:
        aviso = "Crédito do Mercado Pago que não é de reclamação (pode ser bonificação ou outra transferência)."
    return {"conta": conta, "operacao": p.get("id"), "tipo": TIPOS.get(tipo, tipo), "tipo_api": tipo,
            "credito_reclamacao": credito_reclamacao, "data": p.get("date_approved") or p.get("date_created"),
            "liberacao": p.get("money_release_date"), "descricao": p.get("description"), "valor": p.get("transaction_amount"),
            "liquido": (p.get("transaction_details") or {}).get("net_received_amount"), "status": p.get("status"),
            "status_detalhe": p.get("status_detail"), "venda": venda, "estornos": estornos, "ressarcido": ressarcido, "aviso": aviso}


def operacao(numero, conta: str = None) -> dict:
    """Uma operação do Mercado Pago, procurada nas contas (a primeira que conhecer o número)."""
    n = str(numero or "").strip()
    if not n.isdigit():
        raise ValueError("informe o número da operação (só dígitos)")
    erros = {}
    for c in _contas(conta):
        st, p = _get(c, f"/v1/payments/{n}")
        if st == 200 and p.get("id"):
            return dict(_resumo_operacao(c, p), encontrado=True)
        if st not in (403, 404):
            erros[c] = f"HTTP {st}"
    return {"encontrado": False, "operacao": n, "erros": erros, "motivo": "operação não encontrada nas contas do Mercado Pago"}


def _operacoes_das_contestacoes(conta: str, pedidos: set) -> list:
    """Operações de ressarcimento que o SAC já ligou aos pedidos (lidas das contestações)."""
    try:
        with open(CONTESTACOES, encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        return []
    ops = []
    for x in (dados.get(conta) or {}).values():
        if str(x.get("pedido")) in pedidos and x.get("operacao"):
            ops.append({"operacao": x["operacao"], "claim": x.get("claim_id"), "confianca": x.get("confianca")})
    return ops


def _pagamentos_da_venda(conta: str, numero: str) -> tuple:
    """(pedidos, pagamentos): busca pela referência da venda; se for um pacote, abre os pedidos dele."""
    st, r = _get(conta, "/v1/payments/search", external_reference=numero, sort="date_created", criteria="asc")
    pags = (r.get("results") or []) if st == 200 else []
    if pags:
        return {numero}, pags
    pedidos = set()
    try:
        cli = httpx.get(f"https://api.mercadolibre.com/packs/{numero}", headers={"Authorization": f"Bearer {ml_api.get_token(conta)}"},
                        timeout=30, verify=False)
        if cli.status_code == 200:
            pedidos = {str(o.get("id")) for o in cli.json().get("orders") or []}
    except httpx.HTTPError:
        pass
    for ped in sorted(pedidos):
        st, r = _get(conta, "/v1/payments/search", external_reference=ped, sort="date_created", criteria="asc")
        pags += (r.get("results") or []) if st == 200 else []
    return pedidos | {numero}, pags


def _creditos_reclamacao(conta: str, desde: str, dias: int = 60) -> list:
    """Créditos 'correspondente a sua reclamação' a partir de `desde` (ISO), até `dias` depois. Só leitura."""
    from datetime import datetime, timedelta
    try:
        ini = datetime.fromisoformat(str(desde)[:19])
    except ValueError:
        return []
    fim = ini + timedelta(days=dias)
    saida, off = [], 0
    for _ in range(20):
        st, r = _get(conta, "/v1/payments/search", operation_type="money_transfer", range="date_created",
                     begin_date=ini.strftime("%Y-%m-%dT00:00:00.000-03:00"), end_date=fim.strftime("%Y-%m-%dT23:59:59.000-03:00"),
                     sort="date_created", criteria="asc", limit=100, offset=off)
        res = (r.get("results") or []) if st == 200 else []
        saida += [p for p in res if p.get("status") == "approved" and "reclama" in str(p.get("description") or "").lower()]
        off += len(res)
        if len(res) < 100:
            break
    return saida


def venda(numero, conta: str = None) -> dict:
    """Entradas e saídas da venda no Mercado Pago e o saldo final."""
    n = str(numero or "").strip()
    if not n.isdigit():
        raise ValueError("informe o número da venda (pedido ou pacote, só dígitos)")
    for c in _contas(conta):
        pedidos, pags = _pagamentos_da_venda(c, n)
        if not pags:
            continue
        movimentos = []
        for p in pags:
            movimentos.append({"data": p.get("date_approved") or p.get("date_created"), "tipo": "entrada", "operacao": p.get("id"),
                               "descricao": "Venda recebida (" + str(p.get("description") or "")[:60] + ")", "valor": p.get("transaction_amount") or 0})
            for ch in p.get("charges_details") or []:
                if (ch.get("accounts") or {}).get("from") == "payer":
                    continue          # cobrança do comprador (juros), não sai do vendedor
                original = (ch.get("amounts") or {}).get("original") or 0
                if original:
                    movimentos.append({"data": p.get("date_approved"), "tipo": "saída", "operacao": p.get("id"),
                                       "descricao": _nome_cobranca(ch.get("name"), ch.get("type")), "valor": -original})
            for e in _estornos(p):
                if e["coberto_pelo_ml"]:
                    movimentos.append({"data": e["data"], "tipo": "info", "operacao": e["id"],
                                       "descricao": "Devolução ao comprador coberta pelo ML (não sai do vendedor)", "valor": 0})
                    continue
                movimentos.append({"data": e["data"], "tipo": "saída", "operacao": e["id"], "descricao": "Estorno ao comprador", "valor": -(e["valor"] or 0)})
            if p.get("refunds") and p.get("status_detail") != "bpp_covered":
                for ch in p.get("charges_details") or []:
                    devolvido = (ch.get("amounts") or {}).get("refunded") or 0
                    if devolvido and (ch.get("accounts") or {}).get("from") != "payer":
                        movimentos.append({"data": (p.get("refunds") or [{}])[-1].get("date_created"), "tipo": "entrada", "operacao": p.get("id"),
                                           "descricao": _nome_cobranca(ch.get("name"), ch.get("type")) + " devolvida", "valor": devolvido})
        vistos = {m["operacao"] for m in movimentos}
        for o in _operacoes_das_contestacoes(c, pedidos):
            if o["operacao"] in vistos:
                continue
            st, p = _get(c, f"/v1/payments/{o['operacao']}")
            if st == 200 and p.get("operation_type") == "money_transfer" and p.get("status") == "approved":
                movimentos.append({"data": p.get("date_approved") or p.get("date_created"), "tipo": "entrada", "operacao": p.get("id"),
                                   "descricao": f"Ressarcimento da contestação {o['claim']}: {p.get('description')}"
                                   + ("" if o.get("confianca") == "confirmado" else f" (vínculo {o.get('confianca')})"),
                                   "valor": p.get("transaction_amount") or 0})
        # sem ressarcimento ligado pela contestação: procura crédito de reclamação com o valor de cada estorno debitado
        if not any(str(m["descricao"]).startswith("Ressarcimento") for m in movimentos):
            debitos = [m for m in movimentos if m["descricao"] == "Estorno ao comprador"]
            for d in debitos:
                for p in _creditos_reclamacao(c, d["data"]):
                    if p.get("id") not in vistos and abs((p.get("transaction_amount") or 0) + d["valor"]) < 0.01:
                        movimentos.append({"data": p.get("date_approved") or p.get("date_created"), "tipo": "entrada", "operacao": p.get("id"),
                                           "descricao": f"Ressarcimento provável (mesmo valor do estorno): {p.get('description')}",
                                           "valor": p.get("transaction_amount") or 0, "provavel": True})
                        vistos.add(p.get("id"))
                        break
        movimentos.sort(key=lambda m: str(m["data"] or ""))
        entradas = round(sum(m["valor"] for m in movimentos if m["valor"] > 0), 2)
        saidas = round(sum(m["valor"] for m in movimentos if m["valor"] < 0), 2)
        return {"encontrado": True, "conta": c, "venda": n, "pedidos": sorted(pedidos), "movimentos": movimentos,
                "entradas": entradas, "saidas": saidas, "saldo": round(entradas + saidas, 2),
                "ressarcido": round(sum(m["valor"] for m in movimentos if str(m["descricao"]).startswith("Ressarcimento")), 2)}
    return {"encontrado": False, "venda": n, "motivo": "nenhum pagamento dessa venda nas contas do Mercado Pago"}
