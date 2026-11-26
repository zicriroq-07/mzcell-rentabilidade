"""
marketplaces/amazon/ads_api.py — Cliente da Amazon Advertising API (somente leitura) + coleta para o painel
MZ App

Credenciais em amazon_ads_config.json (gitignored): {"amazon02": {client_id, client_secret, refresh_token, profile_id}}.
O access_token (60 min) fica só na memória. Nada aqui cria, altera ou pausa campanha: só lista campanhas e pede
relatórios assíncronos (Reporting v3) de Sponsored Products. Erros nunca carregam segredo (mascarados).
Coleta em segundo plano -> amazon_ads_cache_<conta>.json (campanhas + métricas por dia, produto, alvo e termo de busca).
"""

import gzip
import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

import httpx

from marketplaces.amazon import api

BASE = api.BASE_DIR
HOST = "https://advertising-api.amazon.com"          # NA: Brasil incluso
TOKEN_URL = "https://api.amazon.com/auth/o2/token"
BRT = timezone(timedelta(hours=-3))
JANELA_DIAS = 62
LOTE_DIAS = 31                                        # limite de um relatório com granularidade diária
_tokens = {}
_estado = {}
_trava = threading.Lock()


def gravar_json(caminho, obj):
    """Grava JSON sem depender do renomear: no Windows, antivírus/indexador às vezes seguram o arquivo (WinError 32)."""
    tmp = caminho + ".tmp"
    for _ in range(8):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
            os.replace(tmp, caminho)
            return
        except PermissionError:
            time.sleep(1.5)
    with open(caminho, "w", encoding="utf-8") as f:     # último recurso: grava direto no arquivo final
        json.dump(obj, f, ensure_ascii=False)


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
            t = t.replace(c[k], "***")
    return t[:300]


def _access_token(conta, forcar=False):
    t = _tokens.get(conta)
    if t and not forcar and t["ate"] > time.time() + 120:
        return t["token"]
    c = _cfg(conta)
    r = httpx.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": c["refresh_token"],
                                    "client_id": c["client_id"].strip(), "client_secret": c["client_secret"].strip()}, timeout=40)
    if r.status_code != 200:
        raise ErroAds(r.status_code, "Não renovou o token da Ads API: " + _mascarar(r.text, c))
    j = r.json()
    _tokens[conta] = {"token": j["access_token"], "ate": time.time() + int(j.get("expires_in", 3600))}
    return _tokens[conta]["token"]


def _chamar(conta, metodo, caminho, corpo=None, params=None, ctype=None, accept=None, scope=True, bruto=False):
    c = _cfg(conta)
    for tentativa in range(6):
        h = {"Authorization": "Bearer " + _access_token(conta), "Amazon-Advertising-API-ClientId": c["client_id"].strip()}
        if scope:
            h["Amazon-Advertising-API-Scope"] = str(c["profile_id"])
        if ctype:
            h["Content-Type"] = ctype
        if accept:
            h["Accept"] = accept
        r = httpx.request(metodo, HOST + caminho, headers=h, json=corpo, params=params, timeout=90)
        if r.status_code == 401 and tentativa == 0:
            _access_token(conta, forcar=True)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(3 * (tentativa + 1))
            continue
        if r.status_code >= 400:
            raise ErroAds(r.status_code, f"{metodo} {caminho}: HTTP {r.status_code} {_mascarar(r.text, c)}")
        return r if bruto else (r.json() if r.content else {})
    raise ErroAds(429, f"{metodo} {caminho}: limite de chamadas, tente de novo em instantes")


def perfis(conta):
    return _chamar(conta, "GET", "/v2/profiles", scope=False)


def campanhas_sp(conta):
    """Todas as campanhas Sponsored Products (metadados: estado, orçamento, tipo de segmentação, datas)."""
    out, token = [], None
    while True:
        corpo = {"maxResults": 100}
        if token:
            corpo["nextToken"] = token
        j = _chamar(conta, "POST", "/sp/campaigns/list", corpo, ctype="application/vnd.spCampaign.v3+json", accept="application/vnd.spCampaign.v3+json")
        out += j.get("campaigns", [])
        token = j.get("nextToken")
        if not token:
            return out


def relatorio(conta, nome, tipo, de, ate, colunas, agrupar, diario=True, espera_max=900):
    """Pede um relatório Reporting v3, espera ficar pronto e devolve as linhas (lista de dicts)."""
    cfg = {"adProduct": "SPONSORED_PRODUCTS", "groupBy": agrupar, "columns": colunas, "reportTypeId": tipo, "format": "GZIP_JSON",
           "timeUnit": "DAILY" if diario else "SUMMARY"}
    j = _chamar(conta, "POST", "/reporting/reports", {"name": nome, "startDate": de, "endDate": ate, "configuration": cfg},
                ctype="application/vnd.createasyncreportrequest.v3+json")
    rid, t0, pausa = j["reportId"], time.time(), 5
    while time.time() - t0 < espera_max:
        s = _chamar(conta, "GET", f"/reporting/reports/{rid}")
        if s.get("status") == "COMPLETED":
            r = httpx.get(s["url"], timeout=120)
            return json.loads(gzip.decompress(r.content).decode("utf-8"))
        if s.get("status") == "FAILURE":
            raise ErroAds(500, f"relatório {tipo} falhou: {str(s.get('failureReason'))[:200]}")
        time.sleep(pausa)
        pausa = min(pausa + 3, 20)
    raise ErroAds(504, f"relatório {tipo} não ficou pronto em {espera_max // 60} min")
