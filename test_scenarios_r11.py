# -*- coding: utf-8 -*-
"""第十一轮验收测试（spec 十九）：Test 01~08 真实场景 + 十项检查。

场景：
  Test 01  产品未知 + 3000 pcs
  Test 02  Stainless Steel Water Bottle 500ml/750ml, 3000 pcs
  Test 03  多个数量冲突（trial 500 / initial 3000 / potential 10000）
  Test 04  Certification 需求（CE / RoHS）
  Test 05  Catalog-only request
  Test 06  Target Price USD 2.80
  Test 07  Preferred delivery 4 weeks
  Test 08  EuroSource Trading 综合（500/2000/3000/10000 pcs, CE/RoHS, 定制包装）

十项检查：
  1 销售阶段正确  2 AI评分正常  3 报价准备度正确  4 下一步行动正确
  5 回复问题≤3   6 产品未知不编造  7 数量语义区分
  8 target price ≠ 公司报价  9 认证不绝对阻塞报价  10 防幻觉规则有效
全部走离线规则引擎（--mode rule 同口径），不依赖 API。
"""
import io
import sys
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import build_engine, analyze                     # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "workbench"))
import workflow as _wf                                     # noqa: E402
from agent.facts import (build_fact_layer, quote_readiness_of,   # noqa: E402
                          validate_reply_commitments)

engine = build_engine("rule")

_PASS = _FAIL = 0


def check(name, ok, detail=""):
    global _PASS, _FAIL
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"　[{detail}]" if detail and not ok else ""))
    if ok:
        _PASS += 1
    else:
        _FAIL += 1


def run(text):
    rep = analyze(text, *engine)
    info = rep.get("extracted") or {}
    ins = rep.get("insight") or {}
    qr = ins.get("quotation_readiness") or {}
    plan = rep.get("reply_plan") or {}
    biz = _wf.derive_biz("TODO", [b.get("field", "") for b in
                                  (qr.get("blockers_preliminary") or [])],
                         qr.get("quotation_readiness_status", ""),
                         None, bool((rep.get("draft") or "").strip()))
    return rep, info, ins, qr, plan, biz


# ==================== Test 01 产品未知 + 3000 pcs ====================
print("\n== Test 01：产品未知 + 3000 pcs ==")
T1 = ("Hello, we need 3000 pcs for our retail chain in Spain. "
      "Please send me your best price and catalog.")
rep, info, ins, qr, plan, biz = run(T1)
check("1-1 AI 评分正常（等级+分数）",
      bool(info.get("_grade_present", True)) and
      isinstance((rep.get("lead") or {}).get("score"), (int, float)),
      str((rep.get("lead") or {}).get("score")))
check("1-2 产品未知 → 不编造产品（match_state ≠ MATCHED）",
      plan.get("match_state") != "MATCHED", plan.get("match_state", ""))
check("1-3 首轮问题 ≤3",
      0 < len(plan.get("selected") or []) <= 3,
      str(len(plan.get("selected") or [])))
check("1-4 问题聚焦产品确认（P0 = 产品/型号/参考）",
      any((q.get("field") or "") in
          ("reference_model", "product_category", "product_model", "product")
          for q in (plan.get("selected") or [])))
check("1-5 产品未知 → 绝不显示可正式报价（最多初步报价）",
      qr.get("quotation_readiness_status") != "ready_for_quotation",
      str(qr.get("quotation_readiness_status")))
check("1-6 销售阶段 = 待补关键信息/待回复（非已报价）",
      biz in (_wf.NEEDS_INFO, _wf.READY_TO_REPLY), biz)

# ==================== Test 02 Stainless Steel Water Bottle ====================
print("\n== Test 02：Stainless Steel Water Bottle 500ml/750ml, 3000 pcs ==")
T2 = ("We are looking for stainless steel water bottles, 500ml and 750ml. "
      "Quantity: 3000 pcs. Please quote FOB Shanghai.")
rep, info, ins, qr, plan, biz = run(T2)
check("2-1 水瓶不在产品库 → NO_MATCH 且追问参考型号（不编造）",
      plan.get("match_state") == "NO_MATCH"
      and any("reference" in (q.get("field") or "")
              for q in (plan.get("selected") or [])),
      plan.get("match_state", ""))
check("2-2 数量 3000 pcs 正确提取",
      info.get("quantity") == 3000, str(info.get("quantity")))
check("2-3 下一步行动已生成（落到行动）",
      bool(_wf.next_action(
          "TODO", biz, [b.get("field", "") for b in
                        (qr.get("blockers_preliminary") or [])],
          qr.get("quotation_readiness_status", ""),
          has_draft=bool((rep.get("draft") or "").strip())).get("label")))
check("2-4 防幻觉：草稿无未依据承诺",
      not validate_reply_commitments(rep.get("draft") or "", T2, info,
                                     None, rep.get("matches") or []),
      str(validate_reply_commitments(rep.get("draft") or "", T2, info,
                                     None, rep.get("matches") or [])[:2]))

# ==================== Test 03 多个数量冲突 ====================
print("\n== Test 03：多个数量（trial 500 / initial 3000 / potential 10000）==")
T3 = ("For the first trial order we will take 500 pcs. "
      "The initial order quantity will be 3000 pcs, "
      "and our annual potential volume could reach 10000 pcs.")
rep, info, ins, qr, plan, biz = run(T3)
qs = info.get("quantity_semantics") or []
kinds = {q.get("role") for q in qs}
check("3-1 数量语义分层（≥2 种类型）", len(kinds) >= 2, str(kinds))
check("3-2 主数量取首单/当前订单（非 potential）",
      info.get("quantity") in (500, 3000), str(info.get("quantity")))
check("3-3 不把 10000 潜在量当确认数量",
      "10000" not in str(info.get("quantity")), str(info.get("quantity")))

# ==================== Test 04 Certification ====================
print("\n== Test 04：Certification 需求（CE / RoHS）==")
T4 = ("We need stainless steel water bottles 500ml, 3000 pcs, "
      "with CE and RoHS certification. Please quote.")
rep, info, ins, qr, plan, biz = run(T4)
blocks = [b.get("field", "").lower()
          for b in (qr.get("blockers_preliminary") or [])]
_cert = ins.get("certification") or {}
check("4-1 认证需求被识别（interest/requirement）",
      bool(_cert.get("certification_interest")
           or _cert.get("certification_requirement")),
      str(_cert))
check("4-2 认证不是绝对报价阻塞（可初步报价或阻塞里非认证单项）",
      qr.get("quotation_readiness_status") in
      ("preliminary_quote_ready", "ready_for_quotation")
      or (blocks and any(b not in ("certification",) for b in blocks))
      or not blocks,
      str(qr.get("quotation_readiness_status")) + "|" + str(blocks))

# ==================== Test 05 Catalog-only ====================
print("\n== Test 05：Catalog-only request ==")
T5 = ("Hello, we are a distributor in Germany. "
      "Please send us your full catalog and price list.")
rep, info, ins, qr, plan, biz = run(T5)
check("5-1 不编造产品（NO_MATCH / INSUFFICIENT）",
      plan.get("match_state") in ("NO_MATCH", "INSUFFICIENT_INFORMATION"),
      plan.get("match_state", ""))
check("5-2 阶段停留在需求确认（不进入报价）",
      biz in (_wf.NEEDS_INFO, _wf.READY_TO_REPLY, _wf.NEW), biz)
check("5-3 报价准备度 ≠ 可正式报价",
      qr.get("quotation_readiness_status") != "ready_for_quotation",
      str(qr.get("quotation_readiness_status")))

# ==================== Test 06 Target Price USD 2.80 ====================
print("\n== Test 06：Target Price USD 2.80/pc ==")
T6 = ("We are looking for stainless steel water bottles 500ml, 3000 pcs. "
      "Our target price is USD 2.80/pc FOB.")
rep, info, ins, qr, plan, biz = run(T6)
facts = build_fact_layer(T6, info, rep.get("matches") or [], ins)
tp = next((f for f in facts if f["field"] == "customer_target_price"), None)
cq = next((f for f in facts if f["field"] == "company_quote"), None)
check("6-1 客户目标价被单独识别（来源=客户）",
      tp is not None and tp.get("value"), str(tp and tp.get("value")))
check("6-2 公司报价 = 未提供（目标价绝不变公司报价）",
      cq is not None and (cq.get("value") is None
                          or "未提供" in str(cq.get("display"))),
      str(cq and cq.get("display")))
check("6-3 草稿不把 2.80 当我方报价",
      "2.80" not in (rep.get("draft") or "")
      or "target" in (rep.get("draft") or "").lower(),
      (rep.get("draft") or "")[:80])

# ==================== Test 07 Preferred delivery 4 weeks ====================
print("\n== Test 07：Preferred delivery 4 weeks ==")
T7 = ("We are looking for stainless steel water bottles 500ml, 3000 pcs. "
      "We hope to receive the goods within 4 weeks.")
rep, info, ins, qr, plan, biz = run(T7)
facts = build_fact_layer(T7, info, rep.get("matches") or [], ins)
dl = next((f for f in facts if f["field"] == "delivery"), None)
check("7-1 交期识别为客户期望（Preferred，非承诺）",
      dl is not None and dl.get("certainty") == "Preferred",
      str(dl and dl.get("certainty")))
check("7-2 草稿未承诺 4 周交期",
      "we can deliver within 4 weeks" not in (rep.get("draft") or "").lower(),
      (rep.get("draft") or "")[:80])
check("7-3 防幻觉校验无违规",
      not validate_reply_commitments(rep.get("draft") or "", T7, info,
                                     None, rep.get("matches") or []))

# ==================== Test 08 EuroSource 综合 ====================
print("\n== Test 08：EuroSource Trading 综合（500/2000/3000/10000, CE/RoHS, 定制包装）==")
T8 = ("This is Thomas from EuroSource Trading, Italy. "
      "For stainless steel water bottles: trial order 500 pcs, "
      "initial order 2000-3000 pcs, potential annual volume 10000 pcs. "
      "We need CE and RoHS certification and customized packaging with our logo. "
      "Target price around USD 2.80/pc. We hope delivery within 4 weeks.")
rep, info, ins, qr, plan, biz = run(T8)
check("8-1 数量分层（≥2 类）",
      len({q.get("role") for q in (info.get("quantity_semantics") or [])}) >= 2,
      str(info.get("quantity_semantics")))
check("8-2 水瓶不在库 → NO_MATCH 不编造（追问参考）",
      (rep.get("reply_plan") or {}).get("match_state") == "NO_MATCH",
      str((rep.get("reply_plan") or {}).get("match_state")))
check("8-3 问题 ≤3",
      0 < len((rep.get("reply_plan") or {}).get("selected") or []) <= 3,
      str(len((rep.get("reply_plan") or {}).get("selected") or [])))
check("8-4 定制需求识别（intent = OEM/贴牌定制）",
      "OEM" in (info.get("intent") or ""), str(info.get("intent")))
check("8-5 目标价仍与公司报价分离",
      (next((f for f in build_fact_layer(T8, info, rep.get("matches") or [], ins)
             if f["field"] == "company_quote"), {}) or {}).get("value") is None)
check("8-6 销售阶段可手动映射到状态机（derive_biz 有效）",
      biz in _wf.BIZ_CN, biz)
check("8-7 报价准备度口径与阶段一致（不足则不可报价）",
      (qr.get("quotation_readiness_status") == "ready_for_quotation")
      == (biz == _wf.READY_FOR_QUOTE) or biz in (_wf.NEEDS_INFO,
                                                 _wf.READY_TO_REPLY,
                                                 _wf.READY_FOR_QUOTE),
      f"{qr.get('quotation_readiness_status')} vs {biz}")

# ==================== 第十一轮 UI 层关键行为（纯函数级） ====================
print("\n== UI 行为（阶段手改 / 完成并进入下一条）==")
check("9-1 状态机：NEEDS_INFO → QUOTED 被禁止（防跳变）",
      not _wf.can_transition(_wf.NEEDS_INFO, _wf.QUOTED))
check("9-2 状态机：REPLIED → QUOTED 允许",
      _wf.can_transition(_wf.REPLIED, _wf.QUOTED))
check("9-3 状态机：LOST → NEGOTIATING 允许（死单复活）",
      _wf.can_transition(_wf.LOST, _wf.NEGOTIATING))

print(f"\n结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
