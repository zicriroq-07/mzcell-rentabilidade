"""
agentes/vitrine/ads_cliente.py — Cliente mínimo da Amazon Advertising API (Sponsored Products v3)
MZ App

Mesmo padrão do painel (mz-clone/marketplaces/amazon/ads_api.py): credenciais em amazon_ads_config.json
(gitignored), access_token só em memória, erros mascarados. Este arquivo só fala com a API; quem decide se a
escrita é permitida é a cerca (cerca.py) e quem chama é escrita.py.
"""

import gzip
import json
import os
import time

import httpx

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOST = "https://advertising-api.amazon.com"          # NA: Brasil incluso
TOKEN_URL = "https://api.amazon.com/auth/o2/token"
_tokens = {}

CT = {"campaign": "application/vnd.spCampaign.v3+json", "adGroup": "application/vnd.spAdGroup.v3+json",
      "productAd": "application/vnd.spProductAd.v3+json", "keyword": "application/vnd.spKeyword.v3+json",
      "negativeKeyword": "application/vnd.spNegativeKeyword.v3+json",
      "targeting": "application/vnd.spTargetingClause.v3+json"}


class ErroAds(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def _cfg(conta):
    try:
        with open(os.path.join(BASE, "amazon_ads_config.json"), encoding="utf-8-sig") as f:
            c = json.load(f)[conta]
    except (OSError, KeyError, ValueError):
        raise ErroAds(404, f"{conta}: sem credencial da Ads API (amazon_ads_config.json)")
    for k in ("client_id", "client_secret", "refresh_token", "profile_id"):
        if not c.get(k):
            raise ErroAds(404, f"{conta}: falta {k} em amazon_ads_config.json")
    return c


def _mascarar(txt, c):
    t = str(txt)
    for k in ("client_secret", "refresh_token", "client_id"):
        if c.get(k):
            t = t.replace(c[k].strip(), "***")
    return t[:500]


def _token(conta, forcar=False):
    t = _tokens.get(conta)
    if t and not forcar and t["ate"] > time.time() + 120:
        return t["token"]
    c = _cfg(conta)
    r = httpx.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": c["refresh_token"].strip(),
                                    "client_id": c["client_id"].strip(), "client_secret": c["client_secret"].strip()}, timeout=40)
    if r.status_code != 200:
        raise ErroAds(r.status_code, "Não renovou o token da Ads API: " + _mascarar(r.text, c))
    j = r.json()
    _tokens[conta] = {"token": j["access_token"], "ate": time.time() + int(j.get("expires_in", 3600))}
    return _tokens[conta]["token"]


def chamar(conta, metodo, caminho, corpo=None, tipo=None, params=None):
    c = _cfg(conta)
    for tentativa in range(6):
        h = {"Authorization": "Bearer " + _token(conta), "Amazon-Advertising-API-ClientId": c["client_id"].strip(),
             "Amazon-Advertising-API-Scope": str(c["profile_id"])}
        if tipo:
            h["Content-Type"] = h["Accept"] = CT.get(tipo, tipo)
        r = httpx.request(metodo, HOST + caminho, headers=h, json=corpo, params=params, timeout=90)
        if r.status_code == 401 and tentativa == 0:
            _token(conta, forcar=True)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(3 * (tentativa + 1))
            continue
        if r.status_code >= 400:
            raise ErroAds(r.status_code, f"{metodo} {caminho}: HTTP {r.status_code} {_mascarar(r.text, c)}")
        return r.json() if r.content else {}
    raise ErroAds(429, f"{metodo} {caminho}: limite de chamadas, tente de novo em instantes")


def listar(conta, tipo, plural, filtro=None):
    """POST /sp/<plural>/list com paginação. filtro = corpo extra (ex.: {"campaignIdFilter": {"include": [...]}})."""
    out, token = [], None
    while True:
        corpo = {"maxResults": 100, **(filtro or {})}
        if token:
            corpo["nextToken"] = token
        j = chamar(conta, "POST", f"/sp/{plural}/list", corpo, tipo)
        out += j.get(plural, [])
        token = j.get("nextToken")
        if not token:
            return out


def _id_duplicado(e):
    """Id do relatório já pendente numa recusa 425 'duplicate of : <uuid>'; None se não for esse caso."""
    import re
    if getattr(e, "status", None) != 425:
        return None
    m = re.search(r"duplicate of\s*:?\s*([0-9a-fA-F-]{36})", str(e))
    return m.group(1) if m else None


def relatorio(conta, nome, tipo, de, ate, colunas, agrupar, diario=True, espera_max=2700):
    # 45 min: em nov/2026 o spAdvertisedProduct passou de 15 min 3 vezes em 2 dias (pico de Black Friday)
    """Reporting v3 (assíncrono): cria, espera e devolve as linhas."""
    cfg = {"adProduct": "SPONSORED_PRODUCTS", "groupBy": agrupar, "columns": colunas, "reportTypeId": tipo,
           "format": "GZIP_JSON", "timeUnit": "DAILY" if diario else "SUMMARY"}
    try:
        j = chamar(conta, "POST", "/reporting/reports", {"name": nome, "startDate": de, "endDate": ate, "configuration": cfg},
                   "application/vnd.createasyncreportrequest.v3+json")
        rid = j["reportId"]
    except ErroAds as e:
        # 425 = já existe um relatório igual sendo gerado (ex.: de uma coleta anterior que desistiu de esperar):
        # a Amazon devolve o id dele ("duplicate of : <id>") e o certo é esperar esse, não falhar
        rid = _id_duplicado(e)
        if not rid:
            raise
    t0, pausa = time.time(), 5
    while time.time() - t0 < espera_max:
        s = chamar(conta, "GET", f"/reporting/reports/{rid}")
        if s.get("status") == "COMPLETED":
            r = httpx.get(s["url"], timeout=120)
            return json.loads(gzip.decompress(r.content).decode("utf-8"))
        if s.get("status") == "FAILURE":
            raise ErroAds(500, f"relatório {tipo} falhou: {str(s.get('failureReason'))[:200]}")
        time.sleep(pausa)
        pausa = min(pausa + 3, 20)
    raise ErroAds(504, f"relatório {tipo} não ficou pronto em {espera_max // 60} min")
