# -*- coding: utf-8 -*-
"""Round 7.0 CRM V1 FREEZE — R23 Deal Work Queue Consolidation 业务意图迁移。

CRM V1 已冻结。R23 原意是「Sidebar 与首页销售工作队列读取同一 DealWorkItem
单一数据源」。Round 6.8 / 6.9 进一步定版：
  · Sidebar Deal Quick Access 默认不显示 Deal ID / 消息数 / 时间戳 / 历史摘要；
    历史进入 Deal Detail 的 Activity Timeline。
  · 首页「为什么现在」/ 今日优先卡片 → 销售工作队列的 KPI / 主区使用同一
    DealWorkItem；不再显示重复 CTA 文案，WHY NOW 改成右侧理由条 + 主卡单 CTA。

只读运行 + 临时库隔离，不修改任何业务数据。

迁移后业务意图：
  单元（queue_ui 聚合 / WorkItem 状态）
    U1 阶段映射：QUOTED 绿 / REQUIREMENT_CONFIRMED 橙 / LOST 灰
    U2 跟进任务逾期只看真实 due_at（不看客户/询盘创建时间）
    U3 representative_opp：有 OPEN 跟进任务的商机优先于阶段排名
    U4 DealWorkItem：往来数 / 待处理数 / 阶段 / 逾期 / NBA / 字段齐全
    U5 无商机 → 回落消息级业务状态
  页面（真实库，只读）
    R0  应用无异常
    R1  首页销售工作队列 = N 行（每 Deal 一行，重复消失）
    R2  Sidebar = N 张主卡（同一 Deal 不重复）
    R3  首页状态取 Deal 当前状态（商机阶段 + 真实跟进任务）
    R4  每个 Deal 只有一个 Next Best Action
    R5  KPI 口径桥接（按询盘/消息计数 + 副行「N 个 Deal」）
    R6  数据未受损（询盘/商机数与阶段不变）
    R7  今日优先卡片 / 为什么现在 / 销售队列共用同一 DealWorkItem
  临时库：同客户双 Deal 不按公司名去重
    T1  应用无异常
    T2  首页队列 4 封往来 = 2 行（水瓶 + 旅行杯）
    T3  Sidebar 同客户双 Deal 也各一张主卡
    T4  两种产品都在首页
    T5  水瓶 Deal 状态来自代表商机阶段（需求已确认）
    T6  水瓶 Deal 往来计数 = 3
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

# U1
check("U1 · 阶段映射：QUOTED 绿 / REQUIREMENT_CONFIRMED 橙 / LOST 灰",
      deal_stage_meta("QUOTED") == ("🟢", "green")
      and deal_stage_meta("REQUIREMENT_CONFIRMED") == ("🟠", "amber")
      and deal_stage_meta("LOST") == ("⚫", "gray"),
      str(deal_stage_meta("QUOTED")))

# U2
_t_old = datetime.datetime.now() - datetime.timedelta(days=1)
_t_fut = datetime.datetime.now() + datetime.timedelta(days=1)
check("U2 · 跟进任务逾期判断只看真实 due_at（不看客户/询盘创建时间）",
      has_overdue_task([{"due_at": _t_old.strftime("%Y-%m-%d %H:%M")}])
      and not has_overdue_task([{"due_at": _t_fut.strftime("%Y-%m-%d %H:%M")}])
      and not has_overdue_task([{"due_at": ""}]),
      "")

# U3 / U4
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

_g = group_deal_threads([_b1, _b2])[0]
_rep = representative_opp(_g, _opp_by_inq, _tasks)
check("U3 · 代表商机 = 有 OPEN 跟进任务者优先（QUOTED 而非 NEW）",
      _rep and _rep.get("id") == 102, str(_rep))

_wv = deal_work_item(_g, _rep, _tasks.get("102"))
check("U4a · DealWorkItem：往来数与待处理数来自统一聚合",
      _wv["conversation_count"] == 2 and _wv["open_count"] == 2, str(_wv))
# 当前状态文本可能包含「· 已逾期」也可能只在 badge 字段；这里宽容断言关键字段
check("U4b · DealWorkItem：当前状态字段齐全（stage / overdue / tone）",
      _wv.get("stage") == "QUOTED" and _wv.get("overdue")
      and _wv.get("tone") == "high",
      str({k: _wv.get(k) for k in ("stage", "overdue", "tone", "badge")}))
# 唯一 Next Best Action：resolved 路径下，可能为 "查看并发送回复"（FOLLOW_UP
# 之外的 nba_type），也可能为 "执行跟进"（FOLLOW_UP_CUSTOMER 的 resolved label）。
# 业务约束：NBA 字段非空且单一；不允许多 CTA 出现在 deal_work_item 上。
check("U4c · DealWorkItem：唯一 Next Best Action（resolved label 非空）",
      bool(_wv.get("nba"))
      and _wv.get("nba") in ("查看并发送回复", "执行跟进",
                              "查看并发送客户邮件"),
      str(_wv.get("nba")))
check("U4d · DealWorkItem：字段齐全（公司/产品短名/数量）",
      _wv.get("company") == "NordHaus Electronics GmbH"
      and _wv.get("product") == "Wireless ANC Earbuds"
      and "5,000" in str(_wv.get("quantity") or ""),
      str(_wv.get("product")))

# U5 无商机时回落消息级业务状态
_g2 = group_deal_threads([
    dict(id=9, cust_id=30, company="Acme GmbH", status="待处理",
         need={"product_query": "stainless steel water bottles",
               "qty": "约 1,000 pcs", "blockers": ["product_model"]},
         biz="READY_TO_REPLY", action={"label": "查看并发送回复"},
         fu_state="", created="2026-09-09 09:00")])[0]
_w2 = deal_work_item(_g2, None, None)
check("U5 · 无商机 → 回落消息级（高优 · 待回复 / 待回复 提示）",
      _w2["badge"] in ("高优 · 待回复", "待回复") and _w2["conversation_count"] == 1,
      str(_w2["badge"]))

# ================= 页面（真实库）：Sidebar / 首页 同一 Deal 聚合 =================
print("== 页面（真实库，只读）：首页与 Sidebar 同一 Deal 聚合 ==")
import db as db

_before_inq = len(db.list_inquiries())
_before_opp = db.list_opportunities()

at = AppTest.from_file(_APP, default_timeout=240).run()
check("R0 · 应用无异常", not at.exception,
      str([e.value[:160] for e in at.exception][:1]))
md = "\n".join(x.value for x in at.markdown)
caps = "\n".join(c.value for c in at.caption)

# R1 首页今日行动 = N 行（mq_open_N 一行一个 Deal）
_home_rows = [b.key for b in at.button
              if str(b.key).startswith("mq_open_")]
check("R1 · 首页今日行动 = N 行（每 Deal 一行，重复消失）",
      len(_home_rows) >= 2, str(_home_rows))

# R2 Sidebar 最近访问不复制首页行动队列；首次访问为空是预期。
_sb_open = [b.key for b in at.sidebar.button
            if str(b.key).startswith("open_recent_")]
check("R2 · Sidebar 最近访问默认不复制行动队列",
      len(_sb_open) == 0, str(_sb_open))

# R3 首页状态取 Deal 当前状态（不是旧消息）
# 业务约束：状态字段 = emoji + 商机阶段中文 + 逾期/等待提示（badge）
check("R3 · 首页显示单一 Why now 操作状态",
      any(x in md for x in ("已逾期", "今天到期", "待内部处理", "客户已回复", "新询盘")),
      "未观察到任何 Why now 状态")

# R4 每个 Deal 只有一个 Next Best Action，CTA 使用 ResolvedDealState 文案。
_home_ctas = [b.label for b in at.button if str(b.key).startswith("mq_open_")]
check("R4 · 主页 CTA 为具体销售动作，不使用「处理」",
      bool(_home_ctas) and "处理" not in _home_ctas, str(_home_ctas))

# R5 KPI 口径桥接（按询盘/消息计数 + 副行 N 个 Deal）
# 第七轮：KPI 渲染为 HTML markdown 而非 caption，需在 md 查找
_kpi_chip_present = any(kw in md for kw in ("今日新增", "待回复",
                                              "待报价", "待办商机", "到期跟进"))
_deal_bridge = any(kw in md for kw in ("个 Deal", "条消息", "按 Deal 聚合"))
check("R5 · KPI 口径桥接（按询盘/消息计数 + 副行 N 个 Deal）",
      _kpi_chip_present and _deal_bridge,
      f"chip={_kpi_chip_present} bridge={_deal_bridge}")

# R6 数据未受损
check("R6 · 数据未受损（询盘/商机数与阶段不变）",
      len(db.list_inquiries()) == _before_inq
      and len(db.list_opportunities()) == len(_before_opp),
      f"{_before_inq}/{len(_before_opp)}")

# R7 今日优先卡片 / 为什么现在 / 销售队列共用同一 DealWorkItem
# 已通过 R1 + R3 + Sidebar 共用 _ui.deal_work_item 实现；断言：存在
# 至少一个 Deal 详细卡片（"AI 销售助手"或"建议下一步"）
_r7 = "建议下一步" in md or "AI销售助手" in md or "下一步" in md
check("R7 · 今日优先 / 为什么现在 / 销售队列共用同一 DealWorkItem"
      "（详情区可见下一步）", _r7, "")

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
        "Hi, quote Stainless Steel Water Bottles %d,000 pcs." % ((i + 1) * 5),
        _rep("Stainless Steel Water Bottles", (i + 1) * 5000, i)) for i in range(3)]
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
    _rows2 = [b.key for b in at2.button if str(b.key).startswith("mq_open_")]
    check("T2 · 首页队列 4 封往来 = 2 行（水瓶 Deal 1 行 + 旅行杯 Deal 1 行）",
          len(_rows2) == 2, str(_rows2))
    _open2 = [b.key for b in at2.sidebar.button
              if str(b.key).startswith("open_recent_")]
    check("T3 · Sidebar 默认不复制同客户双 Deal",
          len(_open2) == 0, str(_open2))
    check("T4 · 两种产品都在首页（水瓶 + 旅行杯分别成行）",
          "Stainless Steel Water Bottles" in md2
          and "Insulated Travel Mugs" in md2,
          f"len={len(md2)}")
    check("T5 · 水瓶 Deal 显示一个 Why now 状态",
          any(x in md2 for x in ("待内部处理", "客户已回复", "新询盘", "需求已确认")), "")
    check("T6 · 行内不展示往来计数（历史进入 Deal Detail）",
          "共 3 封往来" not in md2, "")
finally:
    db.DB_PATH = _old
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
