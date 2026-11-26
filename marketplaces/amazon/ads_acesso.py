"""
marketplaces/amazon/ads_acesso.py — Portão de acesso por PIN para o painel de Ads quando ele escuta na rede
MZ App

Do próprio computador (127.0.0.1) entra direto. De qualquer outro aparelho (celular na mesma rede ou na rede Tailscale) pede um PIN.
O PIN e a chave do cookie ficam em ads_painel_acesso.json (gitignored): {"pin": "123456", "chave": "<aleatória>"}.
Errou 5 vezes: bloqueia aquele IP por 60 s. O cookie é HttpOnly e vale 30 dias. Não protege contra quem enxerga o tráfego Wi-Fi
(HTTP simples): para uso fora de casa, prefira o endereço do Tailscale, que já é criptografado.
"""

import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from marketplaces.amazon import api

ARQ = os.path.join(api.BASE_DIR, "ads_painel_acesso.json")
LOCAIS = ("127.0.0.1", "::1", "localhost")
_falhas = {}

LOGIN = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MZ · Acesso</title><style>
body{margin:0;background:#0a0a0f;color:#f0f0f5;font-family:'Segoe UI',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.c{background:#111118;border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:32px 28px;width:100%;max-width:360px;text-align:center}
.l{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#f6c90e,#f97316);display:flex;align-items:center;justify-content:center;font-weight:800;color:#0a0a0f;margin:0 auto 16px}
h1{font-size:17px;margin-bottom:6px}p{font-size:13px;color:#9ca3af;margin-bottom:20px}
input{width:100%;background:#1a1a24;border:1px solid rgba(255,255,255,.12);color:#f0f0f5;padding:14px;border-radius:10px;font-size:20px;text-align:center;letter-spacing:.3em;margin-bottom:12px}
button{width:100%;background:#f6c90e;color:#0a0a0f;border:none;padding:14px;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer}
.e{color:#ef4444;font-size:13px;margin-bottom:12px}</style></head><body><form class="c" method="post" action="/amazonads/entrar">
<div class="l">MZ</div><h1>Amazon 2 · Rentabilidade + Ads</h1><p>Digite o PIN de acesso</p>%ERRO%
<input name="pin" type="password" inputmode="numeric" autocomplete="one-time-code" autofocus><button>Entrar</button></form></body></html>"""


def _cfg():
    try:
        with open(ARQ, encoding="utf-8-sig") as f:
            c = json.load(f)
        if c.get("pin") and c.get("chave"):
            return c
    except (OSError, ValueError):
        pass
    c = {"pin": "".join(secrets.choice("0123456789") for _ in range(6)), "chave": secrets.token_hex(24)}
    with open(ARQ, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2)
    return c


def _cookie(c):
    return hmac.new(c["chave"].encode(), c["pin"].encode(), hashlib.sha256).hexdigest()


def instalar(app):
    _cfg()

    @app.middleware("http")
    async def portao(request: Request, call_next):
        ip = request.client.host if request.client else ""
        if ip in LOCAIS:
            return await call_next(request)
        c = _cfg()
        if request.url.path == "/amazonads/entrar":
            if request.method == "GET":
                return HTMLResponse(LOGIN.replace("%ERRO%", ""))
            agora = time.time()
            f = [t for t in _falhas.get(ip, []) if agora - t < 60]
            if len(f) >= 5:
                return HTMLResponse(LOGIN.replace("%ERRO%", '<div class="e">Muitas tentativas. Aguarde 1 minuto.</div>'), status_code=429)
            corpo = (await request.body()).decode("utf-8", "ignore")
            pin = ""
            for par in corpo.split("&"):
                if par.startswith("pin="):
                    pin = par[4:]
            if hmac.compare_digest(pin, c["pin"]):
                _falhas.pop(ip, None)
                r = RedirectResponse("/amazonads/painel", status_code=303)
                r.set_cookie("mz_ads", _cookie(c), max_age=30 * 86400, httponly=True, samesite="lax")
                return r
            f.append(agora)
            _falhas[ip] = f
            return HTMLResponse(LOGIN.replace("%ERRO%", '<div class="e">PIN incorreto.</div>'), status_code=401)
        if hmac.compare_digest(request.cookies.get("mz_ads", ""), _cookie(c)):
            return await call_next(request)
        if request.url.path.startswith("/amazonads/painel") or request.url.path == "/":
            return RedirectResponse("/amazonads/entrar", status_code=303)
        return JSONResponse({"detail": "Acesso restrito: entre com o PIN."}, status_code=401)
