"""
agentes/vitrine/vitrine.py — Linha de comando do agente Vitrine (Amazon 2)
MZ App

  python -m agentes.vitrine.vitrine coletar [--sem-ads] [--sem-trafego]   fotografia do dia (somente leitura)
  python -m agentes.vitrine.vitrine acao <arquivo.json> [--confirmar]     executa UMA ação pela cerca (sem --confirmar: simula)
  python -m agentes.vitrine.vitrine vigia                                 freio automático (PARAR, teto de gasto, fim do mandato)
  python -m agentes.vitrine.vitrine pausar "<motivo>"                     pausa todas as campanhas do Vitrine
  python -m agentes.vitrine.vitrine margem                                piso, preço atual e MC por TACoS
  python -m agentes.vitrine.vitrine pedir-envio <ASIN> <qtd> "<motivo>"   pede envio ao FBA por e-mail (não envia nada à Amazon)
  python -m agentes.vitrine.vitrine relatorio [--enviar]                  resumo do dia (e-mail aos destinatários do mandato)
"""

import json
import os
import smtplib
import sys
from datetime import date
from email.mime.text import MIMEText

from agentes.vitrine import cerca, escrita
from agentes.vitrine.escrita import BRT, PARAR, carregar_mandato, hoje, ler_estado, registrar

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _brl(v) -> str:
    return "—" if v is None else f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v) -> str:
    return "—" if v is None else f"{float(v):.2f}%".replace(".", ",")


def enviar_email(dest: list, assunto: str, texto: str) -> bool:
    try:
        with open(os.path.join(BASE, "email_config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        msg = MIMEText(texto, "plain", "utf-8")
        msg["From"], msg["To"], msg["Subject"] = cfg["email_remetente"], ", ".join(dest), assunto
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as s:
            s.starttls()
            s.login(cfg["email_remetente"], cfg["senha_app"])
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"e-mail FALHOU: {type(e).__name__}")
        return False


def cmd_margem():
    m = carregar_mandato()
    foto = (ler_estado().get("foto") or {}).get("produtos", {})
    for p in m["produtos"]:
        atual = (foto.get(p["asin"], {}).get("margem_preco_atual") or {}).get("preco")
        print(f"\n{p['nome']} ({p['asin']}) | piso {_brl(p['preco_piso'])} | teto {_brl(p['preco_teto'])} | preço atual {_brl(atual)}")
        for t in (0, 0.05, 0.10, 0.15, 0.20):
            z = cerca.piso_zero_a_zero(p, t)
            alvo = cerca.piso_zero_a_zero({**p, "cmv": p["cmv"]}, t + float(m.get("margem_alvo", 0.20)))
            linha = f"  TACoS {t:>4.0%}: zero a zero {_brl(z)} | MC {m.get('margem_alvo', 0.20):.0%} {_brl(alvo)}"
            if atual:
                linha += f" | MC no preço atual {_brl(cerca.margem(p, atual, t)['mc'])}"
            print(linha)


def cmd_vigia() -> int:
    """Sem IA: só regras. Pausa tudo se houver PARAR, se o gasto bater no limite ou se o mandato acabou."""
    m = carregar_mandato()
    motivo = None
    if os.path.exists(PARAR):
        motivo = "arquivo PARAR presente"
    elif hoje() > date.fromisoformat(m["fim"]):
        motivo = f"mandato encerrado em {m['fim']}"
    else:
        try:
            cerca.checar_gasto(m, float(ler_estado().get("gasto_acumulado", 0)))
        except cerca.Recusa as e:
            motivo = str(e)
    if not motivo:
        print("vigia: tudo dentro do mandato")
        return 0
    try:
        r = escrita.pausar_tudo(motivo)
    except Exception as e:
        registrar({"tipo": "vigia", "motivo": motivo, "erro": f"{type(e).__name__}: {str(e)[:300]}"})
        print(f"vigia: QUERIA PAUSAR ({motivo}) e falhou: {type(e).__name__}")
        return 2
    if r["pausadas"]:
        enviar_email(m["destinatarios"], "Vitrine: campanhas pausadas pelo vigia", f"Motivo: {motivo}\nCampanhas: {r['pausadas']}")
    print(f"vigia: {motivo} | pausadas: {r['pausadas']}")
    return 1


def cmd_pedir_envio(asin: str, qtd: int, motivo: str) -> int:
    m = carregar_mandato()
    p = cerca.produto_do_asin(m, asin)
    texto = (f"Pedido de envio ao FBA (Full da Amazon 2), gerado pelo agente Vitrine.\n\n"
             f"Produto: {p['nome']}\nASIN: {asin}\nSKU FBA: {p.get('sku_fba')}\nQuantidade sugerida: {qtd} unidades\n\n"
             f"Motivo: {motivo}\n\nO Vitrine não cria o envio: quem confere e envia é a equipe. Responder a este e-mail "
             f"com o número do envio quando sair.")
    ok = enviar_email(m["responsaveis_envio"] + m["destinatarios"], f"Vitrine: enviar {qtd} un de {p['nome']} ao FBA", texto)
    registrar({"tipo": "pedir_envio", "alvo": asin, "qtd": qtd, "motivo": motivo, "esperado": "estoque FBA reposto",
               "resultado": {"email": ok}, "confirmado": True})
    print("pedido enviado" if ok else "pedido NÃO enviado")
    return 0 if ok else 2


def montar_relatorio() -> str:
    m = carregar_mandato()
    est = ler_estado()
    foto = est.get("foto") or {}
    dia = hoje()
    ini, fim = date.fromisoformat(m["inicio"]), date.fromisoformat(m["fim"])
    faltam = (fim - dia).days
    L = [f"VITRINE — Amazon 2 — {dia:%d/%m/%Y} (dia {(dia - ini).days + 1} de {(fim - ini).days + 1})",
         f"⏳ Faltam {faltam} dia(s) para o fim do mandato ({fim:%d/%m})" if faltam > 0 else "⏳ Último dia do mandato", ""]
    L.append(f"Ads: gasto acumulado {_brl(est.get('gasto_acumulado', 0))} de {_brl(m['ads']['teto_total'])} "
             f"(teto/dia {_brl(m['ads']['teto_dia'])})")
    for c in foto.get("campanhas", []):
        L.append(f"  - {c['nome']}: {c['estado']}, orçamento {_brl(c['orcamento'])}/dia")
    for asin, r in (foto.get("produtos") or {}).items():
        L += ["", f"{r.get('nome')} ({asin})"]
        mg = r.get("margem_preco_atual") or {}
        v, t, a = r.get("vendas") or {}, r.get("trafego_30d") or {}, r.get("ads_desde_inicio") or {}
        L.append(f"  Preço {_brl(mg.get('preco'))} | MC antes do Ads {_brl(mg.get('mc'))} ({_pct(mg.get('mc_pct'))})")
        L.append(f"  Vendas 7d {v.get('vendido_7d')} | 30d {v.get('vendido_30d')} | sessões 30d {t.get('sessoes')} | conversão {_pct(t.get('conversao_pct'))}")
        L.append(f"  Estoque FBA {(r.get('estoque_fba') or {}).get('disponivel')} | em trânsito {(r.get('estoque_fba') or {}).get('em_transito')}")
        if a:
            L.append(f"  Ads desde o início: {a.get('impressoes')} impressões, {a.get('cliques')} cliques, gasto {_brl(a.get('gasto'))}, "
                     f"vendas atribuídas {_brl(a.get('vendas_ads'))}")
        for s, an in (r.get("anuncios") or {}).items():
            if an.get("problemas"):
                L.append(f"  Problemas {s}: " + "; ".join(f"{x['code']} {x['severity']}" for x in an["problemas"]))
    hoje_txt = dia.isoformat()
    try:
        with open(escrita.DIARIO, encoding="utf-8") as f:
            acoes = [json.loads(x) for x in f if x.strip()]
    except OSError:
        acoes = []
    do_dia = [x for x in acoes if str(x.get("quando", "")).startswith(hoje_txt)]
    L += ["", f"Ações de hoje ({len(do_dia)}):"]
    for x in do_dia:
        situ = "RECUSADA: " + x["recusado"] if x.get("recusado") else ("ERRO: " + x["erro"] if x.get("erro") else
                                                                         ("feita" if x.get("confirmado") else "simulada"))
        L.append(f"  - {x.get('tipo')} {x.get('alvo') or ''}: {situ}")
        if x.get("motivo"):
            L.append(f"      por quê: {x['motivo']}")
        if x.get("esperado"):
            L.append(f"      espero: {x['esperado']}")
    if foto.get("erros"):
        L += ["", "Falhas na coleta: " + "; ".join(foto["erros"])]
    L += ["", "Para parar tudo: criar o arquivo agentes\\vitrine\\PARAR (o vigia pausa as campanhas na próxima passada)."]
    return "\n".join(L)


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "coletar":
        from agentes.vitrine import coleta
        foto = coleta.coletar(com_ads="--sem-ads" not in argv, com_trafego="--sem-trafego" not in argv)
        print(json.dumps(foto, ensure_ascii=False, indent=1)[:6000])
        return 0
    if cmd == "acao":
        with open(argv[1], encoding="utf-8-sig") as f:
            acao = json.load(f)
        try:
            r = escrita.executar(acao, confirmar="--confirmar" in argv)
        except cerca.Recusa as e:
            print(f"RECUSADA pela cerca: {e}")
            return 3
        print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
        return 0
    if cmd == "vigia":
        return cmd_vigia()
    if cmd == "ativo":            # 0 = hoje está dentro da janela do mandato
        m = carregar_mandato()
        dentro = date.fromisoformat(m["inicio"]) <= hoje() <= date.fromisoformat(m["fim"])
        print("dentro da janela" if dentro else f"fora da janela ({m['inicio']} a {m['fim']})")
        return 0 if dentro else 1
    if cmd == "pausar":
        print(escrita.pausar_tudo(argv[1] if len(argv) > 1 else "pausa manual"))
        return 0
    if cmd == "margem":
        cmd_margem()
        return 0
    if cmd == "pedir-envio":
        return cmd_pedir_envio(argv[1], int(argv[2]), argv[3])
    if cmd == "relatorio":
        texto = montar_relatorio()
        print(texto)
        if "--enviar" in argv:
            m = carregar_mandato()
            if enviar_email(m["destinatarios"], f"Vitrine — resumo de {hoje():%d/%m}", texto):
                # marca usada pelo rodar_vitrine.ps1: o dia só conta como feito se o resumo saiu
                with open(os.path.join(os.path.dirname(escrita.DIARIO), "resumo_enviado.txt"), "w", encoding="utf-8") as f:
                    f.write(hoje().isoformat())
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
