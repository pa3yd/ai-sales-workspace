# -*- coding: utf-8 -*-
"""
PRESSURE TEST 02 — MINIMUM QUESTION STRATEGY 回归测试
======================================================
核心命题：AI 能识别缺失信息 ≠ 该一次性全问。
要求：ASK THE MINIMUM INFORMATION REQUIRED TO ADVANCE THE DEAL.

  TEST02  LOW_COMPLETENESS_HIGH_POTENTIAL（不锈钢水瓶，品类已知但容量/数量缺失）
          → ASK_MINIMUM_BLOCKING_QUESTIONS，绝不盲报、绝不填 CRM 表
  TEST01  HIGH_COMPLETENESS_HIGH_INTENT（NordHaus 无线耳机，完整需求但库内无 SKU）
          → ASK_LESS / ACT_MORE（内部选型先行）—— 回归保证，不得被 TEST02 改坏

断言覆盖（spec TEST02 §14）：
  UNKNOWN ≠ 自动 ASK
  澄清邮件问句 ≤ 3（正常流程）
  PROFILE_ONLY 字段不产生问题
  ASK_LATER 字段不提前出现
  已知发件邮箱永不索取
  早期产品匹配不自动索取付款方式
  无 SLA 支撑时不承诺 24 小时报价
  认证问题上下文化（不机械罗列 CE/EN71/REACH/FDA）

用法：python test_test02_min_questions.py （离线规则模式）
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from main import build_engine, analyze
from agent.reply_strategy import (count_questions, classify_ask,
                                  minimum_info_to_advance, question_sentences,
                                  STAGE_NEW_INQUIRY, STAGE_FORMAL,
                                  STAGE_NEGOTIATION, ASK_NOW, ASK_LATER,
                                  DO_NOT_ASK, INTENT_RFQ, INTENT_CATALOG)
from workbench.gapcheck import build_followup_email
from test_test01_round2 import TEST01_TEXT          # TEST01 原文复用，禁止二次修改

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


# ---------- TEST02 输入：低完整度、高潜力的不锈钢水瓶询盘 ----------
TEST02_TEXT = """From: Julia Schmidt <j.schmidt@globaldrink.de>
To: sales@ourcompany.com
Subject: Inquiry - Stainless Steel Water Bottles

Hello,

We are Global Drink GmbH, a beverage retailer in Germany with 30 stores.

We are looking for stainless steel water bottles for our retail chain.
Please send us your catalog with MOQ and best price.
We may need custom logo printing.

Could you please reply within 24 hours?

Best regards,
Julia Schmidt
Purchasing Manager
Global Drink GmbH
j.schmidt@globaldrink.de"""

# 变体：文本里完全没有任何邮箱（CRM email=UNKNOWN 也不能触发索取）
TEST02_NO_EMAIL_TEXT = """Hello,

We are a trading company from Germany looking for stainless steel water bottles.
Please send us your quotation and catalog.

Best regards,
Daniel
GreenTrade GmbH"""

# 首轮邮件/追问邮件绝不允许出现的"非最小"字段
_FORBIDDEN_EARLY = {"email", "certification", "packaging", "payment",
                    "payment_terms", "incoterm", "delivery", "delivery_time",
                    "company_scale", "annual_volume", "sales_channel",
                    "competitor", "purchase_cycle", "target_price", "website"}
_ALLOWED_ASK_NOW = {"reference_model", "product_spec", "specification",
                    "quantity", "quantity_confirm", "quantity_conflict",
                    "product_category", "product_type", "model_selection",
                    "candidate_confirm", "open_option"}

# 机械式认证罗列（TEST02 §6 禁止）
_MECH_CERT = re.compile(
    r"do\s+you\s+need\s+any\s+specific\s+certification[^?]*"
    r"(CE|EN\s?71|REACH|FDA)", re.I)
_MECH_CERT2 = re.compile(
    r"which\s+certifications\s+do\s+you\s+require[^?]*(CE|EN\s?71|REACH|FDA)", re.I)
# 无 SLA 的时间承诺（TEST02 §11 禁止）
_PROMISE_24 = re.compile(
    r"(we|we'll|we\s+will)[^.]{0,60}?within\s+24\s*hours?", re.I)
_PROMISE_TIME = re.compile(
    r"(we|we'll|we\s+will)[^.]{0,60}?within\s+\d+\s*(hours?|days?)\b", re.I)
# 盲报价痕迹（数字价格 / MOQ 数字 / 交期数字）
_BLIND_QUOTE = re.compile(
    r"(USD\s*\$?|\$)\s?\d[\d,]*|moq[^.\n]{0,15}\d|lead\s*time[^.\n]{0,20}\d"
    r"|our\s+(?:best\s+)?price[^.\n]{0,10}\d", re.I)
_ACK_24 = re.compile(
    r"we\s+have\s+noted\s+your\s+requested\s+timeframe\s+and\s+will\s+prioritize"
    r"\s+the\s+quotation\s+accordingly", re.I)


def _asked_fields(draft, plan):
    sel = plan.get("selected") or []
    qs = [s.lower() for s in question_sentences(draft or "")]
    return [q.get("field") for q in sel], " ".join(qs)


# ---------------------------------------------------------------- TEST02
def run_test02(rep, info_ok=True):
    print("\n【TEST02 · 低完整度 + 高潜力 → 只问最小阻塞信息、不盲报】")
    ins = rep.get("insight") or {}
    comp = ins.get("requirement_completeness") or {}
    plan = rep.get("reply_plan") or {}
    draft = rep.get("draft") or ""
    gaps = rep.get("gaps") or []
    sel_fields, qtext = _asked_fields(draft, plan)
    fu = build_followup_email(gaps, rep.get("extracted") or {}, "ABC Co", ("A", "B"))

    check("T02-1 · 需求完整度 = LOW（<6，属低完整度高潜）",
          comp.get("level") == "LOW" and (comp.get("score") or 99) < 6,
          f"{comp.get('level')} {comp.get('score')}/{comp.get('total')}")
    check("T02-2 · 阶段 = 新询盘/产品匹配（推进信息 = 品类/核心规格/数量）",
          plan.get("advance_stage") == STAGE_NEW_INQUIRY
          and "核心规格" in "".join(
              (plan.get("minimum_info_to_advance") or {})
              .get("required_for_next_action") or []),
          str(plan.get("advance_stage")))
    check("T02-3 · 不盲报：草稿无价格/MOQ/交期数字",
          not _BLIND_QUOTE.search(draft), "")
    check("T02-4 · 草稿问句 ≤ 3（当前为最小阻塞集）",
          count_questions(draft) <= 3, f"问句={count_questions(draft)}")
    check("T02-5 · 计划选中问题数 1-3",
          1 <= len(plan.get("selected") or []) <= 3,
          f"selected={len(plan.get('selected') or [])}")
    check("T02-6 · 选中问题全部 ASK_NOW，且只可能是阻塞字段",
          all(q.get("ask_timing") == ASK_NOW
              and q.get("field") in _ALLOWED_ASK_NOW
              for q in plan.get("selected") or []),
          str([q.get("field") for q in plan.get("selected") or []]))
    check("T02-7 · 邮箱/认证/包装/付款/Incoterm 等非最小字段不进首轮问句",
          not (set(sel_fields) & _FORBIDDEN_EARLY)
          and not any(t in qtext for t in
                      ("email", "certification", "packaging", "payment",
                       "incoterm", "trade term", "annual")),
          str(sel_fields))
    if info_ok:
        check("T02-8 · 已知发件邮箱 → 缺失清单与追问邮件都不索取邮箱",
              all(g.get("key") != "email" for g in gaps)
              and "email" not in (fu or "").lower(),
              str([g.get("key") for g in gaps if g.get("key") == "email"]))
    check("T02-9 · 追问邮件问句 1-3 且无 'within 24 hours' 承诺",
          (lambda qn: 1 <= qn <= 3)(
              len(re.findall(r"^\s*\d+\.\s", (fu or ""), re.M)))
          and "within 24 hours" not in (fu or ""),
          str(len(re.findall(r"^\s*\d+\.\s", (fu or ""), re.M))) + " 项")
    check("T02-10 · 无 SLA 不承诺时间：草稿不出现 will…within…24h 承诺",
          not _PROMISE_24.search(draft) and not _PROMISE_TIME.search(draft))
    check("T02-11 · 客户要求 24h → 只确认收到并优先，不承诺 SLA",
          _ACK_24.search(draft) is not None
          and "we will send you" not in draft.lower(),
          "（含时限确认句）")
    cert_gaps = [g for g in gaps if g.get("key") == "certification"]
    check("T02-12 · 认证问题上下文化：无机械 CE/EN71/REACH/FDA 罗列",
          all(not _MECH_CERT.search(g.get("question", ""))
              and not _MECH_CERT2.search(g.get("question", ""))
              for g in cert_gaps)
          and all(g.get("ask_timing") != ASK_NOW for g in cert_gaps),
          str([(g.get("ask_timing"), g.get("question", "")[:40])
               for g in cert_gaps]))
    profile_only = ((plan.get("question_classification") or {}).get("profile_only") or [])
    check("T02-13 · PROFILE_ONLY 字段不产生问题（不进 selected / 草稿）",
          all(q.get("field") not in sel_fields
              and q.get("ask_timing") == DO_NOT_ASK for q in profile_only),
          str([q.get("field") for q in profile_only]))
    later = [g for g in gaps if g.get("ask_timing") == ASK_LATER]
    check("T02-14 · ASK_LATER 字段不提前出现（不进追问邮件）",
          all(g.get("key") not in (fu or "") for g in later)
          and all(l.get("field") not in sel_fields
                  for l in ((plan.get("question_classification") or {})
                            .get("non_blocking") or [])),
          str([g.get("key") for g in later]))
    unknown_not_asked = [g for g in gaps if g.get("ask_timing") == DO_NOT_ASK]
    check("T02-15 · UNKNOWN ≠ 自动 ASK（存在 UNKNOWN 字段被标 DO_NOT_ASK 且未问）",
          bool(unknown_not_asked)
          and all(g.get("key") not in (fu or "")
                  and g.get("key") not in qtext for g in unknown_not_asked),
          str([g.get("key") for g in unknown_not_asked]))
    # 无盲报 → 报价准备度不能是"可正式报价"
    check("T02-16 · 报价准备度 ≠ READY_FOR_QUOTE（信息不足时如实 NOT_READY）",
          (ins.get("quotation_readiness_alias") or "") != "READY_FOR_QUOTE",
          str(ins.get("quotation_readiness_alias")))


def run_test02_no_email(rep):
    print("\n【TEST02 变体 · 文本无邮箱（CRM email=UNKNOWN 也不索取）】")
    plan = rep.get("reply_plan") or {}
    draft = rep.get("draft") or ""
    gaps = rep.get("gaps") or []
    sel_fields, qtext = _asked_fields(draft, plan)
    fu = build_followup_email(gaps, rep.get("extracted") or {}, "ABC Co", ("A", "B"))
    em = [g for g in gaps if g.get("key") == "email"]
    check("NE-1 · 邮箱缺失被分类为 DO_NOT_ASK（渠道可回，不索取）",
          all(g.get("ask_timing") == DO_NOT_ASK for g in em)
          and "email" not in sel_fields
          and not re.search(r"your\s+e-?mail|email\s+address|share\s+your\s+e-?mail",
                            draft, re.I)
          and not re.search(r"your\s+e-?mail|email\s+address", (fu or ""), re.I),
          str([(g.get("key"), g.get("ask_timing")) for g in em]))
    check("NE-2 · 仍只问最小阻塞集（≤3）",
          count_questions(draft) <= 3 and 1 <= len(plan.get("selected") or []) <= 3,
          f"selected={len(plan.get('selected') or [])}, 问句={count_questions(draft)}")


# ---------------------------------------------------------------- TEST01
def run_test01(rep):
    print("\n【TEST01 回归 · 高完整度 + 高意向 → ASK_LESS / ACT_MORE】")
    ins = rep.get("insight") or {}
    comp = ins.get("requirement_completeness") or {}
    plan = rep.get("reply_plan") or {}
    draft = rep.get("draft") or ""
    sel_fields, qtext = _asked_fields(draft, plan)
    acts = ins.get("next_actions") or []

    check("T01-1 · 需求完整度仍为 HIGH（12/12）",
          comp.get("level") == "HIGH", f"{comp.get('score')}/{comp.get('total')}")
    check("T01-2 · 首轮问题少：selected ≤ 2 且只问 reference/规格级阻塞",
          len(plan.get("selected") or []) <= 2
          and set(sel_fields) <= {"reference_model", "product_spec",
                                  "specification", "model_selection"},
          str(sel_fields))
    check("T01-3 · 不问邮箱/认证/包装/付款/Incoterm（与 TEST02 同一套最小口径）",
          not (set(sel_fields) & _FORBIDDEN_EARLY)
          and not any(t in qtext for t in
                      ("email", "certification", "payment", "incoterm")),
          str(sel_fields))
    check("T01-4 · ACT_MORE：内部选型动作排第一（match_closest_product P1）",
          bool(acts) and acts[0].get("action") == "match_closest_product"
          and acts[0].get("priority") == "P1",
          str((acts[0] if acts else {}).get("action")))
    check("T01-5 · 无 P0 动作（供应缺口不伪装成客户阻塞）",
          all(a.get("priority") != "P0" for a in acts))
    check("T01-6 · 产品匹配 UNRESOLVED 但报价准备度 PARTIALLY_READY（双维独立）",
          (ins.get("product_match") or {}).get("status") == "UNRESOLVED"
          and ins.get("quotation_readiness_alias") == "PARTIALLY_READY",
          f"{(ins.get('product_match') or {}).get('status')} / "
          f"{ins.get('quotation_readiness_alias')}")
    check("T01-7 · 草稿问句 ≤ 3 且无盲报",
          count_questions(draft) <= 3 and not _BLIND_QUOTE.search(draft),
          f"问句={count_questions(draft)}")
    check("T01-8 · TEST02 与 TEST01 同时成立（未互相破坏）",
          (ins.get("requirement_completeness") or {}).get("level") == "HIGH"
          and (rep.get("draft_issues") or []) == [])


# ------------------------------------------------- 分类器单元断言
def run_classifier_units():
    print("\n【分类器单元断言 · UNKNOWN→阶段→是否阻塞→该不该问】")
    rfq_text = "Please send your best price for 3000 pcs."
    c_email = classify_ask("email", STAGE_NEW_INQUIRY, has_email=False)
    check("CL-1 · email @ 新询盘 → DO_NOT_ASK（渠道可回，不索取）",
          c_email["ask"] == DO_NOT_ASK, str(c_email["ask"]))
    c_email_formal = classify_ask("email", STAGE_FORMAL, has_email=False)
    check("CL-2 · email @ 正式报价阶段（需要交付报价单）→ ASK_NOW",
          c_email_formal["ask"] == ASK_NOW, str(c_email_formal["ask"]))
    c_pay = classify_ask("payment_terms", STAGE_NEW_INQUIRY)
    check("CL-3 · 付款方式 @ 早期产品匹配 → ASK_LATER",
          c_pay["ask"] == ASK_LATER, str(c_pay["ask"]))
    c_pay_nego = classify_ask("payment_terms", STAGE_NEGOTIATION)
    check("CL-4 · 付款方式 @ 谈判阶段 → ASK_NOW",
          c_pay_nego["ask"] == ASK_NOW, str(c_pay_nego["ask"]))
    c_qty_rfq = classify_ask("quantity", STAGE_NEW_INQUIRY,
                             intent=INTENT_RFQ, has_quantity=False,
                             text=rfq_text)
    check("CL-5 · 数量 @ RFQ 且缺失 → ASK_NOW",
          c_qty_rfq["ask"] == ASK_NOW, str(c_qty_rfq["ask"]))
    c_qty_cat = classify_ask("quantity", STAGE_NEW_INQUIRY,
                             intent=INTENT_CATALOG, has_quantity=False,
                             text="Please send me your catalog.")
    check("CL-6 · 数量 @ 纯 Catalog 请求 → ASK_LATER（不强行要资格审查）",
          c_qty_cat["ask"] == ASK_LATER, str(c_qty_cat["ask"]))
    c_cert = classify_ask("certification", STAGE_NEW_INQUIRY)
    check("CL-7 · 认证 @ 产品匹配阶段 → ASK_LATER（默认 NON_BLOCKING）",
          c_cert["ask"] == ASK_LATER, str(c_cert["ask"]))
    c_prof = classify_ask("company_scale", STAGE_NEW_INQUIRY)
    check("CL-8 · 公司规模 → DO_NOT_ASK（档案类）",
          c_prof["ask"] == DO_NOT_ASK, str(c_prof["ask"]))
    c_spec = classify_ask("product_spec", STAGE_NEW_INQUIRY)
    check("CL-9 · 核心规格（容量/尺寸/型号）@ 匹配阶段 → ASK_NOW",
          c_spec["ask"] == ASK_NOW, str(c_spec["ask"]))
    mi = minimum_info_to_advance(STAGE_NEW_INQUIRY)
    need = "、".join(mi["required_for_next_action"])
    check("CL-10 · MinimumInformationToAdvance(新询盘) 列出推进必需项",
          all(any(k in s for s in mi["required_for_next_action"])
              for k in ("产品方向", "核心规格", "数量")),
          need)


def main():
    print("=" * 70)
    print("PRESSURE TEST 02 · MINIMUM QUESTION STRATEGY 回归（离线规则模式）")
    print("=" * 70)
    engine = build_engine("rule")
    rep02 = analyze(TEST02_TEXT, *engine)
    rep02_ne = analyze(TEST02_NO_EMAIL_TEXT, *engine)
    rep01 = analyze(TEST01_TEXT, *engine)

    run_test02(rep02)
    run_test02_no_email(rep02_ne)
    run_test01(rep01)
    run_classifier_units()

    print("\n" + "=" * 70)
    print(f"TEST02 回归结果：{_PASS}/{_PASS + _FAIL} 项通过"
          + ("　✅ 全部通过" if _FAIL == 0 else f"　❌ 失败 {_FAIL} 项"))
    print("=" * 70)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
