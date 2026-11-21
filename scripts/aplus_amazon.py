"""A+ Content da Amazon (SP-API aplus-content 2020-11-01) a partir de um arquivo de configuracao.

Uso (venv do projeto):
    python scripts/aplus_amazon.py config.json            # sobe as imagens e VALIDA (nao cria nada)
    python scripts/aplus_amazon.py config.json --criar    # valida, cria, associa ao ASIN e envia para revisao
    python scripts/aplus_amazon.py config.json --atualizar CHAVE   # valida e troca o conteudo de um A+ existente

O --criar ESCREVE na conta Amazon: so rodar com pedido/confirmacao explicita do Zicri, produto a produto.
Sem --criar o script sobe as imagens para a biblioteca de midia da conta (exigencia da API para validar)
e chama a validacao, que nao publica nada.

Formato do config.json (ver docs/PLAYBOOK_AMAZON_APLUS.md):
{
  "conta": "amazon02", "asin": "B0DZN43HGG", "nome": "Torneira ... A2",
  "pasta": "C:/pasta/com/as/imagens",
  "modulos": [
    {"tipo": "header", "img": "b1.png", "alt": "...", "titulo": "...", "texto": "..."},
    {"tipo": "overlay", "img": "b2.png", "alt": "...", "titulo": "...", "texto": "...", "cor": "DARK"},
    {"tipo": "quatro", "itens": [{"img": "b4.png", "alt": "...", "titulo": "...", "texto": "..."}, x4]}
  ]
}
Tamanhos exatos (a imagem e redimensionada se vier diferente): header 970x600, overlay 970x300, quatro 220x220.
"""
import base64
import hashlib
import io
import json
import os
import sys
from urllib.parse import parse_qsl, urlparse

import httpx
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from marketplaces.amazon import api  # noqa: E402

TAM = {"header": (970, 600), "overlay": (970, 300), "quatro": (220, 220)}
MODULO = {"header": "STANDARD_HEADER_IMAGE_TEXT", "overlay": "STANDARD_IMAGE_TEXT_OVERLAY",
          "quatro": "STANDARD_FOUR_IMAGE_TEXT"}


def _jpeg(caminho, w, h) -> bytes:
    im = Image.open(caminho).convert("RGB")
    if im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    dados = buf.getvalue()
    if len(dados) > 3 * 1024 * 1024:
        raise SystemExit(f"{caminho}: acima de 3 MB depois de converter")
    return dados


def _subir(conta, dados: bytes, nome: str) -> str:
    """Pede o destino e envia por POST multipart (formulario S3). Nao mandar campo Content-Type extra."""
    md5 = base64.b64encode(hashlib.md5(dados).digest()).decode()
    st, b = api._enviar(conta, "POST", "/uploads/2020-11-01/uploadDestinations/aplus/2020-11-01/contentDocuments",
                        params={"marketplaceIds": api.MARKETPLACE_BR, "contentMD5": md5, "contentType": "image/jpeg"})
    if st not in (200, 201):
        raise SystemExit(f"destino de upload falhou ({st}): {json.dumps(b)[:300]}")
    p = b["payload"]
    u = urlparse(p["url"])
    r = httpx.post(f"{u.scheme}://{u.netloc}/", data=dict(parse_qsl(u.query)),
                   files={"file": (nome, dados, "image/jpeg")}, timeout=60)
    if r.status_code >= 300:
        raise SystemExit(f"upload S3 falhou ({r.status_code}): {r.text[:300]}")
    return p["uploadDestinationId"]


def _t(v):
    return {"value": v, "decoratorSet": []}


def _bloco(conta, pasta, it, tam):
    w, h = tam
    uid = _subir(conta, _jpeg(os.path.join(pasta, it["img"]), w, h), it["img"])
    img = {"uploadDestinationId": uid, "altText": it.get("alt", ""),
           "imageCropSpecification": {"size": {"width": {"value": w, "units": "pixels"},
                                               "height": {"value": h, "units": "pixels"}},
                                      "offset": {"x": {"value": 0, "units": "pixels"},
                                                 "y": {"value": 0, "units": "pixels"}}}}
    b = {"image": img}
    if it.get("titulo"):
        b["headline"] = _t(it["titulo"])
    if it.get("texto"):
        b["body"] = {"textList": [_t(it["texto"])]}
    return b


def montar(cfg) -> dict:
    conta, pasta = cfg["conta"], cfg["pasta"]
    lista = []
    for m in cfg["modulos"]:
        tipo = m["tipo"]
        if tipo == "header":
            lista.append({"contentModuleType": MODULO[tipo],
                          "standardHeaderImageText": {"block": _bloco(conta, pasta, m, TAM[tipo])}})
        elif tipo == "overlay":
            lista.append({"contentModuleType": MODULO[tipo],
                          "standardImageTextOverlay": {"overlayColorType": m.get("cor", "DARK"),
                                                       "block": _bloco(conta, pasta, m, TAM[tipo])}})
        elif tipo == "quatro":
            if len(m["itens"]) != 4:
                raise SystemExit("modulo 'quatro' precisa de exatamente 4 itens")
            lista.append({"contentModuleType": MODULO[tipo], "standardFourImageText": {
                f"block{i + 1}": _bloco(conta, pasta, it, TAM[tipo]) for i, it in enumerate(m["itens"])}})
        else:
            raise SystemExit(f"tipo de modulo desconhecido: {tipo}")
    return {"name": cfg["nome"], "contentType": "EBC", "locale": "pt-BR", "contentModuleList": lista}


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cfg = json.load(open(argv[0], encoding="utf-8"))
    conta, asin, M = cfg["conta"], cfg["asin"], api.MARKETPLACE_BR
    doc = montar(cfg)
    st, b = api._enviar(conta, "POST", "/aplus/2020-11-01/contentAsinValidations",
                        params={"marketplaceId": M, "asinSet": asin}, corpo={"contentDocument": doc})
    print("validacao:", st, json.dumps(b, ensure_ascii=False)[:800])
    if st != 200 or b.get("errors"):
        return 2
    if "--atualizar" in argv:
        # Troca o conteudo de um A+ ja existente (mesma chave) e reenvia para revisao. ESCREVE na conta.
        k = argv[argv.index("--atualizar") + 1]
        st, b = api._enviar(conta, "POST", f"/aplus/2020-11-01/contentDocuments/{k}", params={"marketplaceId": M},
                            corpo={"contentDocument": doc})
        print("atualizar:", st, json.dumps(b, ensure_ascii=False)[:400])
        if st not in (200, 201):
            return 3
        st, b = api._enviar(conta, "POST", f"/aplus/2020-11-01/contentDocuments/{k}/approvalSubmissions",
                            params={"marketplaceId": M})
        print("revisao:", st, json.dumps(b)[:300], "| chave:", k)
        return 0
    if "--criar" not in argv:
        print("Validado. Nada foi criado (rode de novo com --criar ou --atualizar CHAVE, com confirmacao do Zicri).")
        return 0
    st, b = api._enviar(conta, "POST", "/aplus/2020-11-01/contentDocuments", params={"marketplaceId": M},
                        corpo={"contentDocument": doc})
    print("criar:", st, json.dumps(b, ensure_ascii=False)[:400])
    if st not in (200, 201):
        return 3
    k = b["contentReferenceKey"]
    st, b = api._enviar(conta, "POST", f"/aplus/2020-11-01/contentDocuments/{k}/asins",
                        params={"marketplaceId": M}, corpo={"asinSet": [asin]})
    print("associar:", st, json.dumps(b)[:300])
    st, b = api._enviar(conta, "POST", f"/aplus/2020-11-01/contentDocuments/{k}/approvalSubmissions",
                        params={"marketplaceId": M})
    print("revisao:", st, json.dumps(b)[:300], "| chave:", k)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
