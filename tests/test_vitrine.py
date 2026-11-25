"""Testes da cerca do agente Vitrine (sem rede, sem disco)."""
import unittest
from datetime import date

from agentes.vitrine import cerca
from agentes.vitrine.cerca import Recusa

M = {"conta": "amazon02", "inicio": "2026-11-26", "fim": "2026-12-10", "limite_variacao_preco_dia": 0.15,
     "ads": {"prefixo": "VITRINE | ", "teto_dia": 100.0, "teto_total": 1500.0, "reserva_atraso": 100.0, "lance_max": 3.0},
     "produtos": [{"nome": "A", "asin": "B0DHK7NPMN", "skus": ["FRT-ARM-FBA", "FRUT-OND-PRETO"], "cmv": 15.76,
                   "imposto_pct": 0.1072, "comissao_pct": 0.12, "tarifa_fba": 5.85, "preco_piso": 28.0, "preco_teto": 69.9}]}


def oferta(*precos):
    return [{"marketplace_id": "X", "currency": "BRL", "our_price": [{"schedule": [{"value_with_tax": precos[0]}]}],
             "discounted_price": [{"schedule": [{"value_with_tax": p} for p in precos[1:]]}]}]


class Geral(unittest.TestCase):
    def test_parar_bloqueia(self):
        with self.assertRaises(Recusa):
            cerca.checar_geral(M, date(2026, 11, 30), parar=True)

    def test_fora_da_janela(self):
        with self.assertRaises(Recusa):
            cerca.checar_geral(M, date(2026, 12, 11), parar=False)
        with self.assertRaises(Recusa):
            cerca.checar_geral(M, date(2026, 11, 25), parar=False)
        cerca.checar_geral(M, date(2026, 12, 10), parar=False)


class Listing(unittest.TestCase):
    def test_sku_fora(self):
        with self.assertRaises(Recusa):
            cerca.checar_listing(M, "ESC-FRES-PRETO", [{"op": "replace", "path": "/attributes/item_name", "value": []}])

    def test_atributo_proibido(self):
        for attr in ("brand", "fulfillment_availability", "list_price", "merchant_shipping_group"):
            with self.assertRaises(Recusa):
                cerca.checar_listing(M, "FRT-ARM-FBA", [{"op": "replace", "path": f"/attributes/{attr}", "value": []}])

    def test_imagem_e_texto_permitidos(self):
        cerca.checar_listing(M, "FRT-ARM-FBA", [{"op": "replace", "path": "/attributes/main_product_image_locator", "value": []},
                                                {"op": "replace", "path": "/attributes/bullet_point", "value": []}])

    def test_preco_piso_teto_e_variacao(self):
        p = lambda *v: [{"op": "replace", "path": "/attributes/purchasable_offer", "value": oferta(*v)}]
        cerca.checar_listing(M, "FRT-ARM-FBA", p(35.99, 31.0), preco_atual=35.99)
        with self.assertRaises(Recusa):      # promocional abaixo do piso
            cerca.checar_listing(M, "FRT-ARM-FBA", p(35.99, 27.0), preco_atual=35.99)
        with self.assertRaises(Recusa):      # acima do teto
            cerca.checar_listing(M, "FRT-ARM-FBA", p(79.9), preco_atual=69.9)
        with self.assertRaises(Recusa):      # mais de 15% num dia
            cerca.checar_listing(M, "FRT-ARM-FBA", p(29.9), preco_atual=35.99)

    def test_apagar_oferta_proibido(self):
        with self.assertRaises(Recusa):
            cerca.checar_listing(M, "FRT-ARM-FBA", [{"op": "delete", "path": "/attributes/purchasable_offer"}])


class Ads(unittest.TestCase):
    def test_prefixo(self):
        with self.assertRaises(Recusa):
            cerca.checar_campanha_nova(M, "Fruteira auto", 30, "2026-12-10", 0)
        cerca.checar_campanha_nova(M, "VITRINE | A auto", 30, "2026-12-10", 0)

    def test_teto_dia(self):
        with self.assertRaises(Recusa):
            cerca.checar_campanha_nova(M, "VITRINE | A", 40, "2026-12-10", 70)
        cerca.checar_orcamento(M, 30, 70)

    def test_fim_da_campanha(self):
        with self.assertRaises(Recusa):
            cerca.checar_campanha_nova(M, "VITRINE | A", 30, "2026-12-31", 0)
        with self.assertRaises(Recusa):
            cerca.checar_campanha_nova(M, "VITRINE | A", 30, None, 0)

    def test_teto_total_com_reserva(self):
        cerca.checar_gasto(M, 1399.0)
        with self.assertRaises(Recusa):
            cerca.checar_gasto(M, 1400.0)

    def test_lance(self):
        cerca.checar_lance(M, 1.2)
        with self.assertRaises(Recusa):
            cerca.checar_lance(M, 3.5)


class Margem(unittest.TestCase):
    def test_margem_fruteira_a(self):
        p = M["produtos"][0]
        r = cerca.margem(p, 35.99)
        self.assertAlmostEqual(r["mc"], 35.99 - 35.99 * 0.1072 - 35.99 * 0.12 - 5.85 - 15.76, places=2)
        self.assertAlmostEqual(cerca.margem(p, cerca.piso_zero_a_zero(p))["mc"], 0, delta=0.01)
        self.assertGreater(cerca.piso_zero_a_zero(p, 0.10), cerca.piso_zero_a_zero(p))


class CapaSoDaFotoReal(unittest.TestCase):
    def _patch(self, url):
        return [{"op": "replace", "path": "/attributes/main_product_image_locator",
                 "value": [{"marketplace_id": "A2Q3Y263D00KWC", "media_location": url}]}]

    def test_capa_gerada_recusada(self):
        with self.assertRaises(Recusa):
            cerca.checar_listing(M, "FRT-ARM-FBA", self._patch("https://x/amazon02/vitrine/B0DHK7NPMN/capa_v2_1.jpg"))

    def test_capa_da_foto_aceita(self):
        cerca.checar_listing(M, "FRT-ARM-FBA", self._patch("https://x/amazon02/vitrine/B0DHK7NPMN/capa_real_v1_1.jpg"))


class LimitesDoDia(unittest.TestCase):
    """Teto de ações confirmadas por dia e 1 mudança de preço por SKU por dia (lidos do diário)."""

    def setUp(self):
        import json
        import os
        import tempfile
        from agentes.vitrine import escrita
        self.escrita, self.json = escrita, json
        self.tmp = tempfile.mkdtemp()
        self.antigo = escrita.DIARIO
        escrita.DIARIO = os.path.join(self.tmp, "diario.jsonl")
        self.dia = escrita.hoje().isoformat()

    def tearDown(self):
        self.escrita.DIARIO = self.antigo

    def _gravar(self, entradas):
        with open(self.escrita.DIARIO, "w", encoding="utf-8") as f:
            for e in entradas:
                f.write(self.json.dumps(e) + "\n")

    def _preco(self, sku):
        return {"tipo": "listing", "sku": sku, "patches": [{"op": "replace", "path": "/attributes/purchasable_offer"}]}

    def test_teto_de_acoes(self):
        ok = {"quando": self.dia + "T10:00:00-03:00", "tipo": "ajustar_campanha", "confirmado": True, "resultado": {}}
        self._gravar([ok] * 7 + [{**ok, "recusado": "x"}, {**ok, "confirmado": False}])
        self.escrita.checar_limites_do_dia({"tipo": "ajustar_campanha"})      # 7 válidas: ainda pode
        self._gravar([ok] * 8)
        with self.assertRaises(Recusa):
            self.escrita.checar_limites_do_dia({"tipo": "ajustar_campanha"})

    def test_um_preco_por_sku(self):
        e = {"quando": self.dia + "T08:30:00-03:00", "tipo": "listing", "confirmado": True, "resultado": {},
             "acao": self._preco("FRT-ARM-FBA")}
        ontem = {**e, "quando": "2000-01-01T08:30:00-03:00"}
        self._gravar([ontem])
        self.escrita.checar_limites_do_dia(self._preco("FRT-ARM-FBA"))
        self._gravar([e])
        with self.assertRaises(Recusa):
            self.escrita.checar_limites_do_dia(self._preco("FRT-ARM-FBA"))
        self.escrita.checar_limites_do_dia(self._preco("FRTCLK-FBA"))           # outro SKU pode
        self.escrita.checar_limites_do_dia({"tipo": "listing", "sku": "FRT-ARM-FBA",
                                            "patches": [{"op": "replace", "path": "/attributes/item_name"}]})


if __name__ == "__main__":
    unittest.main()
