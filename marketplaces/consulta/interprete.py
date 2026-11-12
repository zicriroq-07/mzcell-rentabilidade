"""
marketplaces/consulta/interprete.py — Entende a pergunta em português sem IA
MZ App

Compara o texto com as palavras de cada consulta do banco (frases valem mais que palavras soltas) e tira os parâmetros do próprio
texto: número de pedido, SKU, conta e plataforma. Empate ou falta de parâmetro volta como pergunta, nunca como chute.
"""

import re
import unicodedata


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if unicodedata.category(c) != "Mn")).strip()


def _tem(texto: str, palavra: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(palavra)}(?![a-z0-9])", texto) is not None


def extrair(texto: str) -> dict:
    """Parâmetros que dá para ler do texto: numero (pedido), sku, conta, plataforma, dias."""
    p = {}
    bruto = str(texto or "")
    m = re.search(r"(?<![A-Za-z0-9])(\d{10,}|\d{6}[A-Za-z0-9]{6,}|\d{4,9})(?![A-Za-z0-9-])", bruto)
    if m:
        p["numero"] = m.group(1).upper()
    m = re.search(r"(?<![A-Za-z0-9])([A-Za-z0-9]{2,}(?:-[A-Za-z0-9]+)+)(?![A-Za-z0-9])", bruto)
    if m:
        p["sku"] = m.group(1).upper()
    m = re.search(r"\b(?:sku|codigo)\s+([A-Za-z0-9._-]{3,})", normalizar(bruto).replace(" ", " "), re.I)
    if m and "sku" not in p:
        p["sku"] = m.group(1).upper()
    n = normalizar(bruto)
    m = re.search(r"\b(meli|bling|amazon)\s?0?([1-3])\b", n)
    if m:
        p["conta"] = f"{m.group(1)}0{m.group(2)}"
    for nome, termos in (("shopee", ("shopee",)), ("magalu", ("magalu", "magazine")), ("amazon", ("amazon",)),
                         ("meli", ("mercado livre", "meli", "flex"))):
        if any(_tem(n, t) for t in termos):
            p["plataforma"] = nome
            break
    for canal in ("flex", "full", "coleta", "places", "dba"):
        if _tem(n, canal):
            p["canal"] = canal
            break
    if any(_tem(n, t) for t in ("cidade", "cep", "endereco", "bairro")):
        p["endereco"] = "sim"
    m = re.search(r"\b(\d{1,2}) dias\b", n)
    if m:
        p["dias"] = int(m.group(1))
    return p


def interpretar(texto: str, consultas: list) -> dict:
    """{"consulta": id|None, "params": {...}, "candidatas": [ids empatadas], "faltam": [parâmetros obrigatórios sem valor]}"""
    n = normalizar(texto)
    params = extrair(texto)
    placar = []
    for c in consultas:
        pontos = sum(1 + len(p.split()) for p in c["palavras"] if _tem(n, p))
        if not pontos:
            continue
        obrig = [x["nome"] for x in c["params"] if x["obrigatorio"]]
        if obrig and all(o in params for o in obrig):
            pontos += 2
        placar.append((pontos, c))
    if not placar:
        return {"consulta": None, "params": params, "candidatas": [], "faltam": []}
    placar.sort(key=lambda x: -x[0])
    topo = [c for pt, c in placar if pt == placar[0][0]]
    if len(topo) > 1:
        return {"consulta": None, "params": params, "candidatas": [c["id"] for c in topo], "faltam": []}
    c = placar[0][1]
    aceitos = {x["nome"] for x in c["params"]}
    return {"consulta": c["id"], "params": {k: v for k, v in params.items() if k in aceitos},
            "candidatas": [], "faltam": [x["nome"] for x in c["params"] if x["obrigatorio"] and x["nome"] not in params]}
