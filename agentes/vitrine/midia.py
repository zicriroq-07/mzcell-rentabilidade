"""
agentes/vitrine/midia.py — Imagens do Vitrine: referências, geração (Codex) e publicação em URL pública
MZ App

  baixar_origem(ASIN)                 baixa as imagens atuais do catálogo para dados/midia/<ASIN>/origem (referência)
  gerar(ASIN, nome, prompt, capa)     gera UMA imagem com `codex exec` (geração de imagem do Codex) usando as referências;
                                      capa=True força o fundo quase-branco para branco puro (#FFFFFF), exigência da Amazon
  publicar(ASIN, arquivo)             converte para JPG e publica no ramo midia-amazon do GitHub (o mesmo da torneira);
                                      devolve a URL raw, que o Listings aceita em media_location

Restrições: só ASINs do mandato; só arquivos dentro de dados/midia/<ASIN>/. O Codex roda com sandbox workspace-write
preso à pasta do ASIN. A publicação usa um worktree separado em dados/midia_wt (nunca mexe no main).
"""

import os
import re
import shutil
import subprocess
import time

import httpx
from PIL import Image

from agentes.vitrine import cerca
from agentes.vitrine.escrita import PASTA, carregar_mandato
from marketplaces.amazon import api

RAIZ = os.path.dirname(os.path.dirname(PASTA))
MIDIA = os.path.join(PASTA, "dados", "midia")
WT = os.path.join(PASTA, "dados", "midia_wt")
RAMO = "midia-amazon"
RAW = "https://raw.githubusercontent.com/zicriroq-07/mzcell-rentabilidade/" + RAMO + "/"


def _pasta(asin: str) -> str:
    cerca.produto_do_asin(carregar_mandato(), asin)          # recusa ASIN fora do mandato
    p = os.path.join(MIDIA, asin)
    os.makedirs(os.path.join(p, "origem"), exist_ok=True)
    return p


def _dentro(asin: str, arquivo: str) -> str:
    base = os.path.realpath(_pasta(asin))
    alvo = os.path.realpath(arquivo if os.path.isabs(arquivo) else os.path.join(base, arquivo))
    if not alvo.startswith(base + os.sep) or not os.path.isfile(alvo):
        raise cerca.Recusa(f"arquivo precisa existir dentro de dados/midia/{asin}/")
    return alvo


def baixar_origem(asin: str) -> list:
    pasta = os.path.join(_pasta(asin), "origem")
    st, cat = api.get(carregar_mandato()["conta"], f"/catalog/2022-04-01/items/{asin}",
                      marketplaceIds=api.MARKETPLACE_BR, includedData="images")
    if st != 200:
        raise api.falha(st, cat)
    links, vistos = [], set()
    for g in cat.get("images", []):
        for i in sorted(g.get("images", []), key=lambda x: -x.get("height", 0)):
            if i.get("variant") not in vistos:
                vistos.add(i.get("variant"))
                links.append((i.get("variant"), i["link"]))
    salvos = []
    for variante, url in links:
        destino = os.path.join(pasta, f"atual_{variante}.jpg")
        r = httpx.get(url, timeout=60)
        if r.status_code == 200:
            with open(destino, "wb") as f:
                f.write(r.content)
            salvos.append(destino)
    return salvos


def _branquear(caminho: str, limiar: int = 246):
    im = Image.open(caminho).convert("RGB")
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b = px[x, y]
            if r >= limiar and g >= limiar and b >= limiar:
                px[x, y] = (255, 255, 255)
    im.save(caminho)


def gerar(asin: str, nome: str, prompt: str, capa: bool = False, referencias: list = None) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", nome):
        raise cerca.Recusa("nome da imagem: só letras, números, _ e - (até 40)")
    pasta = _pasta(asin)
    refs = [_dentro(asin, r) for r in (referencias or [])]
    if not refs:
        origem = os.path.join(pasta, "origem")
        if not os.listdir(origem):
            baixar_origem(asin)
        refs = sorted(os.path.join(origem, f) for f in os.listdir(origem))[:4]
    instrucao = (prompt.strip() + "\n\nGere UMA imagem com a ferramenta de geração de imagem (quadrada, a maior disponível). "
                 "As imagens anexas são o PRODUTO REAL: mantenha fidelidade total (forma, cor, proporção, detalhes). "
                 "No fim, responda só com o caminho completo do arquivo gerado.")
    cmd = ["codex", "exec", "-C", pasta, "--skip-git-repo-check", "--sandbox", "workspace-write", "--image", ",".join(refs)]
    r = subprocess.run(cmd, input=instrucao, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900, shell=(os.name == "nt"))
    saida = (r.stdout or "") + "\n" + (r.stderr or "")
    caminhos = [c for c in re.findall(r"[A-Za-z]:\\[^\r\n\"']+?\.png", saida) if os.path.isfile(c)]
    if not caminhos:
        raise RuntimeError("o Codex não devolveu imagem: " + saida.strip()[-400:])
    destino = os.path.join(pasta, nome + ".png")
    shutil.copyfile(caminhos[-1], destino)
    if capa:
        _branquear(destino)
    return destino


def _git(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd or WT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} falhou: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def publicar(asin: str, arquivo: str) -> str:
    origem = _dentro(asin, arquivo)
    if os.path.getsize(origem) > 10 * 1024 * 1024:
        raise cerca.Recusa("imagem acima de 10 MB")
    if not os.path.isdir(os.path.join(WT, ".git")) and not os.path.isfile(os.path.join(WT, ".git")):
        _git("fetch", "origin", RAMO, cwd=RAIZ)
        _git("worktree", "add", "--detach", WT, f"origin/{RAMO}", cwd=RAIZ)
    _git("fetch", "origin", RAMO)
    _git("checkout", "--detach", "--force", f"origin/{RAMO}")
    rel = f"amazon02/vitrine/{asin}/{os.path.splitext(os.path.basename(origem))[0]}_{time.strftime('%Y%m%d%H%M')}.jpg"
    destino = os.path.join(WT, *rel.split("/"))
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    Image.open(origem).convert("RGB").save(destino, "JPEG", quality=92)
    _git("add", rel)
    _git("-c", "user.name=Vitrine (MZ App)", "-c", "user.email=vitrine@mzcell.local", "commit", "-q", "-m", f"midia: Vitrine {asin}")
    _git("push", "-q", "origin", f"HEAD:{RAMO}")
    url = RAW + rel
    for _ in range(10):
        if httpx.get(url, timeout=30).status_code == 200:
            return url
        time.sleep(6)
    raise RuntimeError(f"publicado, mas a URL ainda não responde: {url}")
