"""
marketplaces/consulta/router.py — Consulta por PowerShell (somente leitura) de qualquer PC da rede
MZ App

POST /consulta/login          opcional (leitura direta e estatísticas): mesmo usuário e senha do app -> token Bearer (8 h)
Sem token: rede interna apenas, como "rede-interna" (expedicao e sac), sem senha
GET  /consulta/consultas      o que o seu acesso permite perguntar
GET  /consulta/perguntar?texto=...   entende a pergunta pelo banco de consultas (sem IA) e responde
GET  /consulta/executar/{id}  executa uma consulta do catálogo com parâmetros na URL
GET  /consulta/bling/{conta}/{recurso}   leitura direta do Bling (lista de recursos permitidos abaixo)
GET  /consulta/ml/{conta}/{recurso}      leitura direta do Mercado Livre (idem)
POST /consulta/solicitar      pedido de consulta nova (grava só em consulta.db)
GET  /consulta/estatisticas   mais usadas, perguntas sem resposta e pedidos de consulta (só quem tem acesso a todos os módulos)
Só GET, o login e o pedido de consulta: nenhuma rota daqui grava no Bling, no ML ou nos arquivos do app. Cada uso fica no banco.
"""

import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from marketplaces.consulta import acesso, banco, interprete, livre
from marketplaces.consulta.catalogo import CATALOGO
from marketplaces.consulta.executores import BLINGS, EXECUTORES, MELIS

router = APIRouter(prefix="/consulta", tags=["consulta"])
banco.semear(CATALOGO)

# leitura direta: prefixo do recurso -> módulos que podem usar. Quem tem acesso a todos os módulos lê qualquer recurso; os demais ficam sem contatos (dados pessoais) e sem o que não está aqui.
BLING_LIBERADO = {"pedidos/vendas": ("expedicao", "sac", "meli"), "produtos": ("expedicao", "meli", "amazon", "shopee", "magalu"),
                  "estoques/saldos": ("expedicao", "meli", "amazon", "shopee", "magalu"), "depositos": ("expedicao",),
                  "nfe": ("expedicao",), "situacoes": ("expedicao",), "canais-venda": ("expedicao",), "logisticas": ("expedicao",)}
ML_LIBERADO = {"orders": ("expedicao", "sac", "meli"), "shipments": ("expedicao", "sac", "meli"), "items": ("expedicao", "sac", "meli"),
               "post-purchase": ("sac",), "questions": ("sac", "meli"), "messages": ("sac",)}


@router.post("/login")
def login(payload: dict, request: Request):
    return acesso.entrar(payload.get("login"), payload.get("senha"), request.client.host if request.client else "")


def _rodar(u: dict, consulta_id: str, params: dict, texto: str = ""):
    inicio = time.time()
    c = next((x for x in banco.consultas() if x["id"] == consulta_id), None)
    if not c or consulta_id not in EXECUTORES:
        raise HTTPException(status_code=404, detail="consulta desconhecida")
    acesso.exigir(u, c["modulos"])
    status = 200
    try:
        res = EXECUTORES[consulta_id](u, **params)
    except HTTPException as e:
        status = e.status_code
        raise
    except Exception as e:
        status = 500
        raise HTTPException(status_code=502, detail=f"a consulta falhou: {type(e).__name__}: {str(e)[:150]}")
    finally:
        banco.registrar_uso(u["login"], u["origem"], consulta_id, params, texto, status, int((time.time() - inicio) * 1000))
    return dict(res, consulta=consulta_id, titulo=c["titulo"], parametros=params)


@router.get("/tela", response_class=HTMLResponse)
def tela(u: dict = Depends(acesso.usuario_atual)):
    """A mesma tela retrô do terminal, no navegador (rede interna). Chama as rotas desta página, com o mesmo acesso."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tela.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


@router.get("/consultas")
def consultas(u: dict = Depends(acesso.usuario_atual)):
    return [{k: c[k] for k in ("id", "titulo", "descricao", "exemplo", "params", "modulos")} for c in banco.consultas() if acesso.pode(u, c["modulos"])]


@router.get("/perguntar")
def perguntar(texto: str, u: dict = Depends(acesso.usuario_atual)):
    permitidas = [c for c in banco.consultas() if acesso.pode(u, c["modulos"])]
    r = interprete.interpretar(texto, permitidas)
    if r["consulta"] is None:
        banco.registrar_sem_resposta(u["login"], texto)
        if r["candidatas"]:
            return {"entendi": False, "texto": ["Não tenho certeza do que você quer. Pode ser:"] + [f"  {i}" for i in r["candidatas"]] +
                    ["Seja mais específico ou use: mz-consulta.ps1 executar <consulta>"], "candidatas": r["candidatas"]}
        return {"entendi": False, "texto": ["Não entendi a pergunta. Use 'ajuda' para ver o que dá para consultar."], "candidatas": []}
    if r["faltam"]:
        c = next(x for x in permitidas if x["id"] == r["consulta"])
        return {"entendi": True, "consulta": r["consulta"], "texto": [f"{c['titulo']}: falta informar {', '.join(r['faltam'])}.", f"Exemplo: {c['exemplo']}"], "faltam": r["faltam"]}
    return dict(_rodar(u, r["consulta"], r["params"], texto), entendi=True)


@router.get("/livre")
def livre_(texto: str, u: dict = Depends(acesso.usuario_atual)):
    """Pergunta livre: o assistente do servidor escolhe as consultas (só leitura, validadas aqui) e resume a resposta."""
    permitidas = [c for c in banco.consultas() if acesso.pode(u, c["modulos"])]
    inicio = time.time()
    status = 200
    try:
        return livre.perguntar(u, texto, permitidas, lambda cid, params: _rodar(u, cid, params, texto))
    except HTTPException as e:
        status = e.status_code
        raise
    finally:
        banco.registrar_uso(u["login"], u["origem"], "livre", {}, texto, status, int((time.time() - inicio) * 1000))


@router.get("/executar/{consulta_id}")
def executar(consulta_id: str, request: Request, u: dict = Depends(acesso.usuario_atual)):
    return _rodar(u, consulta_id, dict(request.query_params))


def _leitura(u, liberado: dict, recurso: str, quem: str):
    recurso = recurso.strip("/")
    if ".." in recurso or "//" in recurso:
        raise HTTPException(status_code=400, detail="caminho inválido")
    if "todos" in u["modulos"]:      # acesso completo (zicri, michel, eduardo): qualquer recurso de leitura, inclusive contatos
        return
    for prefixo, mods in liberado.items():
        if recurso == prefixo or recurso.startswith(prefixo + "/"):
            acesso.exigir(u, mods)
            return
    raise HTTPException(status_code=403, detail=f"recurso não liberado para consulta no {quem}: libera " + ", ".join(sorted(liberado)))


@router.get("/bling/{conta}/{recurso:path}")
def bling_leitura(conta: str, recurso: str, request: Request, u: dict = Depends(acesso.usuario_atual)):
    acesso.exigir_login(u)
    if conta not in BLINGS:
        raise HTTPException(status_code=400, detail="conta inválida (bling01, bling02 ou bling03)")
    _leitura(u, BLING_LIBERADO, recurso, "Bling")
    from marketplaces.bling import api as bling
    params = {}
    for k, v in request.query_params.multi_items():
        params.setdefault(k, []).append(v)
    params = {k: (v if k.endswith("[]") else v[0]) for k, v in params.items()}
    inicio = time.time()
    st, r = bling.get(conta, "/" + recurso.strip("/"), **params)
    banco.registrar_uso(u["login"], u["origem"], f"bling/{conta}/{recurso}", params, "", st, int((time.time() - inicio) * 1000))
    if st != 200:
        raise HTTPException(status_code=st if st in (400, 404, 429) else 502, detail=f"Bling respondeu {st}: {str(r)[:200]}")
    return r


@router.get("/ml/{conta}/{recurso:path}")
def ml_leitura(conta: str, recurso: str, request: Request, u: dict = Depends(acesso.usuario_atual)):
    acesso.exigir_login(u)
    if conta not in MELIS:
        raise HTTPException(status_code=400, detail="conta inválida (meli01, meli02 ou meli03)")
    _leitura(u, ML_LIBERADO, recurso, "Mercado Livre")
    from reposicao_full import ML
    params = dict(request.query_params)
    inicio = time.time()
    st, r = ML(conta).get("/" + recurso.strip("/"), **params)
    banco.registrar_uso(u["login"], u["origem"], f"ml/{conta}/{recurso}", params, "", st, int((time.time() - inicio) * 1000))
    if st != 200:
        raise HTTPException(status_code=st if st in (400, 404, 429) else 502, detail=f"Mercado Livre respondeu {st}: {str(r)[:200]}")
    return r


@router.post("/solicitar")
def solicitar(payload: dict, u: dict = Depends(acesso.usuario_atual)):
    """Pedido de consulta nova: só grava no banco da consulta (consulta.db), nada do app. Aparece nas estatísticas."""
    texto = str(payload.get("texto") or "").strip()
    nome = str(payload.get("nome") or "").strip()
    if len(texto) < 10:
        raise HTTPException(status_code=400, detail="descreva melhor o que você precisa consultar (mínimo 10 letras)")
    try:
        numero = banco.solicitar(u["login"], u["origem"], nome, texto)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    return {"numero": numero, "texto": [f"Pedido nº {numero} registrado. A equipe de sistemas vai avaliar e cadastrar a consulta."]}


@router.get("/estatisticas")
def estatisticas(dias: int = 30, u: dict = Depends(acesso.usuario_atual)):
    acesso.exigir_login(u)
    if "todos" not in u["modulos"]:
        raise HTTPException(status_code=403, detail="só quem tem acesso a todos os módulos")
    return banco.estatisticas(max(1, min(dias, 365)))
