# -*- coding: utf-8 -*-
"""ROUND 7.1 · DEAL PROGRESSION ENGINE（领域层唯一权威）。

设计原则（用户 ROUND 7.1 §2 / §3）：
  STATE ≠ HEALTH ≠ REASON ≠ NEXT ACTIVITY ≠ STAGE TRANSITION
  五个概念彼此独立，但由本模块**统一解析**——Pipeline / Sidebar / 首页 /
  Deal Detail / AI 销售助手 / Follow-up 一律只读这里的结果，禁止各自计算。

复用而非重建（REUSE > MERGE > EXTEND > ADD）：
  · DealStage          → sales_crm.STAGES / PIPELINE_STAGES / TERMINAL / PROBABILITY
  · 阶段准入校验        → sales_crm.transition_errors（阶段推进唯一守卫）
  · 报价生命周期        → sales_crm.quote_lifecycle_summary / has_sent_quote
  · FollowUpStatus     → followup.PENDING / DUE_TODAY / OVERDUE / WAITING / SNOOZED
                          / fu_status_of（逾期只按真实 due_at 即时算）
  · FollowUpReason     → followup.REASON_CN / REASON_NBA / REASON_WHY
  · ResolvedDealState  → queue_ui.deal_work_item()['resolvedState']（需求/动作/风险）
  · 阶段进入时间        → opportunity_stage_history.changed_at（change→本阶段的最新一条）
  · 历史轨迹            → deal_activity（STAGE_CHANGE / QUOTE_SENT / …）

输出：ResolvedDealProgression（纯只读 dict，不写任何业务数据）
    deal_id / opportunity_id
    current_stage / stage_entered_at / stage_age_days / stage_age_inferred
    health / primary_reason / reason_code
    next_activity / next_activity_due_at / next_activity_status
    recommended_transition / transition_reason / transition_level
    requires_action / waiting_customer / overdue
    stages.detail（次级理由，供「为什么?」折叠区）

本模块**不**做自动阶段推进。AI 只推荐，人确认（§9）。
"""
from __future__ import annotations

import datetime
from typing import Iterable

import followup as fu
import queue_ui as q_ui
import sales_crm as crm

# ==========================================================================
# §5.1 · Stage Aging 阈值（**唯一**定义处；UI 不得各自硬编码）
#   attention_days：超过 → health 至少 ATTENTION
#   at_risk_days  ：超过 → health 至少 AT_RISK
#   终态（WON/LOST）不产生任何 aging 告警。
# ==========================================================================
STAGE_AGING = {
    "NEW":                   {"attention_days": 1,  "at_risk_days": 2},
    "QUALIFIED":             {"attention_days": 2,  "at_risk_days": 4},
    "REQUIREMENT_CONFIRMED": {"attention_days": 3,  "at_risk_days": 5},
    "QUOTED":                {"attention_days": 4,  "at_risk_days": 7},
    "SAMPLE":                {"attention_days": 7,  "at_risk_days": 14},
    "NEGOTIATION":           {"attention_days": 5,  "at_risk_days": 10},
    # PO_PENDING 是项目里的 ORDER_PENDING 等价物
    "PO_PENDING":            {"attention_days": 5,  "at_risk_days": 10},
}
# 未列入阈值的阶段（例如未来新增）：给一个保守默认，绝不静默无告警
_DEFAULT_AGING = {"attention_days": 4, "at_risk_days": 8}

# ---------------- Deal Health（§6：无数字分，仅三态） ----------------
HEALTHY = "HEALTHY"
ATTENTION = "ATTENTION"
AT_RISK = "AT_RISK"

HEALTH_CN = {HEALTHY: "健康", ATTENTION: "需关注", AT_RISK: "有风险"}
HEALTH_ICON = {HEALTHY: "🟢", ATTENTION: "🟡", AT_RISK: "🔴"}
# health 强度（用于多信号取最严重者）
_HEALTH_RANK = {HEALTHY: 0, ATTENTION: 1, AT_RISK: 2}

# ---------------- Next Activity Status（§4） ----------------
NA_SCHEDULED = "SCHEDULED"
NA_DUE_TODAY = "DUE_TODAY"
NA_OVERDUE = "OVERDUE"
NA_MISSING = "MISSING"
NA_WAITING_CUSTOMER = "WAITING_CUSTOMER"
NA_NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
# 终态专用：不是"没安排下一步"，而是"已结束，无需下一步"
NA_CLOSED = "CLOSED"

NA_CN = {
    NA_SCHEDULED: "已排程", NA_DUE_TODAY: "今日到期", NA_OVERDUE: "已逾期",
    NA_MISSING: "未安排", NA_WAITING_CUSTOMER: "等待客户",
    NA_NO_ACTION_REQUIRED: "无需动作", NA_CLOSED: "已结束",
}
# 下一步活动是否"存在的问题"（用于 requires_action）
_NA_NEEDS_ACTION = {NA_OVERDUE, NA_MISSING, NA_DUE_TODAY}

# ---------------- Stage Transition 建议等级（§10 人工推进校验同一套） ----------------
TV_VALID = "VALID"
TV_WARNING = "WARNING"
TV_BLOCKED = "BLOCKED"

# ---------------- Primary Reason 优先级（§7） ----------------
#   OVERDUE > AT_RISK > MISSING_NEXT_ACTIVITY > INTERNAL_BLOCKER
#   > CUSTOMER_RESPONSE > NORMAL_PROGRESS
REASON_PRIORITY = [
    "OVERDUE",
    "STAGE_AGING_AT_RISK",
    "MISSING_NEXT_ACTIVITY",
    "INTERNAL_BLOCKER",
    "CUSTOMER_REPLY_NEGOTIATION",
    "CUSTOMER_RESPONSE_WAIT",
    "QUOTE_EXPIRED",
    "NORMAL_PROGRESS",
]
_REASON_RANK = {code: i for i, code in enumerate(REASON_PRIORITY)}


# ==========================================================================
# 时间工具
# ==========================================================================
def _dt(value):
    """宽松解析时间字符串/对象；失败返回 None（不猜时间）。"""
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(value).strip()[:19], fmt)
        except Exception:
            continue
    return None


def _now(value=None):
    return _dt(value) or datetime.datetime.now()


# ==========================================================================
# §5 · Stage Aging
# ==========================================================================
def stage_entered_at(opp: dict, history: Iterable | None = None,
                     activities: Iterable | None = None) -> tuple[str, bool]:
    """本阶段进入时间（权威） + 是否为推断值。

    关键区分（ROUND 7.1 §5 / §17）：
      · **真实迁移**：from_stage 非空（人或规则明确改过阶段）
      · **创建/重同步行**：from_stage 为空（"由询盘创建"），是保存询盘时的
        副产物，不代表阶段变化。历史库里同一 opp 会因多次 save_inquiry
        反复写入 `None → NEW`，若把它当作阶段进入时间，就会得出
        "NEW 只停留 1 天"的错误结论（真实业务已在 QUOTED 停留多日）。

    优先级（只用真实证据，绝不发明历史事件）：
      1. 真实迁移里 to_stage == 当前阶段 的**最新** changed_at
      2. deal_activity 的 STAGE_CHANGE（metadata.to == 当前阶段）
      3. 该商机**最后一条真实迁移**（之后无变更 → 保守下限）
      4. 兜底 last_activity_at / updated_at / created_at，标记 inferred=True

    返回 (iso字符串, inferred)。
    """
    stage = str((opp or {}).get("stage") or "")
    rows = list(history or [])

    def _parts(r):
        """(from_stage, to_stage, changed_at_dt) —— 兼容 tuple / dict。"""
        try:
            return (r[0], r[1], _dt(r[2]))
        except Exception:
            r = r or {}
            return (r.get("from_stage"), r.get("to_stage"), _dt(r.get("changed_at")))

    real_moves = [(f, t, d) for (f, t, d) in (_parts(r) for r in rows)
                  if d is not None and str(f or "").strip()]

    # 1) 真实迁移中，进入当前阶段的最新一次
    entered_now = [d for (f, t, d) in real_moves if str(t or "") == stage]
    if entered_now:
        return max(entered_now).strftime("%Y-%m-%d %H:%M:%S"), False

    # 2) Activity Timeline 的 STAGE_CHANGE
    act_hits = []
    for a in (activities or []):
        if str((a or {}).get("type") or "").upper() != "STAGE_CHANGE":
            continue
        meta = (a or {}).get("metadata") or {}
        if str(meta.get("to") or "") == stage and _dt((a or {}).get("ts")):
            act_hits.append(_dt(a.get("ts")))
    if act_hits:
        return max(act_hits).strftime("%Y-%m-%d %H:%M:%S"), False

    # 3) 最后一条真实迁移（说明此后再无真实阶段变更）
    if real_moves:
        return max(d for (_, _, d) in real_moves).strftime("%Y-%m-%d %H:%M:%S"), True

    # 4) 业务时间兜底
    for key in ("last_activity_at", "updated_at", "created_at"):
        cand = _dt((opp or {}).get(key))
        if cand:
            return cand.strftime("%Y-%m-%d %H:%M:%S"), True
    return "", True


def stage_regression_of(opp: dict, history: Iterable | None = None) -> dict:
    """检测"阶段回退"：真实迁移曾到达更靠前的阶段，但当前 stage 更靠后。

    本项目已知成因：save_inquiry 重新关联商机时把 stage 重置为 NEW，
    使 QUOTED 的 Deal 表面回到新询盘。Progression 必须把这种状态暴露为
    数据一致性问题，而不是当作"刚从新询盘开始"。

    返回 {"regressed": bool, "peak_stage": str, "peak_at": str,
          "current_stage": str}
    """
    stage = str((opp or {}).get("stage") or "")
    peak, peak_at = "", None
    for r in (history or []):
        try:
            to_stage, changed = r[1], _dt(r[2])
        except Exception:
            r = r or {}
            to_stage, changed = r.get("to_stage"), _dt(r.get("changed_at"))
        if not changed:
            continue
        t = str(to_stage or "")
        if _stage_index(t) > _stage_index(peak or ""):
            peak, peak_at = t, changed
    if peak and _stage_index(peak) > _stage_index(stage) and stage not in crm.TERMINAL:
        return {"regressed": True, "peak_stage": peak,
                "peak_at": peak_at.strftime("%Y-%m-%d %H:%M:%S") if peak_at else "",
                "current_stage": stage,
                "peak_stage_cn": crm.STAGE_CN.get(peak, peak)}
    return {"regressed": False, "peak_stage": peak,
            "peak_at": peak_at.strftime("%Y-%m-%d %H:%M:%S") if peak_at else "",
            "current_stage": stage,
            "peak_stage_cn": crm.STAGE_CN.get(peak, peak) if peak else ""}


def stage_age_days(opp: dict, history=None, activities=None, now=None) -> dict:
    """本阶段停留天数（用 stage_entered_at，**不用** Deal 创建时间）。"""
    now = _now(now)
    entered, inferred = stage_entered_at(opp, history, activities)
    dt_entered = _dt(entered)
    if dt_entered is None:
        return {"days": None, "entered_at": "", "inferred": True, "text": ""}
    days = max(0.0, (now - dt_entered).total_seconds() / 86400.0)
    return {"days": round(days, 2), "entered_at": entered, "inferred": inferred,
            "text": _age_text(days)}


def _age_text(days) -> str:
    """紧凑展示（§5.2）：只给"今天 / 停留 N 天"，不铺开说明。"""
    if days is None:
        return ""
    if days < 1:
        return "今天"
    return f"停留 {int(days)} 天"


def aging_thresholds(stage: str) -> dict:
    """阶段 aging 阈值（唯一来源）。终态返回空阈值。"""
    s = str(stage or "").upper()
    if s in crm.TERMINAL:
        return {"attention_days": None, "at_risk_days": None}
    return dict(STAGE_AGING.get(s) or _DEFAULT_AGING)


def aging_level(stage: str, age_days, now=None) -> str:
    """按阈值返回 "" / ATTENTION / AT_RISK（终态永远 ""）。"""
    if str(stage or "").upper() in crm.TERMINAL or age_days is None:
        return ""
    th = aging_thresholds(stage)
    if th.get("at_risk_days") is not None and age_days > th["at_risk_days"]:
        return AT_RISK
    if th.get("attention_days") is not None and age_days > th["attention_days"]:
        return ATTENTION
    return ""


# ==========================================================================
# §4 · Next Activity 解析（P0）
# ==========================================================================
def _active_tasks(tasks: Iterable) -> list[dict]:
    """未完成的跟进任务（OPEN 且非 COMPLETED/CANCELLED）。"""
    out = []
    for t in tasks or []:
        if str((t or {}).get("status") or "OPEN").upper() != "OPEN":
            continue
        if str((t or {}).get("fu_status") or "").upper() in (fu.COMPLETED, fu.CANCELLED):
            continue
        out.append(t)
    return out


def resolve_next_activity(opp: dict, tasks: Iterable = (), now=None) -> dict:
    """解析该 Deal 唯一的 Next Activity。

    返回 next_activity_status ∈ {SCHEDULED, DUE_TODAY, OVERDUE, MISSING,
    WAITING_CUSTOMER, NO_ACTION_REQUIRED, CLOSED}。

    规则（§4）：
      · 终态 → CLOSED（不是"缺失"）
      · 有活跃 follow-up 任务 → 按 due_at 即时算 OVERDUE/DUE_TODAY，
        基态 WAITING_CUSTOMER 则在未过复查点时保持 WAITING_CUSTOMER
        （§4.2：等待客户 ≠ 缺少客户信息）
      · 商机自身 next_action_at 也算一次排程
      · 都没有 → MISSING（可执行例外，不是静默"未安排下一步"）
    """
    now = _now(now)
    stage = str((opp or {}).get("stage") or "")
    if stage in crm.TERMINAL:
        return {"status": NA_CLOSED, "label": NA_CN[NA_CLOSED], "action": "",
                "due_at": "", "reason": "", "task_id": None,
                "reason_code": "", "source": "terminal"}
    act = _active_tasks(tasks)
    if act:
        def _key(t):
            return (str((t or {}).get("due_at") or "9999-12-31"),
                    int((t or {}).get("id") or 0))
        t = sorted(act, key=_key)[0]
        base = str(t.get("fu_status") or fu.PENDING).upper()
        st = fu.fu_status_of(base, t.get("due_at"), now)
        na = {fu.OVERDUE: NA_OVERDUE, fu.DUE_TODAY: NA_DUE_TODAY,
              fu.WAITING: NA_WAITING_CUSTOMER, fu.SNOOZED: NA_SCHEDULED,
              fu.PENDING: NA_SCHEDULED}.get(st, NA_SCHEDULED)
        return {
            "status": na, "label": NA_CN.get(na, na),
            "action": str(t.get("next_action") or t.get("title") or "跟进客户"),
            "due_at": str(t.get("due_at") or ""),
            "reason": str(t.get("reason") or ""),
            "reason_code": str(t.get("reason") or ""),
            "task_id": t.get("id"), "source": "followup_task",
            "waiting_base": base == fu.WAITING,
        }
    # 商机自身的 next_action / next_action_at
    if str((opp or {}).get("next_action") or "").strip() \
            or str((opp or {}).get("next_action_at") or "").strip():
        due = _dt((opp or {}).get("next_action_at"))
        if due is None:
            st, na = "", NA_SCHEDULED
        elif due.date() < now.date():
            st, na = fu.OVERDUE, NA_OVERDUE
        elif due.date() == now.date():
            st, na = fu.DUE_TODAY, NA_DUE_TODAY
        else:
            st, na = fu.PENDING, NA_SCHEDULED
        return {"status": na, "label": NA_CN.get(na, na),
                "action": str((opp or {}).get("next_action") or "跟进客户"),
                "due_at": str((opp or {}).get("next_action_at") or ""),
                "reason": "", "reason_code": "", "task_id": None,
                "source": "opportunity"}
    # §4.3：客户在等我们 = 前一步已外发且需求未阻塞 → 显式 WAITING_CUSTOMER 回退
    deriv = _derive_waiting_customer(opp, tasks, now)
    if deriv:
        return deriv
    return {"status": NA_MISSING, "label": NA_CN[NA_MISSING], "action": "",
            "due_at": "", "reason": "", "reason_code": "",
            "task_id": None, "source": "none"}


def _derive_waiting_customer(opp: dict, tasks: Iterable, now) -> dict | None:
    """已外发（报价/回复）且球在客户方、但没排下一次动作 → 显式 WAITING_CUSTOMER。

    只认**已有证据**：已发送报价 或 已完成过外发类跟进任务。
    绝不因为"缺少客户资料"就判成等待客户（那是 INFORMATION_WAITING/阻塞）。
    """
    stage = str((opp or {}).get("stage") or "")
    done_outbound = any(
        str((t or {}).get("status") or "").upper() == "OPEN"
        and str((t or {}).get("fu_status") or "").upper() == fu.WAITING
        for t in (tasks or []))
    if not done_outbound and stage not in {"QUOTED", "SAMPLE", "NEGOTIATION", "PO_PENDING"}:
        return None
    return {"status": NA_WAITING_CUSTOMER, "label": NA_CN[NA_WAITING_CUSTOMER],
            "action": "等待客户回复", "due_at": "", "reason": "",
            "reason_code": fu.CUSTOMER_PROMISED_REPLY, "task_id": None,
            "source": "derived_waiting"}


# ==========================================================================
# §6 · Deal Health（三态，无数字分）
# ==========================================================================
def resolve_health(opp: dict, *, next_activity: dict, age_days, aging_lvl: str,
                   tasks: Iterable = (), quotes: Iterable = (),
                   activities: Iterable = (), resolved_state: dict | None = None,
                   regression: dict | None = None, now=None) -> tuple[str, list[str]]:
    """返回 (health, signals[])。AT_RISK > ATTENTION > HEALTHY。

    优先级（§6.1）：
      AT_RISK  : 下一活动逾期 / aging 超 at_risk / 强风险信号
      ATTENTION: 下一活动缺失 / aging 超 attention / 内部动作待办 / 报价后无跟进
      HEALTHY  : 有有效下一步且无逾期、无超期、无关键阻塞

    重要（§6.1 末段）：WAITING_CUSTOMER 本身**不**自动等于不健康。
    """
    now = _now(now)
    stage = str((opp or {}).get("stage") or "")
    signals: list[str] = []
    if stage in crm.TERMINAL:
        return HEALTHY, ["terminal"]

    na_status = (next_activity or {}).get("status")
    rs = resolved_state or {}
    level = HEALTHY

    def _bump(to, sig):
        nonlocal level
        if _HEALTH_RANK[to] > _HEALTH_RANK[level]:
            level = to
        signals.append(sig)

    # —— AT_RISK 级信号 ——
    if na_status == NA_OVERDUE:
        _bump(AT_RISK, "next_activity_overdue")
    if aging_lvl == AT_RISK:
        _bump(AT_RISK, "stage_aging_at_risk")
    qsum = crm.quote_lifecycle_summary(quotes, now)
    if qsum.get("status") == "EXPIRED":
        _bump(AT_RISK, "quote_expired")
    # 阶段回退（真实迁移曾到更靠前阶段，当前却更靠后）→ ATTENTION，
    # 这是数据一致性问题，需要人复核，但不等于客户侧逾期。
    if (regression or {}).get("regressed"):
        _bump(ATTENTION, "stage_regression")

    # —— ATTENTION 级信号 ——
    if na_status == NA_MISSING:
        _bump(ATTENTION, "next_activity_missing")
    if aging_lvl == ATTENTION:
        _bump(ATTENTION, "stage_aging_attention")
    if na_status == NA_DUE_TODAY:
        _bump(ATTENTION, "next_activity_due_today")
    # 报价已发送但没有活跃跟进（§4.1 的可执行例外）
    if qsum.get("has_sent") and not _active_tasks(tasks):
        _bump(ATTENTION, "quote_sent_no_followup")
    # 内部前置（供应能力未知 / 产品未匹配）—— 内部动作，不是客户阻塞
    if str((rs.get("productMatchStatus") or "")).upper() in (
            "NO_MATCH", "UNRESOLVED", "INSUFFICIENT_INFORMATION"):
        _bump(ATTENTION, "internal_product_match")
    if str((rs.get("supplierCapabilityStatus") or "")).upper() in ("UNKNOWN", "NEEDS_CHECK"):
        _bump(ATTENTION, "internal_supplier_unknown")
    if rs.get("actionState") == "WAITING_INTERNAL":
        _bump(ATTENTION, "internal_action_required")

    return level, signals


# ==========================================================================
# §7 · Primary Reason（一个 Deal 一条主因）
# ==========================================================================
def resolve_primary_reason(opp: dict, *, health, next_activity, age_days,
                           aging_lvl: str, quotes: Iterable = (),
                           resolved_state: dict | None = None,
                           activities: Iterable = (),
                           regression: dict | None = None, now=None) -> dict:
    """返回 {code, text, detail[]}。text 必须是**具体业务原因**，不是套话。

    优先级（§7）：
      OVERDUE > AT_RISK > MISSING_NEXT_ACTIVITY > INTERNAL_BLOCKER
      > CUSTOMER_RESPONSE > NORMAL_PROGRESS
    """
    now = _now(now)
    stage = str((opp or {}).get("stage") or "")
    stage_cn = crm.STAGE_CN.get(stage, stage or "新询盘")
    rs = resolved_state or {}
    qsum = crm.quote_lifecycle_summary(quotes, now)
    na = next_activity or {}
    na_status = na.get("status")
    detail: list[str] = []

    if stage in crm.TERMINAL:
        return {"code": "NORMAL_PROGRESS", "text": "商机已结束，无需推进。",
                "detail": ["终态商机不产生进度告警"]}

    # 1) 逾期（最高优先级）——说清"什么逾期了、逾期多久"
    if na_status == NA_OVERDUE:
        due = _dt(na.get("due_at"))
        overdue_days = max(0, (now - due).days) if due else 0
        if na.get("reason_code") == fu.QUOTE_SENT_NO_REPLY or qsum.get("has_sent"):
            txt = (f"报价已发出 {_sent_age_text(quotes, now)}，"
                   f"客户未回复，报价跟进已逾期 {overdue_days} 天")
        else:
            txt = f"{na.get('action') or '下一步动作'} 已逾期 {overdue_days} 天未处理"
        detail.append(f"逾期任务：{na.get('action') or '—'} · 到期 {na.get('due_at') or '—'}")
        return {"code": "OVERDUE", "text": txt, "detail": detail}

    # 2) 阶段停留超 at-risk
    if aging_lvl == AT_RISK:
        th = aging_thresholds(stage)
        txt = (f"{stage_cn} 阶段已停留 {int(age_days or 0)} 天，"
               f"超过风险阈值 {th.get('at_risk_days')} 天，需要推动或复盘")
        detail.append(f"阶段进入时间：{na.get('_entered') or ''}")
        return {"code": "STAGE_AGING_AT_RISK", "text": txt, "detail": detail}

    # 3) 缺少下一步（§4.1：必须是可执行的例外，不是静默）
    if na_status == NA_MISSING:
        if qsum.get("has_sent"):
            return {"code": "MISSING_NEXT_ACTIVITY",
                    "text": "报价已发送，但尚未安排后续跟进",
                    "detail": ["报价状态：" + str(qsum.get("label"))]}
        return {"code": "MISSING_NEXT_ACTIVITY",
                "text": "尚未安排下一步动作，需要明确负责人与时间",
                "detail": [f"当前阶段：{stage_cn}"]}

    # 4) 内部阻塞（供应能力 / 产品匹配）—— 明确是**内部**问题
    pm = str(rs.get("productMatchStatus") or "").upper()
    sup = str(rs.get("supplierCapabilityStatus") or "").upper()
    if pm in ("NO_MATCH", "UNRESOLVED", "INSUFFICIENT_INFORMATION"):
        return {"code": "INTERNAL_BLOCKER",
                "text": "客户需求已明确，但内部产品方案尚未确认",
                "detail": ["需内部匹配产品或确认替代方案（不向客户追问）"]}
    if rs.get("actionState") == "WAITING_INTERNAL" or sup in ("UNKNOWN", "NEEDS_CHECK"):
        return {"code": "INTERNAL_BLOCKER",
                "text": "客户需求已确认，等待内部产品/供应方案确认",
                "detail": ["内部前置事项未完成前不推进商务阶段"]}

    # 5) 客户回复 / 谈判
    if _has_negotiation_evidence(activities):
        return {"code": "CUSTOMER_REPLY_NEGOTIATION",
                "text": "客户已提出价格或商务条款异议，需要处理谈判",
                "detail": ["存在 NEGOTIATION / 客户议价类活动记录"]}
    if na_status == NA_WAITING_CUSTOMER:
        why = "报价已发送，等待客户回复（复查点已排程）" if qsum.get("has_sent") \
            else "球在客户方，等待客户回复（复查点已排程）"
        return {"code": "CUSTOMER_RESPONSE_WAIT", "text": why,
                "detail": ["等待客户不等于缺少客户信息"]}
    if qsum.get("status") == "EXPIRED":
        return {"code": "QUOTE_EXPIRED",
                "text": "报价已过期，需要确认价格与有效期后再推进",
                "detail": [str(qsum.get("risk") or "")]}

    # 6) 尾部：阶段 aging 刚过 attention 或竞态兜底
    if aging_lvl == ATTENTION:
        th = aging_thresholds(stage)
        return {"code": "MISSING_NEXT_ACTIVITY",
                "text": f"{stage_cn} 已停留 {int(age_days or 0)} 天，"
                        f"接近风险阈值 {th.get('at_risk_days')} 天，建议尽快推进",
                "detail": []}
    if na_status == NA_DUE_TODAY:
        return {"code": "CUSTOMER_RESPONSE_WAIT",
                "text": f"{na.get('action') or '跟进'} 今天到期，需要按计划处理",
                "detail": []}
    # 7) 阶段回退（数据一致性）—— 放在"按计划推进"之前，避免用套话掩盖问题
    if (regression or {}).get("regressed"):
        return {"code": "STAGE_REGRESSION",
                "text": f"阶段状态与历史不一致：曾推进至"
                        f"{regression.get('peak_stage_cn') or regression.get('peak_stage')}，"
                        f"当前显示为 {stage_cn}，需要复核",
                "detail": [f"历史最高阶段时间：{regression.get('peak_at') or '—'}"]}
    return {"code": "NORMAL_PROGRESS",
            "text": f"{stage_cn} 按计划推进，下一步已排程",
            "detail": []}


def _sent_age_text(quotes: Iterable, now) -> str:
    """报价发出至今的天数文本（用于主因，只基于真实 created_at）。"""
    best = None
    for q in quotes or []:
        try:
            status = str(q[5] or "").upper()
            created = _dt(q[6])
        except Exception:
            status = str((q or {}).get("status") or "").upper()
            created = _dt((q or {}).get("created_at"))
        if status in ("SENT", "REVISED", "ACCEPTED") and created:
            if best is None or created > best:
                best = created
    if best is None:
        return "未记录发送时间"
    days = max(0, (now - best).days)
    return f"{days} 天" if days else "今天"


def _has_negotiation_evidence(activities: Iterable) -> bool:
    """谈判证据（stage transition 到 NEGOTIATION 的守卫也认同一批信号）。"""
    for a in activities or []:
        if str((a or {}).get("type") or "").upper() in (
                "NEGOTIATION", "MEETING", "CALL", "PRICE_OBJECTION"):
            return True
        meta = (a or {}).get("metadata") or {}
        if str(meta.get("negotiation") or "").upper() in ("1", "TRUE", "YES"):
            return True
    return False


# ==========================================================================
# §8 · Stage Transition Engine（推荐，不自动执行）
# ==========================================================================
def _stage_index(stage: str) -> int:
    try:
        return crm.STAGES.index(str(stage or ""))
    except ValueError:
        return -1


def recommend_transition(opp: dict, *, next_activity=None, quotes: Iterable = (),
                         tasks: Iterable = (), activities: Iterable = (),
                         resolved_state: dict | None = None,
                         details: dict | None = None,
                         regression: dict | None = None, now=None) -> dict:
    """返回 {recommended_transition, transition_reason, transition_level, evidence[]}。

    只推荐**向前**的阶段变化；证据不足就返回空推荐（不猜）。
    终态商机不推荐任何活跃阶段迁移（§8.1 末段）。

    特例：若检测到阶段回退（真实迁移曾到更靠前阶段，当前却更靠后），
    优先推荐**恢复到历史峰值阶段**——因为业务事实（例如报价已发出）已经
    支持那个阶段；否则会误导业务员重新走一遍已完成的流程。
    """
    now = _now(now)
    stage = str((opp or {}).get("stage") or "")
    rs = resolved_state or {}
    det = details if details is not None else ((opp or {}).get("details") or {})
    if stage in crm.TERMINAL:
        return {"recommended_transition": "", "transition_reason": "",
                "transition_level": "", "evidence": ["terminal_no_transition"]}
    qsum = crm.quote_lifecycle_summary(quotes, now)
    acts = list(activities or [])
    act_types = {str((a or {}).get("type") or "").upper() for a in acts}
    target, reason, evidence = "", "", []

    # 0) 阶段回退恢复（优先于常规推荐）
    reg = regression or {}
    if reg.get("regressed"):
        peak = str(reg.get("peak_stage") or "")
        peak_cn = reg.get("peak_stage_cn") or crm.STAGE_CN.get(peak, peak)
        cur_cn = crm.STAGE_CN.get(stage, stage)
        return {"recommended_transition": peak,
                "transition_reason": f"业务事实已支持 {peak_cn}"
                                     f"（历史已推进过），当前显示为 {cur_cn}，建议恢复阶段",
                "transition_level": TV_WARNING,
                "evidence": ["stage_regression_restore"]}

    req_level = str(rs.get("requirementCompleteness") or "").upper()
    has_customer_blocker = bool(rs.get("customerBlockingItems"))
    pm = str(rs.get("productMatchStatus") or "").upper()

    # QUOTED → NEGOTIATION（客户议价/条款异议；最高优先，因为这是最明确信号）
    if stage == "QUOTED" and (_has_negotiation_evidence(acts)
                              or str(det.get("negotiation_evidence") or "").upper() in ("1", "TRUE", "YES")):
        target = "NEGOTIATION"
        reason = "客户回复包含明确的价格/条款谈判，应进入谈判阶段"
        evidence = ["negotiation_evidence"]
    # SAMPLE → NEGOTIATION
    elif stage == "SAMPLE" and (_has_negotiation_evidence(acts)
                                or str(det.get("negotiation_evidence") or "").upper() in ("1", "TRUE", "YES")):
        target = "NEGOTIATION"
        reason = "样品评估阶段已出现商务谈判信号"
        evidence = ["negotiation_evidence"]
    # NEGOTIATION → PO_PENDING
    elif stage == "NEGOTIATION" and (str(det.get("po_signal") or "").upper() in ("1", "TRUE", "YES")
                                     or "PO_EXPECTED" in act_types):
        target = "PO_PENDING"
        reason = "商务条款已基本谈定，下一步等待 PO / 订单确认"
        evidence = ["po_signal"]
    # REQUIREMENT_CONFIRMED → QUOTED（**必须**报价真的发出）
    elif stage == "REQUIREMENT_CONFIRMED" and qsum.get("has_sent"):
        target = "QUOTED"
        reason = f"Quotation V{qsum.get('latest_version') or 1} 已发送给客户"
        evidence = ["quote_sent"]
    # QUOTED → SAMPLE（样品流程真的启动）
    elif stage == "QUOTED" and ({"SAMPLE_SENT", "SAMPLE_REQUEST", "SAMPLE_APPROVED"} & act_types
                                or str(det.get("sample_started") or "").upper() in ("1", "TRUE", "YES")):
        target = "SAMPLE"
        reason = "样品流程已启动"
        evidence = ["sample_started"]
    # NEW → QUALIFIED
    elif stage == "NEW" and rs.get("customerProductRequirement") \
            and req_level in ("HIGH", "COMPLETE", "MEDIUM"):
        target = "QUALIFIED"
        reason = "客户身份与产品需求已足够明确，可进入已验证阶段"
        evidence = ["identity_resolved", "product_identified"]
    # QUALIFIED → REQUIREMENT_CONFIRMED（关键：内部不确定 ≠ 客户需求不完整）
    elif stage == "QUALIFIED" and req_level in ("HIGH", "COMPLETE") \
            and not has_customer_blocker:
        target = "REQUIREMENT_CONFIRMED"
        reason = "客户需求已充分完整，且无客户侧阻塞项"
        evidence = ["requirement_complete"]
        if pm in ("NO_MATCH", "UNRESOLVED", "INSUFFICIENT_INFORMATION"):
            evidence.append("internal_uncertainty_not_a_customer_blocker")

    if not target:
        return {"recommended_transition": "", "transition_reason": "",
                "transition_level": "", "evidence": []}
    return {"recommended_transition": target,
            "transition_reason": reason,
            "transition_level": TV_VALID,
            "evidence": evidence}


def validate_transition(opp: dict, target: str, *, quotes: Iterable = (),
                        tasks: Iterable = (), activities: Iterable = (),
                        resolved_state: dict | None = None,
                        flags: dict | None = None, now=None) -> dict:
    """人工推进校验（§10）：VALID / WARNING / BLOCKED。

    复用 sales_crm.transition_errors 作为硬性守卫；把"非关键资料缺失"
    降级为 WARNING（人工可覆盖），只有真正不安全（终态重开、无报价却要到
    已报价、赢单缺成交事实）才 BLOCKED。
    """
    now = _now(now)
    stage = str((opp or {}).get("stage") or "")
    target = str(target or "").upper()
    if target not in crm.STAGES:
        return {"level": TV_BLOCKED, "errors": ["未知销售阶段"], "warnings": []}
    if stage == target:
        return {"level": TV_VALID, "errors": [], "warnings": []}
    acts = list(activities or [])
    errors = crm.transition_errors(
        stage, target, opp, has_quote=crm.has_sent_quote(quotes),
        activity_types=[str((a or {}).get("type") or "") for a in acts],
        flags=flags or {})
    if not errors:
        return {"level": TV_VALID, "errors": [], "warnings": []}
    # 终态重开 / 赢单事实缺失 → 硬阻止
    hard = [e for e in errors if e in ("已结束商机需重新开启后才能推进",
                                       "人工确认赢单", "PO 已收到或订单已确认",
                                       "最终成交金额", "客户明确下单信号",
                                       "已发送报价", "输单原因")]
    soft = [e for e in errors if e not in hard]
    if hard:
        return {"level": TV_BLOCKED, "errors": errors,
                "warnings": [], "blocking": hard}
    return {"level": TV_WARNING, "errors": [], "warnings": soft,
            "message": "仍缺少一项非关键资料，是否继续推进？"}


# ==========================================================================
# §13 · 执行优先级排序键（Pipeline 阶段内 / 队列共用）
# ==========================================================================
#   OVERDUE > DUE TODAY > AT_RISK > MISSING NEXT ACTIVITY > ATTENTION
#   > WAITING CUSTOMER > FUTURE SCHEDULED > NO ACTION REQUIRED
_EXEC_ORDER = {
    NA_OVERDUE: 0, NA_DUE_TODAY: 1,
}
_HEALTH_EXEC = {AT_RISK: 2, ATTENTION: 3, HEALTHY: 6}


def exec_priority(p: dict) -> tuple:
    """Deal Progression → 执行优先级排序键（越小越先做）。"""
    na = (p or {}).get("next_activity_status") or NA_MISSING
    if na in _EXEC_ORDER:
        return (_EXEC_ORDER[na], 0, 0, 0)
    if na == NA_MISSING:
        return (3, 0, 0, 0)
    health = (p or {}).get("health") or HEALTHY
    if health == AT_RISK:
        return (2, 0, 0, 0)
    if health == ATTENTION:
        return (4, 0, 0, 0)
    if na == NA_WAITING_CUSTOMER:
        return (5, 0, 0, 0)
    if na == NA_SCHEDULED:
        return (6, 0, 0, 0)
    if na == NA_CLOSED:
        return (8, 0, 0, 0)
    return (7, 0, 0, 0)


def needs_action(p: dict) -> bool:
    """「需处理」（§12）：ATTENTION / AT_RISK / MISSING / DUE_TODAY / OVERDUE。"""
    if not p:
        return False
    if (p.get("next_activity_status") or "") in _NA_NEEDS_ACTION:
        return True
    return (p.get("health") or HEALTHY) in (ATTENTION, AT_RISK)


# ==========================================================================
# 主入口 · ResolvedDealProgression
# ==========================================================================
def resolve_progression(opp: dict, *, tasks: Iterable = (), quotes: Iterable = (),
                        activities: Iterable = (), history: Iterable = (),
                        resolved_state: dict | None = None, now=None) -> dict:
    """Deal Progression 唯一权威解析器。

    所有运行 UI（Pipeline / Deal Detail / AI 销售助手 / Sidebar / 跟进台）
    必须读这个函数的结果；不得在组件内重复推导 Health / Aging / Next Activity。

    返回只读 dict —— 见模块 docstring。
    绝不写库、绝不自动改阶段。
    """
    now = _now(now)
    opp = opp or {}
    tasks = list(tasks or [])
    quotes = list(quotes or [])
    activities = list(activities or [])
    rs = resolved_state or {}

    # —— Stage Aging ——
    entered, inferred = stage_entered_at(opp, history, activities)
    dt_entered = _dt(entered)
    age = None if dt_entered is None else round(
        max(0.0, (now - dt_entered).total_seconds() / 86400.0), 2)
    alvl = aging_level(opp.get("stage"), age, now)
    regression = stage_regression_of(opp, history)

    # —— Next Activity ——
    na = resolve_next_activity(opp, tasks, now)
    na["_entered"] = entered

    # —— Health ——
    health, signals = resolve_health(
        opp, next_activity=na, age_days=age, aging_lvl=alvl, tasks=tasks,
        quotes=quotes, activities=activities, resolved_state=rs,
        regression=regression, now=now)

    # —— Primary Reason ——
    reason = resolve_primary_reason(
        opp, health=health, next_activity=na, age_days=age, aging_lvl=alvl,
        quotes=quotes, resolved_state=rs, activities=activities,
        regression=regression, now=now)

    # —— Transition ——
    tr = recommend_transition(opp, next_activity=na, quotes=quotes, tasks=tasks,
                              activities=activities, resolved_state=rs,
                              regression=regression, now=now)

    stage = str(opp.get("stage") or "")
    stage_cn = crm.STAGE_CN.get(stage, stage or "新询盘")
    na_status = na.get("status")
    overdue = na_status == NA_OVERDUE
    waiting_customer = na_status == NA_WAITING_CUSTOMER
    p = {
        "deal_id": opp.get("inquiry_id") or opp.get("id"),
        "opportunity_id": opp.get("id"),
        "inquiry_id": opp.get("inquiry_id"),
        "current_stage": stage,
        "current_stage_cn": stage_cn,
        "stage_entered_at": entered,
        "stage_age_days": age,
        "stage_age_text": _age_text(age),
        "stage_age_inferred": inferred,
        "aging_level": alvl,
        "aging_thresholds": aging_thresholds(stage),

        "health": health,
        "health_cn": HEALTH_CN[health],
        "health_icon": HEALTH_ICON[health],
        "health_signals": signals,

        "primary_reason": reason["text"],
        "reason_code": reason["code"],
        "primary_reason_detail": reason["detail"],

        "next_activity": na.get("action") or "",
        "next_activity_due_at": na.get("due_at") or "",
        "next_activity_status": na_status,
        "next_activity_status_cn": NA_CN.get(na_status, na_status or ""),
        "next_activity_reason": na.get("reason") or "",
        "next_activity_source": na.get("source") or "",

        "recommended_transition": tr.get("recommended_transition") or "",
        "recommended_transition_cn": crm.STAGE_CN.get(
            tr.get("recommended_transition") or "", ""),
        "transition_reason": tr.get("transition_reason") or "",
        "transition_level": tr.get("transition_level") or "",
        "transition_evidence": tr.get("evidence") or [],

        "requires_action": bool(na_status in _NA_NEEDS_ACTION
                                or health in (ATTENTION, AT_RISK)),
        "waiting_customer": waiting_customer,
        "overdue": overdue,
        "is_terminal": stage in crm.TERMINAL,
        "stage_regression": regression,
        "stage_regressed": bool(regression.get("regressed")),
        "peak_stage": regression.get("peak_stage") or "",
        "resolved_state": rs,
    }
    p["needs_action"] = needs_action(p)
    # 卡片一行用（§11）
    p["card_reason"] = reason["text"]
    p["card_next"] = (
        f"{na.get('action')} · {_due_text(na.get('due_at'))}"
        if na.get("action") and na.get("due_at")
        else (na.get("action") or NA_CN.get(na_status, "")))
    return p


def _due_text(due_at) -> str:
    """下一步到期文本：逾期 N 天 / 今天 / MM-DD。"""
    d = _dt(due_at)
    if d is None:
        return ""
    today = datetime.date.today()
    if d.date() < today:
        return f"已逾期 {(today - d.date()).days} 天"
    if d.date() == today:
        return "今天"
    if (d.date() - today).days == 1:
        return "明天"
    return d.strftime("%m-%d")


# ==========================================================================
# 便捷封装：一次取数（供 UI 直接调用，避免每处各查一次库）
# ==========================================================================
def progression_of(opportunity_id, *, db_mod=None, now=None) -> dict:
    """按 opportunity_id 解析（内部查库；只读）。

    这是 UI 唯一推荐入口 —— Pipeline / Deal Detail / AI 助手 / 跟进台都用它，
    确保三处看到完全一致的 progression。
    """
    import db as _db
    dbm = db_mod or _db
    opp = None
    try:
        opp = dbm.get_opportunity(opportunity_id)
    except Exception:
        opp = None
    if not opp:
        opp = next((o for o in (dbm.list_opportunities() or [])
                    if o.get("id") == opportunity_id), None)
    if not opp:
        return {}
    try:
        tasks = dbm.list_followup_tasks(opportunity_id=opportunity_id) or []
    except Exception:
        tasks = []
    try:
        quotes = dbm.list_quotes(opportunity_id) or []
    except Exception:
        quotes = []
    try:
        activities = dbm.list_deal_activity(opportunity_id) or []
    except Exception:
        activities = []
    try:
        history = dbm.list_stage_history(opportunity_id) or []
    except Exception:
        history = []
    # 需求/动作口径复用 ResolvedDealState（不重复推导）
    rs = {}
    try:
        group, opp_by_inq, tasks_by_opp = _thread_context(dbm, opp)
        if group is not None:
            w = q_ui.deal_work_item(group, opp, [t for t in tasks if str(t.get("status") or "").upper() == "OPEN"] or None)
            rs = w.get("resolvedState") or {}
    except Exception:
        rs = {}
    return resolve_progression(opp, tasks=tasks, quotes=quotes,
                               activities=activities, history=history,
                               resolved_state=rs, now=now)


def _thread_context(dbm, opp):
    """构造 deal_work_item 需要的 group / 索引（供 resolved_state 复用）。"""
    try:
        rows = dbm.list_inquiries() or []
    except Exception:
        rows = []
    opps = dbm.list_opportunities() or []
    opp_by_inq = {}
    for o in opps:
        det = o.get("details") or {}
        ids = [o.get("inquiry_id"), det.get("current_inquiry_id")]
        ids += list(det.get("related_inquiry_ids") or [])
        for iid in {i for i in ids if i}:
            opp_by_inq[iid] = o
    tasks_by_opp = {}
    for t in (dbm.list_followup_tasks() or []):
        if str(t.get("status") or "").upper() == "OPEN":
            tasks_by_opp.setdefault(t.get("opportunity_id"), []).append(t)
    # 用商机身份键聚合，与 queue_ui 同一把尺
    key = q_ui.deal_identity_of(opp)
    items = [it for it in _iter_dict_inquiries(rows)
             if key and q_ui.deal_identity_of(opp_by_inq.get(it.get("id")) or {}) == key]
    if not items:
        return None, opp_by_inq, tasks_by_opp
    return ({"key": key, "lead": items[0], "items": items, "count": len(items)},
            opp_by_inq, tasks_by_opp)


def _iter_dict_inquiries(rows):
    """list_inquiries() 返回 tuple；这里统一成 dict 供身份键计算。"""
    out = []
    for r in rows or []:
        if isinstance(r, dict):
            out.append(r)
            continue
        try:
            # db.list_inquiries 列序：id, created_at, status, ..., report_json
            rid = r[0]
            rep = r[-1]
            import json as _json
            try:
                data = _json.loads(rep) if isinstance(rep, str) else (rep or {})
            except Exception:
                data = {}
            info = data.get("extracted") or data.get("info") or {}
            out.append({"id": rid, "company": info.get("company"),
                        "cust_id": data.get("customer_id"),
                        "need": {"product_query": info.get("product_query")}})
        except Exception:
            continue
    return out


def progression_for_all(*, db_mod=None, now=None) -> dict:
    """全部商机的 progression（{opportunity_id: progression}），供 Pipeline 用。"""
    import db as _db
    dbm = db_mod or _db
    return {o["id"]: progression_of(o["id"], db_mod=dbm, now=now)
            for o in (dbm.list_opportunities() or [])}
