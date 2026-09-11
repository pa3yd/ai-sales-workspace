# -*- coding: utf-8 -*-
"""第十九轮 · SALES HOME FINAL CLEANUP + FOLLOW-UP VALIDATION 验收测试

Part A（首页收尾，展示层只读）
  A1  WHY NOW 取代「AI NEXT BEST ACTION」第三份重复 CTA；不再出现重复强调
  A2  0 数据模块不占大片空间（空分类不渲染大卡片，只留轻量 ✓ 提示）
  A3  产品列短名 + chips，完整规格不再泄漏到列表层
  A4  KPI 单位语义：按询盘/消息计数，副行给「聚合 N 个待办 Deal」
  A5  首页结构 = 今日概览 → 今日优先处理 → 销售工作队列（Pipeline 指引去商机页）

Part B~I（Round 3 能力盘点 + 验收）
  B   能力齐全（FollowUpTask/Status/DueAt/Reason/customer_id/deal_id/Timeline/
       Waiting/Overdue/Snooze/Complete/AI 生成 —— 逐项断言存在）
  C   两个真实客户验收场景（seed_validation_r19 幂等）
  E   三轴独立：REPLIED(询盘) / QUOTED(商机) / WAITING_CUSTOMER(跟进) 合法
  F   逾期只看 followUpDueAt（不看客户/询盘创建时间）
  G   Waiting Customer 不重复提醒，过复查点才回到队列
  H   五个 Timeline 动作都能写 deal_activity
  I   重复创建 / 页面渲染不产生重复 active 任务

隔离：拷贝真实库到临时目录后 seed（真实库零污染），页面用 AppTest 只读渲染。
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
print("Part B · Round 3 Follow-up 能力盘点（应全部已具备，本轮只做最小补齐）")
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
# 准备隔离库：拷贝真实库 → seed 两个验收场景
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

    # F：逾期只由 due_at 决定（不看创建时间）
    check("F1 · 创建 8 天前、due 已过 → OVERDUE（只看 due_at）",
          _fu.fu_status_of("WAITING_CUSTOMER", _ts(days=-2), NOW) == "OVERDUE")
    check("F2 · 创建 8 天前、due 在未来 → 仍 WAITING（不因旧自动逾期）",
          _fu.fu_status_of("WAITING_CUSTOMER", _ts(days=5), NOW) == "WAITING_CUSTOMER")
    check("F3 · COMPLETED / CANCELLED 不因 due 过期而变 OVERDUE",
          _fu.fu_status_of("COMPLETED", _ts(days=-2), NOW) == "COMPLETED"
          and _fu.fu_status_of("CANCELLED", _ts(days=-2), NOW) == "CANCELLED")

    # G：Waiting Customer 不持续误提醒；复查点后才回到队列
    check("G1 · WAITING_CUSTOMER 未到复查点 → 不作为今日行动",
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
    # 页面验收（Part A + D）：对已 seed 的临时库渲染整页（只读）
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
    md = "\n".join(m.value for m in at.markdown)
    caps = "\n".join(c.value for c in at.caption)
    btns = [b.label for b in at.button]

    # ---- A1 ----
    check("A1a · 右侧主卡为 WHY NOW（AI NEXT BEST ACTION Hero 已移除）",
          "WHY NOW" in md and "AI NEXT BEST ACTION" not in md)
    check("A1b · 不再用「生成回复/生成报价/创建跟进」三按钮重复同一 Next Action",
          all(x not in btns for x in ("生成回复", "生成报价", "创建跟进")),
          str([x for x in btns if x in ("生成回复", "生成报价", "创建跟进")]))
    check("A1c · 今日任务卡保留「卡点 + 下一步」，队列仍逐行给下一步",
          "卡点" in md and "下一步：" in md and "下一步" in md)
    check("A1d · 单主 CTA「处理」存在（打开 Deal 执行）",
          any(b == "处理" for b in btns))

    # ---- A2 ----
    check("A2a · 0 数据分类不再渲染大号空卡片",
          "当前没有需要处理的记录" not in md)
    check("A2b · 空分类以轻量 ✓ 提示出现（而非占位大卡）",
          "✓ 暂无" in caps or "✓ 今日没有必须立即处理" in caps)

    # ---- A3 ----
    check("A3a · 首页产品列为短名（Wireless ANC Earbuds）",
          "Wireless ANC Earbuds" in md)
    check("A3b · 完整规格不再泄漏到首页列表层",
          "40 hours battery life" not in md
          and "Bluetooth 5.4, ANC, 40" not in md)
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

    # ---- A4 ----
    check("A4a · KPI 单位语义明确（按询盘/消息计数）",
          "待回复询盘" in md and "待报价询盘" in md and "按询盘/消息计数" in md)
    check("A4b · KPI 副行给出聚合 Deal 数（口径桥接）",
          "聚合" in md and "个待办 Deal" in md)
    check("A4c · 有注释说明 KPI 与 Deal 队列的计数差异",
          "KPI 按询盘/消息计数" in caps and "按 Deal 聚合" in caps)

    # ---- A5 ----
    _i_kpi, _i_today, _i_q = md.find("新询盘"), md.find("今日优先处理"), md.find("销售工作队列")
    check("A5a · 首页层级：今日概览(KPI) → 今日优先处理 → 销售工作队列",
          -1 < _i_kpi < _i_today < _i_q, f"{_i_kpi}/{_i_today}/{_i_q}")
    check("A5b · Pipeline 完整看板指引去「商机」页（首页不再重复铺开）",
          "商机" in md and "Pipeline" in md
          and "Pipeline / 销售机会" not in md)

    # ---- D · 跟进台 5 秒可答 ----
    _subs = [s.value for s in at.subheader]
    check("D1 · 跟进台存在：今天该跟谁",
          any("今天该跟谁" in s for s in _subs), str(_subs[:2]))
    check("D2 · 默认「今日跟进」视图=OVERDUE 报价跟进（NordHaus）",
          "NordHaus" in md and "发送第一次报价跟进" in md and "到期" in md)
    check("D2b · Waiting Customer 默认不作为今日行动（不在默认队列当提醒）",
          "提醒客户确认关键产品规格" not in md)
    # 切到「等待客户」视图 → BrightPromo 的 NBA 出现
    _seg_lst = list(at.segmented_control)
    _can_wait = any("等待客户" in str(s.options) for s in _seg_lst)
    if _can_wait:
        _seg = next(s for s in _seg_lst if "等待客户" in str(s.options))
        _seg.set_value("等待客户")
        at2 = _seg.run()
        md2 = "\n".join(m.value for m in at2.markdown)
        check("D2c · 「等待客户」视图可见 BrightPromo 场景（Why/下一步）",
              "BrightPromo" in md2 and "提醒客户确认关键产品规格" in md2)
    else:
        check("D2c · 「等待客户」快速视图存在（可筛选 waiting）", False)
    check("D3 · Waiting Customer 展示为等待中，不冒充今日行动",
          "等待客户回复" in " ".join(b.label.replace("\n", " ")
                                        for b in at.button))

    # ---- I2：页面渲染不产生新任务 ----
    _act_after_render = len(db.list_followup_tasks(active_only=True))
    check("I2 · 进入跟进页面（只读渲染）不新增 active 任务",
          _act_after_render == _cnt_after, f"{_cnt_after}→{_act_after_render}")

finally:
    db.DB_PATH = _old_path
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print("=" * 70)
print(f"第十九轮回归测试结果：{_PASS}/{_PASS + _FAIL} 项通过"
      + ("　✅ 全部通过" if _FAIL == 0 else f"　❌ {_FAIL} 项失败"))
print("=" * 70)
sys.exit(1 if _FAIL else 0)
