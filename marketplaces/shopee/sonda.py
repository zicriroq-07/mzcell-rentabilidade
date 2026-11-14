"""
marketplaces/shopee/sonda.py — Sonda de endpoints da Shopee (SOMENTE LEITURA, só GET)
MZ App

Uso: python -m marketplaces.shopee.sonda [loja] [--salvar]

Chama uma bateria de endpoints de leitura e diz quais o app tem permissão de usar, quais devolvem dados
e quais campos vêm. Encadeia: usa um item_id/order_sn real da loja nas chamadas seguintes.
Grava (--salvar) em sonda_shopee.json (gitignored) só status, nomes de campos e contagens; nenhum valor
de pedido, comprador, token ou chave.
"""

import json
import os
import sys
import time

from marketplaces.shopee import api, lojas
from marketplaces.shopee.lojas import ErroShopee

SAIDA = os.path.join(lojas.BASE_DIR, "sonda_shopee.json")
DIA = 86400


def _chaves(x, n=0):
    """Nomes de campos de uma resposta (até 2 níveis), sem valores."""
    if isinstance(x, dict):
        return sorted(f"{k}" + (f"{{{','.join(_chaves(v, n + 1))}}}" if n < 1 and isinstance(v, (dict, list)) and _chaves(v, n + 1) else "")
                      for k, v in x.items())
    if isinstance(x, list) and x:
        return _chaves(x[0], n)
    return []


def _tamanho(r):
    for k in ("item", "item_list", "order_list", "response_list", "campaign_list", "discount_list", "voucher_list",
              "return_list", "more", "list", "models", "model", "tier_variation", "transaction_list"):
        v = r.get(k)
        if isinstance(v, list):
            return len(v)
    return None


class Sonda:
    def __init__(self, shop_id):
        self.sid = shop_id
        self.res = []
        self.itens, self.pedidos = [], []

    def ir(self, grupo, caminho, params=None, nota=""):
        time.sleep(0.4)
        try:
            d = api.chamar(caminho, self.sid, params or {})
            r = d.get("response", d) if isinstance(d, dict) else {}
            reg = {"grupo": grupo, "caminho": caminho, "status": "OK", "itens": _tamanho(r) if isinstance(r, dict) else None,
                   "campos": _chaves(r)[:40], "aviso": d.get("warning") or "", "nota": nota}
        except ErroShopee as e:
            msg = str(e)
            tipo = "NEGADO" if any(s in msg.lower() for s in ("permission", "auth", "scope", "api_not_support", "no_permission")) \
                else "NAO_EXISTE" if "not_found" in msg.lower() or "invalid_path" in msg.lower() else "ERRO"
            reg = {"grupo": grupo, "caminho": caminho, "status": tipo, "erro": msg[:200], "nota": nota}
            d, r = {}, {}
        self.res.append(reg)
        marca = {"OK": "[OK  ]", "NEGADO": "[NEGA]", "NAO_EXISTE": "[N/E ]", "ERRO": "[ERRO]"}[reg["status"]]
        extra = f" itens={reg.get('itens')}" if reg["status"] == "OK" else f" {reg.get('erro', '')[:90]}"
        print(f"{marca} {caminho}{extra}")
        return r if isinstance(r, dict) else {}

    def rodar(self):
        agora = int(time.time())
        s = self.ir
        # ── loja
        s("loja", "/api/v2/shop/get_shop_info")
        s("loja", "/api/v2/shop/get_profile")
        s("loja", "/api/v2/shop/get_warehouse_detail")
        s("loja", "/api/v2/shop/get_shop_notification")
        s("loja", "/api/v2/shop/get_authed_shop")
        # ── produtos
        r = s("produto", "/api/v2/product/get_item_list", {"offset": 0, "page_size": 20, "item_status": "NORMAL"})
        self.itens = [i["item_id"] for i in r.get("item", [])][:5]
        if self.itens:
            ids = ",".join(map(str, self.itens))
            s("produto", "/api/v2/product/get_item_base_info", {"item_id_list": ids, "need_complaint_policy": "false"})
            s("produto", "/api/v2/product/get_item_extra_info", {"item_id_list": ids})
            s("produto", "/api/v2/product/get_model_list", {"item_id": self.itens[0]})
            s("produto", "/api/v2/product/get_comment", {"item_id": self.itens[0], "cursor": "", "page_size": 5})
            s("produto", "/api/v2/product/get_item_promotion", {"item_id_list": ids})
            s("produto", "/api/v2/product/get_item_violation_info", {"item_id": self.itens[0]})
        s("produto", "/api/v2/product/get_boosted_list")
        s("produto", "/api/v2/product/get_item_limit")
        s("produto", "/api/v2/product/get_category", {"language": "pt-BR"})
        s("produto", "/api/v2/product/search_item", {"offset": 0, "page_size": 5, "item_name": "a"})
        s("produto", "/api/v2/product/get_dts_limit", {"category_id": 100001})
        # ── pedidos
        r = s("pedido", "/api/v2/order/get_order_list", {"time_range_field": "create_time", "time_from": agora - 14 * DIA,
              "time_to": agora, "page_size": 20, "order_status": "COMPLETED"})
        self.pedidos = [o["order_sn"] for o in r.get("order_list", [])][:5]
        if not self.pedidos:
            r = s("pedido", "/api/v2/order/get_order_list", {"time_range_field": "create_time", "time_from": agora - 14 * DIA,
                  "time_to": agora, "page_size": 20}, "sem filtro de status")
            self.pedidos = [o["order_sn"] for o in r.get("order_list", [])][:5]
        if self.pedidos:
            sn = ",".join(self.pedidos)
            s("pedido", "/api/v2/order/get_order_detail", {"order_sn_list": sn, "response_optional_fields":
              "buyer_user_id,item_list,pay_time,total_amount,actual_shipping_fee,estimated_shipping_fee,package_list,"
              "payment_method,fulfillment_flag,cancel_by,cancel_reason,note"})
            s("pedido", "/api/v2/order/get_shipment_list", {"page_size": 10})
            s("financeiro", "/api/v2/payment/get_escrow_detail", {"order_sn": self.pedidos[0]})
            s("logistica", "/api/v2/logistics/get_tracking_number", {"order_sn": self.pedidos[0]})
            s("logistica", "/api/v2/logistics/get_shipping_parameter", {"order_sn": self.pedidos[0]})
            s("pedido", "/api/v2/order/get_buyer_invoice_info", {"queries": json.dumps([{"order_sn": self.pedidos[0]}])})
        s("pedido", "/api/v2/order/get_order_list", {"time_range_field": "update_time", "time_from": agora - 14 * DIA,
          "time_to": agora, "page_size": 5, "order_status": "READY_TO_SHIP"}, "a enviar")
        # ── financeiro
        s("financeiro", "/api/v2/payment/get_escrow_list", {"release_time_from": agora - 14 * DIA, "release_time_to": agora,
          "page_size": 10, "page_no": 1})
        s("financeiro", "/api/v2/payment/get_payout_info", {"payout_time_from": agora - 14 * DIA, "payout_time_to": agora,
          "page_size": 10, "page_no": 1})
        s("financeiro", "/api/v2/payment/get_billing_transaction_info", {"page_size": 10})
        s("financeiro", "/api/v2/payment/get_shop_installment_status")
        s("financeiro", "/api/v2/payment/get_income_overview", {"release_time_from": agora - 14 * DIA, "release_time_to": agora})
        s("financeiro", "/api/v2/payment/get_income_report", {"release_time_from": agora - 14 * DIA, "release_time_to": agora})
        # ── devoluções
        r = s("devolucao", "/api/v2/returns/get_return_list", {"page_no": 0, "page_size": 10,
              "create_time_from": agora - 14 * DIA, "create_time_to": agora})
        devs = [x.get("return_sn") for x in r.get("return", []) if x.get("return_sn")][:1]
        if devs:
            s("devolucao", "/api/v2/returns/get_return_detail", {"return_sn": devs[0]})
        # ── logística
        s("logistica", "/api/v2/logistics/get_channel_list")
        s("logistica", "/api/v2/logistics/get_address_list")
        # ── Ads
        s("ads", "/api/v2/ads/get_total_balance")
        s("ads", "/api/v2/ads/get_shop_toggle_info")
        s("ads", "/api/v2/ads/get_recommended_item_list")
        s("ads", "/api/v2/ads/get_all_cpc_ads_daily_performance", {"start_date": time.strftime("%d-%m-%Y", time.localtime(agora - 7 * DIA)),
          "end_date": time.strftime("%d-%m-%Y", time.localtime(agora - DIA))})
        s("ads", "/api/v2/ads/get_all_cpc_ads_hourly_performance", {"performance_date": time.strftime("%d-%m-%Y", time.localtime(agora - DIA))})
        r = s("ads", "/api/v2/ads/get_product_level_campaign_id_list", {"ad_type": "all", "offset": 0, "limit": 20})
        camp = [c.get("campaign_id") for c in r.get("campaign_list", []) if c.get("campaign_id")][:3]
        if camp:
            ids = ",".join(map(str, camp))
            s("ads", "/api/v2/ads/get_product_level_campaign_setting_info", {"info_type_list": "1,2,3,4", "campaign_id_list": ids})
            s("ads", "/api/v2/ads/get_product_campaign_daily_performance", {"start_date": time.strftime("%d-%m-%Y", time.localtime(agora - 7 * DIA)),
              "end_date": time.strftime("%d-%m-%Y", time.localtime(agora - DIA)), "campaign_id_list": ids})
        s("ads", "/api/v2/ads/get_gms_campaign_performance", {"start_date": time.strftime("%d-%m-%Y", time.localtime(agora - 7 * DIA)),
          "end_date": time.strftime("%d-%m-%Y", time.localtime(agora - DIA))})
        # ── promoções
        r = s("promocao", "/api/v2/discount/get_discount_list", {"discount_status": "ongoing", "page_no": 1, "page_size": 20})
        ds = [d.get("discount_id") for d in r.get("discount_list", []) if d.get("discount_id")][:1]
        s("promocao", "/api/v2/discount/get_discount_list", {"discount_status": "upcoming", "page_no": 1, "page_size": 20})
        if ds:
            s("promocao", "/api/v2/discount/get_discount", {"discount_id": ds[0], "page_no": 1, "page_size": 10})
        s("promocao", "/api/v2/voucher/get_voucher_list", {"page_no": 1, "page_size": 20, "status": "ongoing"})
        s("promocao", "/api/v2/add_on_deal/get_add_on_deal_list", {"promotion_status": "ongoing", "page_no": 1, "page_size": 20})
        s("promocao", "/api/v2/bundle_deal/get_bundle_deal_list", {"time_range_field": "create_time", "time_from": agora - 30 * DIA,
          "time_to": agora, "page_no": 1, "page_size": 20})
        s("promocao", "/api/v2/follow_prize/get_follow_prize_list", {"page_no": 1, "page_size": 20})
        s("promocao", "/api/v2/shop_flash_sale/get_shop_flash_sale_list", {"timeslot_id": 0, "pageno": 0, "limit": 20, "type": 0})
        s("promocao", "/api/v2/shop_flash_sale/get_time_slot_id", {"start_time": agora, "end_time": agora + 7 * DIA})
        s("promocao", "/api/v2/shop_category/get_shop_category_list", {"page_size": 20, "page_no": 1})
        s("promocao", "/api/v2/top_picks/get_top_picks_list")
        s("promocao", "/api/v2/marketing/get_campaign_list")  # chute: campanhas oficiais da Shopee
        # ── desempenho da conta
        s("conta", "/api/v2/account_health/get_shop_performance")
        s("conta", "/api/v2/account_health/shop_penalty")
        s("conta", "/api/v2/account_health/get_metric_source_detail", {"metric_id": 1, "page_no": 1, "page_size": 10})
        s("conta", "/api/v2/account_health/get_late_orders", {"page_no": 1, "page_size": 10})
        s("conta", "/api/v2/account_health/get_listing_with_issues", {"page_no": 1, "page_size": 10})
        # ── Full (FBS) e estoque Shopee — caminhos por tentativa; N/E = não existe nesta API
        s("full", "/api/v2/fbs/get_stock_list", {"page_no": 1, "page_size": 10})
        s("full", "/api/v2/fbs/get_inbound_list", {"page_no": 1, "page_size": 10})
        s("full", "/api/v2/first_mile/get_channel_list")
        s("full", "/api/v2/product/get_stock_detail", {"item_id": self.itens[0] if self.itens else 0})
        s("full", "/api/v2/fbs/get_shop_warehouse")
        s("full", "/api/v2/fbs/get_fbs_shop_info")
        return self.res


def resumo(res):
    por = {}
    for r in res:
        por.setdefault(r["status"], []).append(r["caminho"].replace("/api/v2/", ""))
    return por


def main(argv):
    nome = next((a for a in argv if not a.startswith("--")), "techseller")
    sid = lojas.shop_id_da(nome)
    print(f"Sonda Shopee — loja {nome} — ambiente {lojas.ambiente().lower()} — SOMENTE LEITURA\n")
    res = Sonda(sid).rodar()
    por = resumo(res)
    print("\nResumo:", {k: len(v) for k, v in por.items()})
    if "--salvar" in argv:
        with open(SAIDA, "w", encoding="utf-8") as f:
            json.dump({"geradoEm": time.strftime("%Y-%m-%d %H:%M"), "loja": nome, "resultados": res}, f, ensure_ascii=False, indent=1)
        print("Gravado em", SAIDA)


if __name__ == "__main__":
    main(sys.argv[1:])
