# -*- coding: utf-8 -*-
"""Sales Pipeline 的唯一业务规则来源。UI 与数据库均从这里读取。"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

PIPELINE_STAGES = [
    {"id": "NEW", "en": "New Inquiry", "cn": "新询盘", "probability": 10},
    {"id": "QUALIFIED", "en": "Qualified", "cn": "已验证", "probability": 20},
    {"id": "REQUIREMENT_CONFIRMED", "en": "Requirement Confirmed", "cn": "需求已确认", "probability": 35},
    {"id": "QUOTED", "en": "Quoted", "cn": "已报价", "probability": 50},
    {"id": "SAMPLE", "en": "Sample", "cn": "样品阶段", "probability": 60},
    {"id": "NEGOTIATION", "en": "Negotiation", "cn": "谈判", "probability": 70},
    {"id": "PO_PENDING", "en": "PO Pending", "cn": "待订单", "probability": 90},
    {"id": "WON", "en": "Won", "cn": "赢单", "probability": 100},
    {"id": "LOST", "en": "Lost", "cn": "输单", "probability": 0},
]
STAGES = [x["id"] for x in PIPELINE_STAGES]
STAGE_CN = {x["id"]: x["cn"] for x in PIPELINE_STAGES}
STAGE_EN = {x["id"]: x["en"] for x in PIPELINE_STAGES}
PROBABILITY = {x["id"]: x["probability"] for x in PIPELINE_STAGES}
TERMINAL = {"WON", "LOST"}
LOST_REASONS = ["价格", "交期", "MOQ", "产品不匹配", "客户流失", "竞争对手", "项目取消", "无回复", "其他"]
HEALTH_THRESHOLDS = {"attention_days": 3, "at_risk_days": 7}
QUOTE_STATUSES = ["DRAFT", "SENT", "REVISED", "ACCEPTED", "REJECTED", "EXPIRED"]


def has_sent_quote(quotes: Iterable) -> bool:
    """只有 SENT / REVISED 代表报价已经进入客户侧；DRAFT 不算已报价。"""
    for q in quotes or []:
        try:
            status = str(q[5] or "").upper()
        except Exception:
            status = str((q or {}).get("status") or "").upper()
        if status in {"SENT", "REVISED", "ACCEPTED"}:
            return True
    return False


def quote_lifecycle_summary(quotes: Iterable, now: datetime | None = None) -> dict:
    """报价生命周期摘要：Draft / Sent / Revised / Expired。"""
    now = now or datetime.now()
    rows = list(quotes or [])
    if not rows:
        return {"status": "NONE", "label": "暂无报价", "has_sent": False,
                "count": 0, "latest_version": None, "risk": ""}
    latest = rows[0]
    try:
        version, valid_until, status = latest[1], latest[4], str(latest[5] or "DRAFT").upper()
    except Exception:
        version = latest.get("version")
        valid_until = latest.get("valid_until")
        status = str(latest.get("status") or "DRAFT").upper()
    expired = False
    try:
        expired = bool(valid_until) and datetime.fromisoformat(str(valid_until)[:10]) < now
    except Exception:
        expired = False
    status = "EXPIRED" if expired and status in {"DRAFT", "SENT", "REVISED"} else status
    labels = {"NONE": "暂无报价", "DRAFT": "报价草稿", "SENT": "报价已发送",
              "REVISED": "报价已修订", "ACCEPTED": "报价已接受",
              "REJECTED": "报价被拒", "EXPIRED": "报价已过期"}
    return {"status": status, "label": labels.get(status, status),
            "has_sent": has_sent_quote(rows), "count": len(rows),
            "latest_version": version,
            "risk": "报价已过期，需要重新确认价格与有效期" if status == "EXPIRED" else ""}


def transition_errors(current: str, target: str, opp: dict, has_quote: bool = False,
                      activity_types: Iterable[str] = (), flags: dict | None = None) -> list[str]:
    """校验阶段准入事实。has_quote 表示已发送报价；报价草稿不算。"""
    flags = flags or {}
    activity_types = {str(x).upper() for x in activity_types}
    if target not in STAGES:
        return ["未知销售阶段"]
    if current in {"WON", "LOST"} and target != current:
        return ["已结束商机需重新开启后才能推进"]
    required = []
    if target in STAGES[1:7] + ["WON"]:
        required += [("customer_id", "客户/公司"), ("product", "产品"), ("quantity", "数量")]
    if target in STAGES[2:7] + ["WON"]:
        required += [("specification", "规格"), ("customization", "定制要求（可填写“无”）")]
    if target in STAGES[3:7] + ["WON"]:
        required += [("currency", "报价币种")]
        if not has_quote:
            required.append(("__quote", "已发送报价"))
    errors = [label for key, label in required if key != "__quote" and not str(opp.get(key) or "").strip()]
    errors += [label for key, label in required if key == "__quote"]
    if target == "SAMPLE" and not (flags.get("skip_sample") or {"SAMPLE_REQUEST", "SAMPLE_SENT", "SAMPLE_APPROVED"} & activity_types):
        errors.append("样品记录，或确认跳过样品")
    if target == "NEGOTIATION" and not (flags.get("negotiation_evidence") or {"NEGOTIATION", "MEETING", "CALL"} & activity_types):
        errors.append("客户谈判/会议记录")
    if target == "PO_PENDING" and not flags.get("po_signal"):
        errors.append("客户明确下单信号")
    if target == "WON":
        if not flags.get("manual_confirm"):
            errors.append("人工确认赢单")
        if not (opp.get("final_value") or opp.get("amount")):
            errors.append("最终成交金额")
        if not (flags.get("po_received") or flags.get("order_confirmed") or opp.get("po_number")):
            errors.append("PO 已收到或订单已确认")
    if target == "LOST" and not str(opp.get("lost_reason") or flags.get("lost_reason") or "").strip():
        errors.append("输单原因")
    return list(dict.fromkeys(errors))


def _dt(value):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None) if value else None
    except (ValueError, TypeError):
        return None


def health_of(opp: dict, now: datetime | None = None) -> dict:
    """根据明确的下一步和活动时间计算健康度，并返回可解释原因。"""
    if opp.get("stage") in TERMINAL:
        return {"status": "closed", "label": "已关闭", "reason": "商机已结束", "silent_days": 0}
    now = now or datetime.now()
    due = _dt(opp.get("next_action_at"))
    if due and due < now:
        days = max(0, (now - due).days)
        return {"status": "overdue", "label": "已逾期", "reason": f"下一活动已逾期 {days} 天", "silent_days": days}
    last = _dt(opp.get("last_activity_at") or opp.get("updated_at") or opp.get("created_at"))
    silent = max(0, (now - last).days) if last else 999
    if silent >= HEALTH_THRESHOLDS["at_risk_days"]:
        return {"status": "at_risk", "label": "有风险", "reason": f"已 {silent} 天无销售活动", "silent_days": silent}
    if not opp.get("next_action") or silent >= HEALTH_THRESHOLDS["attention_days"]:
        reason = "尚未安排下一活动" if not opp.get("next_action") else f"已 {silent} 天无销售活动"
        return {"status": "attention", "label": "需关注", "reason": reason, "silent_days": silent}
    return {"status": "healthy", "label": "健康", "reason": "近期有活动且已安排下一步", "silent_days": silent}


def deal_risks(opp: dict, quotes: Iterable = (), tasks: Iterable = (),
               activity_types: Iterable[str] = (), now: datetime | None = None) -> list[dict]:
    """Deal 风险引擎：只基于已有事实提示风险，不自动改变阶段。"""
    now = now or datetime.now()
    risks = []
    health = health_of(opp, now)
    qsum = quote_lifecycle_summary(quotes, now)
    details = opp.get("details") or {}
    stage = opp.get("stage")
    active_tasks = [t for t in (tasks or []) if str((t.get("fu_status") if isinstance(t, dict) else "") or "").upper()
                    in {"PENDING", "WAITING_CUSTOMER", "SNOOZED"}]
    if health["status"] in {"overdue", "at_risk", "attention"}:
        risks.append({"type": "NO_ACTIVITY_RISK", "level": "HIGH" if health["status"] == "overdue" else "MEDIUM",
                      "message": health["reason"], "fix": "创建或完成下一步跟进"})
    if stage == "QUOTED" and not qsum["has_sent"]:
        risks.append({"type": "STAGE_MISMATCH_RISK", "level": "HIGH",
                      "message": "商机处于已报价阶段，但没有已发送报价记录",
                      "fix": "发送报价后再保持 Quoted，或退回需求确认"})
    if qsum["status"] == "EXPIRED":
        risks.append({"type": "QUOTE_STALE_RISK", "level": "MEDIUM",
                      "message": qsum["risk"], "fix": "创建修订报价"})
    if stage == "QUOTED" and not active_tasks:
        risks.append({"type": "QUOTE_NO_FOLLOWUP_RISK", "level": "MEDIUM",
                      "message": "报价后没有安排跟进任务", "fix": "创建报价跟进"})
    if str(details.get("supplier_capability_status") or "").upper() in {"UNKNOWN", "NEEDS_CHECK"}:
        risks.append({"type": "SUPPLIER_UNKNOWN_RISK", "level": "MEDIUM",
                      "message": "供应能力尚未确认", "fix": "检查供应能力"})
    if str(details.get("product_match_status") or "").upper() in {"NO_MATCH", "UNRESOLVED", "INSUFFICIENT_INFORMATION"}:
        risks.append({"type": "PRODUCT_MATCH_RISK", "level": "MEDIUM",
                      "message": "内部产品匹配未完成", "fix": "匹配产品或确认替代方案"})
    if transition_errors(stage, stage, opp, has_quote=qsum["has_sent"],
                         activity_types=activity_types):
        risks.append({"type": "DATA_GAP_RISK", "level": "LOW",
                      "message": "当前阶段存在资料缺口", "fix": "补齐 Deal 关键字段"})
    return risks


def data_quality_score(opp: dict, quotes: Iterable = (), tasks: Iterable = (),
                       activity_types: Iterable[str] = ()) -> dict:
    """Deal Data Quality：衡量 CRM 数据可信度，不替代 Priority Score。"""
    details = opp.get("details") or {}
    checks = [
        ("customer", bool(opp.get("company") or opp.get("customer_id")), 15, "客户资料"),
        ("product", bool(opp.get("product")), 15, "客户产品"),
        ("quantity", bool(opp.get("quantity") or details.get("quantity")), 12, "数量"),
        ("specification", bool(opp.get("specification") or details.get("specification")
                               or details.get("customization")), 12, "规格/定制"),
        ("product_match", str(details.get("product_match_status") or "").upper()
         not in {"", "NO_MATCH", "UNRESOLVED", "INSUFFICIENT_INFORMATION"}, 14, "内部产品匹配"),
        ("supplier", str(details.get("supplier_capability_status") or "").upper()
         not in {"", "UNKNOWN", "NEEDS_CHECK"}, 10, "供应能力"),
        ("quote", bool(list(quotes or [])), 10, "报价记录"),
        ("next_action", bool(opp.get("next_action") or list(tasks or [])), 8, "下一步"),
        ("timeline", bool(set(activity_types or [])), 4, "活动记录"),
    ]
    score = sum(weight for _, ok, weight, _ in checks if ok)
    missing = [label for _, ok, _, label in checks if not ok]
    return {"score": int(score), "missing": missing,
            "label": "可信" if score >= 80 else ("需补齐" if score >= 60 else "资料不足")}


def pipeline_metrics(deals: list[dict]) -> dict:
    """按币种分别聚合，防止把不同币种直接相加。"""
    active = [d for d in deals if d.get("stage") not in TERMINAL]
    totals = {}
    month = datetime.now().strftime("%Y-%m")
    for deal in active:
        currency = deal.get("currency") or "USD"
        bucket = totals.setdefault(currency, {"pipeline": 0.0, "weighted": 0.0, "closing_month": 0.0})
        value = float(deal.get("amount") or 0)
        bucket["pipeline"] += value
        bucket["weighted"] += value * float(deal.get("probability") or PROBABILITY.get(deal.get("stage"), 0)) / 100
        if str(deal.get("expected_close") or "").startswith(month):
            bucket["closing_month"] += value
    return {"active_count": len(active), "by_currency": totals}


def recommended_action(deal: dict, health: dict | None = None) -> dict:
    health = health or health_of(deal)
    actions = {"NEW": "验证客户与询盘价值", "QUALIFIED": "确认规格与定制要求",
               "REQUIREMENT_CONFIRMED": "生成并发送报价", "QUOTED": "确认客户已收到报价",
               "SAMPLE": "记录样品反馈", "NEGOTIATION": "安排谈判并确认异议",
               "PO_PENDING": "确认 PO 与付款条款", "WON": "安排订单交付", "LOST": "记录复盘结论"}
    action = "立即完成逾期跟进" if health["status"] == "overdue" else actions.get(deal.get("stage"), "安排下一活动")
    return {"action": action, "reason": health["reason"], "follow_up": "今天" if health["status"] in {"overdue", "at_risk"} else "按计划"}
