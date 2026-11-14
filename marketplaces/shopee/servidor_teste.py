"""
marketplaces/shopee/servidor_teste.py — Servidor mínimo só com as rotas /shopee/* (teste local, sem login)

Não sobe o main.py: nada de Bling, Mercado Livre nem sincronizações em segundo plano.
Uso (na raiz do projeto):
  .\\venv\\Scripts\\python.exe -m uvicorn marketplaces.shopee.servidor_teste:app --host 127.0.0.1 --port 8001
Painel: http://127.0.0.1:8001/shopee/painel
"""

from fastapi import FastAPI

from marketplaces.shopee.router import router

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)
