# -*- coding: utf-8 -*-
"""
第五轮第三次优化回归测试：Inquiry Intent → Sales Action（避免过度追问）

覆盖：
  1. 八类意图识别单元测试（含「catalog+best price 混合 = RFQ」优先级口径）
  2. Test 05（Anna / Nordic Retail Solutions / Catalog 请求）完整管线
  3. Catalog 防幻觉：虚构「attached catalog」必须被校验拦截
  4. 禁止把型号/图片/数量设为提供 Catalog 的前置条件
  5. RFQ 意图行为不变（Test 04 场景回归）
  6. Sample 请求意图：0-1 问、不追问数量

运行：python test_inquiry_intent_r5p3.py
"""

import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from main import build_engine, analyze
from agent.reply_strategy import (
    classify_inquiry_intent, build_question_plan, validate_reply_strategy,
    count_questions, INTENT_CATALOG, INTENT_PRODUCT, INTENT_RFQ,
    INTENT_SOURCING, INTENT_SAMPLE, INTENT_SPEC, INTENT_PRICE,
    INTENT_GENERAL,
)

_PASS = _FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        print(f"  ❌ {name}" + (f"　→ {detail}" if detail else ""))


# =============== 1. 八类意图识别（单元） ===============
INTENT_CASES = [
    ("Anna·纯 Catalog 请求",
     "We are interested in your products and would like to receive your latest "
     "product catalog and specifications. After reviewing the available products, "
     "we can discuss quantities and pricing.",
     INTENT_CATALOG),
    ("Tim·纯 Catalog 请求",
     "Please send us your latest catalog. We are evaluating Chinese manufacturers "
     "for our project.",
     INTENT_CATALOG),
    ("混合·best price + catalog = RFQ",
     "Please send us your best price, MOQ and fastest delivery time. "
     "Please also send your latest catalog and product specifications.",
     INTENT_RFQ),
    ("Test04·best price for 2,000 pcs",
     "We are looking for 2,000 pcs. First order may start with 500 pcs to test "
     "market, but total order could reach 5,000 pcs. Please send best price for "
     "2,000 pcs and MOQ. Samples first.",
     INTENT_RFQ),
    ("样品请求",
     "Could you send us a sample of your swim cap before we place an order?",
     INTENT_SAMPLE),
    ("规格咨询",
     "What material is the cap made of? Could you provide the specification "
     "of this model?",
     INTENT_SPEC),
    ("价格咨询",
     "What is your unit price for silicone swim caps?",
     INTENT_PRICE),
    ("采购寻源",
     "We are a distributor sourcing swim gear suppliers in Europe "
     "for our retail chain.",
     INTENT_SOURCING),
    ("普通询盘",
     "Hello, we heard about your company from a trade fair. Just reaching out.",
     INTENT_GENERAL),
]


def test_intent_unit():
    print("\n【单元 · 八类 Inquiry Intent 识别】")
    for name, text, expected in INTENT_CASES:
        got = classify_inquiry_intent(text)["intent"]
        check(f"{name} → {expected}", got == expected, f"实际 {got}")


# =============== 2. Test 05（Anna）完整管线 ===============
TEST_05_ANNA = """Dear Sir / Madam,

We are interested in your products and would like to receive your latest product catalog and specifications to understand your product range.

After reviewing the available products, we can discuss quantities and pricing.

Best regards,
Anna
Purchasing
Nordic Retail Solutions
Sweden"""


def run_test_05(engine):
    print("\n【Test 05 · Anna / Nordic Retail Solutions / Catalog 请求】")
    rep = analyze(TEST_05_ANNA, *engine)
    info = rep.get("info") or {}
    plan = rep.get("reply_plan") or {}
    ins = rep.get("insight") or {}
    draft = rep.get("draft") or ""
    low = draft.lower()

    print("-" * 68)
    print(draft)
    print("-" * 68)

    # 意图与销售阶段
    check("Intent = CATALOG_REQUEST", plan.get("intent") == INTENT_CATALOG,
          f"实际 {plan.get('intent')}")
    check("Sales Stage = Product Discovery",
          plan.get("sales_stage") == "Product Discovery",
          f"实际 {plan.get('sales_stage')}")
    check("Immediate Action 指向 Catalog 响应",
          "catalog" in (plan.get("immediate_action") or "").lower(),
          plan.get("immediate_action"))

    # 信息状态：客户已提供的信息不虚构，未提供的不臆造
    check("Product = UNKNOWN（客户未给产品方向）",
          not (plan.get("customer_product") or info.get("product_query")))
    check("Quantity = UNKNOWN（客户未给数量）", not info.get("quantity"))
    check("Target Price = UNKNOWN", not info.get("target_price"))

    # 报价准备度：NOT_READY（不得是可报价）
    qr = (ins.get("quotation_readiness") or {})
    check("Quote Readiness = NOT_READY（insufficient_info）",
          qr.get("quotation_readiness_status") == "insufficient_info",
          f"实际 {qr.get('quotation_readiness_status')}")

    # P0 = 客户当前明确请求（Catalog）；P1 = Product Category；P2 = Quantity
    p0_fields = [q["field"] for q in plan.get("p0", [])]
    p1_fields = [q["field"] for q in plan.get("p1", [])]
    p2_fields = [q["field"] for q in plan.get("p2", [])]
    check("P0 = Catalog Request（客户明确请求优先响应）",
          "explicit_request_response" in p0_fields, f"实际 P0：{p0_fields}")
    check("P1 含 Product Category", "product_category" in p1_fields,
          f"实际 P1：{p1_fields}")
    check("P2 含 Quantity（数量此时不升 P0）",
          "quantity" in p2_fields and "quantity" not in p0_fields,
          f"实际 P2：{p2_fields}")

    # 问题预算：0-1 个
    n_plan = len(plan.get("selected") or [])
    n_draft = count_questions(draft)
    check("首轮问题 ≤ 1（计划）", n_plan <= 1, f"计划 {n_plan} 个")
    check("首轮问题 ≤ 1（草稿）", n_draft <= 1, f"草稿 {n_draft} 个")
    check("首轮只问产品类别方向",
          all(q.get("field") == "product_category"
              for q in (plan.get("selected") or [])),
          str([q.get('field') for q in plan.get('selected') or []]))

    # 禁止的错误追问
    check("禁止：要求先给 reference model/photo/design",
          not re.search(r"reference\s+model|photo|design", low),
          "草稿出现了型号/图片/设计索取")
    check("禁止：要求先确认数量才给 catalog",
          not re.search(r"quantity[^.?\n]{0,60}before", low)
          and not re.search(r"how\s+many|what\s+quantity", low),
          "草稿出现了数量前置条件")
    check("禁止：虚构 Catalog 已发送（attached / sent）",
          not re.search(r"\b(?:attached|attaching|enclosed|have\s+sent|sent\s+you)\b"
                        r"[^.\n]{0,40}\b(?:catalog|catalogue|specifications?)\b", low),
          "草稿虚构了附件")

    # 必须响应客户请求 + 校验通过
    check("草稿回应了 Catalog 请求", "catalog" in low)
    check("Subject 点题 Product Catalog", "catalog" in low.split("\n")[0].lower(),
          low.split("\n")[0])
    issues = rep.get("draft_issues") or []
    check("草稿自检通过（全部校验）", not issues, "；".join(issues))


# =============== 3. Catalog 防幻觉 / 前置条件拦截（校验器单元） ===============
def test_catalog_validation():
    print("\n【校验器 · Catalog 防幻觉 + 前置条件拦截】")
    info = {"company": "Nordic Retail Solutions", "contact_name": "Anna"}
    plan = build_question_plan(TEST_05_ANNA, info, None, {}, matches=[])

    fake_attached = ("Subject: Re: Product Catalog\n\nDear Anna,\n\n"
                     "Please find attached our latest catalog.\n\nBest regards,\nSales Team")
    issues = validate_reply_strategy(fake_attached, TEST_05_ANNA, info, None,
                                     {}, plan=plan)
    check("虚构「attached catalog」被拦截",
          any("attached" in i or "已发送" in i or "虚构" in i for i in issues),
          str(issues))

    fake_precondition = ("Subject: Re: Product Catalog\n\nDear Anna,\n\n"
                         "Thank you for your inquiry.\n\n"
                         "Please send us a reference model and quantity before we "
                         "can provide the catalog.\n\nBest regards,\nSales Team")
    issues2 = validate_reply_strategy(fake_precondition, TEST_05_ANNA, info, None,
                                      {}, plan=plan)
    check("「型号/数量前置条件」被拦截",
          any("前置条件" in i or "不需要的信息" in i for i in issues2),
          str(issues2))

    fake_price = ("Subject: Re: Product Catalog\n\nDear Anna,\n\n"
                  "Our price is USD 0.85 per piece, MOQ 1,000 pcs.\n\n"
                  "Best regards,\nSales Team")
    issues3 = validate_reply_strategy(fake_price, TEST_05_ANNA, info, None,
                                      {}, plan=plan)
    check("无产品时虚构价格被拦截",
          any("价格" in i for i in issues3), str(issues3))


# =============== 4. RFQ / Sample 意图回归 ===============
T04_RFQ = """We are looking for 2,000 pcs. Our customer needs within 2 weeks.
First order may start with 500 pcs to test market,
but if price competitive total order could reach 5,000 pcs.
Please send best price for 2,000 pcs and MOQ.
Samples first.
Confirm if can meet delivery."""


def test_rfq_unchanged(engine):
    print("\n【回归 · RFQ 意图行为不变（Test 04 场景）】")
    rep = analyze(T04_RFQ, *engine)
    plan = rep.get("reply_plan") or {}
    check("Intent = RFQ", plan.get("intent") == INTENT_RFQ,
          f"实际 {plan.get('intent')}")
    check("报价数量 2,000 被记录", plan.get("quotation_quantity") == 2000,
          str(plan.get("quotation_quantity")))
    qty_q = [q for q in (plan.get("selected") or [])
             if "quantity" in (q.get("field") or "")]
    check("无数量追问（语义已区分）", not qty_q, str(qty_q))
    issues = rep.get("draft_issues") or []
    check("草稿自检通过", not issues, "；".join(issues))


def test_sample_intent(engine):
    print("\n【Sample 请求意图 · 0-1 问、不追问数量】")
    text = ("Could you send us a sample of your swim cap before we place an order? "
            "We are a swim school in Germany.")
    plan = build_question_plan(text, {}, None, {}, matches=[])
    check("Intent = SAMPLE_REQUEST", plan.get("intent") == INTENT_SAMPLE,
          f"实际 {plan.get('intent')}")
    check("P0 = 客户明确请求（样品）",
          any(q["field"] == "explicit_request_response" for q in plan.get("p0", [])))
    check("数量不进 P0（样品阶段不需要）",
          not any(q["field"].startswith("quantity") for q in plan.get("p0", [])),
          str([q['field'] for q in plan.get('p0', [])]))
    check("首轮 ≤ 1 问", len(plan.get("selected") or []) <= 1,
          str(len(plan.get("selected") or [])))


def main():
    mode = "llm" if "--mode" in sys.argv and "llm" in sys.argv else "rule"
    print(f"测试模式：{mode}")
    engine = build_engine(mode)

    test_intent_unit()
    run_test_05(engine)
    test_catalog_validation()
    test_rfq_unchanged(engine)
    test_sample_intent(engine)

    print("\n" + "=" * 68)
    print(f"结果：{_PASS} 通过 / {_FAIL} 失败")
    print("=" * 68)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
