"""Validador de operação do SAC: Mercado Pago simulado, sem rede (casos reais de 11/11/2026, valores reduzidos)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from marketplaces.sac import validador as v

VENDA = {"id": 153815391622, "operation_type": "regular_payment", "status": "refunded", "status_detail": "bpp_refunded",
         "transaction_amount": 75.49, "date_approved": "2026-04-08T11:15:52.000-04:00", "description": "Kit facas",
         "order": {"id": "2000015891167432"}, "transaction_details": {"net_received_amount": 58.86},
         "charges_details": [{"name": "shp_fulfillment", "type": "shipping", "amounts": {"original": 7.95, "refunded": 0}},
                             {"name": "ml_sale_fee", "type": "fee", "amounts": {"original": 5.2, "refunded": 5.2}}],
         "refunds": [{"id": 2980473641, "date_created": "2026-04-22T16:30:05.000-04:00", "amount": 75.49,
                      "metadata": {"status_detail": "bpp_refunded"}, "source": {"type": "bpp"}}]}
CREDITO = {"id": 159728613075, "operation_type": "money_transfer", "status": "approved", "status_detail": "accredited",
           "transaction_amount": 75.49, "date_approved": "2026-05-22T09:41:59.000-04:00",
           "description": "Entrada de dinheiro correspondente a sua reclamação", "external_reference": "cashback_2611953091", "order": {}}


class Validador(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.contest = os.path.join(d.name, "c.json")
        with open(self.contest, "w", encoding="utf-8") as f:
            json.dump({"meli02": {"5500925907": {"claim_id": 5500925907, "pedido": 2000015891167432, "operacao": 159728613075,
                                                  "confianca": "confirmado"}}}, f)
        self.pagos = {}
        self.creditos = []
        for alvo, nome, val in ((v, "CONTESTACOES", self.contest), (v, "_get", self.get)):
            p = mock.patch.object(alvo, nome, val); p.start(); self.addCleanup(p.stop)

    def get(self, conta, caminho, **q):
        if conta != "meli02":
            return 404, {}
        if caminho == "/v1/payments/search":
            if q.get("operation_type") == "money_transfer":
                return 200, {"results": self.creditos}
            return 200, {"results": [self.pagos[k] for k in self.pagos if str(self.pagos[k].get("order", {}).get("id")) == q.get("external_reference")]}
        n = caminho.rsplit("/", 1)[1]
        return (200, self.pagos[n]) if n in self.pagos else (404, {})

    def test_operacao_de_credito_de_reclamacao(self):
        self.pagos = {"159728613075": CREDITO}
        r = v.operacao("159728613075")
        self.assertEqual((r["conta"], r["ressarcido"], r["credito_reclamacao"], r["aviso"]), ("meli02", 75.49, True, None))

    def test_operacao_que_e_o_pagamento_da_venda_avisa(self):
        self.pagos = {"153815391622": VENDA}
        r = v.operacao("153815391622")
        self.assertEqual((r["ressarcido"], r["venda"]), (0.0, "2000015891167432"))
        self.assertIn("pagamento da venda", r["aviso"])
        self.assertFalse(v.operacao("999")["encontrado"])

    def test_venda_entradas_saidas_e_saldo_com_ressarcimento_da_contestacao(self):
        self.pagos = {"153815391622": VENDA, "159728613075": CREDITO}
        r = v.venda("2000015891167432")
        # 75,49 venda − 7,95 frete − 5,20 tarifa − 75,49 estorno + 5,20 tarifa devolvida + 75,49 ressarcimento
        self.assertEqual((r["entradas"], r["saidas"], r["saldo"], r["ressarcido"]), (156.18, -88.64, 67.54, 75.49))
        self.assertTrue(any("contestação 5500925907" in m["descricao"] for m in r["movimentos"]))

    def test_venda_sem_contestacao_acha_credito_provavel_pelo_valor(self):
        with open(self.contest, "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.pagos = {"153815391622": VENDA}
        self.creditos = [dict(CREDITO, transaction_amount=10.0, id=1), dict(CREDITO)]
        r = v.venda("2000015891167432")
        prov = [m for m in r["movimentos"] if m.get("provavel")]
        self.assertEqual([(m["operacao"], m["valor"]) for m in prov], [(159728613075, 75.49)])

    def test_coberto_pelo_ml_nao_debita_o_vendedor(self):
        self.pagos = {"153815391622": dict(VENDA, status_detail="bpp_covered")}
        r = v.venda("2000015891167432")
        self.assertEqual(r["saldo"], round(75.49 - 7.95 - 5.2, 2))
        self.assertFalse(any(m["descricao"] == "Estorno ao comprador" for m in r["movimentos"]))

    def test_numero_invalido(self):
        with self.assertRaises(ValueError):
            v.operacao("abc")
        with self.assertRaises(ValueError):
            v.venda("1", "meli09")


if __name__ == "__main__":
    unittest.main()
