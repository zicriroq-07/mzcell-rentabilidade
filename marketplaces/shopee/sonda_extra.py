"""
marketplaces/shopee/sonda_extra.py — Sonda ampla de endpoints da Shopee (SOMENTE LEITURA)
MZ App

Uso: python -m marketplaces.shopee.sonda_extra [loja] [--salvar]

Tenta o maior número possível de caminhos de leitura (FBS, SBS/Full, merchant, first mile, AMS, chat, vídeo, loja,
produto, pedido, pagamento, logística, Ads...). Cada candidato é classificado:
  OK          respondeu com dados
  EXISTE      o caminho existe, mas a Shopee pediu outro parâmetro (error_param): vale ajustar
  NEGADO      existe, mas o app/loja não tem permissão
  NAO_EXISTE  caminho inexistente (error_not_found) — provavelmente nome errado
  ERRO        outra falha (mensagem guardada)
Trava de segurança: só aceita caminhos cuja ação comece com get_/query_/list_/search_/check_ e recusa verbos de escrita.
Grava em sonda_shopee_extra.json (gitignored) só status, nomes de campos e contagens.
"""

import json
import os
import sys
import time

from marketplaces.shopee import api, lojas
from marketplaces.shopee.lojas import ErroShopee
from marketplaces.shopee.sonda import _chaves, _tamanho

SAIDA = os.path.join(lojas.BASE_DIR, "sonda_shopee_extra.json")
LEITURA = ("get_", "query_", "list_", "search_", "check_", "download_")
ESCRITA = ("update", "create", "delete", "add_", "init", "cancel", "confirm", "ship", "set_", "edit", "remove", "unlist",
           "boost", "publish", "upload", "accept", "reject", "dispute", "split", "join", "reply", "send", "generate", "apply")
DIA = 86400
AG = int(time.time())
PAG = {"page_size": 10, "page_no": 1, "offset": 0, "cursor": "", "pageno": 1, "limit": 10}
JAN = {"time_from": AG - 14 * DIA, "time_to": AG, "create_time_from": AG - 14 * DIA, "create_time_to": AG,
       "update_time_from": AG - 14 * DIA, "update_time_to": AG}
DT = {"start_date": time.strftime("%d-%m-%Y", time.localtime(AG - 7 * DIA)), "end_date": time.strftime("%d-%m-%Y", time.localtime(AG - DIA))}


def _cand():
    c = []
    def add(grupo, nomes, modulo, extra=None):
        for n in nomes:
            c.append((grupo, f"/api/v2/{modulo}/{n}", extra or {}))
    # ── FBS (Brasil)
    add("fbs", ["query_br_shop_block_status", "query_br_shop_enrollment_status", "query_br_shop_invoice_error", "query_br_sku_block_status",
                "query_br_shop_enroll_status", "query_br_sku_block", "get_br_shop_block_status", "get_br_shop_enrollment_status",
                "get_br_shop_invoice_error", "get_br_sku_block_status", "get_shop_block_status", "get_fbs_shop_info", "get_stock_list",
                "get_inbound_list", "get_current_inventory", "get_stock_movement", "get_stock_aging", "query_br_inventory", "query_br_stock",
                "query_br_inbound", "query_br_warehouse", "query_br_sku_stock", "query_br_sku_inventory", "query_br_shipment_list",
                "get_br_inventory", "get_br_stock", "get_invoice_list", "query_br_invoice_list", "query_br_sku_list", "query_br_replenishment",
                "get_replenishment_suggestion", "query_br_sku_status"], "fbs", {**PAG, "sku_id_list": "", "item_id_list": ""})
    # ── SBS (Full): inventário, movimentação, envelhecimento
    add("sbs", ["get_current_inventory", "get_stock_movement", "get_stock_aging", "get_expiry_report", "get_bound_whs_info", "get_inbound_list",
                "get_inbound_detail", "get_warehouse_list", "get_sbs_shop_info", "query_current_inventory", "query_stock_movement",
                "query_stock_aging", "get_inventory", "get_stock_list", "get_shop_warehouse", "get_replenishment_list", "get_booking_list",
                "get_sbs_stock", "get_current_inventory_by_sku", "get_sku_inventory"], "sbs", {**PAG, **JAN})
    add("sbs_br", ["get_current_inventory", "get_stock_movement", "get_stock_aging", "get_inbound_list", "get_bound_whs_info"], "seller_sbs", {**PAG, **JAN})
    add("fulfillment", ["get_current_inventory", "get_stock_movement", "get_stock_aging", "get_inventory_list", "get_inbound_list"], "fulfillment", {**PAG, **JAN})
    # ── merchant / loja
    add("merchant", ["get_merchant_info", "get_shop_list_by_merchant", "get_merchant_warehouse_list", "get_merchant_warehouse_location_list",
                     "get_item_list", "get_item_base_info", "get_warehouse_list"], "merchant", PAG)
    add("loja", ["get_shop_info", "get_profile", "get_warehouse_detail", "get_shop_notification", "get_authed_shop", "get_shop_flash_sale_list",
                 "get_bank_account_list", "get_shop_tax", "get_shop_auth_info", "get_shop_dsr", "get_operation_hour", "get_account_status"], "shop", PAG)
    add("public", ["get_shops_by_partner", "get_merchants_by_partner", "get_token_by_resend_code", "get_shopee_ip_ranges", "get_access_token_expire"], "public", PAG)
    # ── first mile
    add("first_mile", ["get_channel_list", "get_unbind_order_list", "get_detail", "get_tracking_number_list", "get_waybill", "get_tracking_number",
                       "get_shipping_parameter", "get_transit_warehouse_list", "get_address_list"], "first_mile", {**PAG, "ship_mode": 1, "first_mile_tracking_number": ""})
    # ── produto
    add("produto", ["get_item_list", "get_item_base_info", "get_item_extra_info", "get_model_list", "get_comment", "get_category", "get_attributes",
                    "get_brand_list", "get_item_limit", "get_boosted_list", "get_item_promotion", "get_item_violation_info", "get_item_content_diagnosis",
                    "get_item_list_by_content_diagnosis", "search_item", "get_variations", "get_dts_limit", "get_recommend_attribute", "get_size_chart_list",
                    "get_size_chart_detail", "get_product_stock", "get_stock_info", "get_item_stock", "get_comment_summary", "get_price_info",
                    "get_item_price", "get_publishable_status", "get_recommended_category", "get_unbind_content_list", "get_main_item_list",
                    "get_mart_item_mapping", "get_kit_list", "get_item_sales", "get_item_statistic", "get_exclusive_price_list"], "product", {**PAG, "item_status": "NORMAL", "item_id": 0})
    # ── pedido
    add("pedido", ["get_order_list", "get_order_detail", "get_shipment_list", "get_buyer_invoice_info", "get_package_detail", "get_booking_list",
                   "get_booking_detail", "get_warehouse_filter_config", "get_pending_buyer_invoice_order_list", "get_order_status", "get_cancel_reason_list",
                   "get_forder_waybill", "get_tracking_number", "get_fbs_order_list", "get_fulfillment_order_list", "get_invoice_list", "get_order_list_by_status"], "order", {**PAG, **JAN, "time_range_field": "create_time", "order_status": "READY_TO_SHIP"})
    # ── pagamento
    add("financeiro", ["get_escrow_detail", "get_escrow_list", "get_payout_info", "get_payout_detail", "get_billing_transaction_info", "get_wallet_transaction_list",
                       "get_income_overview", "get_income_report", "get_shop_installment_status", "get_payment_method_list", "get_escrow_detail_batch",
                       "get_settlement_list", "get_bank_account", "get_withdrawal_list", "get_cash_flow", "get_fee_list", "get_fbs_fee", "get_fee_detail",
                       "get_invoice_list", "get_billing_info", "get_statement_list"], "payment", {**PAG, **JAN, "release_time_from": AG - 14 * DIA, "release_time_to": AG,
                       "payout_time_from": AG - 14 * DIA, "payout_time_to": AG, "order_sn": ""})
    # ── devoluções
    add("devolucao", ["get_return_list", "get_return_detail", "get_available_solution", "get_shipping_carrier", "get_return_dispute_reason",
                      "get_alternative_refund_amount", "get_reverse_logistics_list", "get_return_summary"], "returns", {**PAG, **JAN, "page_no": 0})
    # ── logística
    add("logistica", ["get_channel_list", "get_address_list", "get_shipping_parameter", "get_tracking_number", "get_shipping_document_parameter",
                      "get_shipping_document_result", "get_operating_hours", "get_mass_shipping_parameter", "get_mass_tracking_number", "get_tracking_info",
                      "get_channel_detail", "get_warehouse_list", "get_ship_service_list", "get_pickup_address_list", "get_shipping_fee"], "logistics", PAG)
    # ── Ads
    add("ads", ["get_total_balance", "get_shop_toggle_info", "get_recommended_item_list", "get_all_cpc_ads_daily_performance",
                "get_all_cpc_ads_hourly_performance", "get_product_level_campaign_id_list", "get_product_level_campaign_setting_info",
                "get_product_campaign_daily_performance", "get_product_campaign_hourly_performance", "get_gms_campaign_performance",
                "get_gms_item_performance", "get_ads_facade_shop_performance", "get_shop_level_campaign_list", "get_keyword_list",
                "get_recommended_keyword_list", "get_creative_list", "get_ad_group_list", "get_campaign_list", "get_item_level_performance",
                "get_product_level_item_performance", "get_hourly_performance", "get_ads_balance", "get_shop_level_cpc_performance",
                "get_auto_product_ads_performance", "get_manual_product_ads_performance", "get_search_term_performance", "get_ads_account_info",
                "get_report", "get_product_level_campaign_item_list", "get_campaign_item_list", "get_gms_campaign_item_list", "get_campaign_setting_info"], "ads",
        {**PAG, **DT, "performance_date": DT["end_date"], "ad_type": "all", "limit": 20, "campaign_id": 0, "campaign_id_list": "", "info_type_list": "1,2,3,4"})
    # ── promoções e marketing
    add("promocao", ["get_discount_list", "get_discount", "get_voucher_list", "get_voucher", "get_add_on_deal_list", "get_add_on_deal", "get_add_on_deal_main_item",
                     "get_add_on_deal_sub_item", "get_bundle_deal_list", "get_bundle_deal", "get_bundle_deal_item", "get_follow_prize_list", "get_follow_prize_detail",
                     "get_shop_flash_sale_list", "get_time_slot_id", "get_item_criteria", "get_shop_category_list", "get_top_picks_list", "get_campaign_list",
                     "get_lucky_draw_list", "get_coin_cashback_list", "get_shopee_campaign_list", "get_campaign_item_list", "get_recommended_items"],
        "discount", {**PAG, "discount_status": "ongoing", "status": "ongoing", "promotion_status": "ongoing", "voucher_id": 0, "discount_id": 0, "timeslot_id": 0, "type": 0})
    for mod, nomes in (("voucher", ["get_voucher_list", "get_voucher"]), ("add_on_deal", ["get_add_on_deal_list"]), ("bundle_deal", ["get_bundle_deal_list"]),
                       ("follow_prize", ["get_follow_prize_list"]), ("shop_flash_sale", ["get_shop_flash_sale_list", "get_time_slot_id"]),
                       ("shop_category", ["get_shop_category_list"]), ("top_picks", ["get_top_picks_list"]), ("marketing", ["get_campaign_list", "get_promotion_list"]),
                       ("campaign", ["get_campaign_list", "get_campaign_detail"]), ("livestream", ["get_session_list", "get_session_detail"]),
                       ("video", ["get_video_list", "get_video_detail", "get_video_upload_result"]), ("media", ["get_video_upload_result"]),
                       ("media_space", ["get_video_upload_result"]), ("ams", ["get_open_campaign_added_product", "get_shop_performance", "get_conversion_report",
                       "get_campaign_key_metrics_performance", "get_ams_shop_info", "get_validation_report", "get_performance_data_update_time", "get_open_campaign_batch_task_result",
                       "get_targeted_campaign_list", "get_open_campaign_not_added_product", "get_shop_suspension_status"]),
                       ("sellerchat", ["get_message", "get_conversation_list", "get_one_conversation", "get_unread_conversation_count"]),
                       ("sbs_ads", ["get_info"]), ("fbs_shop", ["get_info"])):
        add("outros", nomes, mod, {**PAG, **DT, "status": "ongoing", "voucher_id": 0, "timeslot_id": 0, "type": 0})
    # ── conta
    add("conta", ["get_shop_performance", "shop_penalty", "get_metric_source_detail", "get_late_orders", "get_listing_with_issues", "get_penalty_point_history",
                  "get_punishment_history", "get_shop_health", "get_violation_list", "get_ongoing_punishment"], "account_health", {**PAG, "metric_id": 1, "type": 1})
    return c


def _tipo(msg):
    m = msg.lower()
    if "not_found" in m or "invalid_path" in m:
        return "NAO_EXISTE"
    if any(s in m for s in ("permission", "no_permission", "api_suspended", "not_in_whitelist", "whitelist", "scope", "api_not_support", "not support",
                            "no access", "access denied", "forbidden", "unauthorized")):
        return "NEGADO"
    if "param" in m or "required" in m or "invalid" in m or "wrong" in m:
        return "EXISTE"
    return "ERRO"


def main(argv):
    nome = next((a for a in argv if not a.startswith("--")), "techseller")
    sid = lojas.shop_id_da(nome)
    cands, vistos, res = _cand(), set(), []
    print(f"Sonda ampla Shopee — loja {nome} — {len(cands)} candidatos — SOMENTE LEITURA\n")
    for grupo, caminho, params in cands:
        if caminho in vistos:
            continue
        vistos.add(caminho)
        acao = caminho.rsplit("/", 1)[-1]
        if not acao.startswith(LEITURA) or any(v in acao for v in ESCRITA):
            continue                       # trava: nada que escreva
        time.sleep(0.3)
        try:
            d = api.chamar(caminho, sid, params)
            r = d.get("response", d) if isinstance(d, dict) else {}
            reg = {"grupo": grupo, "caminho": caminho, "status": "OK", "itens": _tamanho(r) if isinstance(r, dict) else None, "campos": _chaves(r)[:40]}
        except ErroShopee as e:
            msg = str(e)
            reg = {"grupo": grupo, "caminho": caminho, "status": _tipo(msg), "erro": msg[:200]}
        res.append(reg)
        if reg["status"] != "NAO_EXISTE":
            print(f"[{reg['status']:10s}] {caminho}" + (f"  itens={reg.get('itens')}" if reg["status"] == "OK" else f"  {reg.get('erro', '')[:110]}"))
    por = {}
    for r in res:
        por[r["status"]] = por.get(r["status"], 0) + 1
    print("\nResumo:", por, "| (NAO_EXISTE omitidos da lista)")
    if "--salvar" in argv:
        with open(SAIDA, "w", encoding="utf-8") as f:
            json.dump({"geradoEm": time.strftime("%Y-%m-%d %H:%M"), "loja": nome, "resultados": res}, f, ensure_ascii=False, indent=1)
        print("Gravado em", SAIDA)


if __name__ == "__main__":
    main(sys.argv[1:])
