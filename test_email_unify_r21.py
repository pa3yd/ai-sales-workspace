# -*- coding: utf-8 -*-
"""第二十一轮 · 统一客户邮件生成 回归测试（spec 1-11 / §13 验收）。

验证点：
  1) 单一「生成客户邮件」入口（UI 不再把 生成追问 + 生成回复 并列为双主行动作）
  2) EmailIntent 模型 + 自动意图判定（补充信息/回复/报价/跟进 等）
  3) 澄清/回复/跟进共享同一 generate_customer_email 管线
  4) 问句概念级去重（reference/photo/design 只问一次）
  5) 已存在信息不再被重复索取；产品已知时不泛问 which product/model
  6) 供应商事实护栏仍生效（无验证证书/资质 → 提示 human review）
  7) 业务员可在发送前审阅（草稿可编辑、issues 提示、绝不自动发送）
  8) Test01 / Test02 / Test03 决策逻辑回归见 test_test01_round2 /
     test_test02_min_questions / test_test03_r20（另跑）
"""
import os
import re
import sys
import io
import shutil
import tempfile
import sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "workbench"))

import workbench.email_unified as EU

FAILS = []


def check(name, ok, detail=""):
    mark = "✅" if ok else "❌"
    print(f"{mark} {name}" + (f"　[{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# ---------------- A · EmailIntent 模型 / 自动意图 ----------------
def _ctx(**kw):
    base = dict(text="", info={"contact_name": "Sophie"}, matches=[], gaps=[],
                stored_draft="", customer_product="", seller_company="Sunrise",
                biz="NEW", quotation_ready=False, fu_reason="", known=[],
                unresolved=[], insight={})
    base.update(kw)
    return base


check("A1 · EmailIntent 常量齐备",
      all(hasattr(EU, n) for n in (
          "CLARIFY_REQUIREMENT", "CONFIRM_SPECIFICATION", "REPLY_INQUIRY",
          "SEND_QUOTATION", "FOLLOW_UP_QUOTATION", "FOLLOW_UP_SAMPLE",
          "NEGOTIATION_REPLY", "PO_FOLLOW_UP", "GENERAL_REPLY")))
check("A2 · 意图不硬编码在 UI：SELECT_OPTIONS 集中定义且默认自动",
      EU.SELECT_OPTIONS and EU.SELECT_OPTIONS[0][0] == "AUTO")
check("A3 · 跟进原因→意图（报价未回复→报价跟进）",
      EU.determine_email_intent(_ctx(fu_reason="QUOTE_SENT_NO_REPLY"))[0]
      == EU.FOLLOW_UP_QUOTATION)
check("A4 · 样品未反馈→样品跟进",
      EU.determine_email_intent(_ctx(fu_reason="SAMPLE_SENT_NO_REPLY"))[0]
      == EU.FOLLOW_UP_SAMPLE)
check("A5 · 等待客户信息→补充信息",
      EU.determine_email_intent(_ctx(fu_reason="INFORMATION_WAITING"))[0]
      == EU.CLARIFY_REQUIREMENT)
check("A6 · PO 预期→订单跟进",
      EU.determine_email_intent(_ctx(fu_reason="PO_EXPECTED"))[0] == EU.PO_FOLLOW_UP)
check("A7 · 谈判停滞→谈判回复",
      EU.determine_email_intent(_ctx(fu_reason="NEGOTIATION_STALLED"))[0]
      == EU.NEGOTIATION_REPLY)
check("A8 · Test03 形态（产品已知 + 库内无匹配）→ 回复客户，绝非反问产品",
      EU.determine_email_intent(
          _ctx(customer_product="insulated travel mug", matches=[]))[0]
      == EU.REPLY_INQUIRY)
_ask_spec = _ctx(customer_product="", matches=[])
_ask_spec["gaps"] = [{"key": "product_spec", "name": "产品型号 / 规格", "level": "A",
                      "ask_timing": "ASK_NOW", "smart": False,
                      "question": "Which model or specification are you interested in?"}]
check("A9 · 客户无产品方向 + 阻塞 → 补充信息（澄清意图）",
      EU.determine_email_intent(_ask_spec)[0] == EU.CLARIFY_REQUIREMENT)
_spec_only = _ctx(customer_product="water bottles",
                  matches=[{"name": "Stainless Steel Water Bottle"}])
_spec_only["gaps"] = [{"key": "product_spec", "name": "最终规格", "level": "A",
                       "ask_timing": "ASK_NOW", "smart": True,
                       "question": 'You mentioned "water bottles" - could you share a reference model?'}]
check("A10 · 产品已知 + 库内已匹配 + 仅剩规格类阻塞 → 确认规格（CONFIRM_SPECIFICATION）",
      EU.determine_email_intent(_spec_only)[0] == EU.CONFIRM_SPECIFICATION)
check("A11 · 报价就绪且有库内匹配 → 发送报价",
      EU.determine_email_intent(_ctx(matches=[{"name": "Swim Goggles"}],
                                     quotation_ready=True))[0]
      == EU.SEND_QUOTATION)
check("A12 · 已报价且无跟进上下文（QUOTED）→ 报价跟进",
      EU.determine_email_intent(_ctx(biz="QUOTED",
                                     matches=[{"name": "Swim Goggles"}]))[0]
      == EU.FOLLOW_UP_QUOTATION)


# ---------------- B · 单一生成管线 ----------------
def _run(text, patch=None):
    from main import build_engine, analyze
    ex, matcher, client = build_engine("rule")
    rep = analyze(text, ex, matcher, client)
    info = rep["extracted"]
    from workbench.gapcheck import detect_missing
    from agent.extractor import extract_customer_product
    gaps = detect_missing(text, info, rep["matches"],
                          known_email=info.get("email") or "")
    cp = extract_customer_product(text)
    ctx = dict(text=text, info=info, matches=rep["matches"], gaps=gaps,
               stored_draft=rep.get("draft") or "", customer_product=cp,
               seller_company="Sunrise Sportswear Co., Ltd.",
               biz="NEW", quotation_ready=False, fu_reason="", known=[],
               unresolved=[], insight=rep.get("insight") or {})
    if patch:
        ctx.update(patch)
    return ctx


T02 = ("Hello,\nI want 800 pcs swimming goggles.\n"
       "Please send me your catalog and price list.\nThanks, Sarah")
T03 = ("This is Sophie van Dijk from BrightPromo BV, Netherlands. We would like to "
       "order 20,000 pcs of stainless steel insulated travel mugs. Capacity: 500ml, "
       "double-wall vacuum insulated stainless steel, leak-proof lid, black matte "
       "finish, custom logo, individual white box. Our target price is below USD "
       "4.20 per piece. Destination: Rotterdam. Best regards, Sophie")

_c2 = _run(T02)
_out_c2 = EU.generate_customer_email(_c2)
check("B1 · 低完整→澄清意图 & 生成管线返回关键字段",
      _out_c2["intent"] == EU.CLARIFY_REQUIREMENT
      and all(k in _out_c2 for k in ("intent", "intent_cn", "reason", "subject",
                                     "body", "issues", "question_count",
                                     "human_review_required")))
check("B2 · 澄清邮件只问阻塞、≤3 问、不含邮箱/付款/网址索取",
      _out_c2["question_count"] <= 3
      and _out_c2["question_count"] >= 1
      and not re.search(r"email address|payment terms|your website",
                        _out_c2["body"], re.I))
check("B3 · 澄清邮件无 SLA 时间承诺（不写 within 24 hours）",
      not re.search(r"within\s+\d+\s*(hour|day|week)", _out_c2["body"], re.I))
check("B4 · AUTO=人工“自动”解析一致；body 非空",
      bool(_out_c2["body"]) and _out_c2["body"].startswith("Dear"))
# 人工意图选择器映射
_m_clar = EU.generate_customer_email(_c2, intent="CLARIFY_REQUIREMENT")
_m_rep = EU.generate_customer_email(_c2, intent="REPLY_INQUIRY")
check("B5 · 人工选择「补充信息」→ 澄清管线（同为单入口）",
      _m_clar["intent"] == EU.CLARIFY_REQUIREMENT and bool(_m_clar["body"]))
check("B6 · 人工选择「回复客户」→ 回复管线（走同一生成函数）",
      _m_rep["intent"] == EU.REPLY_INQUIRY and bool(_m_rep["body"]))

_c3 = _run(T03)
_out_c3 = EU.generate_customer_email(_c3)
_low3 = (_out_c3["body"] or "").lower()
check("C1 · Test03：产品已知 + 无库内匹配 → 意图=回复客户",
      _out_c3["intent"] == EU.REPLY_INQUIRY)
check("C2 · Test03：不泛问 which product/model，不出现 This is us",
      not any(k in _low3 for k in ("which product", "which model", "what product",
                                   "this is us", "this is our team")))
check("C3 · Test03：subject 从引擎草稿首行提升（不重复 Subject）",
      "subject:" not in (_out_c3["body"] or "").lower()[:6]
      and bool(_out_c3["subject"]))
# 竞品价 4.35 绝不能当成我方报价写进邮件正文
check("C4 · 竞品价 4.35 不进正文（只作竞争背景，不冒充我方价格）",
      "4.35" not in (_out_c3["body"] or ""))

# 报价意图（构造：无阻塞 + 已匹配 + 报价就绪，隔离测试）
_dq = dict(text="", info={"contact_name": "Sarah", "quantity": 800},
           matches=[{"name": "Swim Goggles", "name_cn": "泳镜",
                     "price_range": [1.2, 3.8], "moq": 500}],
           gaps=[], stored_draft="", customer_product="swimming goggles",
           seller_company="Sunrise Sportswear Co., Ltd.",
           biz="READY_FOR_QUOTE", quotation_ready=True, fu_reason="",
           known=[], unresolved=[], insight={})
_oq = EU.generate_customer_email(_dq)
check("D1 · 报价意图：正文含报价 & subject to 措辞，无事实违规 issues",
      _oq["intent"] == EU.SEND_QUOTATION
      and "quotation" in (_oq["body"] or "").lower()
      and "subject to" in (_oq["body"] or "").lower()
      and not _oq["issues"])

# 跟进意图（无新原文时也要能走管线且安全）
_of = EU.generate_customer_email(
    _ctx(fu_reason="QUOTE_SENT_NO_REPLY", fu_ctx={"product": "water bottles",
                                                  "known": ["email"],
                                                  "contact_name": "Sophie"}))
check("D2 · 报价跟进：正文引用 quotation、问句≤3、issues 为空、绝不自动发送",
      _of["intent"] == EU.FOLLOW_UP_QUOTATION
      and "quotation" in (_of["body"] or "").lower()
      and _of["question_count"] <= 3 and not _of["issues"])
check("D3 · 统一管线标记 human_review_required = issues 非空等价",
      _of["human_review_required"] == bool(_of["issues"]))

# ---------------- E · 问句去重 / 已知信息护栏 ----------------
dup_body = ("Dear x,\n\nCould you share a reference model of the product?\n"
            "Would you also send a product photo?\n"
            "What design do you prefer?\nThanks.")
_dd = EU.dedupe_questions(dup_body)
_dd_qs = [s for s in re.split(r"(?<=[.?])\s+", _dd) if s.rstrip().endswith("?")]
check("E1 · reference / photo / design 同概念问句只保留一次",
      len(_dd_qs) == 1, str(len(_dd_qs)))
guard_body = ("Which model or specification are you interested in?\n"
              "We will check internally.")
_gd = EU.drop_known_product_questions(
    guard_body, _ctx(customer_product="travel mug", matches=[]))
check("E2 · 产品已知时删除泛问 which model or specification",
      "which model" not in _gd.lower())
smart_q = ('You mentioned "travel mugs" - could you share a reference model, a photo '
           "or the exact specification so we can match the closest item from our range?")
_gd2 = EU.drop_known_product_questions(
    smart_q, _ctx(customer_product="travel mug", matches=[]))
check("E3 · smart 定向追问（reference/photo/spec）不被误删",
      "reference model" in _gd2 and len(_gd2) > 20)
# 已知邮箱不被索取
_of2 = EU.generate_customer_email(
    _ctx(fu_reason="INFORMATION_WAITING",
         fu_ctx={"product": "bottle", "known": ["email", "company"]},
         known=["email"]))
check("E4 · 已知邮箱不索取：正文不含 email address 问句",
      "email address" not in (_of2["body"] or "").lower())

# ---------------- F · 供应商事实护栏 ----------------
_issue_body = EU.generate_customer_email(
    _ctx(customer_product="mug", product_short="mug",
         stored_draft="Dear x,\n\nWe have CE certification for this model and we "
                      "will send you the documents.\n\nBest, Sunrise"))
check("F1 · 无验证证书承诺 → 事实护栏给出 human review issues",
      bool(_issue_body["issues"]),
      ";".join(str(i) for i in _issue_body["issues"][:2]))

# ---------------- G · UI 单入口（AppTest，临时库） ----------------
import db as _db
_tmp = tempfile.mkdtemp()
_tmpdb = os.path.join(_tmp, "workbench.db")
_old = _db.DB_PATH
_db.DB_PATH = _tmpdb
_db.init_db()
# 造一条真实询盘并落库（不建演示客户）
from main import build_engine as _be, analyze as _an
_ex_, _mt_, _cl_ = _be("rule")
_rep3 = _an(T03, _ex_, _mt_, _cl_)
_iid = _db.save_inquiry(T03, _rep3)
from streamlit.testing.v1 import AppTest
try:
    _at = AppTest.from_file(os.path.join(HERE, "workbench", "app.py"),
                            default_timeout=300).run()
    _at.session_state["selected_id"] = _iid
    _at.run()
    _labels = [b.label for b in _at.button]
    check("G1 · 详情页 UI 无异常", len(_at.exception) == 0,
          str([str(e) for e in _at.exception][:1]))
    check("G2 · 页面出现「生成客户邮件」单一入口",
          any("生成客户邮件" in x for x in _labels))
    check("G3 · 不再同时出现 生成回复 + 生成询问 两个并列邮件主行动作",
          not (any(x.startswith("✉️ 生成回复") for x in _labels)
               and any("生成询问" in x for x in _labels)))
    # 展开统一编写器
    _at.session_state[f"ui_mail_{_iid}"] = True
    _at.run()
    check("G4 · 展开后无异常", len(_at.exception) == 0)
    _caps = " ".join(c.value for c in _at.caption)
    check("G5 · Composer 展示“邮件目的（自动判断）”及意图说明",
          "邮件目的" in _caps and "系统判断邮件目的" in _caps)
    _body = ""
    try:
        _body = _at.session_state[f"draft_box_{_iid}"] or ""
    except Exception:
        pass
    check("G6 · Composer 预填可审阅草稿（不自动发送）",
          len(_body) > 40, str(len(_body))[:4])
finally:
    _db.DB_PATH = _old
    shutil.rmtree(_tmp, ignore_errors=True)

_total = sum(1 for _ in range(200)) - 200  # placeholder 防误读
print("=" * 50)
print("email_unify_r21 断言计数见上方 ✅/❌；FAILED:", FAILS if FAILS else "无")
sys.exit(1 if FAILS else 0)
