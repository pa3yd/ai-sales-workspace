# -*- coding: utf-8 -*-
"""第十九轮 · Follow-up 验收场景种子（幂等）

用途：给「跟进台 / 首页收尾」验收准备真实可测试的数据入口，用现有测试客户：
  C1  NordHaus Electronics GmbH —— 报价已发、客户 3 天未回复
        → FollowUpReason=QUOTE_SENT_NO_REPLY · 到期已过 → OVERDUE
  C2  BrightPromo BV —— 上一封在等客户补容量/核心规格
        → FollowUpReason=INFORMATION_WAITING · WAITING_CUSTOMER · 3 天后复查

原则：
  - 不新建演示客户、不改 AI 分析/评分/回复逻辑、不改 InquiryStatus / DealStage 状态机；
  - 通过真实 db 层动作推进 DealStage（QUOTED / REQUIREMENT_CONFIRMED）并落 Timeline；
  - 幂等：同 Deal + 同 reason 已有 active 跟进任务即跳过，绝不重复创建；
  - 默认作用于 workbench/workbench.db；传 db_path 可作用于临时库（测试隔离用）。
"""
import datetime
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "workbench"))

import db  # noqa: E402

QUOTE_REASON = "QUOTE_SENT_NO_REPLY"
INFO_REASON = "INFORMATION_WAITING"


def _now(days=0, hours=0):
    return (datetime.datetime.now()
            + datetime.timedelta(days=days, hours=hours)).strftime("%Y-%m-%d %H:%M")


def _opps_by_company(company_part):
    cust_ids = {cid for cid, company, *_ in db.list_customers()
                if company_part.lower() in str(company or "").lower()}
    return [o for o in (db.list_opportunities() or [])
            if o.get("customer_id") in cust_ids]


def _has_active(opp_id, reason):
    return any(t["reason"] == reason for t in
               db.list_followup_tasks(opportunity_id=opp_id, active_only=True))


def seed(db_path=None, verbose=True) -> dict:
    """在指定库上执行两个验收场景（幂等）。返回改动摘要 dict。"""
    if db_path:
        db.DB_PATH = str(db_path)
    out = {"nordhaus": [], "brightpromo": [], "skipped": []}

    # ---------- C1 · NordHaus：QUOTE_SENT_NO_REPLY（OVERDUE） ----------
    nord = sorted(_opps_by_company("NordHaus"), key=lambda o: o["id"])
    if nord:
        latest = nord[-1]
        for o in nord:
            db.move_opportunity_stage(o["id"], "QUOTED",
                                      actor="销售", reason="验收场景 C1：报价已发送")
        # 只给最新商机留一条 SENT 报价（同一 Deal 的历史版本不必重复录）
        existing_quotes = db.list_quotes(latest["id"])
        if not any(q[5] == "SENT" for q in existing_quotes):
            db.create_quote(latest["id"], 45000.00, "USD",
                            _now(days=30), status="SENT")
            out["nordhaus"].append(f"quote v{len(existing_quotes) + 1} SENT")
        if not _has_active(latest["id"], QUOTE_REASON):
            tid, reused = db.create_followup_task(
                latest["id"], QUOTE_REASON, due_at=_now(days=-1),
                title="报价已发未回复 · 客户 3 天未回（验收场景 C1）",
                next_action=db_reason_nba(QUOTE_REASON),
                note="报价于 3 天前发出后客户尚未回复；到期复查点已过，应做第一次报价跟进。",
                actor="销售")
            out["nordhaus"].append(f"followup #{tid} (reused={reused})")
        else:
            out["skipped"].append(f"NordHaus opp#{latest['id']} 已有活跃 {QUOTE_REASON}")
    else:
        out["skipped"].append("NordHaus 商机未找到")

    # ---------- C2 · BrightPromo：INFORMATION_WAITING（WAITING_CUSTOMER） ----------
    bp = sorted(_opps_by_company("BrightPromo"), key=lambda o: o["id"])
    if bp:
        latest = bp[-1]
        for o in bp:
            db.move_opportunity_stage(o["id"], "REQUIREMENT_CONFIRMED",
                                      actor="销售",
                                      reason="验收场景 C2：等待客户确认容量/核心规格")
        if not _has_active(latest["id"], INFO_REASON):
            tid, reused = db.create_followup_task(
                latest["id"], INFO_REASON, due_at=_now(days=3),
                title="等待客户确认容量/核心规格（验收场景 C2）",
                next_action=db_reason_nba(INFO_REASON),
                note="上一封已请客户确认容量/核心规格；客户尚未回复，3 天后复查。",
                actor="销售")
            db.mark_followup_sent(tid, wait_days=3, actor="销售")
            out["brightpromo"].append(f"followup #{tid} → WAITING_CUSTOMER (reused={reused})")
        else:
            # 幂等恢复：已有活跃 INFORMATION_WAITING 但仍是 PENDING → 补成等待客户
            act = [t for t in db.list_followup_tasks(opportunity_id=latest["id"],
                                                     active_only=True)
                   if t["reason"] == INFO_REASON]
            t = act[0]
            if t["fu_status"] != "WAITING_CUSTOMER":
                db.mark_followup_sent(t["id"], wait_days=3, actor="销售")
            out["brightpromo"].append(f"reuse #{t['id']} → WAITING_CUSTOMER")
    else:
        out["skipped"].append("BrightPromo 商机未找到")

    if verbose:
        print("seed_validation_r19 ->", out)
    return out


def db_reason_nba(reason: str) -> str:
    """followup.REASON_NBA 的只读取值（避免本文件额外引入逻辑层时出错兜底）。"""
    try:
        import followup as _fu
        return _fu.REASON_NBA.get(reason, ("跟进", "manual"))[0]
    except Exception:
        return {"QUOTE_SENT_NO_REPLY": "发送第一次报价跟进",
                "INFORMATION_WAITING": "提醒客户确认关键产品规格"}.get(reason, "跟进")


if __name__ == "__main__":
    seed()
