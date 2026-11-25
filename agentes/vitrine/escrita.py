"""
agentes/vitrine/escrita.py — A ÚNICA porta de escrita do Vitrine na Amazon (Listings e Ads)
MZ App

Toda ação: lê o mandato → passa pela cerca → executa (ou simula) → grava no diário.
Sem --confirmar nada é gravado: no Listings usa VALIDATION_PREVIEW (a Amazon valida e não aplica); no Ads
só mostra o corpo que seria enviado. O agente decide O QUE fazer; a cerca decide SE pode.
"""

import json
import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from agentes.vitrine import ads_cliente as ads
from agentes.vitrine import cerca
from marketplaces.amazon import api

PASTA = os.path.dirname(os.path.abspath(__file__))
MANDATO = os.path.join(PASTA, "mandato.json")
PARAR = os.path.join(PASTA, "PARAR")
DIARIO = os.path.join(PASTA, "dados", "diario.jsonl")
ESTADO = os.path.join(PASTA, "dados", "estado.json")
BRT = timezone(timedelta(hours=-3))


def hoje() -> date:
    return datetime.now(BRT).date()


def carregar_mandato() -> dict:
    with open(MANDATO, encoding="utf-8") as f:
        return json.load(f)


def ler_estado() -> dict:
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def gravar_estado(estado: dict):
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    tmp = ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ESTADO)


def registrar(entrada: dict):
    os.makedirs(os.path.dirname(DIARIO), exist_ok=True)
    entrada = {"quando": datetime.now(BRT).isoformat(timespec="seconds"), **entrada}
    with open(DIARIO, "a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")


def _base(acao: dict):
    m = carregar_mandato()
    cerca.checar_geral(m, hoje(), os.path.exists(PARAR))
    for k in ("motivo", "esperado"):
        if not str(acao.get(k, "")).strip():
            raise cerca.Recusa(f"toda ação precisa de '{k}' (é o que o diário usa para medir o acerto)")
    return m


# ─── Listings ─────────────────────────────────────────────────────────────────

def _listing(conta, sku):
    sid = api.conta_cfg(conta)["seller_id"]
    st, b = api.get(conta, f"/listings/2021-08-01/items/{sid}/{quote(sku, safe='')}", marketplaceIds=api.MARKETPLACE_BR,
                    includedData="summaries,attributes,offers")
    if st != 200:
        raise api.falha(st, b)
    return sid, b


def _preco_atual(listing: dict):
    for o in listing.get("offers") or []:
        try:
            return float((o.get("price") or {}).get("amount"))
        except (TypeError, ValueError):
            pass
    return None


def listing(acao: dict, confirmar: bool) -> dict:
    """acao = {tipo: listing, sku, patches: [...], motivo, esperado}"""
    m = _base(acao)
    sid, atual = _listing(m["conta"], acao["sku"])
    cerca.checar_listing(m, acao["sku"], acao["patches"], _preco_atual(atual))
    pt = (atual.get("summaries") or [{}])[0].get("productType")
    params = {"marketplaceIds": api.MARKETPLACE_BR, "issueLocale": "pt_BR"}
    if not confirmar:
        params["mode"] = "VALIDATION_PREVIEW"
    st, r = api._enviar(m["conta"], "PATCH", f"/listings/2021-08-01/items/{sid}/{quote(acao['sku'], safe='')}",
                        params=params, corpo={"productType": pt, "patches": acao["patches"]})
    res = {"http": st, "status": r.get("status"), "issues": r.get("issues"), "submissionId": r.get("submissionId")}
    return res


# ─── Ads ──────────────────────────────────────────────────────────────────────

def campanhas_vitrine(m) -> list:
    return [c for c in ads.listar(m["conta"], "campaign", "campaigns") if cerca.e_do_vitrine(m, c.get("name"))]


def _soma_ativas(m, exceto=None) -> float:
    return sum(float((c.get("budget") or {}).get("budget") or 0) for c in campanhas_vitrine(m)
               if c.get("state") == "ENABLED" and str(c.get("campaignId")) != str(exceto))


def _checar_gasto(m):
    cerca.checar_gasto(m, float(ler_estado().get("gasto_acumulado", 0)))


def _ok(resp, chave):
    bloco = (resp or {}).get(chave) or {}
    if bloco.get("error"):
        raise ads.ErroAds(400, f"Amazon recusou: {json.dumps(bloco['error'], ensure_ascii=False)[:500]}")
    return [s.get(chave[:-1] + "Id") or s.get("adId") or s.get("keywordId") or s.get("targetId") for s in bloco.get("success", [])]


def criar_campanha(acao: dict, confirmar: bool) -> dict:
    """acao = {tipo: criar_campanha, nome, segmentacao: AUTO|MANUAL, orcamento, lance, skus: [...],
               palavras: [{texto, tipo: EXACT|PHRASE|BROAD, lance}], negativas: [{texto, tipo: NEGATIVE_EXACT|NEGATIVE_PHRASE}],
               estrategia: LEGACY_FOR_SALES|AUTO_FOR_SALES|MANUAL, motivo, esperado}"""
    m = _base(acao)
    _checar_gasto(m)
    fim = acao.get("fim") or m["fim"]
    cerca.checar_campanha_nova(m, acao["nome"], float(acao["orcamento"]), fim, _soma_ativas(m))
    cerca.checar_lance(m, float(acao["lance"]))
    for s in acao["skus"]:
        cerca.checar_anuncio_sku(m, s)
    for p in acao.get("palavras", []):
        cerca.checar_lance(m, float(p.get("lance", acao["lance"])))
    inicio = max(hoje(), date.fromisoformat(m["inicio"])).isoformat()
    camp = {"name": acao["nome"], "targetingType": acao.get("segmentacao", "AUTO"), "state": "ENABLED",
            "startDate": inicio, "endDate": fim,
            "dynamicBidding": {"strategy": acao.get("estrategia", "LEGACY_FOR_SALES")},
            "budget": {"budgetType": "DAILY", "budget": float(acao["orcamento"])}}
    if not confirmar:
        return {"simulado": True, "campanha": camp, "skus": acao["skus"], "lance": acao["lance"],
                "palavras": acao.get("palavras", []), "negativas": acao.get("negativas", [])}
    cid = _ok(ads.chamar(m["conta"], "POST", "/sp/campaigns", {"campaigns": [camp]}, "campaign"), "campaigns")[0]
    gid = _ok(ads.chamar(m["conta"], "POST", "/sp/adGroups", {"adGroups": [
        {"campaignId": cid, "name": acao["nome"] + " | grupo", "state": "ENABLED", "defaultBid": float(acao["lance"])}]},
        "adGroup"), "adGroups")[0]
    ids = _ok(ads.chamar(m["conta"], "POST", "/sp/productAds", {"productAds": [
        {"campaignId": cid, "adGroupId": gid, "sku": s, "state": "ENABLED"} for s in acao["skus"]]}, "productAd"), "productAds")
    kw = []
    if acao.get("palavras"):
        kw = _ok(ads.chamar(m["conta"], "POST", "/sp/keywords", {"keywords": [
            {"campaignId": cid, "adGroupId": gid, "keywordText": p["texto"], "matchType": p.get("tipo", "PHRASE"),
             "bid": float(p.get("lance", acao["lance"])), "state": "ENABLED"} for p in acao["palavras"]]}, "keyword"), "keywords")
    neg = []
    if acao.get("negativas"):
        neg = _ok(ads.chamar(m["conta"], "POST", "/sp/negativeKeywords", {"negativeKeywords": [
            {"campaignId": cid, "adGroupId": gid, "keywordText": n["texto"], "matchType": n.get("tipo", "NEGATIVE_PHRASE"),
             "state": "ENABLED"} for n in acao["negativas"]]}, "negativeKeyword"), "negativeKeywords")
    return {"campaignId": cid, "adGroupId": gid, "productAds": ids, "keywords": kw, "negativas": neg}


def _campanha_vitrine(m, cid) -> dict:
    for c in campanhas_vitrine(m):
        if str(c.get("campaignId")) == str(cid):
            return c
    raise cerca.Recusa(f"campanha {cid} não é do Vitrine (ou não existe)")


def ajustar_campanha(acao: dict, confirmar: bool) -> dict:
    """acao = {tipo: ajustar_campanha, campaignId, orcamento?, estado?: ENABLED|PAUSED, motivo, esperado}"""
    m = _base(acao)
    c = _campanha_vitrine(m, acao["campaignId"])
    novo = {"campaignId": str(acao["campaignId"])}
    estado = acao.get("estado", c.get("state"))
    if estado not in ("ENABLED", "PAUSED"):
        raise cerca.Recusa("estado deve ser ENABLED ou PAUSED")
    if "estado" in acao:
        novo["state"] = estado
    orc = float(acao.get("orcamento", (c.get("budget") or {}).get("budget") or 0))
    if estado == "ENABLED":
        _checar_gasto(m)
        cerca.checar_orcamento(m, orc, _soma_ativas(m, exceto=acao["campaignId"]))
    if "orcamento" in acao:
        novo["budget"] = {"budgetType": "DAILY", "budget": orc}
    if not confirmar:
        return {"simulado": True, "antes": {"state": c.get("state"), "budget": c.get("budget")}, "depois": novo}
    return {"ids": _ok(ads.chamar(m["conta"], "PUT", "/sp/campaigns", {"campaigns": [novo]}, "campaign"), "campaigns")}


def ajustar_palavras(acao: dict, confirmar: bool) -> dict:
    """acao = {tipo: ajustar_palavras, campaignId, adicionar: [{texto,tipo,lance}], lances: [{keywordId, lance?, estado?}],
               negativas: [{texto, tipo}], motivo, esperado}"""
    m = _base(acao)
    c = _campanha_vitrine(m, acao["campaignId"])
    grupos = ads.listar(m["conta"], "adGroup", "adGroups", {"campaignIdFilter": {"include": [str(c["campaignId"])]}})
    if not grupos:
        raise cerca.Recusa("campanha sem grupo de anúncios")
    gid = grupos[0]["adGroupId"]
    minhas = {str(k["keywordId"]) for k in ads.listar(m["conta"], "keyword", "keywords",
                                                      {"campaignIdFilter": {"include": [str(c["campaignId"])]}})}
    for p in acao.get("adicionar", []):
        cerca.checar_lance(m, float(p["lance"]))
    for l in acao.get("lances", []):
        if str(l["keywordId"]) not in minhas:
            raise cerca.Recusa(f"palavra {l['keywordId']} não pertence a esta campanha do Vitrine")
        if "lance" in l:
            cerca.checar_lance(m, float(l["lance"]))
        if l.get("estado") not in (None, "ENABLED", "PAUSED"):
            raise cerca.Recusa("estado de palavra deve ser ENABLED ou PAUSED")
    if not confirmar:
        return {"simulado": True, **{k: acao.get(k) for k in ("adicionar", "lances", "negativas")}}
    out = {}
    if acao.get("adicionar"):
        out["adicionadas"] = _ok(ads.chamar(m["conta"], "POST", "/sp/keywords", {"keywords": [
            {"campaignId": str(c["campaignId"]), "adGroupId": gid, "keywordText": p["texto"], "matchType": p.get("tipo", "PHRASE"),
             "bid": float(p["lance"]), "state": "ENABLED"} for p in acao["adicionar"]]}, "keyword"), "keywords")
    if acao.get("lances"):
        corpo = [{"keywordId": str(l["keywordId"]), **({"bid": float(l["lance"])} if "lance" in l else {}),
                  **({"state": l["estado"]} if l.get("estado") else {})} for l in acao["lances"]]
        out["ajustadas"] = _ok(ads.chamar(m["conta"], "PUT", "/sp/keywords", {"keywords": corpo}, "keyword"), "keywords")
    if acao.get("negativas"):
        out["negativas"] = _ok(ads.chamar(m["conta"], "POST", "/sp/negativeKeywords", {"negativeKeywords": [
            {"campaignId": str(c["campaignId"]), "adGroupId": gid, "keywordText": n["texto"],
             "matchType": n.get("tipo", "NEGATIVE_PHRASE"), "state": "ENABLED"} for n in acao["negativas"]]},
            "negativeKeyword"), "negativeKeywords")
    return out


def pausar_anuncios_fora(acao: dict, confirmar: bool) -> dict:
    """Pausa os anúncios (productAds) dos SKUs do mandato em campanhas que NÃO são do Vitrine, para todo o gasto
    com estes produtos ficar dentro do teto e ser medido. Só PAUSA; nunca reativa nem mexe em outro SKU.
    acao = {tipo: pausar_anuncios_fora, motivo, esperado}"""
    m = _base(acao)
    skus = {s for p in m["produtos"] for s in p["skus"]}
    vitrine = {str(c["campaignId"]) for c in campanhas_vitrine(m)}
    alvo = [a for a in ads.listar(m["conta"], "productAd", "productAds")
            if a.get("sku") in skus and a.get("state") == "ENABLED" and str(a.get("campaignId")) not in vitrine]
    for a in alvo:
        cerca.checar_anuncio_sku(m, a["sku"])
    if not confirmar or not alvo:
        return {"simulado": not confirmar, "anuncios": [{k: a.get(k) for k in ("adId", "campaignId", "sku")} for a in alvo]}
    return {"pausados": _ok(ads.chamar(m["conta"], "PUT", "/sp/productAds", {"productAds": [
        {"adId": str(a["adId"]), "state": "PAUSED"} for a in alvo]}, "productAd"), "productAds")}


def pausar_tudo(motivo: str) -> dict:
    """Freio: pausa TODAS as campanhas do Vitrine. Não passa pela janela/PARAR (parar sempre é permitido)."""
    m = carregar_mandato()
    ativas = [c for c in campanhas_vitrine(m) if c.get("state") == "ENABLED"]
    ids = []
    if ativas:
        ids = _ok(ads.chamar(m["conta"], "PUT", "/sp/campaigns", {"campaigns": [
            {"campaignId": str(c["campaignId"]), "state": "PAUSED"} for c in ativas]}, "campaign"), "campaigns")
    registrar({"tipo": "pausar_tudo", "motivo": motivo, "resultado": {"pausadas": ids}, "confirmado": True})
    return {"pausadas": ids}


ACOES = {"listing": listing, "criar_campanha": criar_campanha, "ajustar_campanha": ajustar_campanha,
         "ajustar_palavras": ajustar_palavras, "pausar_anuncios_fora": pausar_anuncios_fora}


def executar(acao: dict, confirmar: bool = False) -> dict:
    fn = ACOES.get(acao.get("tipo"))
    if not fn:
        raise cerca.Recusa(f"tipo de ação desconhecido: {acao.get('tipo')}; permitidos: {', '.join(ACOES)}")
    base = {"tipo": acao.get("tipo"), "alvo": acao.get("sku") or acao.get("campaignId") or acao.get("nome"),
            "motivo": acao.get("motivo"), "esperado": acao.get("esperado"), "confirmado": confirmar}
    try:
        res = fn(acao, confirmar)
    except cerca.Recusa as e:
        registrar({**base, "recusado": str(e)})
        raise
    except (api.ErroAmazon, ads.ErroAds) as e:
        registrar({**base, "erro": str(e)[:500]})
        raise
    registrar({**base, "acao": {k: v for k, v in acao.items() if k not in ("motivo", "esperado")}, "resultado": res})
    return res
