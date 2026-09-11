# -*- coding: utf-8 -*-
"""第十八轮（ROUND 3）· AI 跟进台 — Follow-up 纯逻辑层（无 Streamlit 依赖）。

职责：
  - FollowUpReason 类型（可扩展）
  - 独立 FollowUpStatus 计算（DUE_TODAY / OVERDUE 由 due_at 即时得出，不落库）
  - 逾期判定（只看 followUpDueAt，绝不看客户/询盘创建时间）
  - 队列排序（OVERDUE > 今日到期 > 等待(未到复查点) > 未来排程）
  - 单一下一步（single NBA）与 AI 建议下次跟进时间
  - Deal 级自动建议（没有人工跟进任务时的规则候选）

与 InquiryStatus / DealStage 正交：三个不同维度。
"""
import datetime

# ---------------- FollowUpStatus（独立维度） ----------------
PENDING = "PENDING"                    # 已排程（未到期）
DUE_TODAY = "DUE_TODAY"                # 今天到期（即时计算）
OVERDUE = "OVERDUE"                    # 已逾期（即时计算）
WAITING = "WAITING_CUSTOMER"           # 等待客户（球在客户方）
SNOOZED = "SNOOZED"                    # 稍后提醒
COMPLETED = "COMPLETED"                # 本跟进任务已完成
CANCELLED = "CANCELLED"                # 已取消

STATUS_CN = {PENDING: "已排程", DUE_TODAY: "今日到期", OVERDUE: "已逾期",
             WAITING: "等待客户", SNOOZED: "已稍后提醒", COMPLETED: "已完成",
             CANCELLED: "已取消"}
STATUS_TONE = {PENDING: "low", DUE_TODAY: "amber", OVERDUE: "red",
               WAITING: "blue", SNOOZED: "low", COMPLETED: "green",
               CANCELLED: "low"}
STATUS_ORDER = {OVERDUE: 0, DUE_TODAY: 1, WAITING: 2, SNOOZED: 3,
                PENDING: 4, COMPLETED: 5, CANCELLED: 6}

# ---------------- FollowUpReason（可扩展类型，UI 文案不硬编码进业务） ----------------
REPLY_DUE = "REPLY_DUE"                          # 回复到期 / 草稿待发
QUOTE_SENT_NO_REPLY = "QUOTE_SENT_NO_REPLY"      # 报价已发未回复
SAMPLE_SENT_NO_REPLY = "SAMPLE_SENT_NO_REPLY"    # 样品已发未反馈
CUSTOMER_PROMISED_REPLY = "CUSTOMER_PROMISED_REPLY"  # 客户承诺会回复
SALES_PROMISED_FOLLOWUP = "SALES_PROMISED_FOLLOWUP"  # 我方承诺跟进
QUOTE_PREPARATION_DUE = "QUOTE_PREPARATION_DUE"  # 报价准备到期
PRODUCT_MATCH_PENDING = "PRODUCT_MATCH_PENDING"  # 内部选型待定
INFORMATION_WAITING = "INFORMATION_WAITING"      # 等客户补关键信息
NEGOTIATION_STALLED = "NEGOTIATION_STALLED"      # 谈判停滞
PO_EXPECTED = "PO_EXPECTED"                      # 预计下单
OVERDUE_FOLLOWUP = "OVERDUE_FOLLOWUP"            # 跟进已逾期
MANUAL_FOLLOWUP = "MANUAL_FOLLOWUP"              # 手动跟进

REASONS = [REPLY_DUE, QUOTE_SENT_NO_REPLY, SAMPLE_SENT_NO_REPLY,
           CUSTOMER_PROMISED_REPLY, SALES_PROMISED_FOLLOWUP,
           QUOTE_PREPARATION_DUE, PRODUCT_MATCH_PENDING,
           INFORMATION_WAITING, NEGOTIATION_STALLED, PO_EXPECTED,
           OVERDUE_FOLLOWUP, MANUAL_FOLLOWUP]

REASON_CN = {
    REPLY_DUE: "待回复/跟进到期",
    QUOTE_SENT_NO_REPLY: "报价已发未回复",
    SAMPLE_SENT_NO_REPLY: "样品已发未反馈",
    CUSTOMER_PROMISED_REPLY: "客户承诺回复",
    SALES_PROMISED_FOLLOWUP: "我方承诺跟进",
    QUOTE_PREPARATION_DUE: "报价准备到期",
    PRODUCT_MATCH_PENDING: "内部选型待定",
    INFORMATION_WAITING: "等待客户补信息",
    NEGOTIATION_STALLED: "谈判停滞",
    PO_EXPECTED: "预计下单",
    OVERDUE_FOLLOWUP: "跟进已逾期",
    MANUAL_FOLLOWUP: "手动跟进",
}

# 每个 reason 的"为什么现在跟"一句话（why-now，展示用文案仍集中于此可替换）
REASON_WHY = {
    REPLY_DUE: "回复草稿已就绪 / 约定回复时间已到，客户在等结果",
    QUOTE_SENT_NO_REPLY: "报价已发出但客户尚未回复，需要保持商务热度",
    SAMPLE_SENT_NO_REPLY: "样品已寄出且超过反馈窗口，需要确认样品意见",
    CUSTOMER_PROMISED_REPLY: "客户承诺了回复时间，礼貌提醒而不施压",
    SALES_PROMISED_FOLLOWUP: "我方承诺了跟进节点，到点应主动联系",
    QUOTE_PREPARATION_DUE: "报价要素已齐，需要按计划完成报价准备",
    PRODUCT_MATCH_PENDING: "产品选型仍未内部确认，需推进匹配或补规格",
    INFORMATION_WAITING: "上一封已在请客户补充关键规格（容量 / 型号 / 配置等），尚未收到回复",
    NEGOTIATION_STALLED: "谈判停在同一处，需要重新打开分歧点",
    PO_EXPECTED: "客户已接近下单节点，确认采购时间与最后障碍",
    OVERDUE_FOLLOWUP: "设定的跟进时间已过，应立即处理",
    MANUAL_FOLLOWUP: "业务员手动创建，按设定时间跟进",
}

# 每个 reason 的"唯一下一步"（single NBA 动作 + 生成邮件时的策略键）
REASON_NBA = {
    REPLY_DUE: ("查看并发送回复", "reply_due"),
    QUOTE_SENT_NO_REPLY: ("发送第一次报价跟进", "quote_no_reply"),
    SAMPLE_SENT_NO_REPLY: ("确认样品反馈", "sample_no_reply"),
    CUSTOMER_PROMISED_REPLY: ("礼貌提醒客户确认", "customer_promised"),
    SALES_PROMISED_FOLLOWUP: ("按承诺主动联系客户", "sales_promised"),
    QUOTE_PREPARATION_DUE: ("完成报价并发送", "quote_prep"),
    PRODUCT_MATCH_PENDING: ("内部选型并准备替代方案", "match_pending"),
    INFORMATION_WAITING: ("提醒客户确认关键产品规格", "info_waiting"),
    NEGOTIATION_STALLED: ("确认未决商务分歧点", "negotiation_stalled"),
    PO_EXPECTED: ("确认采购时间与下单障碍", "po_expected"),
    OVERDUE_FOLLOWUP: ("立即跟进客户", "overdue"),
    MANUAL_FOLLOWUP: ("跟进客户", "manual"),
}

# 建议下次跟进天数（reason → 默认 wait/复查间隔，仅作 AI 建议，不自动执行）
REASON_WAIT_DAYS = {
    REPLY_DUE: 1, QUOTE_SENT_NO_REPLY: 3, SAMPLE_SENT_NO_REPLY: 5,
    CUSTOMER_PROMISED_REPLY: 2, SALES_PROMISED_FOLLOWUP: 3,
    QUOTE_PREPARATION_DUE: 0, PRODUCT_MATCH_PENDING: 2,
    INFORMATION_WAITING: 3, NEGOTIATION_STALLED: 2, PO_EXPECTED: 2,
    OVERDUE_FOLLOWUP: 1, MANUAL_FOLLOWUP: 3,
}


def is_active(status: str) -> bool:
    return status in (PENDING, WAITING, SNOOZED)


def fu_status_of(base_status: str, due_at, now=None) -> str:
    """计算展示态：把存储基态 + due_at 合成独立 FollowUpStatus。

    逾期只看 followUpDueAt（spec 21），与客户/询盘创建时间无关。
    语义按"到期日"判定：到期日早于今天=OVERDUE；到期日=今天=DUE_TODAY；
    到期日在未来=保持基态（PENDING 排程 / WAITING 等待 / SNOOZED 稍后）。
    """
    base = str(base_status or PENDING)
    if base in (COMPLETED, CANCELLED):
        return base
    due = _parse_due(due_at)
    now = now or datetime.datetime.now()
    if due is None:
        # 无到期时间：Waiting/稍后照常；PENDING 未排程=一直待办
        return base if base in (WAITING, SNOOZED) else PENDING
    if due.date() < now.date():
        return OVERDUE
    if due.date() == now.date():
        # 到期日就是今天：Waiting/Snooze 过了复查点才转今日行动；
        # 未到具体复查时刻仍算"等待中/稍后中"
        if base == WAITING and now < due:
            return WAITING
        if base == SNOOZED and now < due:
            return SNOOZED
        return DUE_TODAY
    return base if base in (WAITING, SNOOZED) else PENDING


def _parse_due(due_at):
    if not due_at:
        return None
    try:
        s = str(due_at).strip()
        return datetime.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return None


def _age_days(ts: str, now: datetime.datetime) -> float | None:
    dt = _parse_due(ts) if isinstance(ts, str) else ts
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400.0)


# ---------------- 排序（spec 26：可解释，不随机） ----------------
def urgency_of(item, now=None) -> str:
    """跟进紧迫度 URGENT / HIGH / NORMAL / LOW（展示分级，不改评分）。"""
    st = item.get("fu_status") or fu_status_of(
        item.get("base_status") or item.get("fu_status") or PENDING,
        item.get("due_at"), now)
    pri = item.get("priority", "NORMAL")
    if st == OVERDUE:
        return "URGENT" if pri in ("P1", "HIGH", "P2") else "HIGH"
    if st == DUE_TODAY:
        return "HIGH" if pri in ("P1", "HIGH") else "NORMAL"
    if st == WAITING:
        return "NORMAL"
    if st == SNOOZED:
        return "LOW"
    return "NORMAL"


def queue_sort_key(item, now=None) -> tuple:
    """排序键：状态档位 → 客户优先级档 → 距今 → 录入时间倒序。

    保证：逾期 > 今日 > 等待客户 > 稍后提醒 > 未来排程；
    同档内 Deal Priority / due 更近者优先；完全随机被排除。
    """
    st = item.get("fu_status") or fu_status_of(
        item.get("base_status") or item.get("fu_status") or PENDING,
        item.get("due_at"), now)
    pri = {"P1": 0, "HIGH": 0, "P2": 1, "NORMAL": 1, "P3": 2,
           "LOW": 2}.get(item.get("priority", "NORMAL"), 2)
    due = _parse_due(item.get("due_at"))
    epoch = datetime.datetime(1970, 1, 1)
    due_ts = (due - epoch).total_seconds() if due else 1e15
    return (STATUS_ORDER.get(st, 5), pri, due_ts,
            -(item.get("id") or 0))


def sort_queue(items, now=None) -> list:
    return sorted(items, key=lambda it: queue_sort_key(it, now))


# ---------------- AI 下次跟进建议（spec 15/16，仅建议不自动执行） ----------------
def suggest_next_followup(reason: str, base=None, now=None) -> dict:
    """建议下次跟进时间与一句理由（Reason ≤1 行）。"""
    now = now or datetime.datetime.now()
    days = REASON_WAIT_DAYS.get(reason, 3)
    if days == 0:
        due = now
    else:
        due = now + datetime.timedelta(days=days)
    if base:
        try:
            base = datetime.datetime.strptime(str(base)[:16], "%Y-%m-%d %H:%M")
            if (now - base).days >= REASON_WAIT_DAYS.get(reason, 3):
                return {"due_at": now.strftime("%Y-%m-%d %H:%M"),
                        "reason": "已到跟进节点，建议今天就联系客户"}
        except Exception:
            pass
    return {"due_at": due.strftime("%Y-%m-%d %H:%M"),
            "reason": f"建议 {days} 天后复查" if days else "建议尽快处理"}


# ---------------- Deal 自动建议（无人工任务时的规则候选） ----------------
def suggest_deal_followup(deal: dict, ctx: dict, now=None) -> dict | None:
    """为一条活跃商机给出"跟进建议"（不落库；用户动作时才固化为任务）。

    规则按销售阶段与真实时间信号推断；WON/LOST/ON_HOLD 不产生跟进。
    ctx: 关联询盘的派生上下文 {biz, blockers, follow_up_at, follow_up_done,
         has_draft, readiness, last_activity_type, last_activity_ts, created}
    """
    now = now or datetime.datetime.now()
    stage = str(deal.get("stage") or "")
    if stage in ("WON", "LOST", "ON_HOLD"):
        return None
    ctx = ctx or {}
    fu_at, fu_done = ctx.get("follow_up_at"), bool(ctx.get("follow_up_done"))
    biz = ctx.get("biz") or ""
    created = str(ctx.get("created") or "")
    # 1) 已设跟进时间 → 遵循人工设定
    if fu_at and not fu_done:
        st = fu_status_of(PENDING, fu_at, now)
        if st in (OVERDUE, DUE_TODAY):
            return {"reason": OVERDUE_FOLLOWUP,
                    "nba": REASON_NBA[OVERDUE_FOLLOWUP][0],
                    "due_at": fu_at,
                    "why": "设定时间已到，应立即处理" if st == OVERDUE
                           else "设定今天跟进"}
    # 2) 已报价 / 谈判中，且最近一次外发（报价/回复）早于阈值
    la_ts = ctx.get("last_activity_ts") or created
    la_type = ctx.get("last_activity_type") or ""
    if stage in ("QUOTED",) or ctx.get("readiness") == "quoted":
        if la_ts and _age_days(la_ts, now) is not None \
                and _age_days(la_ts, now) >= 3:
            return {"reason": QUOTE_SENT_NO_REPLY,
                    "nba": REASON_NBA[QUOTE_SENT_NO_REPLY][0],
                    "due_at": (now - datetime.timedelta(days=1))
                    .strftime("%Y-%m-%d %H:%M"),
                    "why": "报价已发出未收到回复"}
    if stage == "SAMPLE":
        if la_ts and _age_days(la_ts, now) is not None \
                and _age_days(la_ts, now) >= 5:
            return {"reason": SAMPLE_SENT_NO_REPLY,
                    "nba": REASON_NBA[SAMPLE_SENT_NO_REPLY][0],
                    "due_at": (now - datetime.timedelta(days=1))
                    .strftime("%Y-%m-%d %H:%M"),
                    "why": "样品寄出已超过反馈窗口"}
    # 3) 待关键信息且已外发询问 → 等客户补信息
    if ctx.get("blockers") and la_type in ("REPLIED", "REPLY_GENERATED"):
        if _age_days(la_ts or "", now) is not None \
                and _age_days(la_ts or "", now) >= 3:
            return {"reason": INFORMATION_WAITING,
                    "nba": REASON_NBA[INFORMATION_WAITING][0],
                    "due_at": (now - datetime.timedelta(days=1))
                    .strftime("%Y-%m-%d %H:%M"),
                    "why": "上一封在等客户补充关键信息"}
    # 4) 草稿已就绪 / 待回复 → 回复到期
    if biz in ("READY_TO_REPLY", "NEEDS_INFO") and ctx.get("has_draft"):
        return {"reason": REPLY_DUE,
                "nba": REASON_NBA[REPLY_DUE][0],
                "due_at": now.strftime("%Y-%m-%d %H:%M"),
                "why": "回复草稿已就绪，客户在等结果"}
    return None
