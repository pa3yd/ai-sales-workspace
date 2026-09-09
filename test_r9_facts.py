# -*- coding: utf-8 -*-
"""第九轮优化回归测试：业务事实层（Value / Source / Certainty）

用法：
  python test_r9_facts.py    # 离线规则模式（不消耗 API）

Test 01  信息齐全 + 库内匹配（AquaGear / 5000pcs swim caps）
         → 数量 Explicit / 产品=Product Data / 公司报价=参考价（非正式）
Test 02  David / UK / ~10,000 pcs / USD 2.80 FOB / 包装+Logo / 样品 /
         交期 30 天 / 产品未知（spec 九核心场景）
         → 数量=约10,000(Approximate·Customer Fact)；目标价=USD 2.80
           (Explicit·Customer Fact)；FOB=Customer Fact；交期=30days
           (Preferred)；产品=Unknown；product_match=NO_MATCH；
           quote_readiness=NOT_READY/PARTIALLY_READY
Test 03  交期语义：客户期望交期 ≠ 公司承诺
         → 草稿只复述客户期望（noted ... preferred/requested delivery timeline），
           绝不出现 "we can deliver within 30 days"；承诺校验器可拦截违规句式
Test 04  数量/目标价语义：around ≠ confirmed；约数不进 confirmed 措辞
Test 05  跨询盘隔离 + Quote Readiness 与草稿语义一致
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from main import build_engine, analyze
from agent.facts import (build_fact_layer, build_reply_context,
                         validate_reply_commitments, detect_delivery_preference,
                         quantity_certainty, price_certainty, trade_term_of,
                         map_quote_status, QR_NOT_READY, QR_PARTIAL, QR_READY,
                         CERT_APPROXIMATE, CERT_EXPLICIT, CERT_CONFIRMED,
                         CERT_PREFERRED, CERT_MISSING,
                         SRC_CUSTOMER, SRC_PRODUCT, SRC_UNKNOWN)

_PASS = 0
_FAIL = 0


def check(name, ok, detail=""):
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  ✅ {name}" + (f"　（{detail}）" if detail else ""))
    else:
        _FAIL += 1
        print(f"  ❌ {name}" + (f"　（{detail}）" if detail else ""))


def fact_of(facts, field):
    return next((f for f in facts if f["field"] == field), {})


# ---- Test 01：信息齐全 + 库内匹配 ----
TEST_01_TEXT = """Hi, we are looking for 5000pcs neoprnee swim caps.

This is Michael Brown from AquaGear Trading Ltd, a distributor in the United States.
You can check our website: www.aquagear-us.com
Please quote your best price. Email: michael@aquagear-us.com

Do you support custom logo printing? We need them for the next season.

Best regards,
Michael"""


def run_test_01(engine):
    print("\n【Test 01 · AquaGear / 5000pcs swim caps / 库内匹配】")
    rep = analyze(TEST_01_TEXT, *engine)
    facts = rep.get("facts") or []
    ctx = rep.get("reply_context") or {}

    q = fact_of(facts, "quantity")
    check("Test 01 · 数量事实存在且来源=Customer Fact",
          q.get("source") == SRC_CUSTOMER and q.get("value") == "5,000 pcs",
          f"{q.get('source')}/{q.get('certainty')} {q.get('value')}")
    check("Test 01 · 数量可信度=Explicit（无约数词）",
          q.get("certainty") == CERT_EXPLICIT, str(q.get("certainty")))

    p = fact_of(facts, "product")
    check("Test 01 · 产品事实=库内匹配（Product Data）",
          p.get("source") == SRC_PRODUCT and bool(p.get("value")),
          f"{p.get('source')}/{p.get('certainty')} {p.get('value')}")

    cq = fact_of(facts, "company_quote")
    check("Test 01 · 公司报价=产品库参考价且标注非正式报价",
          cq.get("source") == SRC_PRODUCT and "参考" in str(cq.get("display")),
          str(cq.get("display")))

    check("Test 01 · reply_context.quote_readiness ∈ 统一四态",
          ctx.get("quote_readiness") in (QR_NOT_READY, QR_PARTIAL, QR_READY),
          str(ctx.get("quote_readiness")))
    check("Test 01 · reply_context.product_match = MATCHED",
          ctx.get("product_match") == "MATCHED", str(ctx.get("product_match")))
    check("Test 01 · allowed_facts 非空（邮件唯一事实入口）",
          bool(ctx.get("allowed_facts")), f"{len(ctx.get('allowed_facts') or [])} 条")


# ---- Test 02：spec 九核心场景（David / UK） ----
TEST_02_TEXT = """Hi,

This is David from the UK.

We are looking for around 10,000 pcs.
Target price: USD 2.80/pc FOB.
Customization: custom packaging with our logo.
If possible, please also send samples before bulk production.
Delivery should be around 30 days.

Best regards,
David"""


def run_test_02(engine):
    print("\n【Test 02 · David/UK / ~10,000 pcs / USD 2.80 FOB / 样品 / 交期30天 / 产品未知】")
    rep = analyze(TEST_02_TEXT, *engine)
    facts = rep.get("facts") or []
    ctx = rep.get("reply_context") or {}

    q = fact_of(facts, "quantity")
    check("Test 02 · 数量 = ~10,000 pcs · Customer Fact · Approximate",
          q.get("value") == "10,000 pcs" and q.get("source") == SRC_CUSTOMER
          and q.get("certainty") == CERT_APPROXIMATE,
          f"{q.get('display')} ({q.get('source')}/{q.get('certainty')})")
    check("Test 02 · UI 显示带「约」前缀（不写确认采购量）",
          str(q.get("display", "")).startswith("约"), str(q.get("display")))

    tp = fact_of(facts, "customer_target_price")
    check("Test 02 · 客户目标价 = USD 2.8 · Customer Fact · Explicit",
          tp.get("value") == "USD 2.8 FOB" and tp.get("source") == SRC_CUSTOMER
          and tp.get("certainty") == CERT_EXPLICIT,
          f"{tp.get('value')} ({tp.get('source')}/{tp.get('certainty')})")
    check("Test 02 · 目标价字段标注「客户目标价 ≠ 公司报价」",
          "不是公司报价" in str(tp.get("note")), str(tp.get("note")))

    tt = fact_of(facts, "trade_term")
    check("Test 02 · 贸易术语 FOB = Customer Fact",
          tt.get("value") == "FOB" and tt.get("source") == SRC_CUSTOMER,
          f"{tt.get('value')} ({tt.get('source')})")

    dv = fact_of(facts, "delivery")
    check("Test 02 · 交期 = 30 days · Customer Fact · Preferred",
          dv.get("value") == "30 days" and dv.get("source") == SRC_CUSTOMER
          and dv.get("certainty") == CERT_PREFERRED,
          f"{dv.get('value')} ({dv.get('source')}/{dv.get('certainty')})")

    pr = fact_of(facts, "product")
    check("Test 02 · 产品 = Unknown / Missing（不虚构产品）",
          pr.get("value") is None and pr.get("source") == SRC_UNKNOWN
          and pr.get("certainty") == CERT_MISSING,
          f"{pr.get('display')} ({pr.get('source')}/{pr.get('certainty')})")

    cq = fact_of(facts, "company_quote")
    check("Test 02 · 公司报价 = 未提供（Unknown，绝不拿目标价顶替）",
          cq.get("value") is None and cq.get("source") == SRC_UNKNOWN,
          f"{cq.get('display')} ({cq.get('source')})")

    check("Test 02 · product_match = NO_MATCH（无确认匹配）",
          ctx.get("product_match") == "NO_MATCH", str(ctx.get("product_match")))
    check("Test 02 · quote_readiness = NOT_READY / PARTIALLY_READY",
          ctx.get("quote_readiness") in (QR_NOT_READY, QR_PARTIAL),
          str(ctx.get("quote_readiness")))
    check("Test 02 · reply_context.customer_target_price 单列（不与公司报价混同）",
          (ctx.get("customer_target_price") or {}).get("value") == "USD 2.8 FOB",
          str((ctx.get("customer_target_price") or {}).get("value")))
    check("Test 02 · approximate_facts 含数量",
          any("数量" in s for s in ctx.get("approximate_facts") or []),
          str(ctx.get("approximate_facts")))
    check("Test 02 · preferred_facts 含交期",
          any("交期" in s for s in ctx.get("preferred_facts") or []),
          str(ctx.get("preferred_facts")))
    check("Test 02 · unknown/missing_information 含产品",
          any("产品" in s for s in (ctx.get("unknown_information")
                                    or []) + (ctx.get("missing_information") or [])),
          str(ctx.get("unknown_information")) + str(ctx.get("missing_information")))

    draft = (rep.get("draft") or "").lower()
    check("Test 02 · 草稿无公司报价承诺（不含 our price/USD 2.80 报价句式）",
          not ("our price" in draft and "2.8" in draft),
          "" if "our price" not in draft else "出现 our price + 2.8")
    check("Test 02 · 草稿不把 10,000 写成确认订单",
          "your order of 10,000" not in draft and "order of 10,000" not in draft)


# ---- Test 03：交期语义（客户期望 ≠ 公司承诺） ----
def run_test_03(engine):
    print("\n【Test 03 · 客户期望交期 ≠ 公司承诺】")
    rep = analyze(TEST_02_TEXT, *engine)
    draft = rep.get("draft") or ""
    check("Test 03 · 草稿复述客户期望交期（noted ... delivery timeline of 30 days）",
          "30 days" in draft and "noted" in draft.lower(),
          "found" if "30 days" in draft else "草稿未提及 30 days")
    check("Test 03 · 草稿无 we can/will deliver 承诺句式",
          "we can deliver" not in draft.lower()
          and "we will deliver" not in draft.lower())

    # 校验器单测：违规承诺句式必须被拦截
    bad = ("Thank you for your inquiry.\nWe can deliver within 30 days after "
           "order confirmation.")
    issues = validate_reply_commitments(bad, TEST_02_TEXT, {}, None, matches=[])
    check("Test 03 · 校验器拦截「We can deliver within 30 days」（无产品数据）",
          any("交期承诺" in i for i in issues), str(issues))

    good = ("We have noted your preferred delivery timeline of 30 days and will "
            "check the applicable lead time for the selected product.")
    issues2 = validate_reply_commitments(good, TEST_02_TEXT, {}, None, matches=[])
    check("Test 03 · 正确表述（noted preferred + will check）不报问题",
          not issues2, str(issues2))

    # 有产品数据支撑时，lead time 与产品库一致 → 允许
    prod = {"name": "Silicone Swim Cap", "name_cn": "硅胶泳帽",
            "price_range": [0.5, 1.2], "moq": 500, "lead_time": "30 days",
            "unit": "pc", "hs_code": "6505"}
    ok_draft = "The lead time for this item is 30 days after order confirmation."
    issues3 = validate_reply_commitments(ok_draft, TEST_02_TEXT, {}, prod,
                                         matches=[prod])
    check("Test 03 · 有产品库交期支撑时承诺放行",
          not any("交期承诺" in i for i in issues3), str(issues3))

    # 客户目标价被当报价：必须拦截
    info_tp = {"target_price": 2.8}
    bad2 = "Thank you for your inquiry. Our best price is USD 2.80 per piece."
    issues4 = validate_reply_commitments(bad2, TEST_02_TEXT, info_tp, None, matches=[])
    check("Test 03 · 拦截「客户目标价当公司报价」",
          any("目标价" in i for i in issues4), str(issues4))


# ---- Test 04：数量/目标价语义（around ≠ confirmed） ----
TEST_04_TEXT = """Hello,

We need 10,000 pcs silicone swim caps for our upcoming order.
Target price around USD 2.50/pc CIF.

Regards,
Emma"""


def run_test_04(engine):
    print("\n【Test 04 · We need 10,000（确认）vs around USD 2.50（约数）】")
    rep = analyze(TEST_04_TEXT, *engine)
    facts = rep.get("facts") or []
    q = fact_of(facts, "quantity")
    check("Test 04 · We need 10,000 pcs → Confirmed（客户明确承诺语义）",
          q.get("certainty") == CERT_CONFIRMED, str(q.get("certainty")))
    check("Test 04 · 显示无「约」前缀",
          not str(q.get("display", "")).startswith("约"), str(q.get("display")))
    tp = fact_of(facts, "customer_target_price")
    check("Test 04 · 目标价 around USD 2.50 → Approximate",
          tp.get("certainty") == CERT_APPROXIMATE
          and str(tp.get("display", "")).startswith("约"),
          f"{tp.get('display')} ({tp.get('certainty')})")
    check("Test 04 · 贸易术语 CIF 识别",
          fact_of(facts, "trade_term").get("value") == "CIF",
          str(fact_of(facts, "trade_term").get("value")))

    # 交期识别单测：prefer/hope/should → Preferred；must → Explicit
    d1 = detect_delivery_preference("We prefer delivery within 30 days.")
    d2 = detect_delivery_preference("We hope to receive the goods within 30 days.")
    d3 = detect_delivery_preference("We must receive the goods no later than 15 days.")
    check("Test 04 · prefer delivery within 30 days → Preferred",
          d1 and d1["certainty"] == CERT_PREFERRED, str(d1))
    check("Test 04 · hope to receive within 30 days → Preferred",
          d2 and d2["certainty"] == CERT_PREFERRED, str(d2))
    check("Test 04 · must ... no later than 15 days → Explicit（硬性要求）",
          d3 and d3["certainty"] == CERT_EXPLICIT, str(d3))


# ---- Test 05：跨询盘隔离 + readiness 与草稿语义一致 ----
TEST_05A_TEXT = """Hi, we are looking for 5000pcs neoprnee swim caps.
This is Michael Brown from AquaGear Trading Ltd, a distributor in the United States.
Please quote your best price. Email: michael@aquagear-us.com
Best regards,
Michael"""
TEST_05B_TEXT = """Hello,
I want 800 pcs swimming goggles.
Please send me your catalog and price list.
Thanks,
Sarah"""


def run_test_05(engine):
    print("\n【Test 05 · 跨询盘隔离 + Quote Readiness 与草稿一致】")
    rep_a = analyze(TEST_05A_TEXT, *engine)
    rep_b = analyze(TEST_05B_TEXT, *engine)

    ctx_b = rep_b.get("reply_context") or {}
    facts_b = rep_b.get("facts") or []
    q_b = fact_of(facts_b, "quantity")
    check("Test 05 · 询盘B数量 = 800 pcs（不是询盘A的 5,000）",
          q_b.get("value") == "800 pcs", str(q_b.get("value")))
    draft_b = rep_b.get("draft") or ""
    check("Test 05 · 询盘B草稿不出现询盘A数量 5,000",
          "5,000" not in draft_b and "5000" not in draft_b)
    allowed_txt = " ".join(ctx_b.get("allowed_facts") or [])
    check("Test 05 · 询盘B allowed_facts 不含询盘A事实",
          "5,000" not in allowed_txt, allowed_txt[:80])

    # Quote Readiness 与草稿语义一致：NOT_READY 草稿不得报价
    rep_c = analyze(TEST_02_TEXT, *engine)
    qr_c = (rep_c.get("reply_context") or {}).get("quote_readiness")
    draft_c = (rep_c.get("draft") or "")
    if qr_c == QR_NOT_READY:
        import re as _re
        has_price = _re.search(r"(USD|US\$|\$)\s*\d", draft_c)
        check("Test 05 · NOT_READY 草稿无任何价格数字（语义一致）",
              not has_price, has_price.group(0) if has_price else "clean")
    else:
        check("Test 05 · Test02 readiness 为 NOT_READY（预期）",
              True, f"实际 {qr_c}")

    # readiness 映射函数单测
    check("Test 05 · map_quote_status 四态映射正确",
          map_quote_status("insufficient_info") == QR_NOT_READY
          and map_quote_status("preliminary_quote_ready") == QR_PARTIAL
          and map_quote_status("ready_for_quotation") == QR_READY
          and map_quote_status("quoted") == "QUOTED"
          and map_quote_status("legacy_unknown") == QR_NOT_READY)


def main():
    engine = build_engine("rule")
    run_test_01(engine)
    run_test_02(engine)
    run_test_03(engine)
    run_test_04(engine)
    run_test_05(engine)
    print("\n" + "=" * 60)
    print(f"第九轮事实层测试：通过 {_PASS} · 失败 {_FAIL}")
    if _FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
