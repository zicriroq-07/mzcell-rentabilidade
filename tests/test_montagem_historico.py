"""Banco dos agendamentos de Full (marketplaces/montagem/historico.py) — sem rede, em pasta temporária."""

import os
import tempfile
import unittest
from datetime import date
from unittest import mock

from marketplaces.montagem import armazenamento as arm
from marketplaces.montagem import historico


class Base(unittest.TestCase):
    def setUp(self):
        self._orig = arm.CAMINHO
        self._dir = tempfile.TemporaryDirectory()
        arm.CAMINHO = os.path.join(self._dir.name, "montagem_envios.json")

    def tearDown(self):
        arm.CAMINHO = self._orig
        self._dir.cleanup()

    def criar(self, numero="111", canal="meli", coleta="2026-11-20", produtos=None):
        produtos = produtos or [{"sku": "KIT-A", "unidades": 30, "titulo": "Kit A"}, {"sku": "KIT-B", "unidades": 12}]
        return arm.criar(numero, "meli01", coleta, "", produtos, "felipe", canal)


class TestHistorico(Base):
    def test_envio_criado_vai_para_o_banco_com_sku_e_unidades(self):
        self.criar()
        lista = historico.listar()
        self.assertEqual(len(lista), 1)
        e = lista[0]
        self.assertEqual((e["chave"], e["canal"], e["coleta"], e["status"], e["unidades"]), ("meli-111", "meli", "2026-11-20", "fila", 42))
        self.assertEqual([(i["sku"], i["unidades"]) for i in e["itens"]], [("KIT-A", 30), ("KIT-B", 12)])
        self.assertTrue(os.path.isfile(os.path.join(self._dir.name, "full_agendamentos.db")))     # ao lado do arquivo de trabalho

    def test_mudar_coleta_e_status_gera_eventos(self):
        self.criar()
        arm.alterar_prazo("meli-111", "2026-11-19T17:00", "2026-11-21")
        nomes = [x["evento"] for x in historico.eventos("meli-111")]
        self.assertEqual(nomes, ["criado", "coleta_alterada"])
        self.assertEqual(historico.eventos("meli-111")[1]["detalhe"], {"de": "2026-11-20", "para": "2026-11-21"})
        self.assertEqual(historico.listar()[0]["coleta"], "2026-11-21")

    def test_remover_do_arquivo_nao_apaga_do_banco(self):
        self.criar()
        arm.remover("meli-111")
        self.assertEqual(historico.listar(), [])                                   # some das ativas
        todos = historico.listar(incluir_removidos=True)
        self.assertEqual(len(todos), 1)
        self.assertTrue(todos[0]["removido_em"])
        self.assertEqual(historico.eventos("meli-111")[-1]["evento"], "removido")
        self.assertEqual(historico.listar(incluir_removidos=True)[0]["itens"][0]["sku"], "KIT-A")   # SKU e quantidade continuam

    def test_filtros_por_canal_e_sku(self):
        self.criar("111", "meli")
        self.criar("FBA1", "amazon", produtos=[{"sku": "KIT-A-FBA", "unidades": 90}])
        self.assertEqual([e["canal"] for e in historico.listar(canal="amazon")], ["amazon"])
        self.assertEqual([e["chave"] for e in historico.listar(sku="kit-a")], ["meli-111"])    # sem diferenciar maiúsculas
        self.assertEqual(historico.listar(sku="NAO-EXISTE"), [])

    def test_programado_soma_por_canal_e_sku_e_respeita_a_janela(self):
        self.criar("111", "meli", "2026-11-20", [{"sku": "KIT-A", "unidades": 30}])
        self.criar("222", "meli", "2026-11-25", [{"sku": "KIT-A", "unidades": 20}])
        self.criar("333", "meli", "2026-10-01", [{"sku": "KIT-A", "unidades": 999}])     # coleta antiga: já recebido, fora da conta
        r = historico.programado(hoje=date(2026, 11, 22))
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["canal"], r[0]["sku"], r[0]["unidades"]), ("meli", "KIT-A", 50))
        coletados = {e["chave"]: e["coletado"] for e in r[0]["envios"]}
        self.assertEqual(coletados, {"meli-111": True, "meli-222": False})              # 20/11 já coletado; 25/11 ainda não

    def test_envio_removido_nao_conta_como_programado(self):
        self.criar("111", "meli", "2026-11-25", [{"sku": "KIT-A", "unidades": 30}])
        arm.remover("meli-111")
        self.assertEqual(historico.programado(hoje=date(2026, 11, 22)), [])

    def test_falha_do_banco_nao_derruba_a_gravacao_do_galpao(self):
        with mock.patch.object(historico, "_db", side_effect=RuntimeError("disco cheio")):
            e = self.criar()
        self.assertEqual(e["chave"], "meli-111")                                         # o arquivo de trabalho foi gravado
        self.assertIn("disco cheio", historico.ultimo_erro)
        historico.carregar_do_arquivo()                                                  # com o banco de volta, reconcilia
        self.assertEqual(len(historico.listar()), 1)
        self.assertIsNone(historico.ultimo_erro)

    def test_resumo(self):
        self.criar("111", "meli")
        self.criar("FBA1", "amazon", produtos=[{"sku": "X", "unidades": 5}])
        r = historico.resumo()
        self.assertEqual((r["envios_ativos"], r["por_canal"]), (2, {"amazon": 1, "meli": 1}))


if __name__ == "__main__":
    unittest.main()
