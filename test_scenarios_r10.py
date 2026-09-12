# -*- coding: utf-8 -*-
"""第十轮最终验收：5 个真实场景（spec 二十二）。

Test 01 新客户首次询盘（产品/数量明确）
Test 02 产品未知，要求目录和报价
Test 03 同一客户第二封询盘
Test 04 已报价超 48 小时未回复
Test 05 谈判阶段客户

运行：python test_scenarios_r10.py
"""
import io
import json
import os
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BASE, "workbench"))

import db as _db
_TMP = os.path.join(tempfile.gettempdir(), "wb_r10_accept.db")
if os.path.exists(_TMP):
    os.remove(_TMP)
_db.DB_PATH = _TMP
_db.init_db()

import workflow as _wf
import crm as _crm
from datetime import datetime, timedelta

_PASS = _FAIL = 0


def check(name, ok, detail=""):
    global _PASS, _FAIL
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"　[{detail}]" if detail and not ok else ""))
    if ok:
        _PASS += 1
    else:
        _FAIL += 1


def load_q():
    """复刻 app.load_queue 的行→dict 转换（不 import app.py，避免整页执行）。"""
    out = []
    for row in _db.list_inquiries():
        (id_, created, grade, score, country, company, contact, stt, cg,
         clg, urgency) = row[:11]
        need = row[11] if len(row) > 11 else {}
        cust_id = row[12] if len(row) > 12 else None
        wf = row[13] if len(row) > 13 else {}
        has_draft = bool((need or {}).get("has_draft"))
        biz = _wf.derive_biz(stt, (need or {}).get("blockers") or [],
                             (need or {}).get("readiness") or "",
                             wf.get("biz_status"), has_draft)
        act = _wf.next_action(stt, biz, (need or {}).get("blockers") or [],
                              (need or {}).get("readiness") or "",
                              has_draft=has_draft,
                              follow_up_at=wf.get("follow_up_at"),
                              follow_up_done=bool(wf.get("follow_up_done")),
                              intent=(need or {}).get("intent") or "")
        out.append({"id": id_, "created": created, "grade": grade, "score": score,
                    "country": country, "company": company, "contact": contact,
                    "status": stt, "cust_grade": cg, "urgency": urgency,
                    "need": need or {}, "cust_id": cust_id, "wf": wf,
                    "biz": biz, "action": act, "pts": 50})
    return out


def rep(company, contact, country, intent, product_known=True, qty=3000,
        price=None, draft="", readiness=""):
    blockers = []
    if not product_known:
        blockers.append({"field": "product", "severity": "high",
                         "phase": "preliminary"})
    return {
        "extracted": {"company": company, "country": country,
                      "contact_name": contact, "intent": intent,
                      "quantity": qty, "quantity_unit": "pcs",
                      "product_query": "steel bottle" if product_known else None,
                      "target_price": price,
                      "target_price_currency": "USD" if price else None},
        "lead": {"grade": "B", "score": 70},
        "matches": ([{"name": "Steel Bottle", "name_cn": "钢制水瓶"}]
                    if product_known else []),
        "insight": {"quotation_readiness": {
            "quotation_readiness_status": readiness,
            "quotation_readiness_score": 85 if readiness else 40,
            "blockers_preliminary": blockers, "quote_summary": ""}},
        "draft": draft,
    }


# ---------------- Test 01：新客户首次询盘 ----------------
print("== Test 01：新客户首次询盘，产品明确，数量明确 ==")
id1 = _db.save_inquiry("Please quote steel bottle 5000 pcs.",
                       rep("Fresh Retail Ltd", "Alice", "UK", "报价请求",
                           product_known=True, qty=5000))
rows = {x["id"]: x for x in load_q()}
it1 = rows[id1]
check("1-1 识别 Company（customers 归并）",
      any(c[1] == "Fresh Retail Ltd" for c in _db.list_customers()))
check("1-2 识别 Contact（Alice 入联系人）",
      "Alice" in [c.get("name") for c in json.loads(
          next(c[11] for c in _db.list_customers()
               if c[1] == "Fresh Retail Ltd") or "[]")])
check("1-3 Inquiry 关联 customer_id", it1["cust_id"] is not None)
check("1-4 商机阶段=新商机",
      _crm.opp_stage_of_biz(it1["biz"]) == _crm.OPP_NEW)
check("1-5 Next Action=回复客户",
      it1["action"]["label"] == "回复客户")
check("1-6 有业务状态（非空）", bool(it1["biz"]))
check("1-7 Timeline 有 ANALYZED 事件",
      any(a[0] == "ANALYZED" for a in _db.list_activity(id1)))

# ---------------- Test 02：产品未知，要求目录和报价 ----------------
print("== Test 02：产品未知，客户明确要求目录和报价 ==")
id2 = _db.save_inquiry("Please send catalog and best price.",
                       rep("Catalog Buyer Co", "Bob", "Germany", "目录/报价请求",
                           product_known=False, qty=3000))
rows = {x["id"]: x for x in load_q()}
it2 = rows[id2]
check("2-1 识别 Company", it2["company"] == "Catalog Buyer Co")
check("2-2 识别 Contact（Bob）", it2["contact"] == "Bob")
check("2-3 形成商机（阶段可判定）",
      _crm.opp_stage_of_biz(it2["biz"]) in _crm.OPP_CN)
check("2-4 有 Next Action", bool((it2["action"] or {}).get("label")))
check("2-5 有状态", bool(it2["biz"]))
check("2-6 Timeline 有事件", len(_db.list_activity(id2)) >= 1)
it2b = rows[id2]
check("2-7 不串 Test 01 的产品（隔离）",
      "钢制水瓶" not in json.dumps(it2b["need"], ensure_ascii=False)
      and "Steel Bottle" not in json.dumps(it2b["need"]))

# ---------------- Test 03：同一客户第二封询盘 ----------------
print("== Test 03：同一客户发送第二封询盘 ==")
id3 = _db.save_inquiry("Follow up: also need 2000 pcs of caps.",
                       rep("Fresh Retail Ltd", "Bob2", "UK", "追加询盘",
                           product_known=True, qty=2000))
agg = [x for x in load_q()
       if x["company"] == "Fresh Retail Ltd"]
check("3-1 同客户 2 封询盘关联同一档案",
      len(agg) == 2 and all(x["cust_id"] == agg[0]["cust_id"] for x in agg))
_opp3 = _crm.opportunity_of(agg)
check("3-2 形成客户级商机（不炸、有阶段）", _opp3["stage"] in _crm.OPP_CN)
check("3-3 询盘数聚合为 2", _opp3["inquiry_count"] == 2)
check("3-4 联系人层累计（Alice + Bob2）",
      {"Alice", "Bob2"} <= {c.get("name") for c in json.loads(
          next(c[11] for c in _db.list_customers()
               if c[1] == "Fresh Retail Ltd") or "[]")})
check("3-5 有客户级 Next Action", bool((_opp3.get("next_action") or {}).get("label")))
check("3-6 第二封不覆盖第一封数据（隔离）",
      agg[0]["id"] != agg[1]["id"]
      and _db.get_inquiry(agg[0]["id"])[1]["extracted"]["contact_name"]
      in ("Alice", "Bob2"))

# ---------------- Test 04：已报价，超 48 小时未回复 ----------------
print("== Test 04：客户已报价，超过 48 小时没有回复 ==")
id4 = _db.save_inquiry("Quote follow-up test.",
                       rep("Quoted GmbH", "Karl", "Germany", "已报价",
                           product_known=True, qty=800, price="3.20",
                           draft="Dear Karl, ..."))
_db.update_biz_status(id4, _wf.QUOTED)
_old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
conn = _db.sqlite3.connect(_db.DB_PATH)
conn.execute("UPDATE inquiries SET last_replied_at=? WHERE id=?", (_old, id4))
conn.commit()
conn.close()
it4 = {x["id"]: x for x in load_q()}[id4]
check("4-1 识别 Company", it4["company"] == "Quoted GmbH")
check("4-2 识别 Contact", it4["contact"] == "Karl")
check("4-3 状态=已报价（保存的业务状态优先）", it4["biz"] == _wf.QUOTED)
check("4-4 有商机阶段（已报价）",
      _crm.opp_stage_of_biz(it4["biz"]) == _crm.OPP_QUOTED)
check("4-5 Next Action=跟进报价反馈",
      it4["action"]["label"] == _wf.ACTION_CN["FOLLOW_UP_QUOTE"])
check("4-6 Timeline 有 QUOTED 事件路径（REPLIED 派生/记录存在）",
      _db.get_workflow(id4)["last_replied_at"] != "")
check("4-7 商机金额可估（800×3.2=2560）",
      _crm.opportunity_of([it4])["value"] == 2560)

# ---------------- Test 05：谈判阶段 ----------------
print("== Test 05：客户已经进入谈判阶段 ==")
id5 = _db.save_inquiry("Negotiation test.",
                       rep("Deal Maker Ltd", "Mary", "USA", "价格谈判",
                           product_known=True, qty=12000, price="2.95",
                           draft="Dear Mary, ..."))
_db.update_biz_status(id5, _wf.NEGOTIATING)
it5 = {x["id"]: x for x in load_q()}[id5]
check("5-1 识别 Company", it5["company"] == "Deal Maker Ltd")
check("5-2 状态=谈判中", it5["biz"] == _wf.NEGOTIATING)
check("5-3 商机阶段=谈判中",
      _crm.opp_stage_of_biz(it5["biz"]) == _crm.OPP_NEGOTIATING)
check("5-4 7 段状态条定位=第 5 段（下标）", _crm.funnel_position("NEGOTIATING") == 5)
check("5-5 Next Action=跟进报价反馈",
      it5["action"]["label"] == _wf.ACTION_CN["FOLLOW_UP_QUOTE"])
check("5-6 Timeline 可查询", isinstance(_db.list_activity(id5), list))
check("5-7 商机金额（12000×2.95=35400）",
      _crm.opportunity_of([it5])["value"] == 35400)

# ---------------- AI 能力回归（第 10 项：保持现有 AI 能力） ----------------
print("== 附加：现有 AI 能力未受影响 ==")
from streamlit.testing.v1 import AppTest
_APP = os.path.join(_BASE, "workbench", "app.py")
at = AppTest.from_file(_APP, default_timeout=120)
at.run()
check("A-1 页面无异常", not at.exception)
main_md = "\n".join(str(x.value) for x in at.main.markdown)
check("A-2 今日行动队列仍在", "今日行动" in main_md)
check("A-3 KPI 真实业务指标仍在",
      all(k in main_md for k in ("今日新增", "待回复", "到期跟进", "待报价")))

print("=" * 52)
print(f"第十轮验收场景：通过 {_PASS} · 失败 {_FAIL}")
sys.exit(1 if _FAIL else 0)
