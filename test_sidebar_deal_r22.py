# -*- coding: utf-8 -*-
"""Round 7.0 CRM V1 FREEZE — R22 Sidebar Deal 聚合业务意图迁移。

CRM V1 已冻结。Sidebar 在第 6.8 轮调整为「轻量 CRM Navigation + Deal Quick
Access」：默认不显示 Deal ID / 消息数 / 时间戳 / 历史摘要（数量变更等历史
信息进入 Deal Detail 的 Activity Timeline）。R22 原意是「同客户同产品
聚合为一张主卡」，这一核心业务约束保留；具体可见字段由 R6.8 / R6.9 定版。

只读运行 + 临时库隔离，不修改任何业务数据。

迁移后业务意图：
  单元（queue_ui 聚合不依赖 Streamlit）
    A  同客户同产品 → 同 Deal 键（不按公司名去重）
    B  同客户不同产品 → 不同 Deal 键
    C  3 条往来（2 同产品 + 1 不同）→ 2 个 Deal
    D  产品未知 → 单条隔离
    E  已成交历史 + 新活跃 → 分卡
    F  deal_current_state：往来数 + 待处理数 + 最近时间 + lead 输出
  页面（AppTest + 临时库 BrightPromo 3 瓶 + 1 杯）
    UI-0  应用无异常
    UI-1  4 条往来只渲染 2 张主卡（同瓶 3 封 → 1 卡；杯 1 封 → 1 卡）
    UI-2  两张卡都是 BrightPromo BV（同客户不同 Deal 分开，不重复）
    UI-3  卡 1 产品 = 水瓶短名；数量从主卡 need.qty 取（约 15,000）
    UI-4  卡 2 产品 = 旅行杯短名（不同 Deal 独立成卡）
    UI-5  状态来自 Deal 当前阶段（需求已确认）而不是某条旧消息
    UI-6  Sidebar 主卡不再显示 Deal ID / 消息数 / 时间戳 / 历史摘要
          （R6.8 约定；这些信息进入 Deal Detail 的 Activity Timeline）
    UI-7  排序基于 Deal（同一 Deal 只参与一次）
"""
import os
import re
import sys
import shutil
import tempfile

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


# ================= 单元：Deal 身份 / 聚合 / 当前状态 =================
print("== 单元：deal_thread_key / group_deal_threads / deal_current_state ==")
from workbench.queue_ui import (
    deal_thread_key, group_deal_threads, deal_current_state, priority_tier,
)

# A 同客户 + 同产品 → 同一把 Deal 键
_d1 = dict(id=1, cust_id=10, company="BrightPromo BV", status="待处理",
           need={"product_query": "stainless steel water bottles", "qty": "约 10,000 pcs"},
           biz="READY_TO_REPLY", action={"label": "查看并发送回复", "type": "REVIEW_REPLY"},
           fu_state="", created="2026-09-09 10:00")
_d2 = dict(id=2, cust_id=10, company="BrightPromo BV", status="待处理",
           need={"product_query": "stainless steel water bottles 10000pcs logo",
                 "qty": "约 10,000 pcs"},
           biz="READY_TO_REPLY", action={"label": "查看并发送回复", "type": "REVIEW_REPLY"},
           fu_state="", created="2026-09-09 11:00")
# B 同客户 + 不同产品 → 不同 Deal
_mug = dict(id=3, cust_id=10, company="BrightPromo BV", status="待处理",
            need={"product_query": "Insulated Travel Mugs", "qty": "约 20,000 pcs"},
            biz="READY_TO_REPLY", action={"label": "查看并发送回复", "type": "REVIEW_REPLY"},
            fu_state="", created="2026-09-09 12:00")

check("A · 同客户同产品 → 同一把 Deal 键",
      deal_thread_key(_d1) == deal_thread_key(_d2),
      str(deal_thread_key(_d1)) + " vs " + str(deal_thread_key(_d2)))
check("B · 同客户不同产品 → 不同 Deal 键（不按公司名去重）",
      deal_thread_key(_d1) != deal_thread_key(_mug),
      str(deal_thread_key(_mug)))

# C 3 条往来（2 同产品 + 1 不同）→ 2 个 Deal
_gs = group_deal_threads([_d2, _d1, _mug])
check("C · 3 条往来（2 同一产品 + 1 新产品）聚合为 2 个 Deal",
      len(_gs) == 2, str([(g["count"], g["lead"]["id"]) for g in _gs]))
_g_bottle = next(g for g in _gs if g["count"] == 2)
check("C · 组 lead = 排序最靠前（最该先处理）的往来",
      _g_bottle["lead"]["id"] == _d2["id"], str(_g_bottle["lead"]["id"]))
# F
_st = deal_current_state(_g_bottle)
check("F · deal_current_state：往来数与待处理数",
      _st["conversation_count"] == 2 and _st["open_count"] == 2, str(_st))
check("F · deal_current_state：最近往来时间取组内最新",
      _st["last_created"] == "2026-09-09 11:00", _st["last_created"])
check("F · deal_current_state：lead 信息输出完整",
      _st["lead_id"] == 2 and _st["nba"] == "查看并发送回复"
      and _st["tier"] == priority_tier(_d2), str(_st))

# D 产品未知：退回单条隔离（宁可不合并，也不误并）
_np1 = dict(id=5, cust_id=20, company="Acme Co", status="待处理",
            need={}, biz="NEW", action={}, fu_state="", created="2026-09-09 09:00")
_np2 = dict(id=6, cust_id=20, company="Acme Co", status="待处理",
            need={}, biz="NEW", action={}, fu_state="", created="2026-09-09 09:10")
_gnp = group_deal_threads([_np1, _np2])
check("D · 产品未知 → 单条隔离（不把两个未知 Deal 误并）",
      len(_gnp) == 2, str(len(_gnp)))

# E 终态（WON）与新的活跃同产品往来分开成卡
_lost = dict(id=7, cust_id=30, company="Zeta AB", status="已处理",
             need={"product_query": "Wireless Earbuds"}, biz="REPLIED",
             action={}, fu_state="", created="2026-09-01 09:00")
_new = dict(id=8, cust_id=30, company="Zeta AB", status="待处理",
            need={"product_query": "Wireless Earbuds"}, biz="NEW",
            action={"label": "分析询盘"}, fu_state="", created="2026-09-09 09:00")
_stage_of = {7: "WON", 8: "NEW"}
_gz = group_deal_threads([_lost, _new], _stage_of)
check("E · 已成交历史与新的活跃往来分开成卡",
      len(_gz) == 2, str([(g["count"], g["key"][3]) for g in _gz]))

# ================= 临时库 UI：BrightPromo 3 瓶 + 1 杯 =================
print("== UI（临时库）：重复合并 + 同客户双 Deal + 状态来自 Deal 当前阶段 ==")
# 必须用顶层 import db —— AppTest 内 app.py 也是 `import db`，
# 这样改 DB_PATH 才会在同一模块对象上生效（与 T03 UI 冒烟同法）。
import db as _db


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


_tmp = tempfile.mkdtemp()
_tmpdb = os.path.join(_tmp, "workbench.db")
_old = _db.DB_PATH
_db.DB_PATH = _tmpdb
try:
    _db.init_db()
    # 3 瓶：qty 取最新一条的值（lead 排序最靠前）= 15,000
    _ids_b = [_db.save_inquiry(
        "Hi, quote for Stainless Steel Water Bottles %d,000 pcs." % ((i + 1) * 5),
        _rep("Stainless Steel Water Bottles", (i + 1) * 5000, i)) for i in range(3)]
    _id_mug = _db.save_inquiry(
        "Quote Insulated Travel Mugs 20,000 pcs please.",
        _rep("Insulated Travel Mugs", 20000, "m"))
    # 水瓶组代表商机推进到「需求已确认」→ 主卡显示 Deal 阶段
    _b_opp = next(o for o in _db.list_opportunities()
                  if o.get("inquiry_id") == _ids_b[0])
    _db.move_opportunity_stage(_b_opp["id"], "REQUIREMENT_CONFIRMED")

    at = AppTest.from_file(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "workbench", "app.py"),
                           default_timeout=90).run()
    check("UI-0 · 应用无异常渲染", len(at.exception) == 0,
          str(at.exception[0].value)[:200] if at.exception else "")
    _sb_md = "\n".join(x.value for x in at.sidebar.markdown)
    _open_keys = [b.key for b in at.sidebar.button
                  if str(b.key or "").startswith("open_recent_")]
    check("UI-1 · 侧栏默认不复制销售工作队列", len(_open_keys) == 0,
          str(_open_keys))
    check("UI-2 · 默认显示最近访问空状态", "打开商机后会显示在这里" in _sb_md,
          _sb_md[:300])

    # 访问记录按 Deal 聚合：同一水瓶 Deal 的 3 封往来只展示一张卡；旅行杯独立。
    at.session_state["recent_opened_inquiry_ids"] = [_ids_b[-1], _ids_b[0], _id_mug]
    at.run()
    _sb_md = "\n".join(x.value for x in at.sidebar.markdown)
    _open_keys = [b.key for b in at.sidebar.button
                  if str(b.key or "").startswith("open_recent_")]
    _open_ids = [int(str(k).split("_")[-1]) for k in _open_keys]
    check("UI-3 · 最近访问按 Deal 聚合（同瓶 3 封 → 1 条）",
          len(_open_ids) == 2, str(_open_ids))
    check("UI-4 · 最近访问保留不同 Deal（旅行杯独立）",
          "Stainless Steel Water Bottles" in _sb_md and "Insulated Travel Mugs" in _sb_md,
          _sb_md[:500])
    check("UI-5 · 卡片显示 Deal 当前执行状态",
          any(label in _sb_md for label in ("需求已确认", "待内部处理", "待回复", "待报价")),
          _sb_md[:500])
    _no_msg_count = not bool(re.search(r"\d+\s*条往来", _sb_md)) \
                    and not bool(re.search(r"\d+\s*条询盘", _sb_md))
    check("UI-6 · 最近访问不显示消息数 / 历史摘要", _no_msg_count,
          "sidebar 含 '条往来/条询盘'")
finally:
    _db.DB_PATH = _old
    shutil.rmtree(_tmp, ignore_errors=True)

# ================= 排序基于 Deal（每 Deal 只排一次） =================
print("== 单元：Deal 排序去重 ==")
# 同一 Deal 的 3 封消息放进已排序输入 → 输出只有 1 个该 Deal 的位次
_dup3 = group_deal_threads([
    dict(id=1, cust_id=10, company="BrightPromo BV",
         need={"product_query": "water bottles"}, biz="READY_TO_REPLY",
         action={}, fu_state="", status="待处理", created="2026-09-09 08:00"),
    dict(id=2, cust_id=10, company="BrightPromo BV",
         need={"product_query": "water bottles"}, biz="READY_TO_REPLY",
         action={}, fu_state="", status="待处理", created="2026-09-09 09:00"),
    dict(id=3, cust_id=10, company="BrightPromo BV",
         need={"product_query": "water bottles"}, biz="READY_TO_REPLY",
         action={}, fu_state="", status="待处理", created="2026-09-09 10:00"),
])
check("UI-7 · 同一 Deal 在排序队列只占一个位次", len(_dup3) == 1,
      str([(g["count"]) for g in _dup3]))

print()
print(f"结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
