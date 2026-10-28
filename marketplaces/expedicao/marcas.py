"""
marketplaces/expedicao/marcas.py — Marcas do galpão por envio (embalado, checkout ok)
MZ App

Guardadas em marcacao_expedicao.json (raiz do projeto, gitignored). A chave é o envio do ML:
pedidos que dividem o mesmo envio (mesmo pacote) recebem a mesma marca.
  embalado: o estoquista/embalador concluiu a montagem
  checkout: pedido separado, conferido, saída de estoque feita e etiquetado pronto para levar ou coletar
Até a integração com o Bling, a marca fica só aqui. Nada é enviado ao ML.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARQUIVO = os.path.join(RAIZ, "marcacao_expedicao.json")
CAMPOS = ("embalado", "checkout", "liberado")
BRT = timezone(timedelta(hours=-3))
_trava = threading.RLock()


def ler() -> dict:
    with _trava:
        if not os.path.exists(ARQUIVO):
            return {}
        try:
            with open(ARQUIVO, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            # Arquivo ilegível não pode apagar marcas em silêncio: não sobrescreve
            raise RuntimeError("marcacao_expedicao.json ilegível; corrija o arquivo antes de marcar")


def _gravar_atomico(dados: dict):
    pasta = os.path.dirname(ARQUIVO)
    fd, tmp = tempfile.mkstemp(dir=pasta, prefix=".marcacao_expedicao.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ARQUIVO)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def marcar(envio: str, campo: str, valor: bool) -> dict:
    if campo not in CAMPOS:
        raise ValueError(f"campo inválido: {campo}")
    envio = str(envio)
    with _trava:
        dados = ler()
        reg = dados.get(envio, {})
        if valor:
            reg[campo] = datetime.now(BRT).isoformat(timespec="seconds")
        else:
            reg.pop(campo, None)
        if reg:
            dados[envio] = reg
        else:
            dados.pop(envio, None)
        _gravar_atomico(dados)
        return dados
