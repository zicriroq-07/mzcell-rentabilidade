"""
marketplaces/montagem/historico.py — Banco de dados (SQLite) dos agendamentos de Full com SKUs e quantidades
MZ App

Guarda TODO envio ao Full (Meli, Amazon FBA e Shopee) que passa pelo módulo de montagem, com a data de coleta, o que foi
enviado (SKU + unidades) e o histórico de mudanças. O montagem_envios.json continua sendo a fila de trabalho do galpão
(envios removidos somem de lá); este banco nunca apaga: o envio removido fica marcado com `removido_em`.

Para quê: base da previsão de envio e de compras automatizada (hoje o Felipe cruza à mão vendas de 7 e 30 dias do Meli com o
saldo do Bling). `programado()` devolve, por SKU, o que já está agendado ou a caminho do Full e entra na conta de estoque
válido (apto para venda + enviado ou programado).

Como é alimentado: um único gancho em armazenamento._gravar chama sincronizar() depois de cada gravação (criar, mudar coleta,
iniciar, concluir, remover). Uma falha do banco NUNCA derruba o fluxo do galpão (guarda `ultimo_erro`). Não escreve em nenhuma
API. Arquivo: full_agendamentos.db, ao lado do montagem_envios.json (fora do git).

Tabelas: full_envios (um por envio), full_itens (SKU e unidades do envio) e full_eventos (criado, coleta alterada, status,
itens alterados, removido).
"""

import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta

DIAS_APOS_COLETA = 10   # sem API de recebimento: o envio conta como "a caminho" até este número de dias depois da coleta (ajustável)
_trava = threading.Lock()
ultimo_erro = None


def caminho() -> str:
    from marketplaces.montagem import armazenamento as arm
    return os.path.join(os.path.dirname(arm.CAMINHO), "full_agendamentos.db")


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(caminho(), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS full_envios(chave TEXT PRIMARY KEY, canal TEXT, numero TEXT, conta TEXT, coleta TEXT, prazo TEXT, "
              "status TEXT, criado_em TEXT, criado_por TEXT, iniciado_em TEXT, concluido_em TEXT, removido_em TEXT, atualizado_em TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS full_itens(chave TEXT, idx INT, sku TEXT, codigo_ml TEXT, titulo TEXT, unidades INT, PRIMARY KEY(chave, idx))")
    c.execute("CREATE TABLE IF NOT EXISTS full_eventos(id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT, quando TEXT, evento TEXT, detalhe TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_full_itens_sku ON full_itens(sku)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_full_envios_coleta ON full_envios(coleta)")
    return c


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _status(e: dict) -> str:
    return "concluido" if e.get("concluido_em") else (e.get("status") or "montagem")


def _evento(c, chave, evento, detalhe=None):
    c.execute("INSERT INTO full_eventos(chave, quando, evento, detalhe) VALUES(?,?,?,?)", (chave, _agora(), evento, json.dumps(detalhe, ensure_ascii=False) if detalhe else None))


def _itens(e: dict) -> list:
    return [(i, str(p.get("sku", "")).strip(), p.get("codigo_ml", ""), p.get("titulo", ""), int(p.get("unidades") or 0)) for i, p in enumerate(e.get("produtos") or [])]


def sincronizar(dados: dict):
    """Espelha o estado atual do montagem_envios.json no banco: insere o novo, registra mudanças e marca o que sumiu como removido."""
    global ultimo_erro
    try:
        with _trava:
            c = _db()
            try:
                agora = _agora()
                antigos = {r["chave"]: dict(r) for r in c.execute("SELECT * FROM full_envios")}
                atuais = dados.get("envios") or {}
                for chave, e in atuais.items():
                    st, itens = _status(e), _itens(e)
                    novo = (e.get("canal"), e.get("numero"), e.get("conta"), e.get("coleta"), e.get("prazo"), st, e.get("criado_em"), e.get("criado_por"),
                            e.get("iniciado_em"), e.get("concluido_em"))
                    velho = antigos.get(chave)
                    if velho is None:
                        c.execute("INSERT INTO full_envios VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?)", (chave,) + novo + (agora,))
                        _evento(c, chave, "criado", {"coleta": e.get("coleta"), "status": st, "itens": len(itens), "unidades": sum(i[4] for i in itens)})
                    else:
                        if velho["coleta"] != e.get("coleta"):
                            _evento(c, chave, "coleta_alterada", {"de": velho["coleta"], "para": e.get("coleta")})
                        if velho["status"] != st:
                            _evento(c, chave, "status", {"de": velho["status"], "para": st})
                        if velho["removido_em"]:
                            _evento(c, chave, "reativado")
                        if (velho["canal"], velho["numero"], velho["conta"], velho["coleta"], velho["prazo"], velho["status"], velho["criado_em"], velho["criado_por"],
                                velho["iniciado_em"], velho["concluido_em"]) != novo or velho["removido_em"]:
                            c.execute("UPDATE full_envios SET canal=?, numero=?, conta=?, coleta=?, prazo=?, status=?, criado_em=?, criado_por=?, iniciado_em=?, "
                                      "concluido_em=?, removido_em=NULL, atualizado_em=? WHERE chave=?", novo + (agora, chave))
                    guardados = [(r["idx"], r["sku"], r["codigo_ml"], r["titulo"], r["unidades"]) for r in c.execute("SELECT * FROM full_itens WHERE chave=? ORDER BY idx", (chave,))]
                    if guardados != itens:
                        if guardados:
                            _evento(c, chave, "itens_alterados", {"antes": [[g[1], g[4]] for g in guardados], "depois": [[i[1], i[4]] for i in itens]})
                        c.execute("DELETE FROM full_itens WHERE chave=?", (chave,))
                        c.executemany("INSERT INTO full_itens VALUES(?,?,?,?,?,?)", [(chave,) + i for i in itens])
                for chave, velho in antigos.items():
                    if chave not in atuais and not velho["removido_em"]:
                        c.execute("UPDATE full_envios SET removido_em=?, atualizado_em=? WHERE chave=?", (agora, agora, chave))
                        _evento(c, chave, "removido")
                c.commit()
                ultimo_erro = None
            finally:
                c.close()
    except Exception as e:      # o banco nunca derruba o fluxo do galpão
        ultimo_erro = f"{type(e).__name__}: {str(e)[:200]}"


def carregar_do_arquivo():
    """Primeira carga (ou reconciliação): lê o montagem_envios.json atual e sincroniza."""
    from marketplaces.montagem import armazenamento as arm
    sincronizar(arm._ler())


def _envio(r, itens) -> dict:
    d = dict(r)
    d["itens"] = itens.get(d["chave"], [])
    d["unidades"] = sum(i["unidades"] for i in d["itens"])
    return d


def listar(canal: str = None, sku: str = None, de: str = None, ate: str = None, incluir_removidos: bool = False) -> list:
    """Envios com seus itens, pela data de coleta (mais recente primeiro). Filtra por canal, SKU e período de coleta."""
    c = _db()
    try:
        onde, par = [], []
        if canal:
            onde.append("canal=?"); par.append(canal.lower())
        if de:
            onde.append("coleta>=?"); par.append(de)
        if ate:
            onde.append("coleta<=?"); par.append(ate)
        if sku:
            onde.append("chave IN (SELECT chave FROM full_itens WHERE UPPER(sku)=UPPER(?))"); par.append(sku.strip())
        if not incluir_removidos:
            onde.append("removido_em IS NULL")
        sql = "SELECT * FROM full_envios" + (" WHERE " + " AND ".join(onde) if onde else "") + " ORDER BY coleta DESC, chave"
        envios = c.execute(sql, par).fetchall()
        itens = {}
        for i in c.execute("SELECT * FROM full_itens ORDER BY chave, idx"):
            itens.setdefault(i["chave"], []).append({"sku": i["sku"], "codigo_ml": i["codigo_ml"], "titulo": i["titulo"], "unidades": i["unidades"]})
        return [_envio(r, itens) for r in envios]
    finally:
        c.close()


def eventos(chave: str) -> list:
    c = _db()
    try:
        return [{"quando": r["quando"], "evento": r["evento"], "detalhe": json.loads(r["detalhe"]) if r["detalhe"] else None}
                for r in c.execute("SELECT * FROM full_eventos WHERE chave=? ORDER BY id", (chave,))]
    finally:
        c.close()


def programado(canal: str = None, hoje: date = None, dias_apos_coleta: int = DIAS_APOS_COLETA) -> list:
    """Por canal e SKU: unidades já agendadas ou a caminho do Full (envios não removidos com coleta de hoje - dias_apos_coleta em diante).
    Alimenta o estoque válido da previsão: apto para venda + enviado ou programado. Sem API de recebimento: depois da janela
    o envio sai da conta e o saldo passa a vir do estoque do próprio canal."""
    hoje = hoje or date.today()
    desde = (hoje - timedelta(days=dias_apos_coleta)).isoformat()
    por = {}
    for e in listar(canal=canal, de=desde):
        coletado = bool(e["coleta"]) and e["coleta"] < hoje.isoformat()
        for i in e["itens"]:
            k = (e["canal"], i["sku"].upper())
            x = por.setdefault(k, {"canal": e["canal"], "sku": i["sku"], "unidades": 0, "envios": []})
            x["unidades"] += i["unidades"]
            x["envios"].append({"chave": e["chave"], "coleta": e["coleta"], "status": e["status"], "unidades": i["unidades"], "coletado": coletado})
    return sorted(por.values(), key=lambda x: (x["canal"], x["sku"]))


def resumo() -> dict:
    c = _db()
    try:
        por_canal = {r[0]: r[1] for r in c.execute("SELECT canal, COUNT(*) FROM full_envios WHERE removido_em IS NULL GROUP BY canal")}
        return {"envios_ativos": sum(por_canal.values()), "por_canal": por_canal,
                "removidos": c.execute("SELECT COUNT(*) FROM full_envios WHERE removido_em IS NOT NULL").fetchone()[0],
                "eventos": c.execute("SELECT COUNT(*) FROM full_eventos").fetchone()[0], "ultimo_erro": ultimo_erro, "arquivo": os.path.basename(caminho())}
    finally:
        c.close()
