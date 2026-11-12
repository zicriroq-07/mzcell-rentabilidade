"""
marketplaces/consulta/executores.py — O que cada consulta faz (somente leitura)
MZ App

Cada executor devolve {"texto": linhas prontas para o terminal, "dados": estrutura completa}. O terminal imprime o texto sem
precisar de ninguém (nem do Claude) para interpretar; quem quiser processar usa os dados.
Só chamam leituras já existentes: nada é gravado no Bling, no Mercado Livre ou nos arquivos do app.
"""

import asyncio
import re
from datetime import datetime

from fastapi import HTTPException

BLINGS = ("bling01", "bling02", "bling03")
MELIS = ("meli01", "meli02", "meli03")
PLATAFORMAS = {"shopee": ("shopee",), "magalu": ("magalu",), "amazon": ("amazon",), "meli": ("meli", "mercado", "flex", "coleta", "agencia", "places", "correios")}


def brl(v) -> str:
    try:
        return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "-"


def _limpo(txt, n=300) -> str:
    """Mensagem do ML vem com HTML: tira as marcas e junta os espaços."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(txt or ""))).strip()[:n]


def _hora(iso) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d/%m %H:%M")
    except ValueError:
        return "-"


def _bling_de(x: dict) -> str:
    """Conta do Bling do pedido: a informada ou a da empresa dona do canal (Techseller = bling01, Seller Info = bling02, Mizi = bling03)."""
    if x.get("conta_bling"):
        return x["conta_bling"]
    return {"meli01": "bling01", "meli02": "bling02", "meli03": "bling03", "amazon02": "bling02"}.get(str(x.get("conta") or "").lower(), "bling01")


def _dia():
    from marketplaces.expedicao import router as exr
    return exr


def _enderecos(lista: list) -> None:
    """Cidade, UF e CEP de entrega (só isso: sem nome nem rua) dos pedidos do Mercado Livre, pelo envio. Os demais canais ficam sem."""
    from concurrent.futures import ThreadPoolExecutor
    from reposicao_full import ML

    def um(x):
        if str(x.get("conta") or "").startswith("meli") and x.get("envio"):
            try:
                st, r = ML(x["conta"]).get(f"/shipments/{x['envio']}")
                a = ((r or {}).get("receiver_address") or {}) if st == 200 else {}
                uf = str((a.get("state") or {}).get("id") or "").replace("BR-", "")
                x["cidade"] = f"{(a.get('city') or {}).get('name') or '-'}/{uf}"
                x["cep"] = a.get("zip_code") or "-"
            except Exception:
                pass
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(um, lista))


def checkout_abertos(u, plataforma=None, conta=None, canal=None, endereco=None, **_):
    lista = _dia()._dia(False, None, None, False, so_lista=True)
    if not isinstance(lista, list):
        return {"texto": ["Os dados do dia ainda estão carregando no servidor. Tente de novo em 1 minuto."], "dados": {"carregando": True}}
    if plataforma:
        termos = PLATAFORMAS.get(plataforma, (plataforma,))
        lista = [x for x in lista if any(t in f"{x.get('canal')} {x.get('conta')}".lower() for t in termos)]
    if conta:
        lista = [x for x in lista if str(x.get("conta") or "").lower() == str(conta).lower() or _bling_de(x) == str(conta).lower()]
    if canal:
        lista = [x for x in lista if canal.lower() in str(x.get("canal") or "").lower()]
    if endereco:
        _enderecos(lista[:60])
    por = {}
    for x in lista:
        g = por.setdefault(x.get("canal") or "?", {"canal": x.get("canal"), "pedidos": 0, "atrasados": 0})
        g["pedidos"] += 1
        g["atrasados"] += 1 if x.get("atrasado") else 0
    atras = sum(1 for x in lista if x.get("atrasado"))
    cab = f"{len(lista)} pedido(s) sem checkout" + "".join(f" ({v})" for v in (plataforma, conta, canal) if v) + f", {atras} atrasado(s)."
    texto = [cab] + [f"  {g['canal']}: {g['pedidos']} ({g['atrasados']} atrasados)" for g in sorted(por.values(), key=lambda g: -g["pedidos"])]
    texto += ["", "Pedido                 Canal            Conta     Bling (nº)       " + ("Cidade/UF                CEP        " if endereco else "") + "Corte        Situação"]
    for x in lista[:60]:
        bn = f"{_bling_de(x)} {x['bling_numero']}" if x.get("bling_numero") else f"{_bling_de(x)} -"
        texto.append(f"{str(x.get('pedido') or x.get('envio') or ''):<22} {str(x.get('canal') or ''):<16} {str(x.get('conta') or ''):<9} {bn:<16} {(str(x.get('cidade') or '-')[:24].ljust(24) + ' ' + str(x.get('cep') or '-').ljust(10) + ' ') if endereco else ''}{_hora(x.get('meta')):<12} "
                     f"{'ATRASADO ' if x.get('atrasado') else ''}{'(dia anterior) ' if x.get('herdado') else ''}{x.get('bling_situacao') or x.get('situacao') or ''}")
    if len(lista) > 60:
        texto.append(f"... e mais {len(lista) - 60} (use -Json para a lista completa)")
    return {"texto": texto, "dados": {"total": len(lista), "atrasados": atras, "por_canal": list(por.values()), "pedidos": [dict(x, conta_bling=_bling_de(x)) for x in lista]}}


def expedicao_resumo(u, **_):
    r = _dia()._dia(False, None, None, False)
    if r.get("carregando"):
        return {"texto": ["Os dados do dia ainda estão carregando no servidor. Tente de novo em 1 minuto."], "dados": r}
    g = r.get("geral") or {}
    texto = [f"Expedição de {r.get('dia')} (atualizado {_hora(r.get('atualizado_em') or r.get('agora'))}):",
             f"  Pedidos: {g.get('pedidos')}   Checkout feito: {g.get('checkout')}   Pendentes: {g.get('pendentes')}   Atrasados: {g.get('atrasados')}",
             f"  Pedidos de dias anteriores ainda sem checkout: {r.get('herdados')}", "", "Por canal:"]
    for c in r.get("cartoes") or []:
        texto.append(f"  {str(c.get('canal')):<18} {c.get('checkout')}/{c.get('pedidos')} com checkout")
    cp = r.get("cortes_plataformas") or []
    if cp:
        texto += ["", "Cortes das plataformas:"] + [f"  {c.get('plataforma')} {c.get('canal')}: {c.get('corte')} (em {c.get('minutos')} min)" for c in cp]
    return {"texto": texto, "dados": {k: r.get(k) for k in ("dia", "agora", "geral", "cartoes", "cortes", "cortes_plataformas", "alertas", "herdados")}}


def full_envios(u, **_):
    r = _dia()._dia(False, None, None, False)
    if r.get("carregando"):
        return {"texto": ["Os dados do dia ainda estão carregando no servidor. Tente de novo em 1 minuto."], "dados": r}
    envios = r.get("full") or []
    texto = [f"{len(envios)} envio(s) Full/FBA em montagem:"]
    for e in envios:
        texto.append(f"  {e.get('canal')} {e.get('numero')} ({e.get('conta')}): {e.get('unidades_prontas')}/{e.get('unidades_total')} un "
                     f"({str(e.get('percentual')).replace('.', ',')}%), coleta {e.get('coleta') or '-'}, prazo {_hora(e.get('prazo'))}"
                     f"{' ATRASADO' if e.get('atrasado') else ''}")
    coleta = r.get("full_coleta")
    return {"texto": texto, "dados": {"envios": envios, "coleta": coleta}}


def cancelamentos(u, dias=7, **_):
    r = _dia().cancelamentos(dias=int(dias), atualizar=False)
    itens = (r.get("pendentes") or r.get("itens")) if isinstance(r, dict) else r
    itens = itens or []
    return {"texto": [f"{len(itens)} pedido(s) cancelado(s) no ML que continuam ativos no Bling (últimos {dias} dias)."] +
            [f"  {i.get('conta', '')} pedido {i.get('pedido') or i.get('order_id') or ''} - {_bling_de(i)} nº {i.get('bling_numero') or '-'}: {i.get('bling_situacao') or ''}" for i in itens[:40]], "dados": r}


def _sit_bling(x: dict) -> str:
    from marketplaces.expedicao import bling_checkout
    sit = x.get('situacao') or {}
    return bling_checkout.SITUACOES.get(sit.get('id')) or sit.get('valor') or 'situação ?'


def _achar_bling(numero: str) -> list:
    achados = []
    from marketplaces.bling import api as bling
    for param in ("numerosLojas[]", "numero"):
        for b in BLINGS:
            st, r = bling.get(b, "/pedidos/vendas", **{param: [numero]} if param.endswith("[]") else {param: numero})
            if st == 200:
                for p in r.get("data") or []:
                    achados.append((b, p))
        if achados:
            break
    return achados


def pedido(u, numero=None, **_):
    if not numero:
        raise HTTPException(status_code=400, detail="informe o número do pedido")
    from marketplaces.bling import api as bling
    from marketplaces.expedicao import bling_checkout
    achados = _achar_bling(str(numero).strip())
    if not achados:
        return {"texto": [f"Pedido {numero} não encontrado nos Blings 1, 2 e 3."], "dados": {"encontrados": []}}
    saida, texto = [], []
    for b, p in achados[:3]:
        st, r = bling.get(b, f"/pedidos/vendas/{p['id']}")
        d = (r or {}).get("data") or {} if st == 200 else {}
        sit = bling_checkout.SITUACOES.get((d.get("situacao") or p.get("situacao") or {}).get("id"), (d.get("situacao") or {}).get("valor"))
        vols = (d.get("transporte") or {}).get("volumes") or []
        itens = [{"sku": i.get("codigo"), "descricao": i.get("descricao"), "qtd": i.get("quantidade"), "valor": i.get("valor")} for i in d.get("itens") or []]
        item = {"bling": b, "id": p["id"], "numero": p.get("numero"), "numeroLoja": p.get("numeroLoja"), "data": p.get("data"), "situacao": sit,
                "total": d.get("total") or p.get("total"), "cliente": ((d.get("contato") or p.get("contato") or {}).get("nome")),
                "servico": (vols[0] if vols else {}).get("servico"), "rastreio": (vols[0] if vols else {}).get("codigoRastreamento"), "itens": itens}
        saida.append(item)
        texto += [f"{b} - pedido {item['numero']} (loja {item['numeroLoja']}) de {item['data']}: {sit}",
                  f"  Cliente: {item['cliente'] or '-'}   Total: {brl(item['total'])}   Serviço: {item['servico'] or '-'}   Rastreio: {item['rastreio'] or '-'}"]
        texto += [f"  {i['qtd']}x {i['sku']} {str(i['descricao'] or '')[:60]}" for i in itens]
    return {"texto": texto, "dados": {"encontrados": saida}}


def estoque(u, sku=None, conta=None, **_):
    if not sku:
        raise HTTPException(status_code=400, detail="informe o SKU")
    from marketplaces.bling import estoque as est
    chave = est.chave(sku)
    blings = [conta] if conta in BLINGS else list(BLINGS)
    achados, erros = {}, {}
    for b in blings:
        try:
            d = est.saldos(b)
        except Exception as e:
            erros[b] = f"{type(e).__name__}: {str(e)[:80]}"
            continue
        skus = [chave] if chave in d["saldos"] else sorted(k for k in d["saldos"] if k.startswith(chave))[:15]
        for k in skus:
            achados.setdefault(k, {})[b] = {"fisico": d["saldos"].get(k), "virtual": (d.get("virtuais") or {}).get(k)}
    if not achados:
        return {"texto": [f"SKU {sku} não encontrado."] + [f"  {b}: {e}" for b, e in erros.items()], "dados": {"erros": erros}}
    texto = ["Saldo no depósito Geral (físico / virtual = físico menos reservado):"]
    for k, por in achados.items():
        texto.append(f"  {k}: " + "   ".join(f"{b}: {v['fisico']}/{v['virtual']}" for b, v in por.items()))
    texto += [f"  (sem leitura: {b}: {e})" for b, e in erros.items()]
    return {"texto": texto, "dados": {"saldos": achados, "erros": erros}}


def sac_resumo(u, **_):
    import ml_api
    r = asyncio.run(ml_api.sac_resumo(None, None))
    texto = ["Pendências do SAC:"]
    for conta, c in (r.get("contas") or {}).items():
        texto.append(f"  {conta}: {c.get('perguntas')} pergunta(s), {c.get('mensagens')} mensagem(ns), {c.get('reclamacoes')} reclamação(ões) "
                     f"({c.get('aguardam_voce')} aguardam você; a mais antiga tem {c.get('reclamacoes_mais_antiga_dias')} dias)")
    t = r.get("total") or {}
    texto.append(f"  Total: {t.get('perguntas')} pergunta(s), {t.get('mensagens')} mensagem(ns), {t.get('reclamacoes')} reclamação(ões), {t.get('aguardam_voce')} aguardam você")
    return {"texto": texto, "dados": r}


def reclamacao(u, numero=None, conta=None, **_):
    if not numero:
        raise HTTPException(status_code=400, detail="informe o número do pedido do Mercado Livre")
    from reposicao_full import ML
    texto, dados = [], []
    for c in ([conta] if conta in MELIS else list(MELIS)):
        api = ML(c)
        st, ped = api.get(f"/orders/{numero}")
        if st != 200 or not isinstance(ped, dict):
            continue
        try:
            bl = [f"{b} nº {x.get('numero')} ({_sit_bling(x)})" for b, x in _achar_bling(str(numero).strip())]
        except Exception:
            bl = []
        texto.append(f"Pedido {numero} ({c}) - no Bling: " + (", ".join(bl) if bl else "não encontrado"))
        st, r = api.get("/post-purchase/v1/claims/search", resource_id=str(numero), resource="order")
        claims = (r or {}).get("data") if st == 200 and isinstance(r, dict) else []
        if not claims:
            texto.append(f"{c}: pedido {numero} existe, sem reclamação.")
            dados.append({"conta": c, "pedido": numero, "reclamacoes": []})
            break
        det = []
        for cl in claims[:5]:
            cid = cl.get("id")
            st, ms = api.get(f"/post-purchase/v1/claims/{cid}/messages")
            msgs = [{"de": m.get("sender_role"), "quando": m.get("date_created"), "texto": _limpo(m.get("message"))}
                    for m in (ms if isinstance(ms, list) else [])][-8:]
            det.append({"id": cid, "tipo": cl.get("type"), "etapa": cl.get("stage"), "status": cl.get("status"), "motivo": cl.get("reason_id"),
                        "aberta_em": cl.get("date_created"), "mensagens": msgs})
            texto += [f"{c}: reclamação {cid} - {cl.get('type')} / {cl.get('stage')} / {cl.get('status')} (motivo {cl.get('reason_id')}, aberta em {_hora(cl.get('date_created'))})"]
            texto += [f"    [{m['de']}] {m['texto']}" for m in msgs]
        dados.append({"conta": c, "pedido": numero, "reclamacoes": det})
        break
    if not dados:
        texto = [f"Pedido {numero} não encontrado nas contas do Mercado Livre."]
    return {"texto": texto, "dados": dados}


def ajuda(u, **_):
    from marketplaces.consulta import banco
    from marketplaces.consulta.acesso import pode
    cs = [c for c in banco.consultas() if pode(u, c["modulos"])]
    texto = ["Você pode perguntar (exemplos):"] + [f"  {c['exemplo']}" for c in cs]
    return {"texto": texto, "dados": [{k: c[k] for k in ("id", "titulo", "descricao", "exemplo", "params")} for c in cs]}


EXECUTORES = {"checkout_abertos": checkout_abertos, "expedicao_resumo": expedicao_resumo, "full_envios": full_envios,
              "cancelamentos": cancelamentos, "pedido": pedido, "estoque": estoque, "sac_resumo": sac_resumo,
              "reclamacao": reclamacao, "ajuda": ajuda}
