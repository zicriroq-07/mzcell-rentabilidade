"""
marketplaces/consulta/acesso.py — Login e permissões da consulta por PowerShell
MZ App

Sem senha pela rede interna: quem vem de IP da LAN (192.168.x, 10.x, 172.16-31.x ou o próprio servidor) consulta como "rede-interna",
só leitura, com os módulos de ANONIMO_MODULOS. IP fora da LAN (inclusive Tailscale e internet) é recusado sem token. A senha fica só
no app: o login desta consulta é opcional e serve apenas à leitura direta do Bling/ML e às estatísticas, para quem tem acesso a todos
os módulos. Com login: mesmo usuário e senha do app. O token (Bearer) só vale em /consulta, que só tem leitura: o cookie de sessão do app não é usado
e este token não abre nenhuma outra rota. Os módulos do usuário (expedicao, sac, meli...) decidem o que ele pode consultar.
Cinco senhas erradas seguidas bloqueiam a combinação origem+login por 5 minutos.
"""

import ipaddress
import secrets
import threading
import time

from fastapi import HTTPException, Request

import auth_service

VALIDADE_SEG = 8 * 3600
MAX_FALHAS = 5
BLOQUEIO_SEG = 5 * 60
_tokens = {}    # token -> (login, expira_em)
_falhas = {}    # (origem, login) -> [falhas, bloqueado_ate]
ANONIMO_MODULOS = ["expedicao", "sac"]
_trava = threading.Lock()


def ip_interno(host: str) -> bool:
    """IP da rede local ou do próprio servidor. 100.64.0.0/10 (Tailscale) e IPs públicos não entram."""
    try:
        ip = ipaddress.ip_address(str(host).split("%")[0])
    except ValueError:
        return False
    return (ip.is_private and not ip.is_link_local) or ip.is_loopback


def _usuario(login: str):
    for u in auth_service.load_usuarios():
        if u.get("login") == login and u.get("ativo", True):
            return u
    return None


def entrar(login: str, senha: str, origem: str) -> dict:
    login = str(login or "").strip().lower()
    chave = (origem, login)
    agora = time.time()
    with _trava:
        f = _falhas.get(chave, [0, 0])
        if f[1] > agora:
            raise HTTPException(status_code=429, detail=f"muitas tentativas: aguarde {int(f[1] - agora) // 60 + 1} min")
    u = auth_service.verificar_login(login, str(senha or "")) if login else None
    if not u:
        with _trava:
            f = _falhas.setdefault(chave, [0, 0])
            f[0] += 1
            if f[0] >= MAX_FALHAS:
                f[0], f[1] = 0, agora + BLOQUEIO_SEG
        raise HTTPException(status_code=401, detail="Login ou senha incorretos")
    if u.get("primeiro_acesso"):
        raise HTTPException(status_code=403, detail="primeiro acesso: defina sua senha no MZ App antes de usar a consulta")
    token = secrets.token_urlsafe(32)
    with _trava:
        _falhas.pop(chave, None)
        for t in [t for t, (_, exp) in _tokens.items() if exp < agora]:
            del _tokens[t]
        _tokens[token] = (u["login"], agora + VALIDADE_SEG)
    return {"token": token, "login": u["login"], "nome": u.get("nome", ""), "modulos": u.get("modulos") or ["todos"],
            "validade_horas": VALIDADE_SEG // 3600}


def usuario_atual(request: Request) -> dict:
    """Dependência das rotas: usuário do token, lido do cadastro a cada chamada (bloqueio ou troca de módulos vale na hora)."""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    origem = request.client.host if request.client else ""
    if not token:
        if ip_interno(origem):
            return {"login": "rede-interna", "nome": "Rede interna", "modulos": list(ANONIMO_MODULOS), "origem": origem, "anonimo": True}
        raise HTTPException(status_code=401, detail="acesso só pela rede interna da empresa")
    with _trava:
        s = _tokens.get(token)
    if not s or s[1] <= time.time():
        raise HTTPException(status_code=401, detail="faça login na consulta (mz-consulta.ps1 entrar)")
    u = _usuario(s[0])
    if not u:
        raise HTTPException(status_code=401, detail="usuário bloqueado ou removido")
    return {"login": u["login"], "nome": u.get("nome", ""), "modulos": u.get("modulos") or ["todos"],
            "origem": request.client.host if request.client else ""}


def exigir_login(usuario: dict):
    if usuario.get("anonimo"):
        raise HTTPException(status_code=401, detail="esta consulta pede login: mz-consulta.ps1 entrar")


def pode(usuario: dict, modulos) -> bool:
    if "todos" in modulos:      # consulta aberta a qualquer usuário logado (ajuda)
        return True
    mods = set(usuario.get("modulos") or [])
    return "todos" in mods or bool(mods & set(modulos))


def exigir(usuario: dict, modulos):
    if not pode(usuario, modulos):
        raise HTTPException(status_code=403, detail="seu acesso não inclui esta consulta (módulo: " + "/".join(modulos) + ")")
