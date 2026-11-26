"""
marketplaces/shopee/somente_leitura.py — Acesso à Shopee SÓ DE CONSULTA, com o access token e nada mais
MZ App

Para outras sessões/agentes que precisam ler dados da loja sem poder estragar a autorização:
  - lê o access token de .shopee_acesso.json (cópia SÓ do access token + validade; sem refresh token);
  - se a cópia venceu, só copia de novo o access token do .shopee_tokens.json principal SE ele ainda for válido
    (somente leitura do arquivo principal; nunca grava nele);
  - NUNCA renova: token vencido ou com menos de 10 minutos = erro pedindo para o app/Zicri renovar;
  - só GET e só endpoints de consulta (último trecho do caminho começa com get_ ou search_);
  - erros passam por api.mascarar (token e partner key nunca aparecem).

Uso (cwd = C:\\mz-clone):
  .\\venv\\Scripts\\python.exe -m marketplaces.shopee.somente_leitura validade
  .\\venv\\Scripts\\python.exe -m marketplaces.shopee.somente_leitura consultar /api/v2/shop/get_shop_info
  .\\venv\\Scripts\\python.exe -m marketplaces.shopee.somente_leitura consultar /api/v2/order/get_order_list time_range_field=create_time time_from=... time_to=... page_size=50
  Em código: from marketplaces.shopee.somente_leitura import consultar; consultar("/api/v2/shop/get_shop_info")
"""

import json
import os
import re
import sys
import time

import httpx

from marketplaces.shopee import api, lojas
from marketplaces.shopee.lojas import ErroShopee

ACESSO_PATH = os.path.join(lojas.BASE_DIR, ".shopee_acesso.json")
FOLGA_SEG = 600                                     # recusa token que vence em menos de 10 minutos
_CAMINHO_LEITURA = re.compile(r"^/api/v2/[a-z_]+/(get_|search_)[a-z0-9_]+$")


def _loja_padrao() -> int:
    ids = [int(v["shop_id"]) for v in lojas.lojas().values() if str(v.get("shop_id", "")).isdigit()]
    if len(ids) != 1:
        raise ErroShopee("informe o shop_id: shopee_lojas.json tem mais de uma loja (ou nenhuma)", 400)
    return int(ids[0])


def _ler_copia() -> dict:
    try:
        with open(ACESSO_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def exportar(shop_id: int = None) -> dict:
    """Copia SÓ o access token (se ainda válido) do arquivo principal para .shopee_acesso.json. Não renova."""
    env = lojas.ler_env()
    nome, shop_id = lojas.ambiente(env), int(shop_id or _loja_padrao())
    t = api._token(nome, shop_id)                   # leitura do arquivo principal
    if time.time() >= t["expires_at"] - FOLGA_SEG:
        raise ErroShopee("o access token principal está vencido; o app (ou o Zicri) precisa renovar. "
                         "Este módulo não renova.", 401)
    copia = _ler_copia()
    copia.setdefault(nome, {})[str(shop_id)] = {"access_token": t["access_token"], "expires_at": t["expires_at"],
                                                "copiado_em": int(time.time())}
    tmp = ACESSO_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(copia, f, indent=2)
    try:
        os.replace(tmp, ACESSO_PATH)
    except PermissionError:
        # nesta máquina renomear dentro da pasta às vezes falha (arquivo "em uso" sem processo visível):
        # grava direto no destino e apaga o temporário
        with open(ACESSO_PATH, "w", encoding="utf-8") as f:
            json.dump(copia, f, indent=2)
        try:
            os.remove(tmp)
        except OSError:
            pass
    return {"ambiente": nome, "shop_id": shop_id, "vence_em_min": int((t["expires_at"] - time.time()) // 60)}


def _acesso(nome: str, shop_id: int) -> dict:
    a = _ler_copia().get(nome, {}).get(str(shop_id))
    if not a or time.time() >= a["expires_at"] - FOLGA_SEG:
        exportar(shop_id)                           # só recopia se o principal ainda for válido; senão, erro
        a = _ler_copia()[nome][str(shop_id)]
    return a


def validade(shop_id: int = None) -> dict:
    env = lojas.ler_env()
    nome, shop_id = lojas.ambiente(env), int(shop_id or _loja_padrao())
    a = _acesso(nome, shop_id)
    return {"ambiente": nome, "shop_id": shop_id, "vence_em_min": int((a["expires_at"] - time.time()) // 60)}


def consultar(caminho: str, params: dict = None, shop_id: int = None) -> dict:
    """GET assinado de consulta. Recusa endpoint que não seja get_/search_. Nunca renova token."""
    if not _CAMINHO_LEITURA.match(caminho or ""):
        raise ErroShopee(f"caminho recusado: só consulta (/api/v2/<módulo>/get_... ou search_...): {caminho}", 403)
    env = lojas.ler_env()
    nome, shop_id = lojas.ambiente(env), int(shop_id or _loja_padrao())
    a = _acesso(nome, shop_id)
    segredos = [a["access_token"], lojas.cfg("PARTNER_KEY", env)]
    for espera in (0, 2, 5, 15):
        time.sleep(espera)
        ts = int(time.time())
        q = {"partner_id": int(lojas.cfg("PARTNER_ID", env)), "timestamp": ts, "access_token": a["access_token"],
             "shop_id": shop_id, "sign": api.assinar(caminho, ts, a["access_token"], shop_id, env)}
        q.update(params or {})
        try:
            r = httpx.get(lojas.cfg("BASE_URL", env).rstrip("/") + caminho, params=q, timeout=api.TIMEOUT)
            dados = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ErroShopee(api.mascarar(f"falha ao falar com a Shopee: {e}", segredos))
        erro = str(dados.get("error") or "")
        if not erro:
            return dados
        if r.status_code == 429 or "rate" in erro.lower() or "too_many" in erro.lower():
            continue
        raise ErroShopee(api.mascarar(f"{erro}: {dados.get('message')} (request_id={dados.get('request_id')}). "
                                      "Se for token, este módulo não renova: avise o Zicri.", segredos), 400)
    raise ErroShopee("a Shopee limitou as chamadas (rate limit); tente de novo em alguns minutos", 429)


def main(argv: list) -> int:
    try:
        if argv[:1] == ["validade"]:
            print(json.dumps(validade(), ensure_ascii=False))
        elif argv[:1] == ["consultar"] and len(argv) >= 2:
            params = dict(a.split("=", 1) for a in argv[2:])
            print(json.dumps(consultar(argv[1], params), ensure_ascii=False, indent=1)[:20000])
        else:
            print(__doc__)
            return 1
    except ErroShopee as e:
        print(f"ERRO: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
