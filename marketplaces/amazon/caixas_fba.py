"""
marketplaces/amazon/caixas_fba.py — Base de peso/medida por SKU e montador de caixas FBA (somente leitura na Amazon)
MZ App

Caixa padrão 57x33x33 cm, no máximo 22 kg (limite que usamos para a Amazon). A base por SKU fica em
amazon_fba_base_sku.json (gitignored) e junta três fontes, nesta ordem de confiança:
  1. "historico": caixas de um SKU só em envios anteriores (peso real da balança e quantidade que coube)
  2. "amazon"   : medida e peso de embalagem que a Amazon mediu no FBA (Catalog Items, dimensions)
  3. "manual"   : valor digitado pela expedição (sempre vence: `ajustar`)

  python -m marketplaces.amazon.caixas_fba base amazon02                      atualiza a base (catálogo + histórico)
  python -m marketplaces.amazon.caixas_fba ver amazon02
  python -m marketplaces.amazon.caixas_fba ajustar amazon02 SKU --peso 0.8 --por-caixa 12
  python -m marketplaces.amazon.caixas_fba montar amazon02 CONTROLPC-FBA=62 KIT3ESCFULLAMZ=38
      mostra as caixas e as opções --caixa prontas para `envio_fba simular` / `criar`
"""

import itertools
import json
import math
import os
import sys

CAIXA_CM = (57, 33, 33)
PESO_MAX = 22.0
TARA = 0.6            # papelão da caixa 57x33x33
OCUPACAO = 0.85       # parte do volume que dá para usar de verdade
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE_PATH = os.path.join(RAIZ, "amazon_fba_base_sku.json")


# ---------- cálculo puro (testado sem rede) ----------

def cabe_por_geometria(dims, caixa=CAIXA_CM) -> int:
    """Quantas unidades da embalagem `dims` (cm) cabem na caixa, na melhor orientação, em grade; limitado pelo volume útil."""
    if not dims or min(dims) <= 0:
        return 0
    grade = max(math.floor(caixa[0] / o[0]) * math.floor(caixa[1] / o[1]) * math.floor(caixa[2] / o[2])
                for o in itertools.permutations(dims))
    vol = math.floor(caixa[0] * caixa[1] * caixa[2] * OCUPACAO / (dims[0] * dims[1] * dims[2]))
    return max(grade, min(vol, grade * 2)) if grade else 0


def capacidade(item: dict) -> int:
    """Máximo por caixa: o que já coube de verdade (histórico/manual) ou a geometria; nunca acima do peso de 22 kg."""
    peso = float(item.get("peso_kg") or 0)
    manual = (item.get("manual") or {}).get("por_caixa")
    # o histórico é o que coube numa caixa já enviada (piso, nem sempre a caixa estava cheia): vale o maior dos dois
    por = int(manual) if manual else max(int(item.get("por_caixa") or 0), cabe_por_geometria(item.get("dims_cm")))
    if peso > 0:
        por = min(por, math.floor((PESO_MAX - TARA) / peso)) if por else math.floor((PESO_MAX - TARA) / peso)
    return max(por, 0)


def montar(pedido: dict, base: dict) -> list:
    """pedido {sku: qtd} -> caixas [{"itens": {sku: qtd}, "peso": kg}]. Enche caixas de um SKU só; as sobras vão juntas
    em caixas mistas (first-fit por fração do volume da caixa e peso)."""
    faltando = [s for s in pedido if capacidade(base.get(s) or {}) <= 0 or not (base.get(s) or {}).get("peso_kg")]
    if faltando:
        raise ValueError("sem peso/medida na base: " + ", ".join(faltando) + " (use `ajustar`)")
    caixas, sobras = [], []
    for sku, qtd in pedido.items():
        cap = capacidade(base[sku])
        cheias, resto = divmod(int(qtd), cap)
        caixas += [{"itens": {sku: cap}} for _ in range(cheias)]
        if resto:
            sobras.append((sku, resto, resto / cap))
    mistas = []
    for sku, q, frac in sorted(sobras, key=lambda x: -x[2]):
        peso = q * float(base[sku]["peso_kg"])
        for m in mistas:
            if m["_frac"] + frac <= 1.0 and m["_peso"] + peso <= PESO_MAX - TARA:
                m["itens"][sku] = q; m["_frac"] += frac; m["_peso"] += peso
                break
        else:
            mistas.append({"itens": {sku: q}, "_frac": frac, "_peso": peso})
    for c in caixas + mistas:
        c["peso"] = round(TARA + sum(q * float(base[s]["peso_kg"]) for s, q in c["itens"].items()), 1)
        c.pop("_frac", None); c.pop("_peso", None)
    return caixas + mistas


def opcoes_caixa(caixas: list) -> list:
    """Agrupa caixas iguais no formato do envio_fba: "N;57x33x33;peso;SKU=q,SKU2=q"."""
    grupos = {}
    for c in caixas:
        chave = (tuple(sorted(c["itens"].items())), c["peso"])
        grupos[chave] = grupos.get(chave, 0) + 1
    dim = "x".join(str(v) for v in CAIXA_CM)
    return [f"{n};{dim};{peso};" + ",".join(f"{s}={q}" for s, q in itens) for (itens, peso), n in grupos.items()]


# ---------- base em disco e coleta ----------

def carregar() -> dict:
    try:
        with open(BASE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def salvar(base: dict):
    with open(BASE_PATH, "w", encoding="utf-8") as f:      # direto no arquivo (os.replace falha com WinError 32)
        json.dump(base, f, ensure_ascii=False, indent=1)


def _cm(v):
    u = (v or {}).get("unit", "").lower()
    x = float((v or {}).get("value") or 0)
    return round(x * 2.54, 1) if u.startswith("inch") else round(x / 10, 1) if u.startswith("milli") else round(x, 1)


def _kg(v):
    u = (v or {}).get("unit", "").lower()
    x = float((v or {}).get("value") or 0)
    return round(x * 0.4536, 3) if u.startswith("pound") else round(x / 1000, 3) if u.startswith("gram") else round(x, 3)


def atualizar_base(conta: str, progresso=print) -> dict:
    from marketplaces.amazon import api
    base = carregar()
    b = base.setdefault(conta, {})
    # 1) catálogo: medida e peso que a Amazon mediu, para cada SKU do inventário FBA
    skus = {}
    for i in api.estoque_fba(conta) or []:
        sku = i.get("sellerSku") or i.get("sku")
        if sku and i.get("asin"):
            skus[sku] = i["asin"]
    for sku, asin in skus.items():
        st, r = api.get(conta, f"/catalog/2022-04-01/items/{asin}", marketplaceIds=api.MARKETPLACE_BR, includedData="dimensions")
        p = (((r or {}).get("dimensions") or [{}])[0]).get("package") or {}
        if p.get("weight"):
            it = b.setdefault(sku, {})
            it["asin"] = asin
            it["amazon"] = {"dims_cm": [_cm(p.get("length")), _cm(p.get("width")), _cm(p.get("height"))], "peso_kg": _kg(p.get("weight"))}
    # 2) histórico: caixas de um SKU só nos planos já feitos por este app
    try:
        with open(os.path.join(RAIZ, "amazon_fba_planos.json"), encoding="utf-8") as f:
            planos = [v for v in json.load(f).values() if v.get("conta") == conta]
    except (OSError, ValueError):
        planos = []
    for pl in planos:
        st, r = api.get(conta, f"/inbound/fba/2024-03-20/inboundPlans/{pl['plano']}/boxes", pageSize=100)
        for cx in (r or {}).get("boxes", []):
            itens = cx.get("items") or []
            if len(itens) != 1 or not (cx.get("weight") or {}).get("value"):
                continue
            sku, q = itens[0]["msku"], int(itens[0]["quantity"])
            peso_un = round((_kg(cx["weight"]) - TARA) / q, 3)
            h = b.setdefault(sku, {}).setdefault("historico", {"por_caixa": 0, "peso_kg": peso_un})
            if q >= h["por_caixa"]:
                h["por_caixa"], h["peso_kg"] = q, peso_un
    for sku, it in b.items():
        _consolidar(it)
    salvar(base)
    progresso(f"{conta}: {len(b)} SKUs na base")
    return base


def _consolidar(it: dict):
    """Valor usado = manual > histórico > Amazon (campo a campo)."""
    m, h, a = it.get("manual") or {}, it.get("historico") or {}, it.get("amazon") or {}
    it["peso_kg"] = m.get("peso_kg") or h.get("peso_kg") or a.get("peso_kg")
    it["por_caixa"] = m.get("por_caixa") or h.get("por_caixa") or None
    it["dims_cm"] = m.get("dims_cm") or a.get("dims_cm")
    it["fonte"] = "manual" if m else "historico" if h else "amazon" if a else "?"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(__doc__); return 1
    cmd, conta = argv[0], argv[1]
    if cmd == "base":
        atualizar_base(conta); cmd = "ver"
    base = carregar()
    b = base.get(conta, {})
    if cmd == "ver":
        print(f"{'SKU':24} {'peso un':>8} {'por cx':>6} {'fonte':10} medida embalagem")
        for sku, it in sorted(b.items()):
            print(f"{sku:24} {it.get('peso_kg') or 0:>8.3f} {capacidade(it):>6} {it.get('fonte',''):10} {it.get('dims_cm')}")
        return 0
    if cmd == "ajustar":
        sku = argv[2]
        it = b.setdefault(sku, {}); man = it.setdefault("manual", {})
        for flag, campo, conv in (("--peso", "peso_kg", float), ("--por-caixa", "por_caixa", int)):
            if flag in argv:
                man[campo] = conv(argv[argv.index(flag) + 1])
        _consolidar(it); base[conta] = b; salvar(base)
        print(f"{sku}: peso {it['peso_kg']} kg, {capacidade(it)} por caixa ({it['fonte']})"); return 0
    if cmd == "montar":
        pedido = {s.split("=")[0]: int(s.split("=")[1]) for s in argv[2:]}
        caixas = montar(pedido, b)
        for i, c in enumerate(caixas, 1):
            print(f"  caixa {i}: " + " + ".join(f"{s} {q}" for s, q in c["itens"].items()) + f"  ·  {c['peso']} kg")
        print(f"  total: {len(caixas)} caixas · {sum(pedido.values())} unidades")
        print("  opções para o envio_fba: " + " ".join(f'--caixa "{o}"' for o in opcoes_caixa(caixas)))
        return 0
    print(__doc__); return 1


if __name__ == "__main__":
    sys.exit(main())
