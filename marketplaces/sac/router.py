"""
marketplaces/sac/router.py — Endpoints do SAC que vivem fora do main.py (somente leitura)

GET /sac/devolucoes?dias=15            devoluções em andamento (logística reversa) das três contas
GET /sac/{conta}/devolucoes?dias=15    o mesmo para uma conta
GET /sac/painel                        tela única do SAC (Shopee, Amazon, Meli): saúde, devoluções, mensagens, avaliações
GET /sac/shopee/{devolucoes|mensagens|avaliacoes|saude}, /sac/shopee/conversa/{id}
GET /sac/amazon/{conta}/{devolucoes|avaliacoes|saude|mensagens}
GET /sac/meli/{conta}/resumo           reputação, perguntas, mensagens não lidas e reclamações abertas
Tudo somente leitura: nada é respondido, aceito, contestado nem marcado como lido.
Respondem na hora com o último resultado (`carregando: true` só na primeira leitura depois de ligar o servidor).
"""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from marketplaces.sac import devolucoes

router = APIRouter(prefix="/sac", tags=["sac"])


def _dias(d: int, maximo: int = 60) -> int:
    if not 1 <= d <= maximo:
        raise HTTPException(status_code=400, detail=f"dias deve estar entre 1 e {maximo}")
    return d


def _resposta(conta, dias, atualizar):
    snap = devolucoes.obter(_dias(dias), atualizar)
    if snap["dados"] is None:
        return {"success": True, "carregando": True, "erro": snap["erro"], "itens": [], "resumo": {}}
    itens = [x for x in snap["dados"]["itens"] if conta is None or x["conta"] == conta]
    return {"success": True, "carregando": False, "atualizando": snap["carregando"], "gerado_em": snap["gerado_em"],
            "idade_seg": snap["idade_seg"], "dias": snap["dados"].get("dias"), "itens": itens,
            "resumo": devolucoes._resumo(itens), "erros": snap["dados"].get("erros", {}), "erro": snap["erro"]}


@router.get("/devolucoes")
def devolucoes_todas(dias: int = 15, atualizar: bool = False):
    return _resposta(None, dias, atualizar)


@router.get("/{conta}/devolucoes")
def devolucoes_conta(conta: str, dias: int = 15, atualizar: bool = False):
    if conta not in devolucoes.CONTAS:
        raise HTTPException(status_code=404, detail="conta inválida")
    return _resposta(conta, dias, atualizar)


PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagina.html")


def _seguro(fn, *a, **kw):
    """Erro de plataforma vira HTTP legível (sem credenciais); a tela mostra a mensagem."""
    try:
        return fn(*a, **kw)
    except HTTPException:
        raise
    except Exception as e:
        try:
            from marketplaces.amazon.api import mascarar
            msg = mascarar(str(e))
        except Exception:
            msg = str(e)
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {msg[:220]}")


@router.get("/painel", response_class=HTMLResponse)
def painel():
    with open(PAGINA, encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


# ─── Shopee ──────────────────────────────────────────────────────────────────

@router.get("/shopee/devolucoes")
def shopee_devolucoes(dias: int = 30):
    from marketplaces.sac import shopee
    return _seguro(shopee.devolucoes, _dias(dias))


@router.get("/shopee/mensagens")
def shopee_mensagens(limite: int = 30):
    from marketplaces.sac import shopee
    return _seguro(shopee.mensagens, max(5, min(limite, 60)))


@router.get("/shopee/conversa/{conversa_id}")
def shopee_conversa(conversa_id: str):
    from marketplaces.sac import shopee
    return _seguro(shopee.conversa, conversa_id)


@router.get("/shopee/avaliacoes")
def shopee_avaliacoes(dias: int = 30):
    from marketplaces.sac import shopee
    return _seguro(shopee.avaliacoes, _dias(dias))


@router.get("/shopee/saude")
def shopee_saude():
    from marketplaces.sac import shopee
    return _seguro(shopee.saude)


# ─── Amazon ──────────────────────────────────────────────────────────────────

def _amz(conta):
    from marketplaces.sac import amazon
    if conta not in amazon.CONTAS:
        raise HTTPException(status_code=404, detail="conta inválida")
    return amazon


@router.get("/amazon/{conta}/devolucoes")
def amazon_devolucoes(conta: str, dias: int = 30):
    return _seguro(_amz(conta).devolucoes, conta, _dias(dias))


@router.get("/amazon/{conta}/avaliacoes")
def amazon_avaliacoes(conta: str, dias: int = 90):
    return _seguro(_amz(conta).avaliacoes, conta, _dias(dias, 90))


@router.get("/amazon/{conta}/saude")
def amazon_saude(conta: str):
    return _seguro(_amz(conta).saude, conta)


@router.get("/amazon/{conta}/mensagens")
def amazon_mensagens(conta: str):
    return _amz(conta).mensagens(conta)


# ─── Mercado Livre ───────────────────────────────────────────────────────────

@router.get("/meli/{conta}/resumo")
def meli_resumo(conta: str):
    from marketplaces.sac import meli
    if conta not in meli.CONTAS:
        raise HTTPException(status_code=404, detail="conta inválida")
    return _seguro(meli.resumo, conta)
