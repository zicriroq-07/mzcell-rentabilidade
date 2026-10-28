"""
marketplaces/expedicao/router.py — Endpoints /expedicao/* (somente leitura)
MZ App

GET /expedicao          página do mapa (HTML)
GET /expedicao/galpao   JSON com os envios do galpão e os prazos (ML; Amazon e Shopee entram depois)
"""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from pydantic import BaseModel

from marketplaces.expedicao import marcas, ml

router = APIRouter(prefix="/expedicao", tags=["expedicao"])
PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagina.html")


class MarcaIn(BaseModel):
    envio: str
    campo: str
    valor: bool


@router.get("", response_class=HTMLResponse)
def pagina():
    with open(PAGINA, encoding="utf-8") as f:
        return f.read()


@router.get("/galpao")
def galpao(contas: str = None, atualizar: bool = False):
    lista = [c for c in (contas or "").split(",") if c] or None
    if lista and any(c not in ml.CONTAS for c in lista):
        raise HTTPException(status_code=400, detail="conta inválida")
    return ml.mapa(lista, forcar=atualizar)


@router.get("/marcas")
def listar_marcas():
    try:
        return marcas.ler()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/marca")
def marcar(body: MarcaIn):
    try:
        return marcas.marcar(body.envio, body.campo, body.valor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
