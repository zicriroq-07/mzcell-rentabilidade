"""Leitura viva do saldo do Bling por SKU (marketplaces/bling/estoque.vivo) — sem rede."""

import time
import unittest
from unittest import mock

from marketplaces.bling import estoque


def _resp(ids_saldos, geral=1):
    return 200, {"data": [{"produto": {"id": i}, "depositos": [{"id": geral, "saldoFisico": s, "saldoVirtual": s}]} for i, s in ids_saldos.items()]}


class TestVivo(unittest.TestCase):
    def setUp(self):
        self._c, self._g, self._t = dict(estoque._cache), dict(estoque._geral_id), dict(estoque._vivo_ts)
        estoque._vivo_ts.clear()
        estoque._geral_id.update({b: 1 for b in estoque.BLINGS})
        for b, n in (("bling01", 10), ("bling02", 20), ("bling03", 30)):
            estoque._cache[b] = (time.time(), {"saldos": {"KIT-A": n}, "pais": {}, "virtuais": {}, "ids": {"KIT-A": 100 + n}})

    def tearDown(self):
        estoque._cache.clear(); estoque._cache.update(self._c)
        estoque._geral_id.clear(); estoque._geral_id.update(self._g)
        estoque._vivo_ts.clear(); estoque._vivo_ts.update(self._t)

    def test_atualiza_so_os_skus_pedidos_nos_tres_blings(self):
        def falso(conta, caminho, **p):
            self.assertEqual(caminho, "/estoques/saldos")
            return _resp({p["idsProdutos[]"][0]: {"bling01": 7, "bling02": 8, "bling03": 9}[conta]})
        with mock.patch.object(estoque.api, "get", side_effect=falso) as g:
            r = estoque.vivo("shopee", ["kit-a", "SEM-ID"])
        self.assertEqual(g.call_count, 3)                                   # uma consulta por Bling
        self.assertEqual(r["itens"]["KIT-A"], [7, {"bling02": 8, "bling03": 9}])
        self.assertEqual(r["lidos"], 3)
        self.assertEqual(estoque._cache["bling01"][1]["saldos"]["KIT-A"], 7)

    def test_nao_repete_leitura_dentro_da_janela(self):
        with mock.patch.object(estoque.api, "get", side_effect=lambda c, p, **k: _resp({k["idsProdutos[]"][0]: 1})) as g:
            estoque.vivo("meli01", ["KIT-A"])
            estoque.vivo("meli01", ["KIT-A"])
        self.assertEqual(g.call_count, 3)

    def test_falha_de_um_bling_mantem_o_ultimo_saldo(self):
        def falso(conta, caminho, **p):
            return (500, {}) if conta == "bling02" else _resp({p["idsProdutos[]"][0]: 5})
        with mock.patch.object(estoque.api, "get", side_effect=falso):
            r = estoque.vivo("meli01", ["KIT-A"])
        self.assertIn("bling02", r["erros"])
        self.assertEqual(r["itens"]["KIT-A"][1]["bling02"], 20)             # último saldo conhecido


if __name__ == "__main__":
    unittest.main()
