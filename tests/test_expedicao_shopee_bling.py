"""Shopee pelo Bling na Expedição: Bling simulado e arquivo temporário, sem rede."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from marketplaces.expedicao import shopee_bling as s

BRT = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 11, 6, 9, 0, tzinfo=BRT)
SERVICO = {1: "Shopee Xpress", 2: "Logistica Shopee", 3: "Shopee Xpress", 4: "Logistica Shopee", 5: "Outra Coisa"}


def ped(pid, sit, data="2026-11-05", loja=10):
    return {"id": pid, "numero": 1000 + pid, "numeroLoja": f"S{pid}", "data": data, "loja": {"id": loja}, "situacao": {"id": sit}}


class Linhas(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.caminho = os.path.join(d.name, "estado.json")
        self.chamadas = []
        self.pedidos = {}
        for alvo, nome, v in ((s, "CAMINHO", self.caminho), (s.bling, "get", self.get), (s.time, "sleep", lambda x: None),
                              (s.bling_checkout, "_pedidos", lambda c: self.pedidos), (s.cortes, "carregar", lambda: []),
                              (s, "INICIO", "2026-11-01")):
            p = mock.patch.object(alvo, nome, v); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(s._lojas, {"ts": 0.0, "ids": {}}); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(s._detalhes, clear=True); p.start(); self.addCleanup(p.stop)

    def get(self, conta, path, **k):
        self.chamadas.append(path)
        if path == "/canais-venda":
            return 200, {"data": [{"id": 10, "descricao": "14- Shopee CNPJ"}, {"id": 11, "descricao": "02- MERCADO LIVRE"}, {"id": 12, "descricao": "03- MAGALU"}]}
        pid = int(path.rsplit("/", 1)[1])
        return 200, {"data": {"transporte": {"volumes": [{"servico": SERVICO.get(pid, "Agência Magalu")}]}, "itens": [{"codigo": "SKU", "descricao": "Produto", "quantidade": 2}]}}

    def carregar(self, *lista):
        self.pedidos = {p["numeroLoja"]: p for p in lista}
        return s.linhas(AGORA)

    def test_aberto_entra_com_modalidade_e_outra_loja_fica_de_fora(self):
        ls, erros = self.carregar(ped(1, 6), ped(2, 15), ped(3, 6, loja=11))
        self.assertEqual(erros, {})
        self.assertEqual([(l["pedido"], l["canal"], l["bling_checkout"], l["unidades"], l["conta"]) for l in ls],
                         [("S1", "Shopee Xpress", False, 2, "shopee"), ("S2", "Entrega Direta", False, 2, "shopee")])
        self.assertEqual(ls[0]["meta"], "2026-11-06T12:00:00-03:00")
        self.assertEqual(ls[0]["itens"][0]["sku"], "SKU")

    def test_verificado_conta_no_dia_em_que_virou(self):
        self.carregar(ped(1, 6))
        ls, _ = self.carregar(ped(1, 24))                      # estava em aberto: feito hoje, mesmo com pedido de ontem
        self.assertEqual([(l["pedido"], l["bling_checkout"]) for l in ls], [("S1", True)])
        ls, _ = self.carregar(ped(1, 24))
        self.assertEqual(len(ls), 1)

    def test_verificado_antigo_nao_entra_e_de_hoje_entra(self):
        ls, _ = self.carregar(ped(1, 24, data="2026-11-04"), ped(2, 24, data="2026-11-06"))
        self.assertEqual([l["pedido"] for l in ls], ["S2"])

    def test_feito_de_ontem_nao_volta(self):
        self.carregar(ped(1, 6))
        ontem = AGORA - timedelta(days=1)
        self.pedidos = {"S1": ped(1, 24)}
        s.linhas(ontem)
        ls, _ = s.linhas(AGORA)
        self.assertEqual(ls, [])

    def test_pedido_antes_do_inicio_fica_de_fora(self):
        ls, _ = self.carregar(ped(1, 6, data="2026-10-31"), ped(2, 6, data="2026-11-01"))
        self.assertEqual([(l["pedido"], l["fonte"]) for l in ls], [("S2", "Bling")])

    def test_cancelado_nao_entra(self):
        self.assertEqual(self.carregar(ped(1, 12))[0], [])

    def test_detalhe_so_uma_vez(self):
        self.carregar(ped(1, 6)); self.carregar(ped(1, 6))
        self.assertEqual(self.chamadas.count("/pedidos/vendas/1"), 1)
        self.assertEqual(self.chamadas.count("/canais-venda"), 1)

    def test_modalidade_desconhecida_avisa_e_nao_conta(self):
        ls, erros = self.carregar(ped(5, 6))
        self.assertEqual(ls, [])
        self.assertIn("Outra Coisa", erros["shopee_modalidade"])

    def test_bling_fora_nao_derruba(self):
        with mock.patch.object(s.bling_checkout, "_pedidos", side_effect=RuntimeError("401")):
            ls, erros = s.linhas(AGORA)
        self.assertEqual(ls, [])
        self.assertIn("401", erros["shopee"])

    def test_numero_da_shopee_com_dia_seguinte_vai_para_o_corte_de_amanha(self):
        # 11/11 no Bling e 12/11 na Shopee (UTC+8): criado depois das 13h de Brasília
        depois = dict(ped(1, 6, data="2026-11-06"), numeroLoja="2611073KYB4K72")
        antes = dict(ped(2, 6, data="2026-11-06"), numeroLoja="2611061WA36D7Y")
        self.pedidos = {p["numeroLoja"]: p for p in (depois, antes)}
        ls, _ = s.linhas(datetime(2026, 11, 6, 15, 0, tzinfo=BRT))
        self.assertEqual([(l["pedido"], l["meta"]) for l in ls], [("2611073KYB4K72", "2026-11-07T12:00:00-03:00"),
                                                                ("2611061WA36D7Y", "2026-11-06T12:00:00-03:00")])

    def test_hora_em_que_apareceu_no_bling_conta_so_com_o_servidor_de_olho(self):
        dia = datetime(2026, 11, 6, tzinfo=BRT)
        self.pedidos = {"S1": ped(1, 6, data="2026-11-06")}
        s.linhas(dia + timedelta(hours=11, minutes=50))                       # recém-ligado: hora desconhecida
        self.pedidos["S2"] = ped(2, 6, data="2026-11-06")
        self.pedidos["S4"] = ped(4, 6, data="2026-11-06")
        s.linhas(dia + timedelta(hours=12, minutes=5))                        # S2 e S4: dentro da folga do corte
        s.linhas(dia + timedelta(hours=13, minutes=30))
        self.pedidos["S3"] = ped(3, 6, data="2026-11-06")
        ls, _ = s.linhas(dia + timedelta(hours=13, minutes=40))               # S3 (Xpress, corte 12h no teste): depois
        self.assertEqual({l["pedido"]: l["meta"][:10] for l in ls},
                         {"S1": "2026-11-06", "S2": "2026-11-06", "S4": "2026-11-06", "S3": "2026-11-07"})

    def test_reinicio_depois_de_muito_tempo_nao_empurra_para_amanha(self):
        self.pedidos = {"S1": ped(1, 6, data="2026-11-06")}
        s.linhas(datetime(2026, 11, 6, 8, 0, tzinfo=BRT))
        self.pedidos["S2"] = ped(2, 6, data="2026-11-06")
        ls, _ = s.linhas(datetime(2026, 11, 6, 16, 0, tzinfo=BRT))           # 8 horas sem ler
        self.assertEqual([l["meta"][:10] for l in ls], ["2026-11-06", "2026-11-06"])

    def test_xpress_com_corte_as_13h_e_numero_do_dia_seguinte_vai_para_amanha(self):
        # caso de 11/11: 2611123E8G8WJW, Bling 11/11; criado a partir das 13h, que é o corte do Xpress
        p = dict(ped(1, 6, data="2026-11-11"), numeroLoja="2611123E8G8WJW")
        with mock.patch.object(s.cortes, "carregar", lambda: [{"plataforma": "Shopee", "canal": "Shopee Xpress", "corte": "13:00"}]):
            self.assertEqual(s._meta("Shopee Xpress", datetime(2026, 11, 11).date(), "Shopee", s._entrada_shopee(p)),
                             "2026-11-12T13:00:00-03:00")

    def test_sabado_depois_do_corte_vai_para_segunda(self):
        with mock.patch.object(s.cortes, "carregar", lambda: [{"plataforma": "Shopee", "canal": "Shopee Xpress", "corte": "13:00"}]):
            self.assertEqual(s._meta("Shopee Xpress", datetime(2026, 11, 7).date(), "Shopee",
                                     datetime(2026, 11, 7, 14, 0, tzinfo=BRT)), "2026-11-09T13:00:00-03:00")

    def test_magalu_entra_com_modalidade_unica_e_corte_proprio(self):
        with mock.patch.object(s.cortes, "carregar", lambda: [{"plataforma": "Magalu", "canal": "Magalu", "corte": "10:30"}]):
            ls, erros = self.carregar(ped(7, 6, loja=12), ped(8, 24, data="2026-11-06", loja=12))
        self.assertEqual(erros, {})
        self.assertEqual([(l["conta"], l["plataforma"], l["canal"], l["bling_checkout"]) for l in ls],
                         [("magalu", "Magalu", "Magalu", False), ("magalu", "Magalu", "Magalu", True)])
        self.assertEqual(ls[0]["meta"], "2026-11-06T10:30:00-03:00")


if __name__ == "__main__":
    unittest.main()
