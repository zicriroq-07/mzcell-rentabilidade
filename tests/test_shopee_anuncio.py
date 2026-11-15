"""Testes do modal de anúncio da Shopee (marketplaces/shopee/anuncio.py) — sem rede e sem credencial."""

import sqlite3
import unittest

from marketplaces.shopee import anuncio


def _mem():
    c = sqlite3.connect(":memory:")
    anuncio._tabelas(c)
    return c


class TestAnuncio(unittest.TestCase):
    def test_data_do_ads(self):
        self.assertEqual(anuncio._iso_ads("07-11-2026"), "2026-11-07")

    def test_visitas_por_retrato(self):
        c = _mem()
        c.execute("INSERT INTO views_hist VALUES(1,'2026-11-01',1000,0,0,0,0)")
        c.execute("INSERT INTO views_hist VALUES(1,'2026-11-08',1700,0,0,0,0)")
        self.assertEqual(anuncio._visitas(c, 1, "2026-11-02", "2026-11-08"), 700)
        self.assertIsNone(anuncio._visitas(c, 1, "2026-10-20", "2026-11-08"))     # sem retrato antes do início: não inventa
        self.assertIsNone(anuncio._visitas(c, 2, "2026-11-02", "2026-11-08"))     # anúncio sem retrato

    def test_ads_por_anuncio_so_soma_o_periodo(self):
        c = _mem()
        c.execute("INSERT INTO ads_camp VALUES(10,1,'a','ongoing','manual','all',10,0,0,0)")
        c.execute("INSERT INTO ads_camp VALUES(11,2,'b','ongoing','manual','all',10,0,0,0)")
        for dia, g in (("2026-11-01", 5.0), ("2026-11-02", 7.0), ("2026-11-09", 100.0)):
            c.execute("INSERT INTO ads_dia VALUES(10,?,100,10,?,50,2,40,1)", (dia, g))
        c.execute("INSERT INTO ads_dia VALUES(11,'2026-11-01',9,9,999,9,9,9,9)")
        r = anuncio._ads_periodo(c, 1, "2026-11-01", "2026-11-05")
        self.assertEqual((r["ads_total"], r["cliques"], r["vads"], r["dias_com_dado"]), (12.0, 20, 4, 2))

    def test_pivo_completo_e_faltas(self):
        cat = {"titulo": "x" * 60, "descricao": 150, "n_imagens": 5, "video": True, "promocoes": [{"promotion_staging": "ongoing"}]}
        self.assertTrue(anuncio._pivo(cat, 1)["passa"])
        r = anuncio._pivo({**cat, "video": False, "n_imagens": 3}, 0)
        self.assertEqual(r["pontos"], 1)                       # só o P passa; V, I e O faltam
        self.assertFalse(r["passa"])
        self.assertEqual(len(r["atencao"]), 3)

    def test_aviso_full_por_cobertura(self):
        p30 = {"ads_total": 0, "vads": 0, "receita": 0, "tacos": 0, "cancelados": 0, "pedidos": 0, "unidades": 0}
        sb = {"vendavel": 40, "a_caminho": 0, "nao_apto": 3, "excesso": 0}
        av = anuncio._avisos({"full": True}, sb, 10.0, p30, [], [])          # 4 dias de cobertura
        self.assertTrue(any(a["nivel"] == "critico" and "Full" in a["texto"] for a in av))
        self.assertTrue(any("não aptas" in a["texto"] for a in av))
        av = anuncio._avisos({"full": True}, {**sb, "vendavel": 120, "nao_apto": 0}, 10.0, p30, [], [])   # 12 dias
        self.assertEqual([a["nivel"] for a in av if a["tipo"] == "full"], ["atencao"])
        av = anuncio._avisos({"full": True}, {**sb, "vendavel": 500, "nao_apto": 0}, 10.0, p30, [], [])   # 50 dias
        self.assertFalse([a for a in av if a["tipo"] == "full"])

    def test_cancelamento_por_falta_de_pagamento_nao_conta(self):
        peds = [{"criado": 1794000000, "status": "CANCELLED", "motivo_cancel": "Unpaid Order", "itens": [{"item_id": 1, "qtd": 1, "preco": 10}]},
                {"criado": 1794000000, "status": "CANCELLED", "motivo_cancel": "Others", "itens": [{"item_id": 1, "qtd": 1, "preco": 10}]},
                {"criado": 1794000000, "status": "COMPLETED", "motivo_cancel": "", "itens": [{"item_id": 1, "qtd": 2, "preco": 10}]}]
        d = anuncio.painel._dia(1794000000)
        v = anuncio._vendas_periodo(peds, d, d, 1)
        self.assertEqual((v["cancelados"], v["pedidos"], v["unidades"], v["receita"]), (1, 1, 2, 20.0))


if __name__ == "__main__":
    unittest.main()
