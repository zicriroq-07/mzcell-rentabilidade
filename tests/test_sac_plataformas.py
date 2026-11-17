import json
import unittest
from unittest import mock

from marketplaces.sac import amazon, shopee


class FaseDevolucaoShopee(unittest.TestCase):
    def test_fases(self):
        self.assertEqual(shopee.fase({"status": "REQUESTED"}, {}), "responder")
        self.assertEqual(shopee.fase({"status": "SELLER_DISPUTE"}, {}), "disputa")
        self.assertEqual(shopee.fase({"status": "JUDGING"}, {}), "disputa")
        self.assertEqual(shopee.fase({"status": "CANCELLED"}, {}), "encerrada")
        self.assertEqual(shopee.fase({"status": "ACCEPTED"}, {"logistics_status": "LOGISTICS_DELIVERY_DONE"}), "chegou")
        self.assertEqual(shopee.fase({"status": "ACCEPTED"}, {"logistics_status": "LOGISTICS_PICKUP_DONE"}), "a_caminho")
        self.assertEqual(shopee.fase({"status": "PROCESSING"}, {"logistics_status": "LOGISTICS_PICKUP_DONE"}), "reembolso")
        self.assertEqual(shopee.fase({"status": "ACCEPTED"}, {}), "aceita")

    def test_acao_com_prazo_vencido(self):
        self.assertIn("encerrado", shopee.acao("chegou", "2026-10-20T10:00:00-03:00", True))
        self.assertIn("Contestar", shopee.acao("chegou", "2026-12-20T10:00:00-03:00", False))
        self.assertIn("RESPONDER", shopee.acao("responder", "2026-11-18T07:00:00-03:00"))

    def test_estado_metrica(self):
        self.assertEqual(shopee._estado_metrica("late_shipment_rate", 0, 5), "ok")
        self.assertEqual(shopee._estado_metrica("late_shipment_rate", 6, 5), "ruim")
        self.assertEqual(shopee._estado_metrica("response_rate", 61.2, 60), "ok")
        self.assertEqual(shopee._estado_metrica("response_rate", 50, 60), "ruim")
        self.assertEqual(shopee._estado_metrica("shop_rating", None, 4.5), "sem_dado")


class DevolucoesShopeeComDados(unittest.TestCase):
    def test_contestavel_e_valor(self):
        shopee._cache.clear()
        lista = [{"return_sn": "R1", "order_sn": "O1", "status": "ACCEPTED", "reason": "WRONG_ITEM", "refund_amount": 100.0, "create_time": 1794000000,
                  "return_seller_due_date": 4102444800, "item": [{"item_id": 1, "name": "X", "variation_sku": "SKU1", "amount": 1}]},
                 {"return_sn": "R2", "order_sn": "O2", "status": "ACCEPTED", "reason": "FUNCTIONAL_DMG", "refund_amount": 50.0, "create_time": 1794000000,
                  "return_seller_due_date": 1700000000, "item": [{"item_id": 2, "name": "Y", "variation_sku": "SKU2", "amount": 1}]}]
        with mock.patch.object(shopee, "_sid", return_value=1), mock.patch.object(shopee, "_devolucoes_brutas", return_value=lista), \
                mock.patch.object(shopee, "_chamar", return_value={"logistics_status": "LOGISTICS_DELIVERY_DONE"}):
            d = shopee.devolucoes(30)
        por = {i["return_sn"]: i for i in d["itens"]}
        self.assertTrue(por["R1"]["contestavel"])
        self.assertFalse(por["R2"]["contestavel"])
        self.assertTrue(por["R2"]["prazo_vencido"])
        self.assertEqual(d["resumo"]["contestaveis"], 1)
        self.assertEqual(d["resumo"]["valor_contestavel"], 100.0)
        self.assertEqual(por["R1"]["motivo_texto"], "Produto errado")


class SaudeAmazon(unittest.TestCase):
    def test_normaliza_metricas_e_ordena_pelas_ruins(self):
        rel = {"accountStatuses": [{"marketplaceId": "X", "status": "AT_RISK"}],
               "performanceMetrics": [{"lateShipmentRate": {"status": "FAIR", "targetValue": 0.04, "targetCondition": "LESS_THAN", "orderCount": 4, "lateShipmentCount": 2, "rate": 0.5},
                                       "lateShipmentRateList": [{}], "onTimeDeliveryRate": {"status": "GOOD", "targetValue": 0.97, "targetCondition": "GREATER_THAN", "rate": 1.0},
                                       "listingPolicyViolations": {"status": "BAD", "targetValue": 0, "targetCondition": "EQUALS", "defectsCount": 3},
                                       "accountHealthRating": {"ahrStatus": "GREAT", "ahrScore": 300.0}, "warningStates": []}]}
        with mock.patch.object(amazon, "_relatorio", return_value=json.dumps(rel)):
            s = amazon.saude("amazon02")
        self.assertEqual(s["status_conta"], "AT_RISK")
        self.assertEqual(s["ahr"]["pontos"], 300.0)
        self.assertEqual(s["metricas"][0]["status"], "BAD")                     # ruins primeiro
        self.assertEqual(set(s["ruins"]), {"Violações de política de anúncio", "Taxa de envio atrasado"})
        self.assertNotIn("lateShipmentRateList", [m["nome"] for m in s["metricas"]])

    def test_mensagens_indisponiveis(self):
        self.assertFalse(amazon.mensagens("amazon02")["disponivel"])

    def test_avaliacoes_sem_email(self):
        tsv = "Data\tAvaliação\tComentários\tSua resposta\tID do pedido\tE-mail do avaliador\n2026-11-02\t1\tChegou quebrado\t\t701-1\tfulano@exemplo.com\n"
        with mock.patch.object(amazon, "_relatorio", return_value=tsv):
            d = amazon.avaliacoes("amazon02", 90)
        self.assertEqual((d["total"], d["ruins"], d["media"]), (1, 1, 1.0))
        self.assertNotIn("fulano@exemplo.com", json.dumps(d))


if __name__ == "__main__":
    unittest.main()


class RotasNaOrdemCerta(unittest.TestCase):
    def test_shopee_devolucoes_nao_cai_na_rota_do_ml(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from marketplaces.sac import router as r
        app = FastAPI()
        app.include_router(r.router)
        with mock.patch.object(shopee, "devolucoes", return_value={"itens": [], "resumo": {}}):
            resp = TestClient(app).get("/sac/shopee/devolucoes")
        self.assertEqual(resp.status_code, 200)
