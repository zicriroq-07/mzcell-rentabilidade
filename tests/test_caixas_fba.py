"""Testes do montador de caixas FBA (sem rede)."""
import unittest

from marketplaces.amazon import caixas_fba as C


class Montador(unittest.TestCase):
    BASE = {"CTRL": {"peso_kg": 0.247, "por_caixa": 40, "dims_cm": [18.3, 16.9, 6.4]},
            "ESC": {"peso_kg": 0.06, "dims_cm": [23.1, 5.1, 2.2]},
            "MIC": {"peso_kg": 0.713, "por_caixa": 30, "dims_cm": [30.3, 19.2, 5.9]},
            "PESADO": {"peso_kg": 3.0, "dims_cm": [10, 10, 10]}}

    def test_nunca_passa_de_22kg(self):
        for c in C.montar({"MIC": 95, "PESADO": 20, "CTRL": 7}, self.BASE):
            self.assertLessEqual(c["peso"], C.PESO_MAX)

    def test_peso_limita_capacidade(self):
        self.assertEqual(C.capacidade(self.BASE["PESADO"]), 7)          # (22 - 0,6) / 3 kg

    def test_quantidade_total_preservada(self):
        pedido = {"CTRL": 62, "ESC": 38, "MIC": 45}
        caixas = C.montar(pedido, self.BASE)
        for sku, q in pedido.items():
            self.assertEqual(sum(c["itens"].get(sku, 0) for c in caixas), q)

    def test_sobras_vao_juntas(self):
        caixas = C.montar({"CTRL": 62, "ESC": 38}, self.BASE)
        self.assertEqual(len(caixas), 2)                                  # 40 cheia + (22 CTRL + 38 ESC)

    def test_sem_base_recusa(self):
        with self.assertRaises(ValueError):
            C.montar({"NOVO": 5}, self.BASE)

    def test_manual_vence_geometria(self):
        it = {"peso_kg": 0.5, "dims_cm": [31.5, 23.9, 4.8], "manual": {"por_caixa": 15}}
        self.assertEqual(C.capacidade(it), 15)

    def test_volume_grande_vai_sozinho_na_propria_embalagem(self):
        base = dict(self.BASE, ARV={"peso_kg": 7.0, "dims_cm": [100.0, 30.0, 20.0]})
        caixas = C.montar({"ARV": 3, "CTRL": 5}, base)
        arv = [c for c in caixas if "ARV" in c["itens"]]
        self.assertEqual(len(arv), 3)
        self.assertTrue(all(c["itens"] == {"ARV": 1} and c["dim"] == "100x30x20" and c["peso"] == 7.0 for c in arv))

    def test_opcoes_no_formato_do_envio(self):
        op = C.opcoes_caixa([{"itens": {"CTRL": 40}, "peso": 10.5}, {"itens": {"CTRL": 40}, "peso": 10.5}])
        self.assertEqual(op, ["2;57x33x33;10.5;CTRL=40"])


if __name__ == "__main__":
    unittest.main()
