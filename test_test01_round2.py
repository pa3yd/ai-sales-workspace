# -*- coding: utf-8 -*-
"""
TEST01 FINAL OPTIMIZATION · Round 2 回归测试 A-J
==================================================
压力测试 01：NordHaus Electronics GmbH（德国）5,000 pcs Wireless ANC Earbuds
——需求完整度极高（12/12），但公司产品库无对应 SKU。

本文件验证的硬性行为（与 spec 第 21 节一一对应）：
  TEST A  详细规格已给 → 绝不出现"泛化规格/产品"追问（品类/型号已明确，只允许 smart 追问）
  TEST B  EMAIL + 发件人邮箱已知 → 永不追问邮箱 / 联系方式
  TEST C  官网未知 → 不产生追问、不阻塞报价准备度
  TEST D  无"我方样品能力"事实 → 草稿不做"可安排/免费/可提供样品"承诺（只能确认随报价核实）
  TEST E  需求完整度 HIGH + 产品匹配 UNRESOLVED → 内部匹配先行（不把供应缺口退回给客户）
  TEST F  高价值 + 无内部 SKU → 优先级不因产品匹配风险被过度拉低（product_match 权重已隔离 .10）
  TEST G  InquiryStatus（业务状态机）与 DealStage（销售阶段机）双机并行，互不耦合
  TEST H  未知的档案类字段（公司规模/年采购量/渠道…）不拉低报价准备度（除非销售阻塞）
  TEST I  首轮问题数 1-3 且只问真正阻塞下一步的信息（P0 阻塞，P1/P2 不进首轮问句）
  TEST J  AI 摘要 ≤1-2 行、只陈述事实、不与正文重复堆叠

用法：python test_test01_round2.py   （离线规则模式，不消耗 API）
不修改 TEST01 输入原文。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from main import build_engine, analyze
from agent.reply_strategy import count_questions
from agent.facts import validate_reply_commitments
from agent.insight import build_ai_summary  # noqa: F401（确保摘要函数可达）

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


# ---------- TEST01 输入（原文照录，禁止修改） ----------
TEST01_TEXT = """From: Michael Weber <m.weber@nordhaus-electronics.de>
To: sales@ourcompany.com
Subject: RFQ - 5,000 pcs Wireless ANC Earbuds (TWS) for Germany

Dear Sir or Madam,

We are NordHaus Electronics GmbH, an electronics distributor/retailer in Germany.
I am Michael Weber, Purchasing Manager.

We are looking to place an order for 5,000 pcs of Wireless ANC Earbuds (TWS) for
the German market. Our detailed requirements are as follows:

- Bluetooth 5.4
- Active Noise Cancelling (ANC)
- Minimum battery life of 40 hours
- USB-C charging
- Colors: Black and White
- Custom logo printing on the earbuds and charging case
- Customized packaging
- CE and RoHS certification required

Target price: USD 8.50 - 9.50 per unit, FOB Shenzhen.
Payment terms: 30% deposit, 70% balance before shipment.

We kindly request a sample for evaluation before mass production. Provided the
sample is approved, we expect to place the first order within 30 days after
sample approval.

Could you please send us your quotation within 24 hours?

Best regards,
Michael Weber
Purchasing Manager
NordHaus Electronics GmbH
Hamburg, Germany
m.weber@nordhaus-electronics.de"""

# 泛化追问黑名单：只要出现这些句式就算"泛化追问"（TEST A 禁止）
_GENERIC_RE = [
    re.compile(r"which\s+(model|specification|spec|variant|item|type)\b", re.I),
    re.compile(r"what\s+(model|specification|variant|type|product)\b", re.I),
    re.compile(r"which\s+product\s+(category|type|are\s+you\s+interested)", re.I),
    re.compile(r"what\s+(products?|items?|range)\s+(are\s+you|do\s+you)", re.I),
]

# 样品能力承诺黑名单（TEST D 禁止）
_SAMPLE_PROMISE_RE = [
    re.compile(r"\b(we\s+can\s+arrange|can\s+arrange)\b[^.]{0,40}\bsamples?\b", re.I),
    re.compile(r"\bfree\s+samples?\b", re.I),
    re.compile(r"\bsamples?\s+are\s+available\b", re.I),
    re.compile(r"\b(send|ship|provide|offer)\s+(you\s+)?(free\s+)?samples?\b", re.I),
]

# 档案类字段（TEST H：未知时只影响档案完整度，不阻塞报价）
_PROFILE_FIELDS = {"company_scale", "annual_volume", "sales_channel",
                   "competitor", "purchase_cycle", "website", "contact_phone"}


def _no_generic(txt):
    return not any(r.search(txt or "") for r in _GENERIC_RE)


# ---------------------------------------------------------------- TEST A
def run_test_a(rep):
    print("\n【TEST A · 详细规格已给 → 不出现泛化规格/产品追问】")
    plan = rep.get("reply_plan") or {}
    draft = rep.get("draft") or ""
    ins = rep.get("insight") or {}
    req = ins.get("inquiry") or {}
    hier = {l["level"]: l for l in
            (ins.get("product_hierarchy") or {}).get("levels", [])}
    gaps = rep.get("gaps") or []

    check("A1 · 规格层已确认（Bluetooth/ANC/电池…被识别，不视为 Unknown）",
          (hier.get("specification") or {}).get("state") == "confirmed",
          str((hier.get("specification") or {}).get("value")))
    check("A2 · 品类 = Confirmed（客户已点名 Wireless ANC Earbuds）",
          req.get("product_category") == "confirmed", str(req.get("product_category")))
    sel_q = " ".join(q.get("question", "") for q in (plan.get("selected") or []))
    check("A3 · 草稿无泛化规格/产品追问",
          _no_generic(draft), draft[:80].replace("\n", " "))
    check("A4 · 追问计划无泛化规格/产品问题",
          _no_generic(sel_q), sel_q[:100])
    smart = [g for g in gaps if g.get("key") == "product_spec"]
    check("A5 · 若有规格缺口，只能是引用客户原话的 smart 追问（非模板句）",
          all(g.get("smart") or "Wireless ANC Earbuds" in g.get("question", "")
              for g in smart) if smart else True,
          (smart[0].get("question", "")[:80] if smart else "（无规格缺口）"))
    check("A6 · 不做'品类/型号完全未知'处理：报价准备度 ≠ NOT_READY",
          (ins.get("quotation_readiness_alias") or "") != "NOT_READY",
          str(ins.get("quotation_readiness_alias")))


# ---------------------------------------------------------------- TEST B
def run_test_b(rep):
    print("\n【TEST B · 发件邮箱已知 → 永不追问邮箱/联系方式】")
    info = rep.get("extracted") or {}
    gaps = rep.get("gaps") or []
    plan = rep.get("reply_plan") or {}
    draft = rep.get("draft") or ""
    sel = plan.get("selected") or []

    check("B1 · 已提取到发件人邮箱",
          (info.get("email") or "").strip() == "m.weber@nordhaus-electronics.de",
          str(info.get("email")))
    check("B2 · 缺失清单不含 email / 联系方式",
          not any(g.get("key") in ("email", "contact", "contact_phone")
                  for g in gaps),
          str([g.get("key") for g in gaps]))
    check("B3 · 追问计划不索取邮箱/联系方式",
          all(q.get("field") not in ("email", "contact", "contact_phone")
              for q in sel),
          str([q.get("field") for q in sel]))
    check("B4 · 草稿不出现 'your email / email address / how can we reach'",
          not re.search(r"your\s+e-?mail|e-?mail\s+address|how\s+can\s+we\s+reach"
                        r"|what('| i)s\s+your\s+e-?mail", draft, re.I))


# ---------------------------------------------------------------- TEST C
def run_test_c(rep, rep_no_web):
    print("\n【TEST C · 官网未知 → 不追问、不阻塞】")
    draft = rep.get("draft") or ""
    qr = rep.get("insight") or {}
    web_words = re.compile(r"website|web\s*site|官网|homepage", re.I)

    info2 = rep_no_web.get("extracted") or {}
    check("C1 · 变体（gmail 发件）确实无官网字段",
          not (info2.get("website") or "").strip(), str(info2.get("website")))
    bad_gaps = [g for g in (rep_no_web.get("gaps") or [])
                if web_words.search(g.get("question", ""))
                or g.get("key") == "website"]
    check("C2 · 官网未知 → 缺失清单不追问 website",
          not bad_gaps, str([g.get("key") for g in bad_gaps]))
    bad_sel = [q for q in ((rep_no_web.get("reply_plan") or {}).get("selected") or [])
               if q.get("field") == "website"
               or web_words.search(q.get("question", ""))]
    check("C3 · 官网未知 → 追问计划不索取 website",
          not bad_sel, str([q.get("field") for q in bad_sel]))
    blockers = ((qr.get("quotation_readiness") or {}).get("blockers_preliminary") or []) \
        + ((qr.get("quotation_readiness") or {}).get("blockers_formal") or [])
    check("C4 · 官网未知/未提 → 不进入报价阻塞项",
          not any(web_words.search(b.get("reason", ""))
                  or b.get("field") == "website" for b in blockers))
    check("C5 · 草稿不询问官网/主页",
          not web_words.search(draft))


# ---------------------------------------------------------------- TEST D
def run_test_d(rep):
    print("\n【TEST D · 无'我方样品能力'事实 → 不做可提供样品承诺】")
    draft = rep.get("draft") or ""
    info = rep.get("extracted") or {}
    issues = rep.get("draft_issues") or []
    vc = validate_reply_commitments(draft, TEST01_TEXT, info, None, matches=[])

    check("D1 · 草稿校验 0 问题（含样品承诺自检）", len(issues) == 0,
          "；".join(issues[:2]))
    check("D2 · 草稿无任何'可安排/免费/可提供样品'承诺句式",
          not any(r.search(draft) for r in _SAMPLE_PROMISE_RE))
    check("D3 · 样品回应 = 随报价一起核实（不承诺免费/不承诺可发）",
          re.search(r"sample availability, sample cost and the shipping"
                    r" arrangement will be confirmed together with the quotation",
                    draft, re.I) is not None,
          "（找不到核实式样品话术）")
    check("D4 · validate_reply_commitments 通过（无交期/价格/样品承诺）",
          len(vc) == 0, "；".join(vc[:2]))


# ---------------------------------------------------------------- TEST E
def run_test_e(rep):
    print("\n【TEST E · 完整度 HIGH + 匹配 UNRESOLVED → 内部匹配先行】")
    ins = rep.get("insight") or {}
    comp = ins.get("requirement_completeness") or {}
    pm = ins.get("product_match") or {}
    qr = ins.get("quotation_readiness") or {}
    acts = ins.get("next_actions") or []

    check("E1 · 需求完整度 = HIGH（客户侧信息 12/12）",
          comp.get("level") == "HIGH" and comp.get("score") == comp.get("total"),
          f"{comp.get('level')} {comp.get('score')}/{comp.get('total')}")
    check("E2 · 产品匹配 = UNRESOLVED（供应侧，独立于完整度）",
          pm.get("status") == "UNRESOLVED",
          str(pm.get("status")))
    check("E3 · 内部匹配动作排第一且为 P1（不是向客户退回完整性问题）",
          acts and acts[0].get("action") == "match_closest_product"
          and acts[0].get("priority") == "P1",
          str((acts[0] if acts else {}).get("action")))
    check("E4 · 无 P0 动作（内部缺口不伪装成客户阻塞）",
          all(a.get("priority") != "P0" for a in acts),
          "P0 数 = " + str(sum(1 for a in acts if a.get("priority") == "P0")))
    check("E5 · 报价准备度不被供应缺口拉低（PARTIALLY_READY，可初步报价）",
          ins.get("quotation_readiness_alias") == "PARTIALLY_READY"
          and not qr.get("blockers_preliminary"),
          f"{ins.get('quotation_readiness_alias')} / "
          f"prelim={len(qr.get('blockers_preliminary') or [])}")
    note = (ins.get("product_matching_note") or "") + \
           (pm.get("status_cn") or "")
    check("E6 · 口径 = 内部检索候选 → 需要时才问参考型号（不编造、不泛问）",
          "匹配最接近" in (acts[0].get("action_cn") or "")
          or "候选" in note or "暂无对应" in note)


# ---------------------------------------------------------------- TEST F
def run_test_f(rep):
    print("\n【TEST F · 高价值客户 + 无内部 SKU → 优先级不被过度拉低】")
    lead = rep.get("lead") or {}
    dim = lambda key: next((d["score"] for d in lead.get("dims", [])
                            if d.get("key") == key), None)
    w = lead.get("weights") or {}

    check("F1 · product_match 权重已隔离（≤0.10，不主导综合分）",
          abs(float(w.get("product_match", 1)) - 0.10) < 1e-9,
          f"weight={w.get('product_match')}")
    check("F2 · 匹配维度虽低分（无 SKU 如实低分），综合分仍 ≥ 60",
          (lead.get("overall_score") or 0) >= 60,
          f"{lead.get('overall_score')}（product_match={dim('product_match')}）")
    check("F3 · 优先级等级 = A/B（不因缺 SKU 跌到 C/D）",
          str(lead.get("grade")) in ("A", "B"), str(lead.get("grade")))
    check("F4 · 客户价值维度（意向/数量）独立成立，不被供应缺口拖累",
          (dim("intent") or 0) >= 60 and (dim("volume") or 0) >= 60,
          f"intent={dim('intent')} volume={dim('volume')}")


# ---------------------------------------------------------------- TEST G
def run_test_g(rep):
    print("\n【TEST G · InquiryStatus 与 DealStage 双状态机并存】")
    import workbench.workflow as wf
    import workbench.sales_crm as crm

    biz_exclusive = {"READY_TO_REPLY", "ANALYZING", "NEEDS_INFO", "REPLIED",
                     "FOLLOW_UP", "READY_FOR_QUOTE", "ON_HOLD"}
    stage_exclusive = {"QUALIFIED", "REQUIREMENT_CONFIRMED", "SAMPLE",
                       "NEGOTIATION", "PO_PENDING"}
    check("G1 · 两套状态机是不同枚举（各有专属状态）",
          biz_exclusive <= set(wf.BIZ_CN)
          and stage_exclusive <= set(crm.STAGES),
          f"biz专属⊆BIZ_CN={biz_exclusive <= set(wf.BIZ_CN)}，"
          f"stage专属⊆STAGES={stage_exclusive <= set(crm.STAGES)}")
    # NordHaus 场景：报价准备度 PARTIALLY_READY、无 P0 阻塞 → 业务状态 READY_TO_REPLY
    biz = wf.derive_biz("待处理", [], "preliminary_quote_ready")
    check("G2 · InquiryStatus = READY_TO_REPLY（待回复，由业务机派生）",
          biz == "READY_TO_REPLY", str(biz))
    check("G3 · 同时刻 DealStage 仍可为 NEW(New Inquiry)，两者合法共存",
          "NEW" in crm.STAGES and biz != "NEW",
          f"biz={biz} stage=NEW")
    # 阶段机从 NEW→QUALIFIED 只看自身准入事实，不受业务状态机约束
    errs = crm.transition_errors(
        "NEW", "QUALIFIED",
        {"customer_id": "c-1", "product": "TWS", "quantity": 5000})
    check("G4 · DealStage 推进独立：NEW→QUALIFIED 满足准入即可（与 biz 无关）",
          errs == [], "；".join(errs[:3]))
    check("G5 · 业务机转换规则独立于销售阶段（READY_TO_REPLY→REPLIED 合法）",
          wf.can_transition("READY_TO_REPLY", "REPLIED")
          and wf.can_transition("READY_TO_REPLY", "READY_FOR_QUOTE"),
          "READY_TO_REPLY 出边正常")


# ---------------------------------------------------------------- TEST H
def run_test_h(rep):
    print("\n【TEST H · 未知档案字段不拉低报价准备度】")
    gaps = rep.get("gaps") or []
    qr = rep.get("quotation_readiness") or {}
    blockers = (qr.get("blockers_preliminary") or []) \
        + (qr.get("blockers_formal") or [])
    prof_gaps = [g for g in gaps if g.get("key") in _PROFILE_FIELDS]

    check("H1 · 档案类缺口只允许出现在 C 级（可后续确认）",
          all(g.get("level") == "C" for g in prof_gaps),
          str([(g.get("key"), g.get("level")) for g in prof_gaps]))
    check("H2 · 档案类缺口不进任何报价阻塞项",
          not any(b.get("field") in _PROFILE_FIELDS for b in blockers),
          str([b.get("field") for b in blockers]))
    check("H3 · 档案缺失不降准备度（仍为可初步报价）",
          (rep.get("insight") or {}).get("quotation_readiness_alias")
          == "PARTIALLY_READY",
          str((rep.get("insight") or {}).get("quotation_readiness_alias")))
    check("H4 · 产品规格/认证等真实阻塞（若存在）才进正式报价前确认项",
          all(b.get("field") in ("certification", "final_selection",
                                 "product_spec", "specification")
              for b in blockers),
          str([b.get("field") for b in blockers]))


# ---------------------------------------------------------------- TEST I
def run_test_i(rep):
    print("\n【TEST I · 首轮问题 1-3 个，且只问 P0 阻塞项】")
    plan = rep.get("reply_plan") or {}
    draft = rep.get("draft") or ""
    sel = plan.get("selected") or []
    cls = plan.get("question_classification") or {}

    check("I1 · 追问计划选中问题数 1-3",
          1 <= len(sel) <= 3, f"selected={len(sel)}")
    check("I2 · 草稿实际问句 ≤ 3",
          count_questions(draft) <= 3, f"问句={count_questions(draft)}")
    check("I3 · 阻塞分类 ≤ 3 项且全部标记 BLOCKING",
          0 <= len(cls.get("blocking") or []) <= 3
          and all(q.get("question_class") == "BLOCKING"
                  for q in (cls.get("blocking") or [])),
          str(len(cls.get("blocking") or [])))
    nonblock_asked = [q for q in sel
                      if q.get("field") in
                      {x.get("field") for x in cls.get("non_blocking") or []}]
    check("I4 · 非 P0（P1/P2/档案）信息不进入首轮问句",
          not nonblock_asked,
          str([q.get("field") for q in nonblock_asked]))
    check("I5 · 已提供信息不被重复索取（邮箱/数量/规格/认证均不回问）",
          all(q.get("field") not in
              ("email", "quantity", "certification", "destination",
               "incoterm", "payment")
              for q in sel),
          str([q.get("field") for q in sel]))


# ---------------------------------------------------------------- TEST J
def run_test_j(rep):
    print("\n【TEST J · AI 摘要 1-2 行、事实型、不重复堆叠】")
    ins = rep.get("insight") or {}
    s = ins.get("ai_summary") or ""
    info = rep.get("extracted") or {}

    check("J1 · 摘要非空且由 build_ai_summary 产出",
          isinstance(s, str) and len(s) > 20, f"len={len(s)}")
    check("J2 · 无换行/无 Markdown 结构（≤2 行、纯文本）",
          "\n" not in s and "**" not in s and "#" not in s and "•" not in s
          and "- " not in s)
    check("J3 · 长度受控（≤200 字），不是长分析报告",
          len(s) <= 200, f"len={len(s)}")
    check("J4 · 摘要覆盖核心事实（谁/什么/多少/价格/时间线）",
          "NordHaus" in s and "5,000" in s and "Wireless ANC Earbuds" in s
          and "8.50" in s,
          s[:90])
    check("J5 · 摘要不重复堆叠同一条结论（'暂无对应'只出现一次）",
          s.count("暂无对应") <= 1, f"count={s.count('暂无对应')}")
    check("J6 · 摘要不替代正文：未以'本询盘详细分析'式冗长开头",
          not re.match(r"\s*(本询盘|以下是|详细|分析结果|AI\s+分析)", s)
          and "insight" not in s[:20].lower())
    check("J7 · 规则模式的 extractor 摘要与 AI 摘要不双份堆叠展示",
          not ((info.get("summary") or "").strip()
               and info["summary"] == s),
          "（extractor summary 为空或以独立短句展示）")


# ---------------- TEST01 核心验收（与 spec 预期邮件行为一致） ----------------
def run_core_acceptance(rep):
    print("\n【TEST01 核心验收 · 预期邮件行为】")
    ins = rep.get("insight") or {}
    req = ins.get("inquiry") or {}
    sem = {x["field"]: x for x in (ins.get("requirement_semantics") or [])}
    qr = ins.get("quotation_readiness") or {}
    draft = rep.get("draft") or ""
    cert = ins.get("certification") or {}

    check("CORE1 · 数量 5,000 语义 = Confirmed（精确数，不误判部分确认）",
          req.get("quantity") == "confirmed"
          and "5,000" in str((sem.get("quantity") or {}).get("value")),
          f"{req.get('quantity')} / {(sem.get('quantity') or {}).get('value')}")
    check("CORE2 · 认证 = required（CE and RoHS certification required）",
          cert.get("certification_status") == "required",
          str(cert.get("certification_status")))
    check("CORE3 · 草稿不夹带产品库无关产品（无 swim/goggle/泳具串扰）",
          not re.search(r"\b(swim|swimming|goggle|neoprene|泳)\w*", draft, re.I),
          draft[:100].replace("\n", " "))
    check("CORE4 · 草稿对 'within 24 hours 报价' 不作无 SLA 承诺",
          not re.search(r"within\s+24\s+hours", draft, re.I)
          or re.search(r"(will|can)\s+(send|provide|submit)", draft, re.I) is None)
    check("CORE5 · 一致性检查全部通过",
          all(c.get("ok") for c in (rep.get("consistency") or [])),
          f"{sum(1 for c in rep.get('consistency') or [] if not c.get('ok'))} 处冲突")
    check("CORE6 · 无需人工复核（草稿校验 0 问题）",
          rep.get("human_review_required") is False
          and not (rep.get("draft_issues") or []),
          "；".join((rep.get("draft_issues") or [])[:2]))
    check("CORE7 · 草稿非空且称呼正确（Michael Weber）",
          bool(draft.strip()) and "Michael Weber" in draft)


def main():
    print("=" * 70)
    print("TEST01 压力测试 · FINAL OPTIMIZATION 回归 A-J（离线规则模式）")
    print("=" * 70)
    engine = build_engine("rule")
    rep = analyze(TEST01_TEXT, *engine)

    # TEST C 变体：发件邮箱换成 gmail（官网无从推断 → 验证'官网未知不追问'）
    rep_no_web = analyze(
        TEST01_TEXT.replace("m.weber@nordhaus-electronics.de",
                            "m.weber@gmail.com"), *engine)

    run_core_acceptance(rep)
    run_test_a(rep)
    run_test_b(rep)
    run_test_c(rep, rep_no_web)
    run_test_d(rep)
    run_test_e(rep)
    run_test_f(rep)
    run_test_g(rep)
    run_test_h(rep)
    run_test_i(rep)
    run_test_j(rep)

    print("\n" + "=" * 70)
    print(f"Round 2 回归测试结果：{_PASS}/{_PASS + _FAIL} 项通过"
          + ("　✅ 全部通过" if _FAIL == 0 else f"　❌ 失败 {_FAIL} 项"))
    print("=" * 70)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
