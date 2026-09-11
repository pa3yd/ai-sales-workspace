# -*- coding: utf-8 -*-
"""
第五轮第二次优化 · 补丁 03 回归测试：需求语义层级
（Customer Requirement / Confirmed Requirement / Final Commercial Confirmation）

用法：
  python test_patch03_semantics.py     # 离线规则模式（不消耗 API）

Test 01  品类已知 + 库无匹配（Stainless Steel Water Bottle + 500ml/750ml + ~3000pcs + FOB + Catalog）
         → Product Category=Confirmed；Capacity=500ml/750ml（部分确认）；
           Model=Unknown；Final Selection=待确认（不是 Unknown）；
           Quantity=~3000pcs（初始/大概）；Trade Term=FOB（Confirmed）；
           Quote Readiness=可初步报价；下一步 = 查看产品库并匹配候选产品（不升 P0）
Test 02  品类完全未知（回归第五轮行为）→ product_category P0 保留、readiness=信息不足
Test 03  品类已知 + 库内匹配 + 规格无开放选项 → Final Selection=Confirmed（最终商业确认）
Test 04  近似数量（about 2,000）不降低质量评分（与精确数量逐维一致）；认证 interested 不升 P0
"""
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from main import build_engine, analyze

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


# ---- Test 01：品类已知 + 库无匹配（补丁 03 核心场景） ----
TEST_01_TEXT = """Subject: RFQ - Stainless Steel Water Bottle

Hi,

We are interested in your Stainless Steel Water Bottle, 500ml or 750ml, not sure yet.

Please send us your catalog with MOQ, FOB price and lead time for about 3,000 pcs.

Best regards,
Daniel
GreenLife Import GmbH
Germany"""


def run_test_01(engine):
    print("\n【Test 01 · Stainless Steel Water Bottle / 500ml or 750ml / ~3,000 pcs / FOB / Catalog】")
    rep = analyze(TEST_01_TEXT, *engine)
    ins = rep.get("insight") or {}
    req = ins.get("inquiry") or {}
    qr = ins.get("quotation_readiness") or {}
    hier = (ins.get("product_hierarchy") or {})
    levels = {l["level"]: l for l in hier.get("levels", [])}
    sem = {s["field"]: s for s in (ins.get("requirement_semantics") or [])}
    acts = ins.get("next_actions") or []
    draft = rep.get("draft") or ""
    plan = rep.get("reply_plan") or {}

    check("Test 01 · Product Category = Confirmed（客户原话明确品类，不降级 Unknown）",
          req.get("product_category") == "confirmed", str(req.get("product_category")))
    check("Test 01 · 层级 category 值 = Stainless Steel Water Bottle（客户原话，无 your 前缀）",
          (levels.get("category") or {}).get("value") == "Stainless Steel Water Bottle",
          str((levels.get("category") or {}).get("value")))
    check("Test 01 · 层级 specification = 部分确认（500ml / 750ml）",
          (levels.get("specification") or {}).get("state") == "partially_confirmed"
          and "500ml" in str((levels.get("specification") or {}).get("value")),
          f"{(levels.get('specification') or {}).get('state')} / "
          f"{(levels.get('specification') or {}).get('value')}")
    check("Test 01 · Model = Unknown（客户未给型号；品类已知所以不是 N/A）",
          req.get("model") == "unknown", str(req.get("model")))
    check("Test 01 · Final Selection = Pending Confirmation（待确认，不是 Unknown）",
          req.get("final_selection") == "pending_confirmation",
          str(req.get("final_selection")))
    check("Test 01 · Quantity = 部分确认 + 标注「初始/大概」（不降级 Unknown）",
          req.get("quantity") == "partially_confirmed"
          and "初始/大概" in (sem.get("quantity") or {}).get("note", ""),
          str(req.get("quantity")))
    check("Test 01 · Trade Term (FOB) = Confirmed",
          req.get("incoterm") == "confirmed"
          and (sem.get("incoterm") or {}).get("value") == "FOB",
          f"{req.get('incoterm')} / {(sem.get('incoterm') or {}).get('value')}")
    check("Test 01 · Quote Readiness = 可初步报价（preliminary_quote_ready）",
          qr.get("quotation_readiness_status") == "preliminary_quote_ready",
          str(qr.get("quotation_readiness_status")))
    check("Test 01 · 无 P0 动作（非阻塞信息不升 P0）",
          all(a.get("priority") != "P0" for a in acts),
          "P0 数 = " + str(sum(1 for a in acts if a.get("priority") == "P0")))
    rec = next((a for a in acts if a.get("action") in
                ("recommend_products", "match_closest_product")), None)
    check("Test 01 · 下一步动作 = 查看产品库并匹配候选产品（不是确认产品类别/索取产品）",
          rec is not None and "查看产品库并匹配候选产品" in (rec.get("reason") or ""),
          (rec or {}).get("reason", "（无匹配动作）")[:60])
    check("Test 01 · 草稿不反问产品类别（which/what product category）",
          not re.search(r"(which|what)\s+product\s+categor", draft, re.I))
    check("Test 01 · 缺失检测为智能追问（引用客户品类原话，不泛泛问什么产品）",
          any(g.get("key") == "product_spec" and g.get("smart")
              and "Stainless Steel Water Bottle" in g.get("question", "")
              for g in rep.get("gaps", [])))
    check("Test 01 · 一致性检查全部通过",
          all(c["ok"] for c in rep.get("consistency", [])),
          f"{sum(1 for c in rep.get('consistency', []) if not c['ok'])} 处冲突")
    check("Test 01 · 客户明确需求 catalog 已被草稿回应",
          "catalog" in (plan.get("explicit_requests") or [])
          and re.search(r"catalog", draft, re.I))


# ---- Test 02：品类完全未知（回归第五轮行为，补丁不得误伤） ----
TEST_02_TEXT = """Hi,

We are Global Source Ltd, a Germany-based sourcing company.

We are interested in your products and would like to purchase around 3,000 pcs.

Please send us your best price, MOQ and fastest delivery time.

Please also send your latest catalog and product details.

Best regards,
Michael
Purchasing Manager
Global Source Ltd.
Germany"""


def run_test_02(engine):
    print("\n【Test 02 · 品类完全未知（回归：product_category P0 保留）】")
    rep = analyze(TEST_02_TEXT, *engine)
    ins = rep.get("insight") or {}
    req = ins.get("inquiry") or {}
    qr = ins.get("quotation_readiness") or {}
    hier = {l["level"]: l for l in (ins.get("product_hierarchy") or {}).get("levels", [])}
    plan = rep.get("reply_plan") or {}

    check("Test 02 · Product Category = Unknown（确实没说 → 如实标 Unknown）",
          req.get("product_category") == "unknown", str(req.get("product_category")))
    check("Test 02 · 层级 Model / Final Selection = N/A（品类未知时层级前置未定）",
          (hier.get("model") or {}).get("state") == "not_applicable"
          and (hier.get("final_selection") or {}).get("state") == "not_applicable",
          f"{(hier.get('model') or {}).get('state')} / "
          f"{(hier.get('final_selection') or {}).get('state')}")
    check("Test 02 · Quote Readiness = 信息不足",
          qr.get("quotation_readiness_status") == "insufficient_info",
          str(qr.get("quotation_readiness_status")))
    check("Test 02 · 首轮追问 = product_category（第五轮行为保留）",
          (plan.get("match_state") == "INSUFFICIENT_INFORMATION"
           and (plan.get("selected") or [{}])[0].get("field") == "product_category"),
          f"{plan.get('match_state')} / "
          f"{(plan.get('selected') or [{}])[0].get('field')}")


# ---- Test 03：品类已知 + 库内匹配 + 规格无开放选项 → Final Selection = Confirmed ----
TEST_03_TEXT = """Hello,

We are looking for 3000 pcs neoprene swim caps for our swim school.
Please quote your best price and MOQ. We also need custom logo printing.

Best regards,
Anna
ABC Trading GmbH
Germany"""


def run_test_03(engine):
    print("\n【Test 03 · 品类已知 + 库内匹配 → Final Selection = Confirmed】")
    rep = analyze(TEST_03_TEXT, *engine)
    ins = rep.get("insight") or {}
    req = ins.get("inquiry") or {}
    qr = ins.get("quotation_readiness") or {}
    hier = {l["level"]: l for l in (ins.get("product_hierarchy") or {}).get("levels", [])}
    acts = ins.get("next_actions") or []

    check("Test 03 · 库内匹配到产品", bool(rep.get("matches")),
          "、".join(m.get("name", "") for m in rep.get("matches", [])[:2]))
    check("Test 03 · Product Category = Confirmed",
          req.get("product_category") == "confirmed", str(req.get("product_category")))
    check("Test 03 · Final Selection = Confirmed（最终商业确认：库匹配 + 规格无开放选项）",
          req.get("final_selection") == "confirmed"
          and (hier.get("final_selection") or {}).get("state") == "confirmed",
          f"{req.get('final_selection')} / {(hier.get('final_selection') or {}).get('state')}")
    check("Test 03 · Quote Readiness = 可正式报价",
          qr.get("quotation_readiness_status") == "ready_for_quotation",
          str(qr.get("quotation_readiness_status")))
    check("Test 03 · 无 P0 动作（信息齐全时不制造阻塞）",
          all(a.get("priority") != "P0" for a in acts))


# ---- Test 04：近似数量不降质量评分 + 认证 interested 不升 P0 ----
TEST_04A_TEXT = """Hello,

We would like to order about 2,000 pcs silicone swim caps for the EU market.
Do you have CE and RoHS? We may need certificates for our retail chain.

Please send your quotation and catalog.

Best regards,
Pierre
France Retail Group
France"""

TEST_04B_TEXT = TEST_04A_TEXT.replace("about 2,000 pcs", "2,000 pcs")


def run_test_04(engine):
    print("\n【Test 04 · 近似数量不降质量评分 + 认证 interested 不升 P0】")
    rep_a = analyze(TEST_04A_TEXT, *engine)
    rep_b = analyze(TEST_04B_TEXT, *engine)
    lead_a, lead_b = rep_a["lead"], rep_b["lead"]
    ins_a = rep_a.get("insight") or {}
    req_a = ins_a.get("inquiry") or {}
    sem_a = {s["field"]: s for s in (ins_a.get("requirement_semantics") or [])}
    dim = lambda lead, key: next((d["score"] for d in lead.get("dims", [])
                                  if d["key"] == key), None)

    check("Test 04 · 近似数量：quantity = 部分确认（不是 Unknown / 也不是 confirmed）",
          req_a.get("quantity") == "partially_confirmed", str(req_a.get("quantity")))
    check("Test 04 · 近似数量：语义清单标注「初始/大概」且带「约」值",
          "初始/大概" in (sem_a.get("quantity") or {}).get("note", "")
          and "约" in str((sem_a.get("quantity") or {}).get("value") or ""),
          f"{(sem_a.get('quantity') or {}).get('value')}")
    check("Test 04 · 综合分一致（近似数量 vs 精确数量）",
          lead_a.get("overall_score") == lead_b.get("overall_score"),
          f"{lead_a.get('overall_score')} vs {lead_b.get('overall_score')}")
    check("Test 04 · 采购意向分一致（约数照常 +30，不降档）",
          dim(lead_a, "intent") == dim(lead_b, "intent"),
          f"{dim(lead_a, 'intent')} vs {dim(lead_b, 'intent')}")
    check("Test 04 · 订单价值分一致",
          dim(lead_a, "volume") == dim(lead_b, "volume"),
          f"{dim(lead_a, 'volume')} vs {dim(lead_b, 'volume')}")
    check("Test 04 · 等级一致（质量评分不被约数拖累）",
          lead_a.get("grade") == lead_b.get("grade"),
          f"{lead_a.get('grade')} vs {lead_b.get('grade')}")
    check("Test 04 · 认证 interested → 无 P0 动作（两条均不升 P0）",
          all(a.get("priority") != "P0" for a in (ins_a.get("next_actions") or [])
              + (rep_b.get("insight") or {}).get("next_actions", []) or []),
          "")
    oc = next((a for a in (ins_a.get("next_actions") or [])
               if a.get("action") == "offer_certifications"), None)
    check("Test 04 · 认证动作 = 提供我方认证清单（P2，不是追问）",
          oc is not None and oc.get("priority") == "P2",
          f"{(oc or {}).get('priority')}")
    check("Test 04 · 认证不进入报价阻塞项（interested ≠ required）",
          not any(b.get("field") == "certification"
                  for b in (ins_a.get("quotation_readiness") or {})
                  .get("blockers_preliminary", [])
                  + (ins_a.get("quotation_readiness") or {}).get("blockers_formal", [])))


def main():
    print("=" * 66)
    print("第五轮第二次优化 · 补丁 03 回归测试：需求语义层级（离线规则模式）")
    print("=" * 66)
    engine = build_engine("rule")
    run_test_01(engine)
    run_test_02(engine)
    run_test_03(engine)
    run_test_04(engine)
    print("\n" + "=" * 66)
    print(f"补丁 03 回归测试结果：{_PASS}/{_PASS + _FAIL} 项通过"
          + ("　✅ 全部通过" if _FAIL == 0 else f"　❌ 失败 {_FAIL} 项"))
    print("=" * 66)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
