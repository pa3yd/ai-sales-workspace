# -*- coding: utf-8 -*-
"""
第五轮回归测试：首轮客户追问策略（Reply / Follow-up Question Strategy）

用法：
  python test_reply_strategy_r5.py            # 离线规则模式（不消耗 API）
  python test_reply_strategy_r5.py --mode llm # 真实 LLM 模式（消耗少量 API）

验收标准（第五轮优化十一 / 十二）：
  Test A  产品明确 + 数量明确          → 追问明显减少（期望 0-1 个）
  Test B  产品未知 + 数量明确          → 只问产品/型号等 P0（期望 1 个）
  Test C  产品未知 + 数量冲突          → 问产品 + 数量歧义（期望 2 个）
  Test D  客户提 CE/RoHS 但未说强制    → 认证不作为 Quote Blocker，首轮不问认证
  Test E  上一条 Water Bottle 3,000    → 下一条草稿绝不出现上条产品/客户/数量
  Test F  客户只要 Catalog             → 优先回应 Catalog，不做 7 项资格审查

硬门槛：任何场景首轮问题数 ≤ 3；普通询盘 1-2 个。
"""
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from main import build_engine, analyze
from agent.reply_strategy import (
    count_questions, build_question_plan, validate_reply_strategy,
    MAX_QUESTIONS,
)

# ---- Test A：产品明确 + 数量明确（产品库真实存在的 Neoprene Swim Cap） ----
TEST_A = """Hello,

We are looking for 3000 pcs neoprene swim caps for our swim school.
Please quote your best price and MOQ. We also need custom logo printing.

Best regards,
Anna
ABC Trading GmbH
Germany"""

# ---- Test B：产品未知 + 数量明确 ----
TEST_B = """Subject: Price for 1000 pcs

Hi,

We need 1000 pcs for our customer.

Please send me your best price, MOQ and delivery time.

Please send your catalog as well.

Regards,
Michael
Purchasing Manager
Global Source Ltd.
Germany"""

# ---- Test C：产品未知 + 数量冲突（原文出现两个不同数量） ----
TEST_C = """Hi,

We are interested in your products.

At the moment we are planning 3,000 pcs for the first shipment and maybe 5,000 pcs later.

Please advise your best price and delivery time.

Best regards,
Laura
Nordic Sports AB
Sweden"""

# ---- Test D：客户提 CE/RoHS，但没有明确是否强制 ----
TEST_D = """Hello,

We would like to order 2000 pcs silicone swim caps for the EU market.
Do you have CE and RoHS? We may need certificates for our retail chain.

Please send your quotation and catalog.

Best regards,
Pierre
France Retail Group
France"""

# ---- Test E：上一条询盘（A 组）产品明确：Water Bottle 3,000 pcs ----
PREV_A = """Hi,

We want to buy 3,000 pcs Stainless Steel Water Bottle 500ml.
Please quote FOB Ningbo price.

Best regards,
John
AquaTrade LLC"""

# ---- Test E：本条询盘产品未知、数量 800 pcs ----
TEST_E = """Hello,

We are interested in your products and would like to purchase around 800 pcs.

Please send us your best price, MOQ and fastest delivery time.

Kind regards,
Sophie
BlueLine Distribution
Netherlands"""

# ---- Test F：客户只要求 Catalog（本次优化的核心场景） ----
TEST_F = """Subject: Urgent RFQ - 3,000 pcs

Hi,

We are interested in your products and would like to purchase around 3,000 pcs.

Please send us your best price, MOQ and fastest delivery time.

Please also send your latest catalog and product specifications.

If your price is competitive, we can place an order immediately.

Best regards,
Michael
Purchasing Manager
Prime Sourcing Ltd.
United Kingdom"""

# ---- Test 01（第五轮补丁 03）：单 P0 阻塞（product_category）→ 仅问 1 问 ----
# 场景：Michael / Global Source Ltd / Germany / "around 3,000 pcs" /
#       requests best price + MOQ + fastest delivery + latest catalog + product details
#       产品方向完全未给出
# 期望行为（第五轮补丁 03）：
#   1) match_state = INSUFFICIENT_INFORMATION（连 Product Category 都没有）
#   2) hedge_present = True（"around" 触发 unconfirmed quantity）
#   3) 首轮只问 product_category（hard P0 单一时只 1 问）
#   4) 草稿必须回应 catalog 请求（但不承诺已发 attached / sent）
#   5) 不得把 "around 3,000" 写成 confirmed order of 3,000 pcs
#   6) 不出现上条产品（cross-inquiry pollution prevention）
#   7) Quote Readiness = insufficient_info（不可写 "your order of" / "confirmed quantity"）
TEST_01_TEXT = """Hi,

We are Global Source Ltd, a Germany-based sourcing company.

We are interested in your products and would like to purchase around 3,000 pcs.

Please send us your best price, MOQ and fastest delivery time.

Please also send your latest catalog and product details.

Best regards,
Michael
Purchasing Manager
Global Source Ltd.
Germany"""

# ---- Test 03（第五轮补丁 03）：多重 hedge 数量 + product unknown + Catalog ----
# 场景：3 档 hedge 数量（approximate 2,000 / trial 500 / potential annual 5,000）
#       + 索取 Catalog/Specs/Price/MOQ/Lead Time + Custom Packaging 未定
# 期望行为：
#   1) match_state = INSUFFICIENT_INFORMATION（连产品方向都没有）
#   2) hedge_present = True（3 档都 partially_confirmed）
#   3) 首轮只 1 问（product_category），不附加 quantity_confirm / quantity_conflict
TEST_03_TEXT = """Hello,

We are evaluating Chinese manufacturers for our retail chain.

Approximate initial quantity: approximately 2,000 pcs
Trial order: 500 pcs
Potential annual volume: 5,000 pcs

Packaging: We will finalize later (customized).

Requested:
- Catalog
- Specifications
- FOB Price
- MOQ
- Lead Time

Please advise on what you can supply.

Thanks,
Linda
Pacific Rim Imports
Canada"""

# ---- Test 04（第五轮补丁 03）：产品已匹配 + 数量 vagueness + CE/RoHS interested ----
# 场景：1,500 pcs neoprene swim caps + CE/RoHS "may need"
# 期望行为：
#   1) match_state = MATCHED（swim cap 在产品库 → 可直接报价）
#   2) CE/RoHS with "may" → interested（不是 required → 不当作 Quote Blocker）
#   3) 1,500 是 confirmed 数字（无 hedge 词）→ 数量 confirmed
#   4) hard P0=[] → selected 0-1 个
#   5) 草稿正确展示产品事实（含 USD 价格区间）
#   6) 不得把 interested 当 mandatory（草稿不出现 "must have CE"）
TEST_04_TEXT = """Hi,

We would like to order 1,500 pcs neoprene swim caps for the EU market.

Do you have CE and RoHS? We may need certificates for our retail chain.

Please send quotation with MOQ and lead time.

Thanks,
Karl
EU Sports Group
Germany"""

# ---- Test 05（第五轮补丁 03）：单 Catalog 请求，产品完全未给 ----
# 场景：客户只说要 catalog，没说任何产品和数量
# 期望行为：
#   1) match_state = INSUFFICIENT
#   2) hard_p0 = product_category
#   3) selected ≤ 2（含 catalog 请求 = 1 个 product_category）
#   4) 草稿必须回应 catalog（但不承诺已发 attached / sent）
TEST_05_TEXT = """Subject: Catalog request

Hi,

Please send us your latest catalog. We are evaluating Chinese manufacturers for our project.

Best,
Tim"""

# ---- Test 06（第五轮补丁 03）：产品已匹配 + 明确数量 + 目标价 ----
# 场景：10,000 pcs silicone swim caps + target price USD 2.80（与产品库区间比较）
# 期望行为：
#   1) match_state = MATCHED
#   2) 10,000 confirmed 数字
#   3) target_price = USD 2.80（与产品库价格区间比较 → 给出风险提示但不擅自改价）
#   4) hard_p0=[] → selected 0-1 个
#   5) 草稿不能虚构降价承诺
TEST_06_TEXT = """Hi,

We need 10,000 pcs silicone swim caps, target price USD 2.80 per piece, FOB Ningbo.

Please quote best price, MOQ and lead time.

Regards,
Mia
Sunrise Beach Co.
USA"""

# ---- Test 07（第五轮补丁 03）：产品已匹配 + 明确数量 + 明确交期 ----
# 场景：8,000 pcs silicone swim caps + preferred delivery within 4 weeks
# 期望行为：
#   1) match_state = MATCHED 或 PARTIAL_MATCH
#   2) 8,000 + 4 weeks 都明确
#   3) 0-1 问题
#   4) 草稿正确回应 4 周交期（条件式，不承诺具体交付日）
TEST_07_TEXT = """Hi,

We want 8,000 pcs silicone swim caps within 4 weeks. Please quote FOB price and MOQ.

Best,
James
Ocean Sports Ltd.
Australia"""

# ---- Test 02（第五轮补丁）：产品库无匹配时，禁止"问哪个型号/品类" ----
# 场景：客户主动描述了产品方向（Stainless Steel Water Bottle 500ml/750ml）+ 大概数量 3,000 pcs，
#       还索取了 catalog/specs/price/MOQ/lead time。但产品库没有 water bottle。
# 期望行为（第五轮补丁核心）：
#   1) match_state = NO_MATCH（产品库检索无对应品类）
#   2) 不强制索取 model/reference（应只问 reference/photo/design，最多 1-2 个）
#   3) 不得出现"which product or product category"这种无方向问题
#   4) 必须回应 catalog 请求（客户明确索取）
#   5) 不编造 water bottle 的价格/MOQ/lead time
TEST_02_TEXT = """Hello,

We are GreenPeak Outdoors, a US-based outdoor retailer.

We are interested in your Stainless Steel Water Bottle (500ml or 750ml). Our initial order
quantity would be approximately 3,000 pcs.

Please send us your catalog, full product specifications, price, MOQ and lead time for
this item.

Thanks,
James Wilson
GreenPeak Outdoors
USA"""

# ---- Test 08（第五轮补丁 02）：复杂询盘——hedge 数量 + Product Category 优先 ----
# 场景：客户连 Product Category 都没给 + 多种 hedge 数量（approximately / trial /
#       mentioned / annual volume）+ 索取 Samples + Catalog + Specs + FOB Price + MOQ + Lead Time
# 期望行为：
#   1) match_state = INSUFFICIENT_INFORMATION（连 Product Category / Type 都没有）
#   2) 首轮 P0 只问 Product Category / Product Type，**不得**问 model
#   3) hedge 数量全部跳过 → 不进 conflict / quantity_confirm
#   4) 草稿不得把 "approximately 2,000" / "Trial 500" / "3,000" / "10,000" 当成 confirmed order
#   5) 草稿不得虚构 Catalog 已发送 / 样品已准备
#   6) CE/RoHS interested → 不得当作 mandatory
#   7) Quote Readiness = insufficient_info（不得是 READY_FOR_QUOTATION）
#   8) 首轮问题数 ≤ 2（绝对 ≤ 3）
TEST_08_TEXT = """Hi,

We are a new company looking to source from China.

Initial Quantity: approximately 2,000 pcs
Trial Order: 500 pcs
Customer mentioned: 3,000 pcs
Potential Annual Volume: 10,000 pcs

Delivery: Preferred 3 weeks, Acceptable 4-5 weeks
Certification: CE/RoHS interested, mandatory status unknown
Packaging: Customized, design not ready

Requested:
- Samples
- Catalog
- Specifications
- FOB Price
- MOQ
- Lead Time

Please advise.

Thanks,
Anna"""

RESULTS = []


def check(name, cond, detail=""):
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f"　[{detail}]" if detail and not cond else ""))
    RESULTS.append(bool(cond))


def run_case(engine, label, text, expect_max=None, expect_min=None,
             forbidden=(), must_contain=(), previous=None, prev_report=None):
    print(f"\n{'=' * 68}\n【{label}】\n{'=' * 68}")
    rep = analyze(text, *engine, previous=previous)
    draft = rep["draft"] or ""
    plan = rep.get("reply_plan") or {}
    n = count_questions(draft)

    print("-" * 68)
    print(draft)
    print("-" * 68)
    print(f"  ▸ 计划提问 {len(plan.get('selected', []))} 个 / 实际提问 {n} 个"
          f"　P0：{'、'.join(q['field'] for q in plan.get('p0', [])) or '无'}"
          f"　P1：{'、'.join(q['field'] for q in plan.get('p1', [])) or '无'}"
          f"　P2：{'、'.join(q['field'] for q in plan.get('p2', [])) or '无'}")
    print(f"  ▸ 客户明确需求：{'、'.join(plan.get('explicit_requests') or []) or '无'}")

    # 硬门槛：绝不超过 3 个问题
    check(f"首轮问题数 ≤ {MAX_QUESTIONS}（实际 {n} 个）", n <= MAX_QUESTIONS,
          f"实际 {n} 个")
    if expect_max is not None:
        check(f"问题数 ≤ 期望上限 {expect_max}（实际 {n} 个）", n <= expect_max,
              f"实际 {n} 个")
    if expect_min is not None:
        check(f"问题数 ≥ 期望下限 {expect_min}（实际 {n} 个）", n >= expect_min,
              f"实际 {n} 个")
    # 草稿自检（14 项）必须全过
    issues = rep.get("draft_issues") or []
    check("草稿自检通过（14 项 + 事实一致性）", not issues, "；".join(issues))
    # 禁止出现的词
    for f in forbidden:
        check(f"草稿不得出现「{f}」", f.lower() not in draft.lower())
    # 必须回应的内容
    for m in must_contain:
        check(f"草稿必须回应「{m}」", m.lower() in draft.lower())
    return rep


def main():
    mode = "llm" if "--mode" in sys.argv and "llm" in sys.argv else "rule"
    print(f"测试模式：{mode}")
    engine = build_engine(mode)

    # ---- Test A：产品明确 + 数量明确 → 应减少追问 ----
    run_case(engine, "Test A · 产品明确 + 数量明确（3000 pcs 泳帽）",
             TEST_A, expect_max=1)

    # ---- Test B：产品未知 + 数量明确 → 只问产品/型号 ----
    run_case(engine, "Test B · 产品未知 + 数量明确（1000 pcs）",
             TEST_B, expect_max=2, expect_min=1,
             must_contain=("catalog",))

    # ---- Test C：数量语义补丁更新：3,000 first shipment（首单）/ 5,000 later（后续）
    #      商业语义已由客户上下文区分 → 不再判定为数量冲突 → 只问产品方向 1 问。
    #      （原断言 expect_min=2 期望 "You mentioned both..." 数量歧义追问，
    #        已被多数量语义补丁取代——不同商业语义的数量不是冲突）
    run_case(engine, "Test C · 数量语义已区分（3,000 首单 / 5,000 后续）→ 产品方向 1 问",
             TEST_C, expect_max=2, expect_min=1)

    # ---- Test D：客户提 CE/RoHS 但未明确强制 → 不问认证 ----
    rep_d = run_case(engine, "Test D · 客户提 CE/RoHS 但未明确强制",
                     TEST_D, expect_max=2)
    plan_d = rep_d.get("reply_plan") or {}
    sel_fields = [q["field"] for q in plan_d.get("selected", [])]
    check("认证未被当作首轮 P0 追问项",
          not any("cert" in f for f in sel_fields), f"实际追问：{sel_fields}")
    cert_state = (rep_d.get("insight", {}).get("certification") or {}).get(
        "certification_status")
    print(f"  ▸ 认证判定：{cert_state}（requested/interested 不应成为报价阻塞项）")

    # ---- Test E：跨询盘污染 ----
    print(f"\n{'=' * 68}\n【Test E · 跨询盘污染：先 Water Bottle 3,000，再产品未知 800 pcs】"
          f"\n{'=' * 68}")
    rep_prev = analyze(PREV_A, *engine)
    prev_names = [m["name"] for m in (rep_prev.get("matches") or [])]
    prev_payload = {
        "product_names": prev_names or ["Stainless Steel Water Bottle"],
        "quantity": rep_prev["extracted"].get("quantity") or 3000,
        "customer_name": rep_prev["extracted"].get("contact_name") or "",
        "company": rep_prev["extracted"].get("company") or "",
    }
    print(f"  （上一条：{prev_payload['product_names']} / {prev_payload['quantity']} pcs / "
          f"{prev_payload['company']}）")
    rep_e = analyze(TEST_E, *engine, previous=prev_payload)
    draft_e = rep_e["draft"] or ""
    print("-" * 68)
    print(draft_e)
    print("-" * 68)
    low_e = draft_e.lower()
    check("草稿不得出现上一条询盘的产品（water bottle）",
          "water bottle" not in low_e and "stainless steel" not in low_e)
    check("草稿不得出现上一条询盘的数量（3,000）",
          not re.search(r"3,?000", low_e))
    check("草稿不得出现上一条询盘的客户/公司（AquaTrade）", "aquatrade" not in low_e)
    check("草稿体现当前询盘数量 800", bool(re.search(r"800", low_e)))
    check("草稿自检通过", not (rep_e.get("draft_issues") or []),
          "；".join(rep_e.get("draft_issues") or []))

    # ---- Test F：客户只要求 Catalog → 优先回应 Catalog ----
    rep_f = run_case(engine, "Test F · 客户 RFQ + Catalog 请求（3,000 pcs）",
                     TEST_F, expect_max=2, expect_min=1,
                     must_contain=("catalog",))
    plan_f = rep_f.get("reply_plan") or {}
    check("Catalog 请求被识别为客户的明确需求",
          "catalog" in (plan_f.get("explicit_requests") or []),
          f"识别结果：{plan_f.get('explicit_requests')}")

    # ---- Test 01：单 P0 阻塞 → 仅问 1 问（第五轮补丁 03 §二 baseline） ----
    print(f"\n{'=' * 68}\n"
          f"【Test 01 · Michael/Global Source/Germany/around 3,000/Catalog → 仅问 1 问】"
          f"\n{'=' * 68}")
    rep_01 = analyze(TEST_01_TEXT, *engine)
    draft_01 = rep_01.get("draft") or ""
    plan_01 = rep_01.get("reply_plan") or {}
    sel_01 = plan_01.get("selected") or []
    sel_fields_01 = [q["field"] for q in sel_01]
    n_01 = count_questions(draft_01)
    low_01 = draft_01.lower()

    print("-" * 68)
    print(draft_01)
    print("-" * 68)
    print(f"  ▸ 计划提问 {len(sel_01)} 个 / 实际提问 {n_01} 个"
          f"　match_state: {plan_01.get('match_state')}"
          f"　hedge: {plan_01.get('hedge_present')}"
          f"　P0：{'、'.join(sel_fields_01) or '无'}")

    # 1) match_state = INSUFFICIENT_INFORMATION
    check("Test 01 · match_state = INSUFFICIENT_INFORMATION",
          plan_01.get("match_state") == "INSUFFICIENT_INFORMATION",
          f"实际：{plan_01.get('match_state')}")
    # 2) hedge_present = True（"around" 被并入 _APPROX_HEDGE_RE 后能触发）
    check("Test 01 · hedge_present = True（around 触发）",
          plan_01.get("hedge_present") is True,
          f"实际：{plan_01.get('hedge_present')}")
    # 3) 单 hard P0 时严格 1 问（patch 03 §八核心要求）
    check("Test 01 · 只问 1 个问题（单 hard P0 严格 1 问）",
          len(sel_01) == 1 and n_01 == 1,
          f"计划 {len(sel_01)} 个 / 实际 {n_01} 个")
    check("Test 01 · 追问字段 = product_category（不附加 quantity_confirm）",
          sel_fields_01 == ["product_category"],
          f"实际：{sel_fields_01}")
    # 4) Catalog 响应但不承诺已发
    check("Test 01 · 草稿必须回应 catalog 请求",
          "catalog" in low_01, "草稿未提及 catalog")
    check("Test 01 · 草稿不得虚构 Catalog 已发送（attached / sent you）",
          not re.search(r"\b(?:attached|attaching|please\s+find\s+attached|"
                        r"sent\s+you|have\s+sent\s+you|enclosed)\b[^.\n]{0,40}?"
                        r"\b(?:catalog|catalogue|price\s+list|specifications?)\b", low_01))
    # 5) 不写 confirmed order
    check("Test 01 · 草稿不得把「around 3,000」写成 confirmed order",
          not re.search(r"\b(?:your\s+)?(?:confirmed\s+)?order\s+(?:of\s+)?3,?000\b", low_01))
    # 6) Subject 用 hedge 表述「Initial 3,000 pcs」
    check("Test 01 · Subject 使用「Initial 3,000 pcs」（hedge 表述）",
          "Initial 3,000 pcs" in draft_01,
          "Subject 行未含 Initial 3,000 pcs")
    # 7) Quote Readiness 必为 insufficient_info
    qr_01 = (rep_01.get("insight") or {}).get("quotation_readiness") or {}
    status_01 = qr_01.get("quotation_readiness_status", "")
    check("Test 01 · Quote Readiness 状态 = insufficient_info",
          status_01 == "insufficient_info",
          f"实际：{status_01}")
    # 8) 不问 Payment / Trade / Email / Packaging / Target Price
    check("Test 01 · 草稿不得问 Payment Terms / Trade Terms / Email",
          not re.search(r"\b(?:payment\s+terms?|t/?t|incoterm|fob\s*\?|cif\s*\?|"
                        r"your\s+e-?mail|email\s+address|packag\w+\s*\?|"
                        r"target\s+price\s*\?)\b", low_01))
    # 9) 不问 "which model"
    check("Test 01 · 草稿不得问「which model do you need」",
          not re.search(r"\bwhich\s+model\s+(?:do|would|are)\s+you\b|"
                        r"\bwhat\s+model\s+do\s+you\s+need\b|"
                        r"\bdo\s+you\s+have\s+a\s+specific\s+model\b", low_01))
    # 10) 草稿自检通过
    check("Test 01 · 草稿自检通过（19 项含第五轮补丁 02/03）",
          not (rep_01.get("draft_issues") or []),
          "；".join(rep_01.get("draft_issues") or []))
    # 11) 跨询盘污染：当前询盘不能出现上条产品（如 water bottle）
    #     Test 01 默认没有 previous，但仍然守住这一栏，确保未来补跨询盘场景时工作
    #     （不主动构造 previous，留作回归位）

    # ---- Test 02：产品库无匹配 → 不要"哪个型号/品类"（第五轮补丁核心） ----
    print(f"\n{'=' * 68}\n"
          f"【Test 02 · 产品库无 water bottle → 不要问 product/category 类问题】"
          f"\n{'=' * 68}")
    rep_02 = analyze(TEST_02_TEXT, *engine)
    draft_02 = rep_02.get("draft") or ""
    plan_02 = rep_02.get("reply_plan") or {}
    sel_02 = plan_02.get("selected") or []
    sel_fields_02 = [q["field"] for q in sel_02]
    n_02 = count_questions(draft_02)
    low_02 = draft_02.lower()

    print("-" * 68)
    print(draft_02)
    print("-" * 68)
    print(f"  ▸ 计划提问 {len(sel_02)} 个 / 实际提问 {n_02} 个"
          f"　match_state: {plan_02.get('match_state')}"
          f"　P0：{'、'.join(sel_fields_02) or '无'}")

    # 1) 匹配状态必须是 NO_MATCH（产品库没有 water bottle）
    check("Test 02 · match_state = NO_MATCH",
          plan_02.get("match_state") == "NO_MATCH",
          f"实际：{plan_02.get('match_state')}")
    # 2) 选出的问题字段不能是 product/category/specification（应只问 reference）
    check("Test 02 · 不强制索取 product/category（应走 reference model 路径）",
          not any(f in ("product", "specification") for f in sel_fields_02),
          f"实际追问：{sel_fields_02}")
    # 3) 草稿中不能出现"which product or product category"这种无方向问题
    check("Test 02 · 草稿不得问「which product or product category」",
          "which product or product category" not in low_02)
    # 4) 必须回应 catalog 请求
    check("Test 02 · 草稿必须回应 catalog 请求",
          "catalog" in low_02, "草稿未提及 catalog")
    # 5) 草稿不能编造 water bottle 价格 / MOQ / lead time
    check("Test 02 · 草稿不得给出 water bottle 的具体价格数字",
          not re.search(r"(usd|\$)\s*\d", low_02))
    check("Test 02 · 草稿不得给出具体 MOQ 数字",
          not re.search(r"\bmoq\b[^.\n]{0,20}\d|\bmoq\s*(?:is|:|=)", low_02))
    check("Test 02 · 草稿不得编造 lead time 数字",
          not re.search(r"\b(?:lead\s+time|production\s+time)\b[^.\n]{0,25}?\d", low_02))
    # 6) Quote Readiness 必须与 NO_MATCH 一致（INSUFFICIENT_INFO，不应是 READY_FOR_QUOTATION）
    # 补丁 03 更新：品类已知 + 库无匹配 → 非阻塞，可初步报价
    # （旧断言 insufficient_info 已过时；仍严禁 ready_for_quotation，
    #   因为 Final Selection 尚未确认）
    qr_02 = (rep_02.get("insight") or {}).get("quotation_readiness") or {}
    status_02 = qr_02.get("quotation_readiness_status", "")
    check("Test 02 · Quote Readiness = 可初步报价（品类已知非阻塞；不得标成可正式报价）",
          status_02 == "preliminary_quote_ready"
          and status_02 != "ready_for_quotation",
          f"实际：{status_02}")
    # 7) 问题数 ≤ 3（绝对硬门槛），且 ≤ 2（普通期望）
    check(f"Test 02 · 首轮问题数 ≤ {MAX_QUESTIONS}（实际 {n_02} 个）",
          n_02 <= MAX_QUESTIONS, f"实际 {n_02} 个")
    check("Test 02 · 首轮问题数 ≤ 2（实际期望）",
          n_02 <= 2, f"实际 {n_02} 个")
    # 8) 草稿自检通过
    check("Test 02 · 草稿自检通过（14 项 + 事实一致性）",
          not (rep_02.get("draft_issues") or []),
          "；".join(rep_02.get("draft_issues") or []))

    # ---- Test 08：复杂询盘——hedge 数量 + Product Category 优先（第五轮补丁 02） ----
    print(f"\n{'=' * 68}\n"
          f"【Test 08 · 复杂询盘：hedge 数量 + Samples + Catalog + CE/RoHS 等】"
          f"\n{'=' * 68}")
    rep_08 = analyze(TEST_08_TEXT, *engine)
    draft_08 = rep_08.get("draft") or ""
    plan_08 = rep_08.get("reply_plan") or {}
    sel_08 = plan_08.get("selected") or []
    sel_fields_08 = [q["field"] for q in sel_08]
    n_08 = count_questions(draft_08)
    low_08 = draft_08.lower()

    print("-" * 68)
    print(draft_08)
    print("-" * 68)
    print(f"  ▸ 计划提问 {len(sel_08)} 个 / 实际提问 {n_08} 个"
          f"　match_state: {plan_08.get('match_state')}"
          f"　hedge: {plan_08.get('hedge_present')}"
          f"　P0：{'、'.join(sel_fields_08) or '无'}")

    # 1) match_state = INSUFFICIENT_INFORMATION（连 Product Category 都没给）
    check("Test 08 · match_state = INSUFFICIENT_INFORMATION",
          plan_08.get("match_state") == "INSUFFICIENT_INFORMATION",
          f"实际：{plan_08.get('match_state')}")
    # 2) hedge 数量 → 不进 P0（quantity_conflict / quantity_confirm 都不该出现）
    check("Test 08 · hedge 数量不进 P0（quantity_conflict / quantity_confirm 缺席）",
          "quantity_conflict" not in sel_fields_08
          and "quantity_confirm" not in sel_fields_08,
          f"实际追问：{sel_fields_08}")
    # 3) 首轮 P0 只问 Product Category / Product Type，**不得**问 model
    check("Test 08 · 首轮只问 Product Category（不出现 model 类问题）",
          not any("model" in f for f in sel_fields_08),
          f"实际追问：{sel_fields_08}")
    # 4) 草稿不得问 "which model do you need"
    check("Test 08 · 草稿不得问「which model do you need」",
          not re.search(r"\bwhich\s+model\s+(?:do|would|are)\s+you\b|"
                        r"\bwhat\s+model\s+do\s+you\s+need\b|"
                        r"\bdo\s+you\s+have\s+a\s+specific\s+model\b", low_08))
    # 5) 草稿不得把 hedge 数量写成 confirmed order
    check("Test 08 · 草稿不得把「approximately 2,000」写成 confirmed order",
          not re.search(r"\b(?:your|your\s+initial)\s+(?:confirmed|order)\s+"
                        r"(?:of|is|for|:)?\s*2,?000\s*(?:pcs|pieces|units)\b", low_08))
    check("Test 08 · 草稿不得把「3,000 mentioned」写成 your order of 3,000",
          not re.search(r"\byour\s+order\s+(?:of\s+)?3,?000\b", low_08))
    check("Test 08 · 草稿不得把「10,000 annual volume」写成 your annual volume",
          not re.search(r"\byour\s+annual\s+(?:volume\s+)?(?:of\s+)?10,?000\b", low_08))
    # 6) 草稿不得虚构 Catalog 已发送
    check("Test 08 · 草稿不得虚构 Catalog 已发送（attached / sent you）",
          not re.search(r"\b(?:attached|attaching|please\s+find\s+attached|"
                        r"sent\s+you|have\s+sent\s+you|enclosed)\b[^.\n]{0,40}?"
                        r"\b(?:catalog|catalogue|price\s+list|specifications?)\b", low_08))
    # 7) 草稿不得虚构 Sample 已准备 / 承诺立即发出
    check("Test 08 · 草稿不得承诺立即发样品（we will send samples）",
          not re.search(r"\b(?:we|i|we'll|we\s+will)\s+(?:will\s+)?(?:send|prepare|"
                        r"arrange|dispatch|ship|be\s+sending)\s+(?:you\s+)?"
                        r"(?:the\s+|your\s+|some\s+|free\s+)?samples?\b", low_08))
    # 8) CE/RoHS interested → 不得当作 mandatory（不应出现 "must have CE"）
    check("Test 08 · CE/RoHS interested → 草稿不当 mandatory",
          not re.search(r"\b(?:must|required?|mandatory)\s+(?:have|be|need)\s+"
                        r"(?:CE|RoHS|certificat)", low_08))
    # 9) Quote Readiness = insufficient_info（不得是 READY_FOR_QUOTATION）
    qr_08 = (rep_08.get("insight") or {}).get("quotation_readiness") or {}
    status_08 = qr_08.get("quotation_readiness_status", "")
    check("Test 08 · Quote Readiness 状态 = 信息不足",
          status_08 == "insufficient_info",
          f"实际：{status_08}")
    # 10) 首轮问题数 ≤ 2（期望），≤ 3（绝对硬门槛）
    check("Test 08 · 首轮问题数 ≤ 2（实际期望）",
          n_08 <= 2, f"实际 {n_08} 个")
    check(f"Test 08 · 首轮问题数 ≤ {MAX_QUESTIONS}（绝对硬门槛）",
          n_08 <= MAX_QUESTIONS, f"实际 {n_08} 个")
    # 11) 草稿不得问 Payment / Trade / Packaging / Email
    check("Test 08 · 草稿不得问 Payment Terms / Trade Terms / Email",
          not re.search(r"\b(?:payment\s+terms?|t/?t|incoterm|fob|cif|exw|your\s+"
                        r"e-?mail|email\s+address)\s*\?|"
                        r"\bhow\s+would\s+you\s+(?:like|prefer)\s+to\s+pay\b", low_08))
    # 12) 草稿必须回应 catalog 请求 + samples 请求（但不承诺立即发）
    check("Test 08 · 草稿必须回应 catalog 请求（但不承诺已发）",
          "catalog" in low_08, "草稿未提及 catalog")
    check("Test 08 · 草稿必须回应 samples 请求（但不承诺立即发）",
          "sample" in low_08, "草稿未提及 sample")
    # 13) 草稿自检通过（14 项 + 5 项新增 = 19 项）
    check("Test 08 · 草稿自检通过（19 项含第五轮补丁 02 新增）",
          not (rep_08.get("draft_issues") or []),
          "；".join(rep_08.get("draft_issues") or []))

    # ---- Test 03：多重 hedge 数量 + product unknown → 1 问（第五轮补丁 03） ----
    print(f"\n{'=' * 68}\n"
          f"【Test 03 · 多重 hedge 数量 2,000+500+5,000 + product unknown → 仅问 1 问】"
          f"\n{'=' * 68}")
    rep_03 = analyze(TEST_03_TEXT, *engine)
    draft_03 = rep_03.get("draft") or ""
    plan_03 = rep_03.get("reply_plan") or {}
    sel_03 = plan_03.get("selected") or []
    sel_fields_03 = [q["field"] for q in sel_03]
    n_03 = count_questions(draft_03)
    low_03 = draft_03.lower()

    print("-" * 68)
    print(draft_03)
    print("-" * 68)
    print(f"  ▸ 计划提问 {len(sel_03)} 个 / 实际提问 {n_03} 个"
          f"　match_state: {plan_03.get('match_state')}"
          f"　hedge: {plan_03.get('hedge_present')}"
          f"　P0：{'、'.join(sel_fields_03) or '无'}")

    check("Test 03 · match_state = INSUFFICIENT_INFORMATION",
          plan_03.get("match_state") == "INSUFFICIENT_INFORMATION",
          f"实际：{plan_03.get('match_state')}")
    check("Test 03 · hedge_present = True（3 档 hedge 量都触发）",
          plan_03.get("hedge_present") is True,
          f"实际：{plan_03.get('hedge_present')}")
    check("Test 03 · 只问 1 个问题（hard P0 product_category 单一时）",
          len(sel_03) == 1 and n_03 == 1 and sel_fields_03 == ["product_category"],
          f"计划 {len(sel_03)} 个 / 实际 {n_03} 个 / 字段 {sel_fields_03}")
    check("Test 03 · hedge 数量全部不进 P0（quantity_confirm/quantity_conflict 缺席）",
          "quantity_confirm" not in sel_fields_03
          and "quantity_conflict" not in sel_fields_03,
          f"实际：{sel_fields_03}")
    check("Test 03 · 草稿必须回应 catalog 请求",
          "catalog" in low_03)
    check("Test 03 · 草稿不得把任何数量写成 confirmed order of X",
          not re.search(r"\byour\s+(?:confirmed\s+)?order\s+(?:of\s+)?\d", low_03))
    check("Test 03 · 草稿不得虚构 Catalog 已发送",
          not re.search(r"\b(?:attached|attaching|sent\s+you|have\s+sent\s+you|"
                        r"please\s+find\s+attached|enclosed)\b[^.\n]{0,40}?"
                        r"\b(?:catalog|catalogue|price\s+list|specifications?)\b", low_03))
    check("Test 03 · 草稿不得问「which model」",
          not re.search(r"\bwhich\s+model\s+(?:do|would|are)\s+you\b|"
                        r"\bwhat\s+model\s+do\s+you\s+need\b|"
                        r"\bdo\s+you\s+have\s+a\s+specific\s+model\b", low_03))
    check("Test 03 · 不问 Payment / Trade / Email / Packaging / Target Price",
          not re.search(r"\b(?:payment\s+terms?\s*\?|t/?t\s*\?|incoterm\s*\?|"
                        r"fob\s*\?|cif\s*\?|your\s+e-?mail|email\s+address|"
                        r"packag\w+\s*\?|target\s+price\s*\?)\b", low_03))
    check("Test 03 · 草稿自检通过（19 项含 patch 02/03）",
          not (rep_03.get("draft_issues") or []),
          "；".join(rep_03.get("draft_issues") or []))

    # ---- Test 04：产品已匹配 + 数量 confirmed + CE/RoHS interested（第五轮补丁 03） ----
    print(f"\n{'=' * 68}\n"
          f"【Test 04 · 1,500 swim caps + CE/RoHS interested → 0-1 问】"
          f"\n{'=' * 68}")
    rep_04 = analyze(TEST_04_TEXT, *engine)
    draft_04 = rep_04.get("draft") or ""
    plan_04 = rep_04.get("reply_plan") or {}
    sel_04 = plan_04.get("selected") or []
    sel_fields_04 = [q["field"] for q in sel_04]
    n_04 = count_questions(draft_04)
    low_04 = draft_04.lower()

    print("-" * 68)
    print(draft_04)
    print("-" * 68)
    print(f"  ▸ 计划提问 {len(sel_04)} 个 / 实际提问 {n_04} 个"
          f"　match_state: {plan_04.get('match_state')}"
          f"　P0：{'、'.join(sel_fields_04) or '无'}")

    check("Test 04 · match_state = MATCHED（swim cap 命中）",
          plan_04.get("match_state") == "MATCHED",
          f"实际：{plan_04.get('match_state')}")
    check("Test 04 · CE/RoHS interested → 不进 P0（不被强制）",
          not any("cert" in f for f in sel_fields_04),
          f"实际：{sel_fields_04}")
    check("Test 04 · 数量 1,500 confirmed（无 hedge 词）",
          plan_04.get("hedge_present") is False,
          f"实际 hedge_present：{plan_04.get('hedge_present')}")
    check("Test 04 · 首轮问题数 ≤ 2（MATCHED 时通常 0-1）",
          n_04 <= 2, f"实际 {n_04} 个")
    check("Test 04 · 草稿体现数量 1,500",
          "1,500" in draft_04 or "1500" in draft_04)
    check("Test 04 · 草稿展示产品事实（MATCHED → Quick overview：USD 或 MOQ 或 lead time）",
          ("moq" in low_04 or "lead time" in low_04 or "usd" in low_04),
          "草稿未展示产品库事实")
    check("Test 04 · CE/RoHS interested → 草稿不当 mandatory",
          not re.search(r"\b(?:must|required?|mandatory)\s+(?:have|be|need)\s+"
                        r"(?:CE|RoHS|certificat)", low_04))
    check("Test 04 · 草稿不得问「which model」",
          not re.search(r"\bwhich\s+model\s+(?:do|would|are)\s+you\b|"
                        r"\bwhat\s+model\s+do\s+you\s+need\b", low_04))
    check("Test 04 · 草稿自检通过",
          not (rep_04.get("draft_issues") or []),
          "；".join(rep_04.get("draft_issues") or []))

    # ---- Test 05：单 Catalog 请求（第五轮补丁 03） ----
    print(f"\n{'=' * 68}\n"
          f"【Test 05 · 单 Catalog 请求，其他未给 → 仅问 1 问】"
          f"\n{'=' * 68}")
    rep_05 = analyze(TEST_05_TEXT, *engine)
    draft_05 = rep_05.get("draft") or ""
    plan_05 = rep_05.get("reply_plan") or {}
    sel_05 = plan_05.get("selected") or []
    sel_fields_05 = [q["field"] for q in sel_05]
    n_05 = count_questions(draft_05)
    low_05 = draft_05.lower()

    print("-" * 68)
    print(draft_05)
    print("-" * 68)
    print(f"  ▸ 计划提问 {len(sel_05)} 个 / 实际提问 {n_05} 个"
          f"　match_state: {plan_05.get('match_state')}"
          f"　P0：{'、'.join(sel_fields_05) or '无'}")

    check("Test 05 · match_state = INSUFFICIENT_INFORMATION",
          plan_05.get("match_state") == "INSUFFICIENT_INFORMATION",
          f"实际：{plan_05.get('match_state')}")
    check("Test 05 · Catalog 请求被识别为客户的明确需求",
          "catalog" in (plan_05.get("explicit_requests") or []),
          f"识别结果：{plan_05.get('explicit_requests')}")
    check("Test 05 · 首轮问题数 ≤ 2（默认 1）",
          n_05 <= 2, f"实际 {n_05} 个")
    check("Test 05 · 草稿必须回应 catalog 请求",
          "catalog" in low_05)
    check("Test 05 · 草稿不得虚构 Catalog 已发送",
          not re.search(r"\b(?:attached|attaching|sent\s+you|have\s+sent\s+you|"
                        r"please\s+find\s+attached|enclosed)\b[^.\n]{0,40}?"
                        r"\b(?:catalog|catalogue|price\s+list)\b", low_05))
    check("Test 05 · 草稿不得问「which model」",
          not re.search(r"\bwhich\s+model\s+(?:do|would|are)\s+you\b|"
                        r"\bwhat\s+model\s+do\s+you\s+need\b", low_05))
    check("Test 05 · 草稿自检通过",
          not (rep_05.get("draft_issues") or []),
          "；".join(rep_05.get("draft_issues") or []))

    # ---- Test 06：产品已匹配 + 数量 confirmed + Target Price（第五轮补丁 03） ----
    print(f"\n{'=' * 68}\n"
          f"【Test 06 · 10,000 swim caps + target price USD 2.80 → 0-1 问】"
          f"\n{'=' * 68}")
    rep_06 = analyze(TEST_06_TEXT, *engine)
    draft_06 = rep_06.get("draft") or ""
    plan_06 = rep_06.get("reply_plan") or {}
    sel_06 = plan_06.get("selected") or []
    sel_fields_06 = [q["field"] for q in sel_06]
    n_06 = count_questions(draft_06)
    low_06 = draft_06.lower()

    print("-" * 68)
    print(draft_06)
    print("-" * 68)
    print(f"  ▸ 计划提问 {len(sel_06)} 个 / 实际提问 {n_06} 个"
          f"　match_state: {plan_06.get('match_state')}"
          f"　P0：{'、'.join(sel_fields_06) or '无'}")

    check("Test 06 · match_state = MATCHED（silicone swim cap 命中）",
          plan_06.get("match_state") == "MATCHED",
          f"实际：{plan_06.get('match_state')}")
    check("Test 06 · 数量 10,000 confirmed（无 hedge 词）",
          plan_06.get("hedge_present") is False,
          f"实际 hedge_present：{plan_06.get('hedge_present')}")
    check("Test 06 · 首轮问题数 ≤ 2（MATCHED 时 0-1）",
          n_06 <= 2, f"实际 {n_06} 个")
    check("Test 06 · 草稿体现数量 10,000",
          "10,000" in draft_06 or "10000" in draft_06)
    check("Test 06 · 草稿不得擅自接受/承诺目标价（we can do/meet USD X）",
          not re.search(r"\bwe\s+(?:can|will|shall|could|would)\s+"
                        r"(?:meet|accept|do|offer|quote|achieve)\s+"
                        r"(?:USD\s*\$?\s*)?\d", low_06),
          "草稿不应擅自承诺接受 USD X 目标价")
    check("Test 06 · 草稿不得问「which model」",
          not re.search(r"\bwhich\s+model\s+(?:do|would|are)\s+you\b", low_06))
    check("Test 06 · 草稿不得问 Payment / Trade / Email",
          not re.search(r"\b(?:payment\s+terms?\s*\?|t/?t\s*\?|incoterm\s*\?|"
                        r"your\s+e-?mail|email\s+address)\s*\?", low_06))
    check("Test 06 · 草稿自检通过",
          not (rep_06.get("draft_issues") or []),
          "；".join(rep_06.get("draft_issues") or []))

    # ---- Test 07：产品已匹配 + 数量 confirmed + 明确交期（第五轮补丁 03） ----
    print(f"\n{'=' * 68}\n"
          f"【Test 07 · 8,000 swim caps + 4 weeks delivery → 0-1 问】"
          f"\n{'=' * 68}")
    rep_07 = analyze(TEST_07_TEXT, *engine)
    draft_07 = rep_07.get("draft") or ""
    plan_07 = rep_07.get("reply_plan") or {}
    sel_07 = plan_07.get("selected") or []
    sel_fields_07 = [q["field"] for q in sel_07]
    n_07 = count_questions(draft_07)
    low_07 = draft_07.lower()

    print("-" * 68)
    print(draft_07)
    print("-" * 68)
    print(f"  ▸ 计划提问 {len(sel_07)} 个 / 实际提问 {n_07} 个"
          f"　match_state: {plan_07.get('match_state')}"
          f"　P0：{'、'.join(sel_fields_07) or '无'}")

    check("Test 07 · match_state = MATCHED 或 PARTIAL_MATCH（silicone swim cap 命中）",
          plan_07.get("match_state") in ("MATCHED", "PARTIAL_MATCH"),
          f"实际：{plan_07.get('match_state')}")
    check("Test 07 · 数量 8,000 confirmed（无 hedge 词）",
          plan_07.get("hedge_present") is False,
          f"实际 hedge_present：{plan_07.get('hedge_present')}")
    check("Test 07 · 首轮问题数 ≤ 2（产品+数量+交期齐全时 0-1）",
          n_07 <= 2, f"实际 {n_07} 个")
    check("Test 07 · 草稿体现数量 8,000",
          "8,000" in draft_07 or "8000" in draft_07)
    check("Test 07 · 草稿体现 lead time（产品库 20-25 days 与客户 4 weeks 对齐表达）",
          ("lead time" in low_07 and re.search(r"\b20\s*[-–]\s*25\b|4\s*weeks?", low_07)),
          "草稿未体现 lead time 或 4 weeks")
    check("Test 07 · 草稿不得擅自承诺具体交付日期（we can deliver in X days/weeks）",
          not re.search(r"\b(?:we|we'll|we\s+will|we\s+can)\b[^.?]{0,40}?"
                        r"\b(?:deliver|ship|dispatch)\b[^.?]{0,30}?"
                        r"(?:within\s+)?\d+\s*(?:days?|weeks?)", low_07))
    check("Test 07 · 草稿不得问 Payment / Trade / Email",
          not re.search(r"\b(?:payment\s+terms?\s*\?|t/?t\s*\?|incoterm\s*\?|"
                        r"your\s+e-?mail|email\s+address)\s*\?", low_07))
    check("Test 07 · 草稿自检通过",
          not (rep_07.get("draft_issues") or []),
          "；".join(rep_07.get("draft_issues") or []))

    # ---- 汇总 ----
    print("\n" + "=" * 68)
    passed, total = sum(RESULTS), len(RESULTS)
    print(f"第五轮回归测试结果：{passed}/{total} 项通过"
          + ("　✅ 全部通过" if passed == total else "　❌ 存在失败项，请检查上方 ❌"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
