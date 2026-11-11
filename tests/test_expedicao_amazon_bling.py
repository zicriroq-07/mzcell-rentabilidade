"""Amazon DBA com o checkout do Bling e a rota /expedicao/outros: Bling simulado, sem rede."""
import unittest
from unittest import mock

from marketplaces.expedicao import amazon_bling as a, router


def dba(pedido, conta="amazon02", etiqueta=True, no_ponto=False):
    return {"conta": conta, "canal": "DBA", "pedido": pedido, "envio": pedido, "itens": [], "checkout_auto": etiqueta, "no_ponto": no_ponto}


def bl(numero, sit, pid=1):
    return {"id": pid, "numero": numero, "situacao": {"id": sit}}


class AmazonBling(unittest.TestCase):
    def setUp(self):
        self.bling = {"bling02": {"702-1": bl(11737, 6, 1), "702-2": bl(11738, 24, 2), "702-3": bl(11739, 6, 3)}, "bling01": {}}
        self.lidos = []
        for alvo, nome, v in ((a.bling_checkout, "_pedidos", lambda c: self.bling[c]), (a.bling, "get", self.get), (a.time, "sleep", lambda x: None)):
            p = mock.patch.object(alvo, nome, v); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(a._itens, clear=True); p.start(); self.addCleanup(p.stop)

    def get(self, conta, path, **k):
        self.lidos.append((conta, path))
        return 200, {"data": {"itens": [{"codigo": "KIT-4CX", "descricao": "Kit 4 caixas", "quantidade": 2}]}}

    def test_etiqueta_gerada_sem_verificado_no_bling_fica_pendente(self):
        ls, erros = a.enriquecer([dba("702-1"), dba("702-2"), dba("702-3", no_ponto=True)])
        self.assertEqual(erros, {})
        self.assertEqual([(l["bling_numero"], l["bling_checkout"], l["checkout_auto"]) for l in ls],
                         [(11737, False, False), (11738, True, False), (11739, False, True)])
        self.assertEqual(ls[0]["itens"], [{"mlb": None, "sku": "KIT-4CX", "titulo": "Kit 4 caixas", "qtd": 2}])
        self.assertEqual(ls[0]["bling_situacao"], "Em aberto")

    def test_sem_o_pedido_no_bling_vale_a_etiqueta(self):
        ls, _ = a.enriquecer([dba("702-9"), dba("701-1", conta="amazon01")])
        self.assertEqual([(l["bling_numero"], l["checkout_auto"]) for l in ls], [(None, True), (None, True)])
        self.assertEqual(self.lidos, [])

    def test_itens_lidos_uma_vez(self):
        a.enriquecer([dba("702-1")]); a.enriquecer([dba("702-1")])
        self.assertEqual(self.lidos, [("bling02", "/pedidos/vendas/1")])

    def test_bling_fora_mantem_a_regra_da_etiqueta(self):
        with mock.patch.object(a.bling_checkout, "_pedidos", side_effect=RuntimeError("401")):
            ls, erros = a.enriquecer([dba("702-1")])
        self.assertTrue(ls[0]["checkout_auto"])
        self.assertIn("401", erros["bling:amazon02"])


class RotaOutros(unittest.TestCase):
    def test_so_amazon_shopee_magalu_no_formato_da_tela(self):
        linhas = [{"conta": "meli01", "canal": "Flex"}, dict(dba("702-1"), no_ponto=True), {"conta": "shopee", "canal": "Shopee Xpress", "plataforma": "Shopee"},
                  {"conta": "magalu", "canal": "Magalu", "plataforma": "Magalu"}]
        snap = {"dados": {"linhas": linhas, "erros": {"meli01": "x", "bling:amazon02": "y"}}, "iso": "2026-11-11T10:00:00-03:00"}
        with mock.patch.object(router, "_painel_liberado", lambda: None), mock.patch.object(router.snapshot, "obter", lambda: snap), \
                mock.patch.object(router.composicao, "anexar", lambda ls: ls):
            r = router.outros()
        self.assertEqual([(l["conta"], l["plataforma"], l["status"], l["substatus"]) for l in r["linhas"]],
                         [("amazon02", "Amazon", "ready_to_ship", "dropped_off"), ("shopee", "Shopee", "ready_to_ship", None),
                          ("magalu", "Magalu", "ready_to_ship", None)])
        self.assertEqual(r["erros"], {"bling:amazon02": "y"})


if __name__ == "__main__":
    unittest.main()
