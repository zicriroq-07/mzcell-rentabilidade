"""
marketplaces/amazon/ads_router.py — Rotas do painel "Rentabilidade + Ads" da Amazon (somente leitura)
MZ App

GET  /amazonads/painel      página (HTML)
GET  /amazonads/dados       ASINs, campanhas, totais e alertas do período
GET  /amazonads/asin        detalhe de um ASIN (campanhas, série diária, alvos e termos de busca)
GET  /amazonads/estado      andamento da coleta (Ads e Finances)
POST /amazonads/atualizar   busca Ads (62 dias) e Finances em segundo plano
Contas amazon01 e amazon02 (?conta=). Nada aqui cria, altera ou pausa campanha.
"""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from marketplaces.amazon import ads_api, ads_painel, api

router = APIRouter(prefix="/amazonads", tags=["Amazon Ads"])
CONTAS = ("amazon01", "amazon02")   # amazon01 liberada em 27/11/2026
PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagina_ads.html")


def _conta(conta):
    if conta not in CONTAS:
        raise HTTPException(status_code=400, detail="Conta sem Ads liberado (use amazon01 ou amazon02).")
    return conta


def _http(e):
    return HTTPException(status_code=getattr(e, "status", 500) if isinstance(getattr(e, "status", None), int) and 400 <= e.status < 600 else 500,
                         detail=str(e)[:300])


@router.get("/painel", response_class=HTMLResponse)
def painel_pagina():
    with open(PAGINA, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/dados")
def dados(conta: str = "amazon02", de: str = None, ate: str = None):
    try:
        return ads_painel.dados(_conta(conta), de, ate)
    except (ads_api.ErroAds, api.ErroAmazon) as e:
        raise _http(e)


@router.get("/asin")
def asin(asin: str, conta: str = "amazon02", de: str = None, ate: str = None):
    try:
        return ads_painel.detalhe_asin(_conta(conta), asin, de, ate)
    except (ads_api.ErroAds, api.ErroAmazon) as e:
        raise _http(e)


@router.get("/estado")
def estado(conta: str = "amazon02"):
    from marketplaces.amazon import painel as pf
    conta = _conta(conta)
    return {"ads": ads_painel.estado(conta), "financas": pf.estado(conta)}


@router.post("/atualizar")
def atualizar(conta: str = "amazon02"):
    from marketplaces.amazon import painel as pf
    conta = _conta(conta)
    try:
        return {"ads": ads_painel.iniciar_coleta(conta), "financas": pf.iniciar_atualizacao(conta)}
    except (ads_api.ErroAds, api.ErroAmazon) as e:
        raise _http(e)
