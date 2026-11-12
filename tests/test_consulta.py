"""Consulta por PowerShell: login, permissões por módulo, interpretador e banco. Sem rede e sem Bling/ML reais."""
import os
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_service
from marketplaces.consulta import acesso, banco, interprete
from marketplaces.consulta import router as rt
from marketplaces.consulta.catalogo import CATALOGO

USUARIOS = [
    {"login": "exp", "nome": "Exp", "senha": auth_service.h("123"), "modulos": ["expedicao"], "ativo": True},
    {"login": "sac", "nome": "Sac", "senha": auth_service.h("123"), "modulos": ["sac"], "ativo": True},
    {"login": "chefe", "nome": "Chefe", "senha": auth_service.h("123"), "modulos": ["todos"], "ativo": True},
    {"login": "novo", "nome": "Novo", "senha": auth_service.h("123"), "modulos": ["expedicao"], "primeiro_acesso": True},
    {"login": "fora", "nome": "Fora", "senha": auth_service.h("123"), "modulos": ["expedicao"], "ativo": False},
]


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        for alvo, nome, v in ((banco, "CAMINHO", os.path.join(d.name, "c.db")), (auth_service, "load_usuarios", lambda: USUARIOS)):
            p = mock.patch.object(alvo, nome, v); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(acesso._tokens, clear=True); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(acesso._falhas, clear=True); p.start(); self.addCleanup(p.stop)
        banco.semear(CATALOGO)
        app = FastAPI(); app.include_router(rt.router)
        self.c = TestClient(app)

    def entrar(self, login):
        r = self.c.post("/consulta/login", json={"login": login, "senha": "123"})
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}


class Login(Base):
    def test_sem_token_e_senha_errada(self):
        self.assertEqual(self.c.get("/consulta/consultas").status_code, 401)
        self.assertEqual(self.c.post("/consulta/login", json={"login": "exp", "senha": "x"}).status_code, 401)

    def test_primeiro_acesso_e_inativo_nao_entram(self):
        self.assertEqual(self.c.post("/consulta/login", json={"login": "novo", "senha": "123"}).status_code, 403)
        self.assertEqual(self.c.post("/consulta/login", json={"login": "fora", "senha": "123"}).status_code, 401)

    def test_bloqueio_depois_de_cinco_erros(self):
        for _ in range(5):
            self.c.post("/consulta/login", json={"login": "exp", "senha": "x"})
        self.assertEqual(self.c.post("/consulta/login", json={"login": "exp", "senha": "123"}).status_code, 429)

    def test_usuario_bloqueado_depois_perde_o_token(self):
        h = self.entrar("exp")
        self.assertEqual(self.c.get("/consulta/consultas", headers=h).status_code, 200)
        with mock.patch.object(auth_service, "load_usuarios", lambda: [dict(USUARIOS[0], ativo=False)]):
            self.assertEqual(self.c.get("/consulta/consultas", headers=h).status_code, 401)


class RedeInterna(Base):
    def interno(self, v=True):
        p = mock.patch.object(acesso, "ip_interno", lambda h: v); p.start(); self.addCleanup(p.stop)

    def test_ips(self):
        for h in ("192.168.15.20", "10.0.0.5", "172.16.3.1", "127.0.0.1", "::1"):
            self.assertTrue(acesso.ip_interno(h), h)
        for h in ("8.8.8.8", "100.101.102.103", "169.254.1.1", "testclient", ""):
            self.assertFalse(acesso.ip_interno(h), h)

    def test_sem_senha_na_rede_interna_so_expedicao_e_sac(self):
        self.interno()
        ids = {c["id"] for c in self.c.get("/consulta/consultas").json()}
        self.assertIn("checkout_abertos", ids); self.assertIn("reclamacao", ids)
        with mock.patch.dict(rt.EXECUTORES, {"estoque": lambda u, **k: {"texto": [u["login"]], "dados": {}}}):
            self.assertEqual(self.c.get("/consulta/executar/estoque?sku=A").json()["texto"], ["rede-interna"])

    def test_sem_senha_fora_da_rede_e_recusado(self):
        self.interno(False)
        self.assertEqual(self.c.get("/consulta/consultas").status_code, 401)

    def test_leitura_direta_e_estatisticas_pedem_login(self):
        self.interno()
        self.assertEqual(self.c.get("/consulta/bling/bling01/pedidos/vendas").status_code, 401)
        self.assertEqual(self.c.get("/consulta/ml/meli01/orders/1").status_code, 401)
        self.assertEqual(self.c.get("/consulta/estatisticas").status_code, 401)

    def test_token_invalido_nao_vira_anonimo(self):
        self.interno()
        self.assertEqual(self.c.get("/consulta/consultas", headers={"Authorization": "Bearer lixo"}).status_code, 401)


class Permissoes(Base):
    def test_cada_um_ve_so_o_seu(self):
        ids = lambda login: {c["id"] for c in self.c.get("/consulta/consultas", headers=self.entrar(login)).json()}
        self.assertIn("checkout_abertos", ids("exp"))
        self.assertNotIn("reclamacao", ids("exp"))
        self.assertIn("reclamacao", ids("sac"))
        self.assertNotIn("checkout_abertos", ids("sac"))
        self.assertIn("ajuda", ids("sac"))
        self.assertEqual(ids("chefe"), {c["id"] for c in CATALOGO})

    def test_executar_fora_do_modulo_da_403(self):
        self.assertEqual(self.c.get("/consulta/executar/reclamacao?numero=1", headers=self.entrar("exp")).status_code, 403)

    def test_executar_registra_uso(self):
        with mock.patch.dict(rt.EXECUTORES, {"checkout_abertos": lambda u, **k: {"texto": ["ok"], "dados": k}}):
            r = self.c.get("/consulta/executar/checkout_abertos?plataforma=shopee", headers=self.entrar("exp"))
        self.assertEqual(r.json()["texto"], ["ok"])
        self.assertEqual(r.json()["parametros"], {"plataforma": "shopee"})
        self.assertEqual(banco.estatisticas()["mais_usadas"][0]["consulta"], "checkout_abertos")

    def test_leitura_direta_so_do_que_esta_liberado(self):
        h = self.entrar("exp")
        self.assertEqual(self.c.get("/consulta/bling/bling01/contatos", headers=h).status_code, 403)
        self.assertEqual(self.c.get("/consulta/bling/bling01/pedidos/vendas/../../contatos", headers=h).status_code, 403)
        self.assertEqual(self.c.get("/consulta/bling/bling09/pedidos/vendas", headers=h).status_code, 400)
        self.assertEqual(self.c.get("/consulta/ml/meli01/post-purchase/v1/claims/search", headers=h).status_code, 403)   # SAC, não expedição
        with mock.patch("marketplaces.bling.api.get", return_value=(200, {"data": []})) as g:
            self.assertEqual(self.c.get("/consulta/bling/bling01/pedidos/vendas?numero=5", headers=h).status_code, 200)
        g.assert_called_once_with("bling01", "/pedidos/vendas", numero="5")

    def test_acesso_completo_le_qualquer_recurso(self):
        with mock.patch("marketplaces.bling.api.get", return_value=(200, {"data": []})):
            self.assertEqual(self.c.get("/consulta/bling/bling01/contatos", headers=self.entrar("chefe")).status_code, 200)
            self.assertEqual(self.c.get("/consulta/bling/bling01/contatos", headers=self.entrar("exp")).status_code, 403)
            self.assertEqual(self.c.get("/consulta/bling/bling01/../x", headers=self.entrar("chefe")).status_code, 400)

    def test_so_ha_leitura_alem_do_login(self):
        metodos = {(m, r.path) for r in rt.router.routes for m in r.methods}
        self.assertEqual({p for m, p in metodos if m not in ("GET", "HEAD")}, {"/consulta/login"})

    def test_estatisticas_so_para_quem_tem_todos(self):
        self.assertEqual(self.c.get("/consulta/estatisticas", headers=self.entrar("exp")).status_code, 403)
        self.assertEqual(self.c.get("/consulta/estatisticas", headers=self.entrar("chefe")).status_code, 200)


class Interprete(Base):
    def p(self, texto):
        return interprete.interpretar(texto, banco.consultas())

    def test_exemplos_do_usuario(self):
        self.assertEqual(self.p("quantos pedidos de checkout tenho em aberto?")["consulta"], "checkout_abertos")
        r = self.p("qual é a reclamação do pedido 2000012345678901?")
        self.assertEqual((r["consulta"], r["params"]), ("reclamacao", {"numero": "2000012345678901"}))
        self.assertEqual(self.p("checkouts pendentes da Shopee")["params"], {"plataforma": "shopee"})

    def test_pedido_estoque_full_cancelados(self):
        self.assertEqual(self.p("situação do pedido 2611123E8G8WJW")["params"], {"numero": "2611123E8G8WJW"})
        r = self.p("estoque do KIT2-MIC-SEMFIO-PRETO no bling 1")
        self.assertEqual((r["consulta"], r["params"]), ("estoque", {"sku": "KIT2-MIC-SEMFIO-PRETO", "conta": "bling01"}))
        self.assertEqual(self.p("quando é a coleta do full?")["consulta"], "full_envios")
        self.assertEqual(self.p("tem pedido cancelado ativo?")["consulta"], "cancelamentos")

    def test_falta_parametro_e_nao_entendeu(self):
        self.assertEqual(self.p("reclamação do pedido")["faltam"], ["numero"])
        r = self.p("qual a cor do céu")
        self.assertIsNone(r["consulta"]); self.assertEqual(r["candidatas"], [])

    def test_perguntar_sem_resposta_vai_para_o_banco(self):
        r = self.c.get("/consulta/perguntar", params={"texto": "qual a cor do céu"}, headers=self.entrar("exp")).json()
        self.assertFalse(r["entendi"])
        self.assertEqual(banco.estatisticas()["sem_resposta"][0]["texto"], "qual a cor do céu")

    def test_perguntar_respeita_modulo(self):
        # o usuário da expedição não vê a consulta de reclamação: a pergunta não é entendida em vez de ser executada
        r = self.c.get("/consulta/perguntar", params={"texto": "reclamação do pedido 2000012345678901"}, headers=self.entrar("exp")).json()
        self.assertNotEqual(r.get("consulta"), "reclamacao")


class Banco(Base):
    def test_semear_nao_desfaz_palavras_editadas(self):
        banco._executar("UPDATE consultas SET palavras='[\"minha\"]', editada=1 WHERE id='estoque'")
        banco.semear(CATALOGO)
        self.assertEqual(next(c for c in banco.consultas() if c["id"] == "estoque")["palavras"], ["minha"])
        self.assertEqual(len(banco.consultas()), len(CATALOGO))

    def test_rota_autonoma_na_trava_de_rede(self):
        from marketplaces import acesso_rede
        self.assertTrue(acesso_rede._casa("/consulta/login", acesso_rede.AUTONOMOS))
        self.assertFalse(acesso_rede._casa("/consultax", acesso_rede.AUTONOMOS))


if __name__ == "__main__":
    unittest.main()
