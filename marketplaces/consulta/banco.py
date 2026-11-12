"""
marketplaces/consulta/banco.py — Banco (SQLite) das consultas: catálogo, palavras de busca e histórico de uso
MZ App

consultas: o que dá para perguntar, com as palavras que o interpretador usa (editáveis no banco sem mexer no código).
uso: toda consulta feita (quem, quando, resultado). sem_resposta: textos que o interpretador não entendeu, para virarem consulta nova.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAMINHO = os.path.join(BASE_DIR, "consulta.db")
BRT = timezone(timedelta(hours=-3))
_trava = threading.Lock()


def _con():
    con = sqlite3.connect(CAMINHO, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS consultas (id TEXT PRIMARY KEY, titulo TEXT, descricao TEXT, modulos TEXT, palavras TEXT,
                                              params TEXT, exemplo TEXT, editada INTEGER DEFAULT 0, ativa INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS uso (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, login TEXT, origem TEXT, consulta TEXT,
                                        params TEXT, texto TEXT, status INTEGER, ms INTEGER);
        CREATE TABLE IF NOT EXISTS sem_resposta (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, login TEXT, texto TEXT);
    """)
    return con


def agora() -> str:
    return datetime.now(BRT).isoformat(timespec="seconds")


def _executar(sql: str, args=()):
    with _trava:
        con = _con()
        try:
            con.execute(sql, args)
            con.commit()
        finally:
            con.close()


def semear(catalogo: list):
    """Grava o catálogo do código. As palavras só são atualizadas se ninguém as editou no banco (editada=1)."""
    with _trava:
        con = _con()
        try:
            for c in catalogo:
                dados = (c["titulo"], c["descricao"], json.dumps(c["modulos"]), json.dumps(c["params"], ensure_ascii=False), c["exemplo"])
                palavras = json.dumps(c["palavras"], ensure_ascii=False)
                if con.execute("SELECT 1 FROM consultas WHERE id=?", (c["id"],)).fetchone():
                    con.execute("UPDATE consultas SET titulo=?, descricao=?, modulos=?, params=?, exemplo=? WHERE id=?", dados + (c["id"],))
                    con.execute("UPDATE consultas SET palavras=? WHERE id=? AND editada=0", (palavras, c["id"]))
                else:
                    con.execute("INSERT INTO consultas (titulo, descricao, modulos, params, exemplo, id, palavras) VALUES (?,?,?,?,?,?,?)",
                                dados + (c["id"], palavras))
            con.commit()
        finally:
            con.close()


def consultas(so_ativas: bool = True) -> list:
    with _trava:
        con = _con()
        try:
            linhas = con.execute("SELECT * FROM consultas" + (" WHERE ativa=1" if so_ativas else "") + " ORDER BY id").fetchall()
        finally:
            con.close()
    return [dict(l, modulos=json.loads(l["modulos"]), palavras=json.loads(l["palavras"]), params=json.loads(l["params"])) for l in linhas]


def registrar_uso(login, origem, consulta, params, texto, status, ms):
    try:
        _executar("INSERT INTO uso (ts, login, origem, consulta, params, texto, status, ms) VALUES (?,?,?,?,?,?,?,?)",
                  (agora(), login, str(origem)[:60], consulta, json.dumps(params, ensure_ascii=False)[:500], str(texto or "")[:300], status, ms))
    except sqlite3.Error:
        pass     # o histórico é auxiliar: nunca derruba a consulta


def registrar_sem_resposta(login, texto):
    try:
        _executar("INSERT INTO sem_resposta (ts, login, texto) VALUES (?,?,?)", (agora(), login, str(texto)[:300]))
    except sqlite3.Error:
        pass


def estatisticas(dias: int = 30) -> dict:
    desde = (datetime.now(BRT) - timedelta(days=dias)).isoformat(timespec="seconds")
    with _trava:
        con = _con()
        try:
            top = [dict(r) for r in con.execute("SELECT consulta, COUNT(*) AS vezes, SUM(status>=400) AS erros, CAST(AVG(ms) AS INTEGER) AS ms_medio "
                                                "FROM uso WHERE ts>=? GROUP BY consulta ORDER BY vezes DESC", (desde,))]
            sem = [dict(r) for r in con.execute("SELECT texto, COUNT(*) AS vezes, MAX(ts) AS ultima FROM sem_resposta WHERE ts>=? "
                                                "GROUP BY texto ORDER BY vezes DESC LIMIT 30", (desde,))]
        finally:
            con.close()
    return {"dias": dias, "mais_usadas": top, "sem_resposta": sem}
