# -*- coding: utf-8 -*-
"""
TEST03 FIX 回归 · 产品提取 + 客户匹配 + AI 决策简化（第二十轮）
================================================================
场景：Sophie van Dijk / BrightPromo BV（荷兰）· 20,000 pcs 不锈钢保温旅行杯
  - 客户需求非常完整（规格/容量/定制/包装/样品/目标价/交期/目的地全给）
  - 但公司产品库（泳具）没有任何对应品类 → OUT_OF_CATALOG
  - BrightPromo BV 已是老客户（Test02 水瓶 10,000 pcs 已存在）

硬性行为（对应 spec §17）：
  1  CUSTOMER_PRODUCT_REQUIREMENT ≠ INTERNAL_LIBRARY_MATCH
     —— 客户明说产品 + 库内无匹配 ⇒ Product != UNKNOWN / 不标 customer 缺产品
  2  老客户匹配：同一公司 → 不新建重复客户档案
  3  同一客户 + 新产品/新项目 ⇒ 新建独立 Deal（绝不并入旧水瓶 Deal）
  4  内部缺口 ≠ 客户缺口：blockers 不含 product / 不反向问客户要品类
  5  竞品价只进 competitiveContext，绝不当我方报价
  6  高商业意向 + 低产品匹配 ⇒ 优先级与产品匹配度解耦（不因 fit 低而降级）
  7  NBA = 内部供应能力评估（匹配最接近 SKU 排第一），不是"确认产品"
  8  邮件：不出现 "This is us / This is our team" 空泛占位；同一澄清问题不重复两次；
     out-of-catalog 策略 = 明说不在现行目录 + 内部核实 + 可供应选项，不冒充供应能力
  9  精简决策字段（product_match_status / opportunity_type / supplier_capability）
     —— 供 UI 紧凑卡片直接展示

用法：python test_test03_r20.py （离线规则模式；CRM/UI 段使用临时库，不动 workbench.db）
"""
import os
import re
import sys
import json
import shutil
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench"))
sys.stdout.reconfigure(encoding="utf-8")

from main import build_engine, analyze
from agent.facts import build_fact_layer
from agent.reply_strategy import count_questions
from agent.insight import (PRODUCT_MATCH_NO_MATCH, OUT_OF_CATALOG, SUPPLIER_UNKNOWN,
                           SUPPLIER_CAPABLE)

_PASS = 0
_FAIL = 0


def check(name, ok, detail=""):
    global _PASS, _FAIL
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"　（{detail}）" if detail else ""))
    if ok:
        _PASS += 1
    else:
        _FAIL += 1


TEST03 = """Subject: RFQ - 20,000 pcs Stainless Steel Insulated Travel Mugs

Dear Sir or Madam,

This is Sophie van Dijk from BrightPromo BV, a promotional products distributor in the Netherlands.

We are looking for 20,000 pcs of stainless steel insulated travel mugs with the following specification:

- Capacity: 500ml
- Double-wall vacuum insulated stainless steel
- Leak-proof lid
- Black matte finish
- Custom logo printing
- Individual white box

Our target price is below USD 4.20/pc. A competitor has offered approximately USD 4.35/pc, and we need to make a supplier decision within this week.

Required delivery: 25 days after order confirmation.
Sample: 2 pcs before mass production.
Destination: Rotterdam, Netherlands.

Please quote your best price.

Best regards,
Sophie van Dijk
Purchasing Manager
sophie@brightpromo.nl
"""


def _facts(rep) -> dict:
    return {f.get("field"): f for f in (rep.get("facts") or [])}


def run_engine():
    print("\n【TEST03 · 引擎层：产品提取 / 内外缺口分离 / 优先级解耦 / 竞品价】")
    ex, matcher, client = build_engine("rule")
    rep = analyze(TEST03, ex, matcher, client)
    info = rep["extracted"]
    ins = rep["insight"] or {}
    lead = rep["lead"]
    pm = ins.get("product_match") or {}
    plan = rep.get("reply_plan") or {}
    draft = rep.get("draft") or ""
    comp = ins.get("requirement_completeness") or {}
    facts = _facts(rep)

    # ---- 1 · 客户产品 ≠ 库内匹配 ----
    check("1-1 · 客户公司识别：BrightPromo BV", info.get("company") == "BrightPromo BV",
          str(info.get("company")))
    check("1-2 · 联系人全名：Sophie van Dijk", info.get("contact_name") == "Sophie van Dijk",
          str(info.get("contact_name")))
    check("1-3 · 客户原话产品短语已识别（Product ≠ UNKNOWN）",
          bool(info.get("product_query")) and "travel mug" in (info.get("product_query") or "").lower(),
          str(info.get("product_query")))
    check("1-4 · 库内匹配 = 无（NO_MATCH），而不是把客户需求标成 UNKNOWN",
          not rep.get("matches")
          and pm.get("status") == "UNRESOLVED"
          and pm.get("product_match_status") == PRODUCT_MATCH_NO_MATCH,
          f"matches={len(rep.get('matches') or [])} status={pm.get('status')} "
          f"short={pm.get('product_match_status')}")
    check("1-5 · 需求完整度 = HIGH（客户侧，与库内匹配互不拖累）",
          comp.get("level") == "HIGH", f"{comp.get('level')} {comp.get('score')}/{comp.get('total')}")

    # ---- 2 · 内部缺口 ≠ 客户缺口 ----
    qr = ins.get("quotation_readiness") or {}
    blockers = [b.get("field") for b in (qr.get("blockers_preliminary") or [])]
    formal = [b.get("field") for b in (qr.get("blockers_formal") or [])]
    check("2-1 · 报价阻塞项不包含 product（内部缺口不伪装成客户缺失）",
          "product" not in blockers and "product" not in formal,
          f"prelim={blockers} formal={formal}")
    sel_fields = [q.get("field") for q in (plan.get("selected") or [])]
    check("2-2 · 首轮追问只问 reference（不反问'你要什么产品/型号'）",
          sel_fields == ["reference_model"] or sel_fields == [],
          str(sel_fields))

    # ---- 3 · out-of-catalog 商机分类 + 供应能力 ----
    check("3-1 · opportunity_type = OUT_OF_CATALOG",
          pm.get("opportunity_type") == OUT_OF_CATALOG,
          str(pm.get("opportunity_type")))
    check("3-2 · supplier_capability = UNKNOWN（待内部确认，不写死失败/成功）",
          pm.get("supplier_capability_status") == SUPPLIER_UNKNOWN,
          str(pm.get("supplier_capability_status")))
    check("3-3 · 简明决策文案字段齐备（供 UI 紧凑卡）",
          bool(pm.get("opportunity_type_cn")) and bool(pm.get("supplier_capability_label")),
          f"{pm.get('opportunity_type_cn')} / {pm.get('supplier_capability_label')}")

    # ---- 4 · 竞品价 → competitiveContext，绝不进我方报价 ----
    check("4-1 · 竞品价 ≈ USD 4.35 已单独提取（competitiveContext）",
          info.get("competitor_price") == 4.35 and info.get("competitor_price_approx") is True,
          f"comp={info.get('competitor_price')} approx={info.get('competitor_price_approx')}")
    check("4-2 · 客户目标价 = 4.20（竞品价没有污染目标价）",
          info.get("target_price") == 4.2, str(info.get("target_price")))
    cf = facts.get("competitor_price")
    check("4-3 · 事实层存在 competitor_price 项且标注'≠我方报价'",
          cf is not None and "competitor_price" in (cf.get("field") or "")
          and "≠" in (cf.get("note") or ""), str((cf or {}).get("display")))
    qf = facts.get("company_quote") or {}
    check("4-4 · 我方报价 company_quote = 未提供（绝不拿 4.35 当报价）",
          qf.get("value") is None, str(qf.get("display")))
    check("4-5 · 回复草稿不引用竞品价 4.35",
          "4.35" not in draft, "")

    # ---- 5 · 优先级与产品匹配解耦 ----
    match_score = lead.get("product_match_score")
    check("5-1 · 产品匹配维度得分低（如实反映无货可接）",
          (match_score or 0) <= 20, f"match={match_score}")
    check("5-2 · 客户质量分 ≥ 60 → 优先级不因 fit 低而降为 D",
          (lead.get("customer_quality_total") or 0) >= 60
          and lead.get("grade") in ("A", "B"),
          f"quality={lead.get('customer_quality_total')} grade={lead.get('grade')}")

    # ---- 6 · Next Best Action = 内部能力评估 ----
    acts = [a.get("action") for a in (ins.get("next_actions") or [])]
    check("6-1 · 下一步动作第一位是内部匹配/能力评估（match_closest_product 族）",
          bool(acts) and ("match" in (acts[0] or "").lower() or "closest" in (acts[0] or "").lower()),
          str(acts[:2]))

    # ---- 7 · 邮件生成 ----
    low = draft.lower()
    check("7-1 · 邮件无 'This is us' / 'This is our team' 空泛占位",
          "this is us" not in low and "this is our team" not in low, "")
    check("7-2 · 同一澄清问题不重复两次（reference model 只出现一次问句）",
          low.count("reference model") == 1, f"count={low.count('reference model')}")
    check("7-3 · 邮件问句 ≤ 1（本场景仅 1 个 P0 参考确认）",
          count_questions(draft) <= 1, f"问句={count_questions(draft)}")
    check("7-4 · out-of-catalog 策略：明说不在现行目录 + 内部核实",
          "not currently represented in our standard catalog" in low
          and "checking internally" in low, "")
    check("7-5 · 不冒充供应能力/不虚构报价（无价格/MOQ/交期数字承诺）",
          "we can deliver within" not in low
          and "usd " not in low and "$" not in low
          and re.search(r"\bmoq\b[^.\n]{0,20}\d", low) is None, "")
    check("7-6 · 草稿校验零问题",
          not rep.get("draft_issues"), str(rep.get("draft_issues")))
    return rep


def run_crm(rep):
    print("\n【TEST03 · CRM 层：老客户匹配 + 新项目新 Deal】")
    import db
    tmp = tempfile.mkdtemp()
    tmpdb = os.path.join(tmp, "workbench.db")
    old = db.DB_PATH
    db.DB_PATH = tmpdb
    try:
        db.init_db()
        con = sqlite3.connect(tmpdb)
        c = con.cursor()
        now = db._now()
        # 预置 Test02 老客户：BrightPromo BV（只有公司 key，无邮箱）+ 水瓶 Deal
        c.execute(
            """INSERT INTO customers
               (ckey, company, country, email, website, contact_name,
                inquiry_count, last_grade, last_score, grade, first_seen, last_seen, contacts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("company:brightpromo bv", "BrightPromo BV", "Netherlands", None, None, "Sophie",
             1, "B", 60, "B", now, now, json.dumps([], ensure_ascii=False)))
        cid = c.lastrowid
        c.execute(
            """INSERT INTO opportunities
               (customer_id, inquiry_id, title, product, source, details_json, last_activity_at,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (cid, -5, "BrightPromo BV · Stainless Steel Water Bottles",
             "Stainless Steel Water Bottles", "询盘",
             json.dumps({"quantity": 10000, "product_query": "stainless steel water bottles"}),
             now, now, now))
        con.commit()
        con.close()

        iid = db.save_inquiry(TEST03, rep)
        con = sqlite3.connect(tmpdb)
        con.row_factory = sqlite3.Row
        custs = con.execute("SELECT id, ckey, email, contact_name FROM customers").fetchall()
        opps = con.execute(
            "SELECT id, customer_id, inquiry_id, product FROM opportunities ORDER BY id").fetchall()
        con.close()
        check("C1 · 未新建重复客户档案（仍只有 1 个 BrightPromo BV）",
              len(custs) == 1 and custs[0]["id"] == cid,
              f"customers={len(custs)}")
        check("C2 · 老客户档案被命中并补齐新联系人邮箱",
              (custs[0]["email"] or "") == "sophie@brightpromo.nl"
              and "Sophie van Dijk" in (custs[0]["contact_name"] or ""),
              str(dict(custs[0])))
        check("C3 · 同客户下出现两个独立 Deal（水瓶 + 旅行杯，不合并）",
              len(opps) == 2
              and any("Water Bottles" in str(o["product"]) for o in opps)
              and any("travel mug" in str(o["product"]).lower() for o in opps),
              str([dict(o) for o in opps]))
        new_deal = [o for o in opps if o["inquiry_id"] == iid]
        check("C4 · 新 Deal 绑定新询盘（独立商机行）",
              len(new_deal) == 1 and new_deal[0]["customer_id"] == cid,
              str([dict(o) for o in new_deal]))
        return True
    finally:
        db.DB_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)


def run_ui_smoke(rep):
    print("\n【TEST03 · UI 冒烟：精简展示、无异常】")
    import db
    tmp = tempfile.mkdtemp()
    tmpdb = os.path.join(tmp, "workbench.db")
    old = db.DB_PATH
    db.DB_PATH = tmpdb
    try:
        db.init_db()
        db.save_inquiry(TEST03, rep)
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench", "app.py"),
            default_timeout=300).run()
        md = "\n".join(m.value for m in at.markdown) + "\n" + \
             "\n".join(c.value for c in at.caption)
        check("U1 · 页面无异常", len(at.exception) == 0, f"ex={len(at.exception)}")
        check("U2 · 客户以公司名出现（不再显示 未识别公司）",
              "BrightPromo BV" in md, "")
        check("U3 · 客户产品以短名展示（Travel Mug 短名，R17 A3 列表精简口径）",
              "Insulated Travel" in md or "Travel" in md, "")
        check("U4 · 未出现长规格全文刷屏（首页列表保持简洁）",
              "40 hours battery life" not in md, "")
        return True
    finally:
        db.DB_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    rep = run_engine()
    run_crm(rep)
    run_ui_smoke(rep)
    print("\n" + "=" * 66)
    print(f"TEST03 回归结果：{_PASS}/{_PASS + _FAIL} 项通过　"
          + ("✅ 全部通过" if _FAIL == 0 else "❌ 存在失败"))
    print("=" * 66)
    sys.exit(1 if _FAIL else 0)
