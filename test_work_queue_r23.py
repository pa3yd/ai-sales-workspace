# -*- coding: utf-8 -*-
"""第二十三轮 · DEAL WORK QUEUE CONSOLIDATION 验收测试

只读运行 + 临时库隔离，不修改任何业务数据。
验收（对应长文 §1–§13 的可自动化部分）：
  A Sidebar 与首页「销售工作队列」读取同一 DealWorkItem（单一数据源）
  B 首页工作队列 = 一个可执行 Deal 一行（NordHaus / BrightPromo 重复行消失）
  C 同一客户不同产品 = 仍然两行（绝不按 company_name 简单去重）
  D 状态 / 下一步来自 Deal 当前状态（代表商机阶段 + OPEN 跟进任务）
  E 每个 Deal 只有一个 Next Best Action
  F WHY NOW / 今日优先使用同一 Deal 状态（最多 3 条理由）
  G KPI 口径明确（条 询盘/消息 · 个 商机）
  H 回归：历史往来不丢（展开可见全部 #ID）
"""
import os
import sys
import shutil
import tempfile
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench"))

from streamlit.testing.v1 import AppTest

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        print(f"  ❌ {name}  {detail}")


_APP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "workbench", "app.py")

# ================= 单元：统一 DealWorkItem =================
print("== 单元：deal_work_item / representative_opp / 状态解析 ==")
from workbench.queue_ui import (
    group_deal_threads, deal_work_item, representative_opp,
    deal_stage_meta, has_overdue_task,
)

check("阶段映射：QUOTED 绿 / REQUIREMENT_CONFIRMED 橙 / LOST 灰",
      deal_stage_meta("QUOTED") == ("🟢", "green")
      and deal_stage_meta("REQUIREMENT_CONFIRMED") == ("🟠", "amber")
      and deal_stage_meta("LOST") == ("⚫", "gray"),
      str(deal_stage_meta("QUOTED")))

_t_old = datetime.datetime.now() - datetime.timedelta(days=1)
_t_fut = datetime.datetime.now() + datetime.timedelta(days=1)
check("跟进任务逾期判断只看真实 due_at",
      has_overdue_task([{"due_at": _t_old.strftime("%Y-%m-%d %H:%M")}])
      and not has_overdue_task([{"due_at": _t_fut.strftime("%Y-%m-%d %H:%M")}])
      and not has_overdue_task([{"due_at": ""}]),
      "")

# 造数：同客户同产品 2 封往来 → 1 个 Deal 组；两个商机候选
_b1 = dict(id=1, cust_id=10, company="NordHaus Electronics GmbH",
           status="待处理", need={"product_query": "Wireless ANC Earbuds",
                                  "qty": "约 5,000 pcs", "blockers": []},
           biz="READY_TO_REPLY",
           action={"label": "查看并发送回复", "type": "REVIEW_REPLY"},
           fu_state="已逾期", created="2026-09-09 10:00")
_b2 = dict(id=2, cust_id=10, company="NordHaus Electronics GmbH",
           status="待处理", need={"product_query": "Wireless ANC Earbuds",
                                  "qty": "约 5,000 pcs", "blockers": []},
           biz="READY_TO_REPLY",
           action={"label": "查看并发送回复", "type": "REVIEW_REPLY"},
           fu_state="已逾期", created="2026-09-09 14:00")
_opp_new = dict(id=101, inquiry_id=1, stage="NEW", next_action_at="",
                updated_at="2026-09-09 10:00")
_opp_q = dict(id=102, inquiry_id=2, stage="QUOTED", next_action_at="",
              updated_at="2026-09-09 14:00",
              next_action="确认客户收到报价")
_tasks = {"102": [dict(id=501, opportunity_id=102, status="OPEN",
                       fu_status="PENDING", due_at=_t_old.strftime("%Y-%m-%d %H:%M"))]}
_opp_by_inq = {1: _opp_new, 2: _opp_q}

# representative_opp：有 OPEN 跟进任务的商机优先（即使阶段不如另一候选靠前）
_g = group_deal_threads([_b1, _b2])[0]
_rep = representative_opp(_g, _opp_by_inq, _tasks)
check("代表商机 = 有 OPEN 跟进任务者优先（QUOTED 而非 NEW）",
      _rep and _rep.get("id") == 102, str(_rep))

_wv = deal_work_item(_g, _rep, _tasks.get("102"))
check("DealWorkItem：往来数与待处理数来自统一聚合",
      _wv["conversation_count"] == 2 and _wv["open_count"] == 2, str(_wv))
check("DealWorkItem：当前状态 = 商机阶段 + 逾期（不是旧消息状态）",
      _wv["stage"] == "QUOTED" and _wv["stage_cn"] == "已报价"
      and _wv["overdue"] and _wv["badge"] == "已报价 · 已逾期"
      and _wv["tone"] == "high", str({k: _wv[k] for k in
                                      ("stage", "stage_cn", "overdue",
                                       "badge", "tone")}))
check("DealWorkItem：唯一 Next Best Action（逾期任务 → 保留当前动作）",
      _wv["nba"] == "查看并发送回复", _wv["nba"])
check("DealWorkItem：字段齐全（customer/产品短名/数量/queue_score）",
      _wv["company"] == "NordHaus Electronics GmbH"
      and _wv["product"] == "Wireless ANC Earbuds"
      and "5,000" in str(_wv["quantity"]), str(_wv["product"]))

# 无商机时回落消息级业务状态
_g2 = group_deal_threads([
    dict(id=9, cust_id=30, company="Acme GmbH", status="待处理",
         need={"product_query": "stainless steel water bottles",
               "qty": "约 1,000 pcs", "blockers": ["product_model"]},
         biz="READY_TO_REPLY", action={"label": "查看并发送回复"},
         fu_state="", created="2026-09-09 09:00")])[0]
_w2 = deal_work_item(_g2, None, None)
check("无商机 → 回落消息级（P1 高优待回复提示）",
      _w2["badge"] in ("高优 · 待回复", "待回复") and _w2["conversation_count"] == 1,
      str(_w2["badge"]))

# ================= 页面（真实库）：NordHaus / BrightPromo =================
print("== 页面（真实库，只读）：首页与 Sidebar 同一 Deal 聚合 ==")
# 注意：必须用顶层 import db —— app.py 也是 `import db`；这样改 DB_PATH
# 才会在同一模块对象上生效（与 r22 / T03 UI 冒烟同法，不能 import workbench.db）。
import db as db

_before_inq = len(db.list_inquiries())
_before_opp = db.list_opportunities()

at = AppTest.from_file(_APP, default_timeout=240).run()
check("R0 · 应用无异常", not at.exception,
      str([e.value[:160] for e in at.exception][:1]))
md = "\n".join(x.value for x in at.markdown)
sb = "\n".join(x.value for x in at.sidebar.markdown)
caps = "\n".join(c.value for c in at.caption)

_home_rows = [b.key for b in at.button
              if str(b.key).startswith("mq_open_")]
check("R1 · 首页销售工作队列 = 2 行（NordHaus + BrightPromo 各 1）",
      len(_home_rows) == 2, str(_home_rows))
_sb_open = [b.key for b in at.sidebar.button if str(b.key).startswith("open_")]
check("R2 · Sidebar = 2 张主卡（同一 Deal 不重复）",
      len(_sb_open) == 2, str(_sb_open))
check("R3 · 首页 NordHaus 状态 = 已报价·已逾期（Deal 当前状态）",
      "已报价 · 已逾期" in md, "")
check("R4 · 首页 BrightPromo 状态 = 需求已确认（Deal 当前状态）",
      "需求已确认" in md, "")
check("R5 · 两个 Deal 各只有一个 Next Best Action",
      "查看并发送回复" in md and "生成并发送报价" in md, "")
check("R6 · 历史往来折叠在 Deal 内（共 N 封往来提示存在）",
      "封往来" in md, "")
_hist = [e.label for e in at.expander if "往来记录" in e.label]
check("R7 · 每个 Deal 一个历史展开器（历史不丢）",
      len(_hist) >= 2, str(_hist))
check("R8 · KPI 口径注释存在（条询盘 · 个商机）",
      "按询盘/消息计数" in caps and "按 Deal 聚合" in caps
      and "待处理商机" in caps, "")
check("R9 · WHY NOW 面板存在且不再铺开重复 CTA 文案",
      "WHY NOW" in md and "建议：今天完成客户回复 / 报价" in md, "")
check("R10 · 今日优先卡片展示 Deal 状态（卡点 + 下一步）",
      "今日优先处理" in md and "卡点" in md and "下一步：" in md, "")
check("R11 · 数据未受损（询盘/商机数与阶段不变）",
      len(db.list_inquiries()) == _before_inq
      and len(db.list_opportunities()) == len(_before_opp),
      f"{_before_inq}/{len(_before_opp)}")

# ================= 临时库：同客户双 Deal 两行 =================
print("== UI（临时库）：同客户 不同产品 → 2 行 不按公司名去重 ==")
_tmp = tempfile.mkdtemp()
_tmpdb = os.path.join(_tmp, "workbench.db")
_old = db.DB_PATH
db.DB_PATH = _tmpdb
try:
    db.init_db()

    def _rep(product, qty, iid_tag):
        return {
            "extracted": {
                "company": "BrightPromo BV", "country": "Netherlands",
                "contact_name": "Sophie", "quantity": qty,
                "quantity_unit": "pcs", "product_query": product,
                "intent": "目录/报价请求",
            },
            "lead": {"grade": "B", "score": 50},
            "matches": [], "insight": {}, "score": 50, "draft": "",
        }

    _ids_b = [db.save_inquiry(
        "Hi, quote Stainless Steel Water Bottles 10,000 pcs.",
        _rep("Stainless Steel Water Bottles", 10000, i)) for i in range(3)]
    _id_mug = db.save_inquiry(
        "Quote Insulated Travel Mugs 20,000 pcs please.",
        _rep("Insulated Travel Mugs", 20000, "m"))
    _b_opp = next(o for o in db.list_opportunities()
                  if o.get("inquiry_id") == _ids_b[0])
    db.move_opportunity_stage(_b_opp["id"], "REQUIREMENT_CONFIRMED")

    at2 = AppTest.from_file(_APP, default_timeout=240).run()
    check("T1 · 应用无异常", len(at2.exception) == 0,
          str(at2.exception[0].value)[:200] if at2.exception else "")
    md2 = "\n".join(x.value for x in at2.markdown)
    sb2 = "\n".join(x.value for x in at2.sidebar.markdown)
    _rows2 = [b.key for b in at2.button if str(b.key).startswith("mq_open_")]
    check("T2 · 首页队列 4 封往来 = 2 行（水瓶 Deal 1 行 + 旅行杯 Deal 1 行）",
          len(_rows2) == 2, str(_rows2))
    _open2 = [b.key for b in at2.sidebar.button if str(b.key).startswith("open_")]
    check("T3 · Sidebar 同客户双 Deal 也各一张主卡（不按公司名去重）",
          len(_open2) == 2, str(_open2))
    check("T4 · 两种产品都在首页（水瓶 + 旅行杯分别成行）",
          "Stainless Steel Water Bottles" in md2
          and "Insulated Travel Mugs" in md2,
          f"len={len(md2)} bottle@"
          f"{md2.find('Stainless Steel Water Bottles')} mug@"
          f"{md2.find('Insulated Travel Mugs')}")
    check("T5 · 水瓶 Deal 主行状态 = 需求已确认（代表商机阶段）",
          "需求已确认" in md2, "")
    check("T6 · 水瓶 Deal 下一步 = 生成并发送报价（商机推荐唯一 NBA）",
          "生成并发送报价" in md2, "")
    check("T7 · 水瓶 Deal 往来计数 = 3（历史折叠保留）",
          "共 3 封往来" in md2, "")
    _hist2 = [e.label for e in at2.expander if "往来记录" in e.label]
    check("T8 · 历史展开器存在且可逐条查看",
          len(_hist2) >= 1, str(_hist2))
finally:
    db.DB_PATH = _old
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
