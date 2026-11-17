import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from marketplaces.expedicao import amazon_flash, dia

BRT = timezone(timedelta(hours=-3))


def pedido(**kw):
    base = {"AmazonOrderId": "701-0000000-0000001", "FulfillmentChannel": "MFN", "OrderStatus": "Unshipped",
            "ShipServiceLevel": "Std EZ Regional", "ShipmentServiceLevelCategory": "Standard",
            "NumberOfItemsUnshipped": 1, "NumberOfItemsShipped": 0, "LatestShipDate": "2026-11-17T15:00:00Z"}
    base.update(kw)
    return base


class DeteccaoFlash(unittest.TestCase):
    def test_dba_nao_e_flash(self):
        self.assertFalse(amazon_flash.eh_flash(pedido()))

    def test_flash_pelo_nome_ou_pela_categoria(self):
        self.assertTrue(amazon_flash.eh_flash(pedido(ShipServiceLevel="Amazon Flash")))
        self.assertTrue(amazon_flash.eh_flash(pedido(ShipmentServiceLevelCategory="SameDay")))

    def test_linha_flash(self):
        hoje = datetime(2026, 11, 17, 9, 0, tzinfo=BRT).date()
        l = amazon_flash.linha("amazon02", pedido(ShipServiceLevel="Amazon Flash"), hoje)
        self.assertEqual((l["canal"], l["conta"], l["unidades"], l["bipado"]), ("Flash", "amazon02", 1, False))
        self.assertTrue(l["meta"].startswith("2026-11-17T12:00"))

    def test_cancelado_e_dba_ficam_fora(self):
        hoje = datetime(2026, 11, 17, tzinfo=BRT).date()
        self.assertIsNone(amazon_flash.linha("amazon02", pedido(ShipServiceLevel="Amazon Flash", OrderStatus="Canceled"), hoje))
        self.assertIsNone(amazon_flash.linha("amazon02", pedido(), hoje))

    def test_enviado_conta_como_bipado(self):
        hoje = datetime(2026, 11, 17, tzinfo=BRT).date()
        l = amazon_flash.linha("amazon02", pedido(ShipServiceLevel="Amazon Flash", OrderStatus="Shipped"), hoje)
        self.assertTrue(l["bipado"])


class ModuloDesligado(unittest.TestCase):
    def test_desligado_nao_chama_a_amazon(self):
        with mock.patch.object(amazon_flash, "ativo", return_value=False), mock.patch.object(amazon_flash.api, "get") as g:
            self.assertEqual(amazon_flash.linhas(), ([], {}))
            g.assert_not_called()

    def test_cartao_sem_dados_quando_nao_ha_pedidos(self):
        agora = datetime(2026, 11, 17, 9, 0, tzinfo=BRT)
        r = dia.montar([], {}, [], agora, agora.date(), [{"plataforma": "Amazon", "canal": "Flash", "etapa": "Corte do dia", "corte": "12:00"}])
        m = next(x for x in r["modulos"] if x["id"] == "amazon_flash")
        self.assertTrue(m["sem_dados"])
        self.assertEqual(m["plataforma"], "Amazon")

    def test_cartao_com_pedido_flash(self):
        agora = datetime(2026, 11, 17, 9, 0, tzinfo=BRT)
        l = amazon_flash.linha("amazon02", pedido(ShipServiceLevel="Amazon Flash"), agora.date())
        r = dia.montar([l], {}, [], agora, agora.date(), [{"plataforma": "Amazon", "canal": "Flash", "etapa": "Corte do dia", "corte": "12:00"}])
        m = next(x for x in r["modulos"] if x["id"] == "amazon_flash")
        self.assertEqual((m["pedidos"], m["pendentes"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
