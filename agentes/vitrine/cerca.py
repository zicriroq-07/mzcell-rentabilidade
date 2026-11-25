"""
agentes/vitrine/cerca.py — A cerca do agente Vitrine (regras puras, sem rede, sem disco)
MZ App

Toda escrita do Vitrine (Amazon Listings e Amazon Ads) passa por `validar(...)` antes de sair.
Aqui só se decide SE a ação é permitida; quem executa é `escrita.py`. A cerca recusa:
  - qualquer ação com o arquivo PARAR presente, fora da janela do mandato ou com o mandato inválido;
  - qualquer SKU/ASIN fora do mandato;
  - atributo de anúncio fora da lista permitida (preço, imagens, título, bullets, descrição, termos);
  - preço abaixo do piso, acima do teto ou com variação diária acima do limite;
  - campanha que não seja do Vitrine (nome com o prefixo do mandato);
  - soma dos orçamentos diários acima do teto/dia; gasto acumulado no teto total; campanha além do fim.
"""

from datetime import date

PREFIXO_PADRAO = "VITRINE | "

ATRIBUTOS_PERMITIDOS = {"purchasable_offer", "item_name", "bullet_point", "product_description", "generic_keyword",
                        "main_product_image_locator"} | {f"other_product_image_locator_{i}" for i in range(1, 9)}


class Recusa(Exception):
    """Ação fora do mandato. A mensagem diz o motivo em português."""


def _d(txt) -> date:
    return date.fromisoformat(str(txt)[:10])


def produto_do_sku(mandato: dict, sku: str) -> dict:
    for p in mandato.get("produtos", []):
        if sku in p.get("skus", []):
            return p
    raise Recusa(f"SKU {sku} fora do mandato")


def produto_do_asin(mandato: dict, asin: str) -> dict:
    for p in mandato.get("produtos", []):
        if p.get("asin") == asin:
            return p
    raise Recusa(f"ASIN {asin} fora do mandato")


def checar_geral(mandato: dict, hoje: date, parar: bool):
    if parar:
        raise Recusa("arquivo PARAR presente: o Vitrine está suspenso")
    for k in ("conta", "inicio", "fim", "produtos", "ads"):
        if not mandato.get(k):
            raise Recusa(f"mandato sem '{k}'")
    if not (_d(mandato["inicio"]) <= hoje <= _d(mandato["fim"])):
        raise Recusa(f"fora da janela do mandato ({mandato['inicio']} a {mandato['fim']})")


# ─── Preço ────────────────────────────────────────────────────────────────────

def precos_do_offer(valor: list) -> list:
    """Todos os value_with_tax de um purchasable_offer (preço cheio e promocionais)."""
    saida = []
    for oferta in valor or []:
        for campo in ("our_price", "discounted_price"):
            for item in oferta.get(campo) or []:
                for janela in item.get("schedule") or []:
                    if "value_with_tax" in janela:
                        saida.append(float(janela["value_with_tax"]))
    return saida


def checar_preco(produto: dict, novo: float, atual: float = None, limite_var: float = 0.15):
    piso, teto = float(produto["preco_piso"]), float(produto["preco_teto"])
    if novo < piso:
        raise Recusa(f"preço R$ {novo:.2f} abaixo do piso R$ {piso:.2f} ({produto['asin']})")
    if novo > teto:
        raise Recusa(f"preço R$ {novo:.2f} acima do teto R$ {teto:.2f} ({produto['asin']})")
    if atual and abs(novo - atual) / atual > limite_var + 1e-9:
        raise Recusa(f"variação de {abs(novo - atual) / atual:.0%} no preço passa do limite diário de {limite_var:.0%}")


def checar_listing(mandato: dict, sku: str, patches: list, preco_atual: float = None):
    produto = produto_do_sku(mandato, sku)
    if not patches:
        raise Recusa("patch vazio")
    for p in patches:
        if p.get("op") not in ("replace", "delete"):
            raise Recusa(f"operação {p.get('op')} não permitida")
        caminho = str(p.get("path", ""))
        if not caminho.startswith("/attributes/"):
            raise Recusa(f"caminho {caminho} não permitido")
        attr = caminho.split("/")[2]
        if attr not in ATRIBUTOS_PERMITIDOS:
            raise Recusa(f"atributo {attr} fora da lista permitida")
        if attr == "purchasable_offer":
            if p.get("op") == "delete":
                raise Recusa("apagar a oferta não é permitido")
            precos = precos_do_offer(p.get("value"))
            if not precos:
                raise Recusa("purchasable_offer sem preço")
            for v in precos:
                checar_preco(produto, v, preco_atual, mandato.get("limite_variacao_preco_dia", 0.15))
        if attr == "main_product_image_locator":
            # 25/11: uma capa gerada pelo Codex saiu com produto diferente do real. A capa só pode vir da FOTO REAL,
            # recortada/limpa pelo comando capa-da-foto (arquivos capa_real_*).
            if p.get("op") == "delete":
                raise Recusa("apagar a imagem principal não é permitido")
            for v in p.get("value") or []:
                if "/capa_real_" not in str(v.get("media_location", "")):
                    raise Recusa("a imagem principal só pode ser a foto real limpa (comando capa-da-foto, arquivo "
                                 "capa_real_*); capa gerada por IA não é permitida")
    return produto


# ─── Ads ──────────────────────────────────────────────────────────────────────

def e_do_vitrine(mandato: dict, nome_campanha: str) -> bool:
    return str(nome_campanha or "").startswith(mandato["ads"].get("prefixo", PREFIXO_PADRAO))


def checar_campanha_nova(mandato: dict, nome: str, orcamento: float, fim_campanha: str, soma_atual: float):
    if not e_do_vitrine(mandato, nome):
        raise Recusa(f"campanha nova precisa começar com '{mandato['ads'].get('prefixo', PREFIXO_PADRAO)}'")
    checar_orcamento(mandato, orcamento, soma_atual)
    if not fim_campanha or _d(fim_campanha) > _d(mandato["fim"]):
        raise Recusa(f"a campanha precisa terminar até {mandato['fim']} (fim do mandato)")


def checar_orcamento(mandato: dict, orcamento: float, soma_outras: float):
    """soma_outras = soma dos orçamentos diários das OUTRAS campanhas ativas do Vitrine."""
    ads = mandato["ads"]
    if orcamento <= 0:
        raise Recusa("orçamento precisa ser positivo")
    if soma_outras + orcamento > float(ads["teto_dia"]) + 1e-9:
        raise Recusa(f"orçamentos somariam R$ {soma_outras + orcamento:.2f}/dia; teto R$ {float(ads['teto_dia']):.2f}")


def checar_gasto(mandato: dict, gasto_acumulado: float):
    ads = mandato["ads"]
    limite = float(ads["teto_total"]) - float(ads.get("reserva_atraso", 0))
    if gasto_acumulado >= limite:
        raise Recusa(f"gasto acumulado R$ {gasto_acumulado:.2f} chegou ao limite R$ {limite:.2f} (teto total menos reserva)")


def checar_anuncio_sku(mandato: dict, sku: str):
    produto_do_sku(mandato, sku)


def checar_lance(mandato: dict, lance: float):
    teto = float(mandato["ads"].get("lance_max", 5.0))
    if not (0.1 <= lance <= teto):
        raise Recusa(f"lance R$ {lance:.2f} fora da faixa R$ 0,10 a R$ {teto:.2f}")


# ─── Margem (para o diário e o relatório) ─────────────────────────────────────

def margem(produto: dict, preco: float, tacos: float = 0.0) -> dict:
    """MC por unidade = preço − imposto − CMV − comissão − tarifa FBA − TACoS × preço."""
    imposto = preco * float(produto["imposto_pct"])
    comissao = preco * float(produto["comissao_pct"])
    mc = preco - imposto - comissao - float(produto["tarifa_fba"]) - float(produto["cmv"]) - preco * tacos
    return {"preco": round(preco, 2), "mc": round(mc, 2), "mc_pct": round(mc / preco * 100, 2) if preco else None}


def piso_zero_a_zero(produto: dict, tacos: float = 0.0) -> float:
    fixo = float(produto["tarifa_fba"]) + float(produto["cmv"])
    return round(fixo / (1 - float(produto["imposto_pct"]) - float(produto["comissao_pct"]) - tacos), 2)
