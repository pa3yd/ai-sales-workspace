# -*- coding: utf-8 -*-
"""第十八轮（ROUND 3）· AI 跟进台 Follow-up Workspace 验收测试

覆盖 spec §32-37 场景 A-F + §40 验收 1-20（数据层/纯逻辑自动化部分）：
  A 报价跟进（QUOTE_SENT_NO_REPLY）
  B 等待关键信息（INFORMATION_WAITING）
  C 样品跟进（SAMPLE_SENT_NO_REPLY）
  D 客户回复 → 重估（resolve waiting，不重复建任务）
  E Snooze（SNOOZED + due 更新 + Timeline）
  F 完成（COMPLETED，DealStage 不变）
另含：独立状态 / 逾期判定 / 去重 / 队列排序 / 邮件安全 / UI 渲染冒烟。

隔离：场景部分复制 workbench.db 到临时目录并 monkeypatch db.DB_PATH，
真实库零污染。UI 渲染冒烟在真实库上只读执行（不点击任何写按钮）。
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

NOW = datetime.datetime.now()


def _ts(days=0, hours=0):
    return (NOW + datetime.timedelta(days=days, hours=hours)).strftime("%Y-%m-%d %H:%M")


# =====================================================================
# 0. 纯逻辑：独立状态 / 逾期 / 排序
# =====================================================================
print("=" * 70)
print("单元 · FollowUpStatus / 逾期 / 排序")
print("=" * 70)
check("S1 · 独立状态：PENDING 未到期保持",
      _fu.fu_status_of("PENDING", _ts(days=2), NOW) == "PENDING")
check("S2 · 逾期只看 followUpDueAt（过去=OVERDUE）",
      _fu.fu_status_of("PENDING", _ts(days=-1), NOW) == "OVERDUE")
check("S3 · 今天到期 = DUE_TODAY",
      _fu.fu_status_of("PENDING", _ts(hours=1), NOW) == "DUE_TODAY")
check("S4 · WAITING_CUSTOMER 复查点前不当作销售待办",
      _fu.fu_status_of("WAITING_CUSTOMER", _ts(days=2), NOW) == "WAITING_CUSTOMER")
check("S5 · WAITING 过复查点 → 回到行动",
      _fu.fu_status_of("WAITING_CUSTOMER", _ts(days=-1), NOW) == "OVERDUE")
check("S6 · SNOOZED 到期自动回到队列",
      _fu.fu_status_of("SNOOZED", _ts(days=-1), NOW) == "OVERDUE")
check("S7 · 排序：逾期 > 今日 > 等待 > 未来",
      [x["id"] for x in _fu.sort_queue([
          {"id": 1, "fu_status": "WAITING_CUSTOMER", "priority": "P1",
           "due_at": _ts(days=2)},
          {"id": 2, "fu_status": "PENDING", "priority": "P1", "due_at": _ts(days=5)},
          {"id": 3, "fu_status": "OVERDUE", "priority": "P2", "due_at": _ts(days=-1)},
          {"id": 4, "fu_status": "DUE_TODAY", "priority": "P1", "due_at": _ts(hours=1)},
      ], NOW)] == [3, 4, 1, 2], "got unexpected order")

# =====================================================================
# UI 渲染冒烟（真实库只读，不点击写按钮）
# =====================================================================
print()
print("=" * 70)
print("UI · 跟进台渲染（只读）")
print("=" * 70)
from streamlit.testing.v1 import AppTest
_APP = os.path.join(BASE, "workbench", "app.py")
_at = AppTest.from_file(_APP, default_timeout=300).run()
check("U1 · 跟进台渲染无异常", not _at.exception,
      str([e.value[:150] for e in _at.exception][:1]))
_subs = [s.value for s in _at.subheader]
check("U2 · 跟进台标题为「跟进台：今天该跟谁？」",
      any("今天该跟谁" in s for s in _subs), str(_subs[:2]))
_kpi_lbl = " ".join(b.label.replace("\n", " ") for b in _at.button)
check("U3 · 四个行动 KPI 存在（今日待跟进/逾期/等待客户/P1 商机）",
      all(k in _kpi_lbl for k in ("今日待跟进", "逾期未跟进", "等待客户回复", "P1 商机")))
check("U4 · 快速视图存在（今日/逾期/等待/报价/样品/全部）",
      any("报价未回复" in str(s.options) for s in _at.segmented_control))

# =====================================================================
# 场景测试（临时库隔离）
# =====================================================================
print()
print("=" * 70)
print("场景 A-F（临时库，monkeypatch db.DB_PATH，真实库零污染）")
print("=" * 70)
_tmp = tempfile.mkdtemp(prefix="fu_r18_")
_tmp_db = os.path.join(_tmp, "workbench.db")
shutil.copy(os.path.join("workbench", "workbench.db"), _tmp_db)
_old_db_path = db.DB_PATH
db.DB_PATH = _tmp_db
try:
    _c = sqlite3.connect(_tmp_db)
    _deals = [r[0] for r in _c.execute("SELECT id FROM opportunities ORDER BY id LIMIT 3")]
    _c.close()
    dA, dB, dC = _deals[0], _deals[1], _deals[2]
    # —— A：报价已发未回复 ——
    db.move_opportunity_stage(dA, "QUOTED", actor="测试", reason="场景A")
    db.create_followup_task(dA, "QUOTE_SENT_NO_REPLY",
                            due_at=_ts(days=-1), actor="测试")
    rowsA = db.list_followup_tasks(opportunity_id=dA)
    check("A1 · FollowUpReason=QUOTE_SENT_NO_REPLY",
          rowsA and rowsA[0]["reason"] == "QUOTE_SENT_NO_REPLY")
    check("A2 · 逾期判定基于 due_at（过去 → OVERDUE）",
          rowsA and _fu.fu_status_of(rowsA[0]["fu_status"], rowsA[0]["due_at"], NOW)
          == "OVERDUE")
    emA = _fem.build_followup_email(
        "QUOTE_SENT_NO_REPLY",
        dict(company="NordHaus Electronics GmbH", contact_name="Michael Weber",
             product="Wireless ANC Earbuds", qty="5,000 pcs",
             known={"email", "quantity", "certification", "target_price"}),
        "ABC Co.")
    check("A3 · 报价跟进邮件引用报价上下文，不重发全部规格",
          "quotation" in emA["draft"].lower()
          and "bluetooth" not in emA["draft"].lower())
    check("A4 · 不索取邮箱/不做时间承诺/问句≤3",
          "email address" not in emA["draft"].lower()
          and "within 24" not in emA["draft"].lower()
          and emA["question_count"] <= 3
          and not emA["issues"], str(emA["issues"]))
    check("A5 · 建议下一步 = 发送第一次报价跟进",
          _fu.REASON_NBA["QUOTE_SENT_NO_REPLY"][0] == "发送第一次报价跟进")

    # —— B：等待关键信息 ——
    db.create_followup_task(dB, "INFORMATION_WAITING", due_at=_ts(days=-1), actor="测试")
    emB = _fem.build_followup_email(
        "INFORMATION_WAITING",
        dict(company="BrightPromo BV", contact_name="Sophie",
             product="Stainless Steel Water Bottles", qty="10,000 pcs",
             unresolved=["capacity / reference model"], known={"email"}),
        "ABC Co.")
    lowB = emB["draft"].lower()
    check("B1 · 只问未解决的关键信息（容量/规格）",
          "capacity" in lowB or "reference model" in lowB)
    check("B2 · 不重复问 email / payment / website",
          all(x not in lowB for x in ("email address", "payment terms",
                                      "your website")))
    check("B3 · 问句 ≤3 且无安全 issues", emB["question_count"] <= 3
          and not emB["issues"], str(emB["issues"]))

    # —— C：样品已发未反馈 ——
    db.create_followup_task(dC, "SAMPLE_SENT_NO_REPLY", due_at=_ts(days=-1), actor="测试")
    emC = _fem.build_followup_email(
        "SAMPLE_SENT_NO_REPLY",
        dict(company="NordHaus Electronics GmbH", contact_name="Michael Weber",
             product="Wireless ANC Earbuds", qty="5,000 pcs", known={"email"}),
        "ABC Co.")
    lowC = emC["draft"].lower()
    check("C1 · 样品跟进问样品反馈 + 规格确认 + 下一步",
          ("feedback" in lowC or "samples" in lowC)
          and "specification" in lowC and not emC["issues"])
    check("C2 · 不是泛泛的 Any update？",
          "any update" not in lowC)

    # —— 去重 ——
    _before = len(db.list_followup_tasks(opportunity_id=dA, active_only=True))
    tid2, reused = db.create_followup_task(dA, "QUOTE_SENT_NO_REPLY",
                                           due_at=_ts(days=1), actor="测试")
    _after = len(db.list_followup_tasks(opportunity_id=dA, active_only=True))
    check("D1 · 去重：同 Deal+同 Reason 重复创建 → 复用不新增",
          reused and _after == _before, f"{_before}→{_after} reused={reused}")

    # —— E：Snooze ——
    db.update_followup_task(tid2, fu_status="SNOOZED", due_at=_ts(days=3), actor="测试")
    srow = [r for r in db.list_followup_tasks(opportunity_id=dA) if r["id"] == tid2][0]
    check("E1 · Snooze → fu_status=SNOOZED",
          srow["fu_status"] == "SNOOZED")
    check("E2 · Snooze → due_at 顺延 3 天",
          str(srow["due_at"]).startswith((NOW + datetime.timedelta(days=3))
                                         .strftime("%Y-%m-%d")))
    _ev = [a for a in db.list_deal_activity(dA) if a["type"] == "FOLLOW_UP_SNOOZED"]
    check("E3 · Timeline 记录 FOLLOW_UP_SNOOZED", len(_ev) >= 1)
    check("E4 · Snooze 后从今日队列消失（未到期不显示为行动）",
          _fu.fu_status_of("SNOOZED", _ts(days=3), NOW) == "SNOOZED")

    # —— F：完成跟进 ——
    _stage_before = [o["stage"] for o in db.list_opportunities() if o["id"] == dA][0]
    db.update_followup_task(tid2, fu_status="COMPLETED", actor="测试")
    srow2 = [r for r in db.list_followup_tasks(opportunity_id=dA) if r["id"] == tid2][0]
    _stage_after = [o["stage"] for o in db.list_opportunities() if o["id"] == dA][0]
    check("F1 · 完成 → fu_status=COMPLETED",
          srow2["fu_status"] == "COMPLETED")
    check("F2 · 完成跟进 ≠ DealStage 变更 / 不自动 Won",
          _stage_after == _stage_before and _stage_after != "WON",
          f"{_stage_before}→{_stage_after}")
    _ev2 = [a for a in db.list_deal_activity(dA) if a["type"] == "FOLLOW_UP_COMPLETED"]
    check("F3 · Timeline 记录 FOLLOW_UP_COMPLETED", len(_ev2) >= 1)

    # —— D：客户回复重估 ——
    tidD, _reusedD = db.create_followup_task(dB, "REPLY_DUE", due_at=_ts(days=1),
                                             actor="测试")
    db.update_followup_task(tidD, fu_status="WAITING_CUSTOMER", due_at=_ts(days=3),
                            actor="测试")
    n = db.resolve_waiting_for_deal(dB, actor="测试")
    wait_left = [r for r in db.list_followup_tasks(opportunity_id=dB)
                 if r["fu_status"] == "WAITING_CUSTOMER"]
    check("D2 · 客户回复 → 原等待任务关闭",
          n >= 1 and len(wait_left) == 0, f"closed={n} left={len(wait_left)}")

    # —— 建议逻辑（纯规则）——
    sug_quote = _fu.suggest_deal_followup(
        {"id": 1, "stage": "QUOTED"},
        dict(last_activity_type="QUOTE_SENT", last_activity_ts=_ts(days=-4),
             created=_ts(days=-6), has_draft=False), NOW)
    check("G1 · 报价 3 天后无回复 → 建议 QUOTE_SENT_NO_REPLY",
          sug_quote and sug_quote["reason"] == "QUOTE_SENT_NO_REPLY",
          str(sug_quote))
    sug_none = _fu.suggest_deal_followup(
        {"id": 2, "stage": "WON"}, {}, NOW)
    check("G2 · 赢单商机不产生跟进建议", sug_none is None)
finally:
    db.DB_PATH = _old_db_path
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print("=" * 70)
print(f"第十八轮回归测试结果：{_PASS}/{_PASS + _FAIL} 项通过"
      + ("　✅ 全部通过" if _FAIL == 0 else f"　❌ {_FAIL} 项失败"))
print("=" * 70)
sys.exit(1 if _FAIL else 0)
