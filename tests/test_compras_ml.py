"""Previsão de compras cruzada com a recomendação do ML e os envios agendados — sem rede."""

import unittest
from unittest import mock

from marketplaces.compras import ml_reposicao, previsao


def _x(inv="INV1", sku="KIT-A", total=100, tipo="REPLENISH", q=300):
    return {"identifiers": {"user_product_id": "MLBU1", "inventory_id": inv, "seller_sku": sku},
            "product": {"tags": ["is_fulfillment"]},
            "stock": {"total_stock": total, "shipping_urgency": "THIS_WEEK", "minimum_distributable_stock": 50},
            "sales": {"sales_totals": {"period": "30DAYS", "units_sold": [{"full": 210}]}},
            "recommendation": {"recommendation_type": tipo, "suggested_quantity": {"type": "exact", "value": q} if q else None,
                               "replenishment_deadline": "THIS_WEEK", "replenishment_frequency": 4}}


class TestResumo(unittest.TestCase):
    def test_resume_campos_do_ml(self):
        r = ml_reposicao._resumo(_x())
        self.assertEqual((r["inventory_id"], r["sku"], r["total_stock"], r["urgencia"], r["tipo"], r["sugestao"], r["un_30d_full"]),
                         ("INV1", "KIT-A", 100, "THIS_WEEK", "REPLENISH", 300, 210))

    def test_sem_recomendacao_nao_quebra(self):
        r = ml_reposicao._resumo({"identifiers": {"inventory_id": None}, "stock": None, "sales": None, "recommendation": None})
        self.assertIsNone(r["sugestao"])
        self.assertEqual(r["tags"], [])


class TestCruzar(unittest.TestCase):
    def linha(self, **k):
        l = {"conta": "meli02", "inventory_id": "INV1", "sku_bling": "KIT-A", "disponivel": 60, "transferencia": 10, "sugestao_reposicao": 200}
        l.update(k)
        return l

    def test_a_caminho_e_o_maior_entre_agendado_e_chegada_do_ml(self):
        ml = {"meli02": {"INV1": ml_reposicao._resumo(_x(total=100))}}          # 100 - 60 - 10 = 30 aguardando chegada
        ag = [{"sku": "KIT-A", "unidades": 120, "envios": [{"chave": "meli-1"}]}]
        l = self.linha()
        with mock.patch.object(ml_reposicao, "por_conta", side_effect=lambda c: ml.get(c, {})), mock.patch.object(previsao.historico, "programado", return_value=ag):
            erros = previsao._cruzar_ml_e_agendados([l])
        self.assertEqual(erros, {})
        self.assertEqual((l["agendado"], l["em_chegada_ml"], l["a_caminho"], l["sugestao_liquida"]), (120, 30, 120, 80))
        self.assertEqual(l["sugestao_reposicao"], 200)                              # bruta intacta por padrão

    def test_falha_do_ml_nao_derruba_e_fica_registrada(self):
        l = self.linha()
        with mock.patch.object(ml_reposicao, "por_conta", side_effect=RuntimeError("HTTP 500")), mock.patch.object(previsao.historico, "programado", return_value=[]):
            erros = previsao._cruzar_ml_e_agendados([l])
        self.assertIn("meli02", erros)
        self.assertIsNone(l["ml"])
        self.assertEqual((l["a_caminho"], l["sugestao_liquida"]), (0, 200))


if __name__ == "__main__":
    unittest.main()
