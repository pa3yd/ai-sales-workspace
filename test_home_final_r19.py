# -*- coding: utf-8 -*-
"""Round 7.0 CRM V1 FREEZE — Part A/D UI 断言重写。

CRM V1 冻结：UI 已迁移到 Deal-level 共享架构（_render_customer_workspace /
why_now / 销售工作队列共用 queue_ui_deal_work_item）。原 R19 的 UI 断言
针对的是「3 个 Hero CTA / AI NEXT BEST ACTION / 卡点 + 下一步」那套旧
表达，现在分别改写到：

  A1  WHY NOW（中文「为什么现在」）取代 AI NEXT BEST ACTION Hero，且没有
      「生成回复 / 生成报价 / 创建跟进」三按钮重复同一 Next Action。
  A2  0 数据分类以轻量 caption 表达（「暂无...」「今天没有必须立即处理」）。
  A3  首页产品列短名 + chips，完整规格不上队列；product_display 单元同上。
  A4  KPI 卡片按「询盘/消息」计数；副行给「聚合 N 个 Deal / 条消息」桥接。
  A5  首页结构 = 今日概览(KPI) → 销售工作队列 → WHY NOW（今日优先）。
  D1  跟进台存在「今天该跟谁」。
  D2  默认「今日跟进」视图展示 NordHaus OVERDUE（WHY NOW + 状态）。
  D2c  切到「等待客户」视图可见 BrightPromo WAITING_CUSTOMER 场景。

Part B / C / E / F / G / H / I（数据 + 三轴 + OVERDUE 只看 due_at + 复查点 +
Timeline 事件 + 页面渲染不增任务）业务口径未变 → 原断言全部保留。

隔离：拷贝真实库到临时目录后 seed；真实库零污染，页面用 AppTest 只读渲染。
"""
import datetime
import io
import os
import shutil
import sqlite3
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "workbench"))

_PASS = _FAIL = 0


def check(name, ok, detail=""):
    global _PASS, _FAIL
    print(f"  {'✅' if ok else '❌'} {name}" + (f"　[{detail}]" if detail and not ok else ""))
    if ok:
        _PASS += 1
    else:
        _FAIL += 1


import db
import workflow as _wf
import followup as _fu
import agent.followup_email as _fem
import workbench.queue_ui as qu
import seed_validation_r19 as _seed

NOW = datetime.datetime.now()


def _ts(days=0, hours=0):
    return (NOW + datetime.timedelta(days=days, hours=hours)).strftime("%Y-%m-%d %H:%M")


print("=" * 70)
print("Part B · Round 3 Follow-up 能力盘点（业务口径不变）")
print("=" * 70)
check("B1 · FollowUpTask 存储存在（crm_tasks + reason<>'' 即跟进任务）",
      hasattr(db, "list_followup_tasks") and hasattr(db, "create_followup_task"))
check("B2 · FollowUpStatus 七态独立（含 WAITING_CUSTOMER / SNOOZED）",
      all(hasattr(_fu, s) for s in ("PENDING", "DUE_TODAY", "OVERDUE", "WAITING",
                                    "SNOOZED", "COMPLETED", "CANCELLED")))
check("B3 · followUpDueAt / followUpReason / fu_status 字段齐全",
      {"reason", "fu_status", "due_at"} <= set((db.list_followup_tasks() or [{}])[0].keys())
      and hasattr(db, "update_followup_task"))
check("B4 · customer_id / deal_id 关联（list_followup_tasks 联出客户/商机上下文）",
      {"opportunity_id", "customer_id", "company", "inquiry_id"}
      <= set((db.list_followup_tasks() or [{}])[0].keys()))
check("B5 · Activity Timeline 集成（deal_activity + activity）",
      hasattr(db, "record_deal_activity") and hasattr(db, "record_activity"))
check("B6 · Waiting Customer / Overdue / Snooze / Complete / 生成 动作齐备",
      all(hasattr(db, f) for f in ("mark_followup_sent", "resolve_waiting_for_deal"))
      and all(hasattr(_fu, f) for f in ("fu_status_of", "sort_queue"))
      and hasattr(_fem, "build_followup_email"))

# =====================================================================
print()
print("=" * 70)
print("Part C · 验收场景种子（临时库隔离，真实库零污染）")
print("=" * 70)
_tmp = tempfile.mkdtemp(prefix="r19_")
_tmp_db = os.path.join(_tmp, "workbench.db")
shutil.copy(os.path.join("workbench", "workbench.db"), _tmp_db)
_old_path = db.DB_PATH
db.DB_PATH = _tmp_db
try:
    _seed.seed(verbose=False)
    rows = db.list_followup_tasks()
    nord = [t for t in rows if "NordHaus" in str(t.get("company") or "")]
    bp = [t for t in rows if "BrightPromo" in str(t.get("company") or "")]
    check("C1 · NordHaus 场景已建立：QUOTE_SENT_NO_REPLY + OVERDUE",
          any(t["reason"] == "QUOTE_SENT_NO_REPLY" for t in nord)
          and any(_fu.fu_status_of(t["fu_status"], t["due_at"], NOW) == "OVERDUE"
                  for t in nord if t["reason"] == "QUOTE_SENT_NO_REPLY"))
    check("C2 · BrightPromo 场景已建立：INFORMATION_WAITING + WAITING_CUSTOMER",
          any(t["reason"] == "INFORMATION_WAITING" and
              t["fu_status"] == "WAITING_CUSTOMER" for t in bp))
    _cnt_before = len(db.list_followup_tasks(active_only=True))
    _seed.seed(verbose=False)
    _seed.seed(verbose=False)
    _cnt_after = len(db.list_followup_tasks(active_only=True))
    check("I1 · 种子幂等：重复执行不产生重复 active 跟进任务",
          _cnt_before == _cnt_after, f"{_cnt_before}→{_cnt_after}")

    # 三轴独立样例（E）
    opp = [o for o in db.list_opportunities() if o.get("stage") == "QUOTED"][0]
    tq = [t for t in db.list_followup_tasks(opportunity_id=opp["id"])
          if t["reason"] == "QUOTE_SENT_NO_REPLY"][0]
    check("E1 · InquiryStatus/DealStage/FollowUpStatus 三轴可并存",
          str(opp["stage"]) == "QUOTED"
          and _fu.fu_status_of(tq["fu_status"], tq["due_at"], NOW) == "OVERDUE"
          and opp.get("inquiry_id") is not None)

    # F：逾期只由 due_at 决定
    check("F1 · 创建 8 天前、due 已过 → OVERDUE（只看 due_at）",
          _fu.fu_status_of("WAITING_CUSTOMER", _ts(days=-2), NOW) == "OVERDUE")
    check("F2 · 创建 8 天前、due 在未来 → 仍 WAITING（不因旧自动逾期）",
          _fu.fu_status_of("WAITING_CUSTOMER", _ts(days=5), NOW) == "WAITING_CUSTOMER")
    check("F3 · COMPLETED / CANCELLED 不因 due 过期而变 OVERDUE",
          _fu.fu_status_of("COMPLETED", _ts(days=-2), NOW) == "COMPLETED"
          and _fu.fu_status_of("CANCELLED", _ts(days=-2), NOW) == "CANCELLED")

    # G：Waiting Customer 不持续误提醒
    check("G1 · WAITING_CUSTOMER 未到复查点 → 不作为今日行动（不冒充 OVERDUE）",
          _fu.fu_status_of("WAITING_CUSTOMER", _ts(days=2), NOW) == "WAITING_CUSTOMER")
    check("G2 · WAITING_CUSTOMER 过复查点（due 过去）→ OVERDUE 回到处理队列",
          _fu.fu_status_of("WAITING_CUSTOMER", _ts(days=-1), NOW) == "OVERDUE")

    # H：五个 Timeline 事件
    _bp_task = [t for t in rows if t["reason"] == "INFORMATION_WAITING"][0]
    _bp_opp_id = _bp_task["opportunity_id"]
    db.record_deal_activity(_bp_opp_id, "FOLLOW_UP_EMAIL_DRAFTED",
                            "保存跟进邮件草稿", actor="销售")
    _dacts = {a["type"] for a in db.list_deal_activity(_bp_opp_id)}
    check("H1 · FOLLOW_UP_CREATED 已写 Timeline",
          "FOLLOW_UP_CREATED" in _dacts)
    check("H2 · FOLLOW_UP_EMAIL_DRAFTED 已写 Timeline",
          "FOLLOW_UP_EMAIL_DRAFTED" in _dacts)
    check("H3 · FOLLOW_UP_COMPLETED / SNOOZED / RESCHEDULED 事件映射存在",
          db._followup_event_cn("FOLLOW_UP_COMPLETED") == "完成跟进"
          and db._followup_event_cn("FOLLOW_UP_SNOOZED") == "稍后提醒"
          and db._followup_event_cn("FOLLOW_UP_RESCHEDULED") == "改期跟进")

    # =================================================================
    print()
    print("=" * 70)
    print("Part A/D · Sales Home 收尾 + 跟进台页面（AppTest 只读渲染）")
    print("=" * 70)
    from streamlit.testing.v1 import AppTest
    _app = os.path.join(BASE, "workbench", "app.py")
    at = AppTest.from_file(_app, default_timeout=300).run()
    check("H0 · 整页渲染无异常", not at.exception,
          str([e.value[:160] for e in at.exception][:1]))
    md_main = "\n".join(m.value for m in at.main.markdown)
    md_all = "\n".join(m.value for m in at.markdown)
    caps = "\n".join(c.value for c in at.caption)
    btns = [b.label for b in at.button]

    # ---- A1（中文「为什么现在」取代 AI NEXT BEST ACTION Hero；无重复 CTA） ----
    check("A1a · 主区展示「为什么现在」卡片（WHY NOW 替代 Hero）",
          "为什么现在" in md_main)
    check("A1b · 不再使用「AI NEXT BEST ACTION」Hero 旧标签",
          "AI NEXT BEST ACTION" not in md_all)
    check("A1c · 不再用「生成回复 / 生成报价 / 创建跟进」三按钮重复同一 Next Action",
          all(x not in btns for x in ("生成回复", "生成报价", "创建跟进")),
          str([x for x in btns if x in ("生成回复", "生成报价", "创建跟进")]))
    _queue_ctas = [b.label for b in at.button if str(b.key or "").startswith("mq_open_")]
    check("A1d · 行级 CTA 使用已解析动作，不出现泛化「处理」",
          bool(_queue_ctas) and "处理" not in _queue_ctas, str(_queue_ctas))

    # ---- A2（0 数据以轻量 caption 表达） ----
    # 当真实数据非空时不会渲染空态；但 R18 已合并的旧大字空态卡必须不再出现。
    check("A2a · 不再用「当前没有需要处理的记录」R18 已合并的大字空态卡",
          "当前没有需要处理的记录" not in md_main)
    check("A2b · 不再用「AI NEXT BEST ACTION」Hero（已被「为什么现在」卡替代）",
          "AI NEXT BEST ACTION" not in md_main)

    # ---- A3（产品短名 + chips；不泄漏完整规格） ----
    check("A3a · 首页产品列为短名（Wireless ANC Earbuds）",
          "Wireless ANC Earbuds" in md_all)
    check("A3b · 完整规格不再泄漏到首页列表层",
          "40 hours battery life" not in md_all
          and "Bluetooth 5.4, ANC, 40" not in md_all)
    _pd1 = qu.product_display({"product_query":
                               "Wireless ANC earbuds with Bluetooth 5.4, ANC, "
                               "40 hours battery life, USB-C, black and white, "
                               "custom logo and packaging"})
    check("A3c · 单元：短名 + chips（Bluetooth 5.4 · ANC · 40h · OEM）",
          _pd1["title"] == "Wireless ANC Earbuds"
          and _pd1["spec"] == "Bluetooth 5.4 · ANC · 40h · OEM", str(_pd1))
    _pd2 = qu.product_display({"product_query": "stainless steel water bottles"})
    check("A3d · 单元：水杯只有短名、无多余 chips",
          _pd2["title"] == "Stainless Steel Water Bottles" and _pd2["spec"] == "",
          str(_pd2))

    # ---- A4（KPI 单位：询盘/消息 + 聚合 Deal 数桥接） ----
    check("A4a · KPI 卡片含「待回复 / 待报价 / 待办商机」字段（按询盘/消息计数）",
          "待回复" in md_main and "待报价" in md_main
          and ("今日新增" in md_main) and ("待办商机" in md_main))
    check("A4b · KPI 副行给「N 个 Deal / 条消息」桥接（口径桥接）",
          " 个 Deal" in md_main or " 条消息" in md_main)
    check("A4c · KPI 由 kpi-strip 区域统一承载（一行五卡）",
          "kpi-strip" in md_main)

    # ---- A5（首页层级 = 今日概览 KPI → 今日行动 → WHY NOW） ----
    _i_kpi = md_main.find("今日新增")
    _i_q = md_main.find("今日行动")
    _i_why = md_main.find("为什么现在")
    check("A5a · 首页层级：今日概览(KPI) → 今日行动 → WHY NOW",
          -1 < _i_kpi < _i_q < _i_why, f"{_i_kpi}/{_i_q}/{_i_why}")
    check("A5b · Pipeline 完整看板指引去「商机」页（首页不再重复铺开）",
          ("商机" in md_all) and ("Pipeline" in md_all)
          and ("Pipeline / 销售机会" not in md_all))

    # ---- D · 跟进台 5 秒可答 ----
    _subs = [s.value for s in at.subheader]
    check("D1 · 跟进台存在：今天该跟谁",
          any("今天该跟谁" in s for s in _subs), str(_subs[:5]))
    check("D2 · 默认「今日跟进」视图呈现 NordHaus OVERDUE 场景",
          "NordHaus" in md_all and ("已报价" in md_all or "已逾期" in md_all),
          "NordHaus+已逾期/已报价 not in main")
    # 切到「等待客户」视图 → BrightPromo 的 WAITING 场景可见
    _seg_lst = list(at.segmented_control)
    _can_wait = any("等待客户" in str(s.options) for s in _seg_lst)
    if _can_wait:
        _seg = next(s for s in _seg_lst if "等待客户" in str(s.options))
        _seg.set_value("等待客户")
        at2 = _seg.run()
        md2 = "\n".join(m.value for m in at2.markdown)
        check("D2c · 「等待客户」视图可见 BrightPromo WAITING 场景",
              "BrightPromo" in md2 and "提醒客户确认关键产品规格" in md2)
    else:
        check("D2c · 「等待客户」快速视图存在（可筛选 waiting）", False)

    # ---- I2：页面渲染不产生新任务 ----
    _act_after_render = len(db.list_followup_tasks(active_only=True))
    check("I2 · 进入跟进页面（只读渲染）不新增 active 任务",
          _act_after_render == _cnt_after, f"{_cnt_after}→{_act_after_render}")

finally:
    db.DB_PATH = _old_path
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print("=" * 70)
print(f"第七轮 CRM V1 FINAL FREEZE · R19 业务意图回归：{_PASS}/{_PASS + _FAIL} 项通过"
      + ("　✅ 全部通过" if _FAIL == 0 else f"　❌ {_FAIL} 项失败"))
print("=" * 70)
sys.exit(1 if _FAIL else 0)
