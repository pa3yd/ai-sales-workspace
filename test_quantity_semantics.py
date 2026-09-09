# -*- coding: utf-8 -*-
"""
多数量语义补丁 · 回归测试：复杂询盘多数量商业语义理解

用法：
  python test_quantity_semantics.py     # 离线规则模式（不消耗 API）

核心原则：
  1. 同一封询盘里的 试单数量 / 首单数量 / 报价数量 / 潜在订单量 / 年度采购量
     不是冲突数据，绝不要求客户二选一。
  2. 客户已用上下文说明用途的数量，绝不生成
     "You mentioned both X and Y - could you confirm which quantity applies..."。
  3. 数量语义已从上下文判断清楚时，不把问题重新丢回客户。

场景（spec 九）：
  A  只有一个数量：3,000 pcs
  B  产品未知 + 1,000 pcs
  C  500 trial + 2,000 quotation + 5,000 potential（+ 2 周交期 + 样品）
  D  2,000 initial + 10,000 annual volume
  E  1,000 sample/trial + 5,000 formal order
"""
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from main import build_engine, analyze
from agent.extractor import (
    extract_quantity_semantics, semantic_conflict_roles,
    QUANTITY_TRIAL, QUANTITY_INITIAL, QUANTITY_QUOTATION,
    QUANTITY_POTENTIAL, QUANTITY_ANNUAL, QUANTITY_UNKNOWN,
)

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


def qty_question_fields(plan):
    """计划里与数量相关的追问字段（应为空：语义已区分就不再问数量）。"""
    return [q["field"] for q in (plan.get("selected") or [])
            if q["field"] in ("quantity", "quantity_conflict", "quantity_confirm")]


# =============== 单元级：语义识别角色判定 ===============
def test_semantics_unit():
    print("\n【单元 · extract_quantity_semantics 角色判定】")

    # C：500 trial + 2,000 quotation + 5,000 potential
    sems = extract_quantity_semantics(
        "We are looking for 2,000 pcs. First order may start with 500 pcs to test "
        "market, but if price competitive total order could reach 5,000 pcs.")
    roles = {s["value"]: s["role"] for s in sems}
    check("C · 2,000 = 报价数量（QUOTATION_QUANTITY）",
          roles.get(2000) == QUANTITY_QUOTATION, str(roles.get(2000)))
    check("C · 500 = 试单数量（TRIAL_QUANTITY）",
          roles.get(500) == QUANTITY_TRIAL, str(roles.get(500)))
    check("C · 5,000 = 潜在订单量（POTENTIAL_VOLUME）",
          roles.get(5000) == QUANTITY_POTENTIAL, str(roles.get(5000)))
    check("C · 三者语义互不相同 → 无冲突", not semantic_conflict_roles(sems))

    # D：2,000 initial + 10,000 annual
    sems = extract_quantity_semantics(
        "We need 2,000 pcs for the first order. Our annual purchase volume "
        "could reach 10,000 pcs.")
    roles = {s["value"]: s["role"] for s in sems}
    check("D · 2,000 = 首单数量（INITIAL_ORDER_QUANTITY）",
          roles.get(2000) == QUANTITY_INITIAL, str(roles.get(2000)))
    check("D · 10,000 = 年度采购量（ANNUAL_VOLUME）",
          roles.get(10000) == QUANTITY_ANNUAL, str(roles.get(10000)))
    check("D · 无冲突", not semantic_conflict_roles(sems))

    # E：1,000 trial + 5,000 formal
    sems = extract_quantity_semantics(
        "We would like to start with 1,000 pcs as a sample/trial order. "
        "For the formal order we expect 5,000 pcs.")
    roles = {s["value"]: s["role"] for s in sems}
    check("E · 1,000 = 试单数量（TRIAL_QUANTITY）",
          roles.get(1000) == QUANTITY_TRIAL, str(roles.get(1000)))
    check("E · 5,000 = 正式订单数量（INITIAL_ORDER_QUANTITY）",
          roles.get(5000) == QUANTITY_INITIAL, str(roles.get(5000)))
    check("E · 无冲突", not semantic_conflict_roles(sems))

    # 真冲突仍要抓到：同角色多值（都无法判断/同用途）
    sems = extract_quantity_semantics("We need 3,000 or 5,000 pcs for our stores.")
    groups = semantic_conflict_roles(sems)
    check("真冲突 · 3,000 or 5,000（同角色多值）仍判定为冲突",
          bool(groups), str(groups))


# =============== 场景 A：只有一个数量 ===============
TEST_A_TEXT = """Hello,

We would like to order 3,000 pcs neoprene swim caps for our swim school.
Please quote your best price and MOQ.

Best regards,
Anna
ABC Trading GmbH
Germany"""


def run_test_a(engine):
    print("\n【场景 A · 只有一个数量：3,000 pcs】")
    rep = analyze(TEST_A_TEXT, *engine)
    plan = rep.get("reply_plan") or {}
    ins = rep.get("insight") or {}
    check("A · 无数量追问（单数量语义清楚）", not qty_question_fields(plan),
          str(qty_question_fields(plan)))
    check("A · 数量语义识别为 1 个数量",
          len((rep.get("info") or {}).get("quantity_semantics")
              or plan.get("quantity_semantics") or []) == 1)
    check("A · 草稿不含数量歧义追问（mentioned both）",
          "mentioned both" not in (rep.get("draft") or "").lower())


# =============== 场景 B：产品未知 + 1,000 pcs ===============
TEST_B_TEXT = """Hi,

We are interested in your products and would like to purchase 1,000 pcs.

Please send us your catalog and best price.

Best regards,
Michael
Global Source Ltd.
Germany"""


def run_test_b(engine):
    print("\n【场景 B · 产品未知 + 1,000 pcs】")
    rep = analyze(TEST_B_TEXT, *engine)
    plan = rep.get("reply_plan") or {}
    sel = [q["field"] for q in (plan.get("selected") or [])]
    check("B · 无数量追问（1,000 是唯一数量）", not qty_question_fields(plan),
          str(qty_question_fields(plan)))
    check("B · P0 追问的是产品方向（product_category 或 reference_model）",
          bool(sel) and sel[0] in ("product_category", "reference_model"), str(sel))
    check("B · 首轮问题数 ≤ 2", len(sel) <= 2, f"实际 {len(sel)} 个")
    issues = rep.get("draft_issues") or []
    check("B · 草稿自检通过", not issues, "；".join(issues))


# =============== 场景 C：500 trial + 2,000 quotation + 5,000 potential ===============
TEST_C_TEXT = """Subject: Inquiry

Hi,

We are looking for 2,000 pcs. Our customer needs within 2 weeks.
First order may start with 500 pcs to test market,
but if price competitive total order could reach 5,000 pcs.
Please send best price for 2,000 pcs and MOQ.
Samples first.
Confirm if can meet delivery.

Best regards,
Daniel
GreenLife Import GmbH
Germany"""


def run_test_c(engine):
    print("\n【场景 C · 500 trial + 2,000 quotation + 5,000 potential（spec 核心场景）】")
    rep = analyze(TEST_C_TEXT, *engine)
    plan = rep.get("reply_plan") or {}
    draft = rep.get("draft") or ""
    low = draft.lower()
    ins = rep.get("insight") or {}

    # 1) 语义识别
    sems = plan.get("quantity_semantics") or []
    roles = {s["value"]: s["role"] for s in sems}
    check("C · 500 = 试单数量", roles.get(500) == QUANTITY_TRIAL, str(roles.get(500)))
    check("C · 2,000 = 报价数量", roles.get(2000) == QUANTITY_QUOTATION,
          str(roles.get(2000)))
    check("C · 5,000 = 潜在订单量", roles.get(5000) == QUANTITY_POTENTIAL,
          str(roles.get(5000)))

    # 2) 绝不把语义不同的数量判为冲突
    check("C · 无数量相关追问（试单/报价/潜在量各归各位）",
          not qty_question_fields(plan), str(qty_question_fields(plan)))
    check("C · 草稿绝不出现「You mentioned both ... which quantity」",
          "mentioned both" not in low and "which quantity applies" not in low)

    # 3) 草稿：确认约 2,000 作为报价参考数量；不重新追问 500 / 5,000
    check("C · 草稿确认 2,000 pcs 为当前报价参考数量",
          "2,000 pcs" in draft and "reference quantity" in low)
    check("C · 草稿不重新追问试单 500（不出现 500 pcs）", "500 pcs" not in low)
    check("C · 草稿不重新追问潜在量 5,000（不出现 5,000 pcs）", "5,000 pcs" not in low)

    # 4) 草稿只确认产品方向，1 个问题
    n = len([q for q in (plan.get("selected") or [])])
    sel_fields = [q["field"] for q in (plan.get("selected") or [])]
    check("C · 首轮只问产品方向（≤2 问，且为 product_category/reference_model）",
          0 < len(sel_fields) <= 2
          and all(f in ("product_category", "reference_model", "specification")
                  for f in sel_fields), f"{sel_fields}")

    # 5) 交期：自然提及 2 周但不承诺
    check("C · 草稿自然提及客户要求的 2 weeks 交期", "2 weeks" in low)
    check("C · 草稿不承诺满足交期（无 we will deliver / we can meet）",
          not re.search(r"\bwe\s+(?:will|can)\s+(?:deliver|meet|complete)\b", low))
    check("C · 草稿不自行承诺具体交付天数（within X we will ...）",
          not re.search(r"\bwe\s+(?:will|shall)\b[^.]{0,60}?\bwithin\s+\d", low))

    # 6) 样品：保留为后续行动，不承诺立即提供
    check("C · 样品需求被回应（sample availability）", "sample" in low)
    check("C · 不承诺立即寄样（无 we will send samples）",
          not re.search(r"\bwe\s+(?:will|'ll)\s+send\s+(?:you\s+)?samples?\b", low))

    # 7) 摘要/证据链不制造虚假冲突
    qty_reason = ""
    factors = ins.get("factor_details") or ins.get("factors") or {}
    if isinstance(factors, dict):
        qty_reason = str((factors.get("quantity") or {}).get("reason", ""))
    check("C · 评分证据链注明「不构成数量冲突」",
          ("不构成数量冲突" in qty_reason) or ("不构成" in qty_reason) or not qty_reason,
          qty_reason[:50])

    issues = rep.get("draft_issues") or []
    check("C · 草稿自检通过（14 项 + 事实一致性）", not issues, "；".join(issues))


# =============== 场景 D：2,000 initial + 10,000 annual ===============
TEST_D_TEXT = """Hi,

We need 2,000 pcs for the first order. Our annual purchase volume
could reach 10,000 pcs if the product sells well.

Please send us your catalog and price list.

Best regards,
Laura
Nordic Sports AB
Sweden"""


def run_test_d(engine):
    print("\n【场景 D · 2,000 首单 + 10,000 年度采购量】")
    rep = analyze(TEST_D_TEXT, *engine)
    plan = rep.get("reply_plan") or {}
    roles = {s["value"]: s["role"] for s in (plan.get("quantity_semantics") or [])}
    check("D · 2,000 = 首单数量", roles.get(2000) == QUANTITY_INITIAL,
          str(roles.get(2000)))
    check("D · 10,000 = 年度采购量", roles.get(10000) == QUANTITY_ANNUAL,
          str(roles.get(10000)))
    check("D · 无数量追问（首单/年度不是冲突）", not qty_question_fields(plan),
          str(qty_question_fields(plan)))
    check("D · 草稿不含数量歧义追问",
          "mentioned both" not in (rep.get("draft") or "").lower())
    issues = rep.get("draft_issues") or []
    check("D · 草稿自检通过", not issues, "；".join(issues))


# =============== 场景 E：1,000 trial + 5,000 formal ===============
TEST_E_TEXT = """Hello,

We would like to start with 1,000 pcs as a sample/trial order to test
the market. For the formal order we expect 5,000 pcs.

Please advise your best price.

Best regards,
Karl
BlueLine Distribution
Germany"""


def run_test_e(engine):
    print("\n【场景 E · 1,000 试单 + 5,000 正式订单】")
    rep = analyze(TEST_E_TEXT, *engine)
    plan = rep.get("reply_plan") or {}
    roles = {s["value"]: s["role"] for s in (plan.get("quantity_semantics") or [])}
    check("E · 1,000 = 试单数量", roles.get(1000) == QUANTITY_TRIAL,
          str(roles.get(1000)))
    check("E · 5,000 = 正式订单数量（首单）", roles.get(5000) == QUANTITY_INITIAL,
          str(roles.get(5000)))
    check("E · 无数量追问（试单/正式单各归各位）", not qty_question_fields(plan),
          str(qty_question_fields(plan)))
    check("E · 草稿不含数量歧义追问",
          "mentioned both" not in (rep.get("draft") or "").lower())
    issues = rep.get("draft_issues") or []
    check("E · 草稿自检通过", not issues, "；".join(issues))


# =============== 防幻觉：Company Data 缺失时不得声称 manufacturer/认证 ===============
def test_company_facts_fallback(engine):
    print("\n【防幻觉 · Company Data 缺失时草稿不声称 manufacturer / 点名认证】")
    import agent.replier as replier_mod

    orig = replier_mod.load_config
    try:
        cfg = orig()
        cfg_no_seller = dict(cfg)
        cfg_no_seller["SELLER"] = {"company": "Test Co", "city": "Shenzhen, China",
                                   "sales_name": "Sales"}
        replier_mod.load_config = lambda: cfg_no_seller
        rep = analyze(TEST_C_TEXT, *engine)
        draft = (rep.get("draft") or "").lower()
        check("无 company_type · 草稿不自称 manufacturer / factory",
              not re.search(r"\bmanufacturer\b|\bfactory\b", draft))
        check("无 certifications · 草稿不点名 BSCI / ISO9001",
              "bsci" not in draft and "iso9001" not in draft and "iso 9001" not in draft)
        issues = rep.get("draft_issues") or []
        check("无公司资质数据 · 草稿自检仍通过", not issues, "；".join(issues))
    finally:
        replier_mod.load_config = orig


def main():
    mode = "llm" if "--mode" in sys.argv and "llm" in sys.argv else "rule"
    print("=" * 66)
    print(f"多数量语义补丁回归测试（{mode} 模式，离线规则不耗 API）")
    print("=" * 66)
    engine = build_engine(mode)

    test_semantics_unit()
    run_test_a(engine)
    run_test_b(engine)
    run_test_c(engine)
    run_test_d(engine)
    run_test_e(engine)
    test_company_facts_fallback(engine)

    print("=" * 66)
    if _FAIL == 0:
        print(f"多数量语义补丁回归测试结果：{_PASS}/{_PASS + _FAIL} 项通过　✅ 全部通过")
    else:
        print(f"多数量语义补丁回归测试结果：{_PASS}/{_PASS + _FAIL} 项通过　❌ 失败 {_FAIL} 项")
    print("=" * 66)
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
