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


class TestDestaque(unittest.TestCase):
    def test_taxa_de_campanha_pelo_rule_id(self):
        e = {"order_selling_price": 100, "net_service_fee_info_list": [{"rule_id": 1000091, "fee_amount": 5.0}, {"rule_id": 1000075, "fee_amount": 4.5}]}
        self.assertAlmostEqual(anuncio.taxa_campanha(e), 0.05)
        e["net_service_fee_info_list"] = [{"rule_id": 1000069, "fee_amount": 3.5}]
        self.assertAlmostEqual(anuncio.taxa_campanha(e), 0.035)
        self.assertIsNone(anuncio.taxa_campanha({"order_selling_price": 100, "net_service_fee_info_list": [{"rule_id": 1000075, "fee_amount": 4.5}]}))

    def test_decomposicao_das_tarifas(self):
        e = {"commission_fee": 14.05, "net_service_fee_info_list": [{"rule_id": 1000074, "fee_amount": 1.06}, {"rule_id": 1000075, "fee_amount": 4.5},
                                                                    {"rule_id": 1000091, "fee_amount": 3.78}, {"rule_id": 999, "fee_amount": 1.0}]}
        t = anuncio.decompor_tarifas(e, 1.0, 1, 75.55)
        self.assertAlmostEqual(t["comissao_pct"], 0.20, places=3)       # 18,6% + 1,4%
        self.assertAlmostEqual(t["fixo"], 4.5)
        self.assertAlmostEqual(t["destaque_pct"], 0.05, places=3)
        self.assertAlmostEqual(t["outras_un"], 1.0)                     # regra sem nome vai para "outras"

    def test_validador_confere_e_diverge(self):
        v = {"preco_un": 75.55, "comissao_pct": 0.20, "fixo": 4.5, "pagamento": "Pix", "itens_no_pedido": 1, "outras_un": 0}
        self.assertTrue(anuncio.validar_comissao(v)["ok"])
        r = anuncio.validar_comissao({**v, "comissao_pct": 0.17, "fixo": 4.0})
        self.assertFalse(r["ok"])
        self.assertTrue(anuncio.validar_comissao({**v, "pagamento": "Credit Card"})["obs"])
        self.assertIsNone(anuncio.validar_comissao(None))

    def test_estado_do_destaque_com_solicitacao_de_desativacao(self):
        from datetime import date
        cfg = {"solicitada_em": "2026-11-14", "efeito_previsto": "2026-12-01"}
        self.assertEqual(anuncio.estado_destaque({"cobrou_destaque": True, "dia": "2026-11-20"}, cfg, date(2026, 11, 20)), ("ativa_aguardando", 11))
        self.assertEqual(anuncio.estado_destaque({"cobrou_destaque": True, "dia": "2026-12-04"}, cfg, date(2026, 12, 5)), ("cobrando_apos_previsto", 4))
        self.assertEqual(anuncio.estado_destaque({"cobrou_destaque": False, "dia": "2026-12-04"}, cfg, date(2026, 12, 5)), ("desativada", None))
        self.assertEqual(anuncio.estado_destaque({"cobrou_destaque": True, "dia": "2026-11-20"}, None, date(2026, 11, 20)), ("ativa", None))
        self.assertEqual(anuncio.estado_destaque({"cobrou_destaque": False, "dia": "2026-11-20"}, None, date(2026, 11, 20)), ("inativa", None))


if __name__ == "__main__":
    unittest.main()
