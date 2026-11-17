"""
Teste diário de vaga de Full (FBA) das árvores de Natal 210 cm — Amazon 01.

A Amazon recusa planos que passam do volume NON_SORTABLE (FBA_INB_0132). Este script tenta CRIAR o plano
(10 un de cada cor por padrão), lê as datas de coleta, CANCELA o plano em seguida (não segura espaço nem
agenda nada) e avisa no Slack quando a Amazon aceitar. Só entram datas com pelo menos 2 dias úteis de folga
depois do dia do teste (dia do agendamento). Agendar e emitir NF continuam por comando, com confirmação:
    envio_fba criar ... --confirmar  ->  agendar --data ...  ->  nf

Config opcional (gitignored): full_arvores_config.json  {"conta","itens":[{"sku","qtd"}],"caixa":{"dim","peso"},"dias_uteis_min"}
Estado (gitignored): full_arvores_estado.json (evita repetir o mesmo aviso). Log: full_arvores_agenda.log
Uso:  python full_arvores_agenda.py [--dry]      # --dry não envia Slack nem grava estado (ainda cria e cancela o plano)
"""
import argparse, json, os, sys, traceback
from datetime import date, datetime
from types import SimpleNamespace

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR); os.chdir(BASE_DIR)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from marketplaces.amazon import api, envio_fba as ef

CONFIG = os.path.join(BASE_DIR, "full_arvores_config.json")
ESTADO = os.path.join(BASE_DIR, "full_arvores_estado.json")
LOG = os.path.join(BASE_DIR, "full_arvores_agenda.log")
PADRAO = {"conta": "amazon01",
          "itens": [{"sku": "ARV1200G-DR-FBA", "qtd": 10}, {"sku": "ARV1200G-VM-FBA", "qtd": 10}],
          "caixa": {"dim": "100x30x20", "peso": 7},     # 1 árvore por caixa; medidas do cadastro da Amazon
          "dias_uteis_min": 2}


def log(msg):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(linha, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def _json(path, padrao):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def datas_validas(opcoes: list, hoje: date, dias_uteis: int) -> list:
    """Datas de coleta (parceira, dia útil) com pelo menos `dias_uteis` dias úteis depois de `hoje`."""
    minima = ef.data_minima_coleta(hoje, dias_uteis)
    return sorted({d for d, _ in ef.coletas_parceiras(opcoes) if d >= minima})


def montar_mensagem(cfg: dict, plano_itens: str, datas: list, destino: str, hoje: date) -> str:
    dias = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    lista = "\n".join(f"• {d:%d/%m/%Y} ({dias[d.weekday()]})" for d in datas)
    return (f"🎄 *Full das árvores 210 cm — a Amazon aceitou ({cfg['conta']})*\n"
            f"Teste de {hoje:%d/%m/%Y}: {plano_itens} · destino {destino}.\n"
            f"Datas de coleta possíveis (≥ {cfg['dias_uteis_min']} dias úteis depois de hoje):\n{lista}\n"
            f"Nada foi criado nem agendado (o plano de teste foi cancelado). Para enviar: "
            f"`envio_fba criar` com as mesmas quantidades, depois `agendar --data` e `nf`.")


def sondar(cfg: dict):
    """Cria o plano de teste e devolve (opções, destino, resumo). Cancela sempre que o plano chegou a existir."""
    caixa = cfg["caixa"]
    specs = [{"n": it["qtd"], "dim": ef.parse_dim(caixa["dim"]), "peso": float(caixa["peso"]), "itens": [(it["sku"], 1)]}
             for it in cfg["itens"]]
    tot = ef.totais_por_sku(specs)
    a = SimpleNamespace(conta=cfg["conta"], nome=None, pronto_em=None)
    skus, totais = list(tot.keys()), list(tot.values())
    estado = None
    try:
        estado, opcoes, _ = ef._criar(a, skus, totais, specs, salvar=False)
        return opcoes, estado["destino"], " + ".join(f"{q} {k}" for k, q in zip(skus, totais))
    finally:
        if estado is None:
            pass
        else:
            s, r = api._enviar(cfg["conta"], "PUT", f"{ef.BASE}/inboundPlans/{estado['plano']}/cancellation")
            try:
                ef._ok(s, r, "cancelInboundPlan"); ef._op(cfg["conta"], r, "cancelInboundPlan")
                log(f"plano de teste {estado['plano']} cancelado")
            except Exception as e:      # plano pendente é raro, mas precisa aparecer no log
                log(f"ATENÇÃO: não consegui cancelar o plano de teste {estado['plano']}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    dry = ap.parse_args().dry
    cfg = {**PADRAO, **_json(CONFIG, {})}
    hoje = date.today()
    est = _json(ESTADO, {})
    try:
        opcoes, destino, resumo = sondar(cfg)
    except Exception as e:
        msg = api.mascarar(str(e))
        if "FBA_INB_0132" in msg:
            log("Amazon ainda recusa (volume NON_SORTABLE excedido).")
        else:
            log(f"ERRO no teste: {msg[:300]}")
            log(traceback.format_exc(limit=3))
        if not dry:
            est.pop("avisado", None)         # voltou a recusar: o próximo aceite avisa de novo
            json.dump(est, open(ESTADO, "w", encoding="utf-8"))
        return 0 if "FBA_INB_0132" in msg else 1

    datas = datas_validas(opcoes, hoje, cfg["dias_uteis_min"])
    log(f"Amazon aceitou ({resumo}). Datas válidas: {', '.join(f'{d:%d/%m}' for d in datas) or 'nenhuma'}")
    if not datas:
        return 0
    chave = [d.isoformat() for d in datas]
    if est.get("avisado") == chave:
        log("mesmas datas já avisadas; sem novo Slack.")
        return 0
    texto = montar_mensagem(cfg, resumo, datas, destino, hoje)
    if dry:
        print("\n[--dry] mensagem que iria ao Slack:\n" + texto)
        return 0
    import buybox_monitor
    if buybox_monitor.enviar_slack(texto):
        est["avisado"] = chave
        json.dump(est, open(ESTADO, "w", encoding="utf-8"))
        return 0
    log("Slack não enviado (ver slack_config.json).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
