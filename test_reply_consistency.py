# -*- coding: utf-8 -*-
"""
第三阶段回归测试：Reply Draft 与当前询盘一致性 + 跨询盘污染检查

用法：
  python test_reply_consistency.py            # 离线规则模式（不消耗 API）
  python test_reply_consistency.py --mode llm # 真实 LLM 模式（消耗少量 API）

验收标准（第三阶段优化十一/十二）：
  压力测试 02：不出现 Stainless Steel Water Bottle / 3,000 pcs；
               数量必须是 1,000 pcs；不虚构产品/价格/MOQ/交期/认证。
  Test C（跨询盘污染）：先分析产品明确的询盘 A，再分析产品未知的询盘 B，
               B 的草稿绝不能出现 A 的产品名与数量。
"""
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from main import build_engine, analyze

STRESS_02 = """Subject: Price for 1000 pcs

Hi,

We need 1000 pcs for our customer.

Please send me your best price, MOQ and delivery time.

If the price is good, we can place the order soon.

Please send your catalog as well.

Regards,

Michael
Purchasing Manager
Global Source Ltd.
Germany"""

# Test A：产品明确 + 数量明确（使用产品库真实存在的产品）
TEST_A = """Hello,

We are looking for 3000 pcs neoprene swim caps for our swim school.
Please quote your best price and MOQ. We also need custom logo printing.

Best regards,
Anna
ABC Trading GmbH"""

# Test B：产品未知 + 数量明确（与压力测试 02 同构）
TEST_B = STRESS_02

RESULTS = []


def check(name, cond, detail=""):
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f"　[{detail}]" if (detail and not cond) else ""))
    RESULTS.append(cond)


def run_case(engine, label, text, forbidden=(), must_qty=None):
    print(f"\n{'=' * 60}\n【{label}】\n{'=' * 60}")
    rep = analyze(text, *engine)
    draft = rep["draft"] or ""
    low = draft.lower()
    print("-" * 60)
    print(draft)
    print("-" * 60)
    for f in forbidden:
        check(f"草稿不得出现「{f}」", f.lower() not in low)
    if must_qty:
        qty_pat = re.compile(r"3,?000|3 000")
        wrong_qty = bool(qty_pat.search(low)) if must_qty != 3000 else False
        right_qty = re.search(rf"{must_qty:,}|{must_qty}".replace(",", ",?"), low)
        check(f"草稿不得出现错误数量（非 {must_qty}）", not wrong_qty)
        check(f"草稿体现当前询盘数量 {must_qty}", bool(right_qty))
    # 通用校验：草稿自带校验器应无问题
    issues = rep.get("draft_issues") or []
    check("事实一致性校验通过（无无依据内容）", not issues, "；".join(issues))
    return rep


def main():
    mode = "llm" if "--mode" in sys.argv and "llm" in sys.argv else "rule"
    print(f"测试模式：{mode}")
    engine = build_engine(mode)

    # ---- Test A：产品明确 + 数量明确 ----
    run_case(engine, "Test A · 产品明确 + 数量明确（3000 pcs 泳帽）",
             TEST_A, forbidden=(), must_qty=3000)

    # ---- Test B：产品未知 + 数量明确 ----
    run_case(engine, "Test B · 产品未知 + 数量明确（1000 pcs）",
             TEST_B,
             forbidden=("stainless steel water bottle", "stainless steel"),
             must_qty=1000)

    # ---- Test C：跨询盘污染（同一引擎，先 A 后 B） ----
    print(f"\n{'=' * 60}\n【Test C · 跨询盘污染检查：同一引擎先分析 A 再分析 B】\n{'=' * 60}")
    rep_a = analyze(TEST_A, *engine)
    print(f"  （询盘 A 已分析：{rep_a['extracted'].get('quantity')} pcs，"
          f"产品匹配：{rep_a['matches'][0]['name'] if rep_a['matches'] else '无'}）")
    rep_b = analyze(TEST_B, *engine)
    draft_b = (rep_b["draft"] or "").lower()
    print("-" * 60)
    print(rep_b["draft"])
    print("-" * 60)
    check("询盘 B 草稿不得出现询盘 A 的产品（Stainless Steel Water Bottle）",
          "stainless steel" not in draft_b and "water bottle" not in draft_b)
    check("询盘 B 草稿不得出现询盘 A 的数量（3,000 pcs）",
          not re.search(r"3,?000\s*(pcs|pieces)?", draft_b))
    check("询盘 B 草稿必须使用当前数量 1,000",
          bool(re.search(r"1,?000", draft_b)))

    print("\n" + "=" * 60)
    passed, total = sum(RESULTS), len(RESULTS)
    print(f"回归测试结果：{passed}/{total} 项通过"
          + ("　✅ 全部通过" if passed == total else "　❌ 存在失败项，请检查上方 ❌"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
