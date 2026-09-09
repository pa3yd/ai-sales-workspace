# -*- coding: utf-8 -*-
"""Phase 3 · AI Priority + Next Best Action 纯逻辑层（第十三轮，无 Streamlit 依赖）。

目标：建立真实、可解释、稳定的 AI 销售优先级系统。

一、AI Priority（0-100）
    七维全部 0-100：采购意向 / 需求明确度 / 产品匹配 / 报价准备度 /
    客户价值 / 紧迫度 / 跟进风险。
    总分 = Σ(维度分 × 权重)，权重固定、和 = 1.00，任何人可用计算器复算：
        AI Priority = round( intent×0.20 + clarity×0.10 + match×0.15
                           + quote×0.10 + value×0.15 + urgency×0.15 + risk×0.15 )
    禁止随机：同一输入 → 同一输出；本模块无任何随机源。

二、缺失数据语义（不猜数）
    - 某维没有足够数据 → state=nodata，UI 显示「暂无数据」，内部按 0 分计入总分；
    - 例如产品匹配：客户没说清要什么、产品库也无匹配痕迹 → Product Match：暂无数据；
    - 报价准备度：关键信息缺失 → 显示 NOT_READY（枚举），不虚报成分数；
    - 绝不输出「无依据的高分」（如 83），nodata 维永远 0 分 + 明确原因。

三、评分解释（UI 用）
    每维输出净贡献点 = 得分 − 中性基准 50（正为加分项、负为减分项），
    并带一句人类可读原因；nodata 维单独列出「暂无数据 · 未虚估」。

四、Next Best Action（NBA）
    基于既有 workflow.next_action（不重写状态机），包装输出：
    current_stage / current_status / next_action / reason / action_type / priority。
    Action Type 八类：REPLY · COLLECT_INFO · MATCH_PRODUCT · CREATE_QUOTE ·
    FOLLOW_UP · NEGOTIATE · SCHEDULE_FOLLOW_UP · MARK_COMPLETE。

五、Queue Score（队列默认排序）
    不只按 AI Priority：综合 Priority + 跟进风险 + 紧迫度 + 阶段紧急度：
        Queue Score = round( AIP×0.50 + risk×0.15 + urgency×0.15 + stage×0.20 )

数据来源（全部来自调用方传入的真实字段，模块只读、绝不回写业务数据）：
  item.need.lead_summary   ← report_json.lead（LeadScorer 结果，db 只读派生）
  item.need.readiness      ← report_json.insight.quotation_readiness
  item.need.blockers       ← 报价准备度阻塞项
  item.need.product_cat    ← 客户已明确的产品品类
  item.matches             ← 产品库匹配结果
  item.wf / item.created   ← 销售闭环时间字段（回复/跟进/创建）
"""
import datetime

# ---------------- 七维定义（key / 中文 / 权重，权重和 = 1.00） ----------------
AIP_DEF = [
    ("purchase_intent", "采购意向", 0.20),
    ("product_clarity", "需求明确度", 0.10),
    ("product_match", "产品匹配", 0.15),
    ("quote_readiness", "报价准备度", 0.10),
    ("customer_value", "客户价值", 0.15),
    ("urgency", "紧迫度", 0.15),
    ("followup_risk", "跟进风险", 0.15),
]
AIP_WEIGHTS = {k: w for k, _cn, w in AIP_DEF}

# ---------------- 报价准备度：统一四态（与 agent.facts 口径一致） ----------------
QR_UNIFY = {
    # insight 原始四级
    "insufficient_info": "NOT_READY",
    "needs_confirmation": "NOT_READY",      # 旧版兼容
    "cannot_quote": "NOT_READY",            # 旧版兼容
    "preliminary_quote_ready": "PARTIALLY_READY",
    "ready_for_quotation": "READY_FOR_QUOTE",
    "quoted": "QUOTED",
    # 统一四态原样通过
    "NOT_READY": "NOT_READY",
    "PARTIALLY_READY": "PARTIALLY_READY",
    "READY_FOR_QUOTE": "READY_FOR_QUOTE",
    "QUOTED": "QUOTED",
}
QR_CN = {
    "NOT_READY": "NOT_READY · 暂不可报价",
    "PARTIALLY_READY": "PARTIALLY_READY · 可初步报价",
    "READY_FOR_QUOTE": "READY_FOR_QUOTE · 可正式报价",
    "QUOTED": "QUOTED · 已报价",
}
QR_SCORE = {      # 枚举 → 0-100 分（映射写死，可复算；nodata 不在此表）
    "NOT_READY": 15,
    "PARTIALLY_READY": 55,
    "READY_FOR_QUOTE": 85,
    "QUOTED": 92,
}

# ---------------- 紧迫度 label → 分 ----------------
URGENCY_SCORE = {"high": 90, "medium": 55, "low": 15}
URGENCY_REASON = {
    "high": "客户明确紧急（urgent / ASAP / 明确短交期），需优先处理",
    "medium": "客户表达希望尽快（fast delivery / 时间紧张），正常从速",
    "low": "未识别到紧急表述，按常规节奏跟进",
}

# ---------------- NBA：Action Type 八类（Phase 3 规范五） ----------------
ACT_TYPE_CN = {
    "REPLY": "REPLY · 回复客户",
    "COLLECT_INFO": "COLLECT_INFO · 收集/确认信息",
    "MATCH_PRODUCT": "MATCH_PRODUCT · 产品匹配",
    "CREATE_QUOTE": "CREATE_QUOTE · 创建报价",
    "FOLLOW_UP": "FOLLOW_UP · 客户跟进",
    "NEGOTIATE": "NEGOTIATE · 商务谈判",
    "SCHEDULE_FOLLOW_UP": "SCHEDULE_FOLLOW_UP · 安排跟进计划",
    "MARK_COMPLETE": "MARK_COMPLETE · 完成/归档",
}

# workflow 原始 action type → Phase 3 八类
_ACT_TYPE_MAP = {
    "CONFIRM_PRODUCT": "COLLECT_INFO", "CONFIRM_QUANTITY": "COLLECT_INFO",
    "CONFIRM_SPECIFICATION": "COLLECT_INFO", "CONFIRM_DESTINATION": "COLLECT_INFO",
    "CONFIRM_TERMS": "COLLECT_INFO", "CONFIRM_CONTACT": "COLLECT_INFO",
    "CHECK_CERTIFICATION": "COLLECT_INFO", "CHECK_SAMPLE": "COLLECT_INFO",
    "SEND_CATALOG": "COLLECT_INFO",
    "REVIEW_REPLY": "REPLY", "SEND_REPLY": "REPLY", "ANALYZE_REPLY": "REPLY",
    "CREATE_QUOTE": "CREATE_QUOTE",
    "FOLLOW_UP_CUSTOMER": "FOLLOW_UP", "FOLLOW_UP_QUOTE": "FOLLOW_UP",
    "CREATE_FOLLOW_UP": "SCHEDULE_FOLLOW_UP",
}

# blocker 字段英文 → 中文（供 NBA reason 与 UI，与 app.FIELD_CN 对齐）
_FIELD_CN = {
    "product": "产品/品类", "product_category": "产品类别", "product_model": "产品型号",
    "reference_model": "参考型号", "product_spec": "产品规格", "spec": "规格",
    "quantity": "数量", "customization": "定制要求", "destination": "目的地",
    "certification": "认证", "delivery": "交期", "packaging": "包装",
    "payment": "付款方式", "incoterm": "贸易术语", "email": "邮箱",
    "company": "公司信息", "contact": "联系人", "logo": "Logo/贴牌",
}

# 业务状态（12 态）→ 兜底「阶段紧急度」分（Queue Score 的 stage 项；
# 实际 stage 由 stage_emergency() 按 act / 跟进状态细化）
STAGE_PTS = {
    "NEW": 45, "ANALYZING": 45,
    "NEEDS_INFO": 78, "READY_TO_REPLY": 85,
    "REPLIED": 45, "FOLLOW_UP": 55,
    "READY_FOR_QUOTE": 82, "QUOTED": 60, "NEGOTIATING": 58,
    "WON": 0, "LOST": 0, "ON_HOLD": 0,
}
# 需我方先动作、以「询盘到达时间」计跟进风险的业务状态
_WAITING_BIZ = {"NEW", "ANALYZING", "NEEDS_INFO", "READY_TO_REPLY",
                "READY_FOR_QUOTE"}
_TERMINAL_BIZ = {"WON", "LOST", "ON_HOLD"}

# ---------------- 工具 ----------------
def _cap(x):
    return max(0, min(100, int(round(x))))


def _num(v):
    """只接受数字/可安全转数字的字符串；其它（含 None/''/不可解析）→ None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _parse_ts(s):
    """'2026-09-07 09:30'（或仅日期）→ datetime；失败返回 None。"""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def lead_summary_of(item: dict) -> dict:
    """从 item 提取 lead 摘要：need.lead_summary 优先，其次完整 lead dict（详情页）。"""
    need = item.get("need") or {}
    ls = need.get("lead_summary") or {}
    if ls:
        return ls
    lead = item.get("lead") or {}
    out = {}
    dmap = {}
    for d in (lead.get("dims") or []):
        if isinstance(d, dict) and d.get("key"):
            s = d.get("score")
            if isinstance(s, (int, float)):
                dmap[d["key"]] = s

    def g(top, dim):
        v = lead.get(top)
        return v if isinstance(v, (int, float)) else dmap.get(dim)

    out["intent"] = g("purchase_intent_score", "intent")
    out["volume"] = g("order_volume_score", "volume")
    out["clarity"] = g("requirement_clarity_score", "clarity")
    out["quality"] = g("customer_quality_score", "quality")
    out["match"] = g("product_match_score", "product_match")
    out["urgency"] = str(lead.get("urgency") or "")
    out["urgency_score"] = g("urgency_score", "urgency")
    return out


def field_cn(field: str) -> str:
    return _FIELD_CN.get(str(field or ""), str(field or ""))


# ---------------- 七维评分 ----------------
def _reason_bucket(cn: str, s: float) -> str:
    if s >= 70:
        return f"{cn}信号强，是得分主力"
    if s >= 40:
        return f"{cn}处于中等水平"
    return f"{cn}偏弱，是当前主要短板"


def dim_purchase_intent(ls: dict) -> dict:
    s = _num(ls.get("intent"))
    if s is None:
        return {"state": "nodata", "score": 0, "display": "暂无数据",
                "reason": "缺少采购意向评分数据（不虚估，按 0 计入）"}
    return {"state": "known", "score": _cap(s), "display": str(_cap(s)),
            "reason": "高采购意向" if s >= 70 else (
                "有采购意向表达" if s >= 40 else "采购意向偏弱（泛询盘/信号不足）")}


def dim_product_clarity(ls: dict) -> dict:
    s = _num(ls.get("clarity"))
    if s is None:
        return {"state": "nodata", "score": 0, "display": "暂无数据",
                "reason": "缺少需求明确度评分数据（不虚估，按 0 计入）"}
    return {"state": "known", "score": _cap(s), "display": str(_cap(s)),
            "reason": "需求描述清晰（规格/数量/要求明确）" if s >= 70 else (
                "需求方向清楚但细节不全" if s >= 40 else "需求表述笼统")}


def dim_product_match(item: dict, ls: dict) -> dict:
    """产品匹配：matched（产品库命中）/ low（知道要什么但未命中）/ nodata（无数据）。

    命中判定顺序：调用方显式传 matches → need.match_top（队列行只读派生）→
    未命中/无数据分支。绝不把“没数据”当成精确分。
    """
    need = item.get("need") or {}
    matches = item.get("matches") or []
    best, best_s = None, 0.0
    for m in matches:
        if isinstance(m, dict):
            ms = _num(m.get("match_score"))
            if ms is not None and ms >= best_s:
                best_s, best = ms, m
    # 队列行没有完整 matches → 用 db 只读派生的 match_top
    if best is None and (need.get("match_top") or {}).get("name"):
        _ms = _num((need.get("match_top") or {}).get("score"))
        if _ms is not None:
            best_s, best = _ms, need["match_top"]
    if best is not None and best_s >= 0.3:
        name = str(best.get("name_cn") or best.get("name") or "")
        return {"state": "matched", "score": _cap(best_s * 100),
                "display": str(_cap(best_s * 100)),
                "reason": f"产品库匹配「{name}」，匹配度 {int(best_s * 100)}%"}
    cat = str(need.get("product_cat") or "").strip()
    prod = str(need.get("product") or "").strip()
    lm = _num(ls.get("match"))
    if matches or cat or prod:
        base = _cap(lm) if lm is not None else 10
        score = max(5, min(20, base))
        kw = f"（客户提到：{cat or prod}）" if (cat or prod) else ""
        return {"state": "low", "score": score, "display": str(score),
                "reason": f"产品库未命中匹配产品{kw}，需人工选品/确认供应（按低分计，不虚估）"}
    return {"state": "nodata", "score": 0, "display": "暂无数据",
            "reason": "缺少产品信息，未评估产品匹配（不虚估，按 0 计入）"}


def dim_quote_readiness(item: dict) -> dict:
    need = item.get("need") or {}
    raw = str(need.get("readiness") or "").strip()
    unif = QR_UNIFY.get(raw)
    if unif is None:
        return {"state": "nodata", "score": 0, "display": "暂无数据",
                "reason": "缺少报价准备度数据（不虚估，按 0 计入）"}
    blk = [field_cn(b) for b in (need.get("blockers") or [])]
    reason = {
        "NOT_READY": "关键报价信息缺失" + (f"（{('、'.join(blk[:3]))}）" if blk else "") + "，暂不可报价",
        "PARTIALLY_READY": "关键项部分确认，可先给参考价/价格区间",
        "READY_FOR_QUOTE": "产品/规格/数量及关键商务条件已确认，可正式报价",
        "QUOTED": "正式报价已发送，转入跟进",
    }[unif]
    return {"state": "known", "score": QR_SCORE[unif], "display": QR_CN[unif],
            "reason": reason}


def dim_customer_value(ls: dict) -> dict:
    vol, qua = _num(ls.get("volume")), _num(ls.get("quality"))
    if vol is None and qua is None:
        return {"state": "nodata", "score": 0, "display": "暂无数据",
                "reason": "缺少客户公司与订单规模信息（不虚估，按 0 计入）"}
    vals = [v for v in (vol, qua) if v is not None]
    s = sum(vals) / len(vals)
    return {"state": "known", "score": _cap(s), "display": str(_cap(s)),
            "reason": "客户价值高（公司背景/采购规模信号强）" if s >= 70 else (
                "客户价值中等" if s >= 40 else "客户价值偏低（背景/规模信息有限）")}


def dim_urgency(item: dict, ls: dict) -> dict:
    label = str(ls.get("urgency") or (item.get("need") or {}).get("urgency") or "").lower()
    s = _num(ls.get("urgency_score"))
    if s is None and label:
        s = URGENCY_SCORE.get(label)
    if s is None:
        s = 15.0      # 没催 = 不急（lead 同口径：无紧急表达按 low 计）
        label = "low"
    return {"state": "known", "score": _cap(s), "display": str(_cap(s)),
            "reason": URGENCY_REASON.get(label, "未识别到紧急表述")}


def _risk_anchor(item: dict) -> datetime.datetime:
    """跟进风险的时间锚点：需我方先动作的询盘从创建时间起算；已回复/报价后从最近
    回复时间起算（回复会重置沉默）。返回 None 表示无时间数据。"""
    biz = item.get("biz") or ""
    if biz in _WAITING_BIZ:
        return _parse_ts(item.get("created"))
    wf = item.get("wf") or {}
    return _parse_ts(wf.get("last_replied_at")) or _parse_ts(item.get("created"))


def dim_followup_risk(item: dict, now: datetime.datetime = None) -> dict:
    now = now or datetime.datetime.now()
    biz = item.get("biz") or ""
    if biz in _TERMINAL_BIZ:
        return {"state": "known", "score": 0, "display": "0",
                "reason": "终态/暂缓，无跟进风险"}
    anchor = _risk_anchor(item)
    if anchor is None:
        return {"state": "nodata", "score": 0, "display": "暂无数据",
                "reason": "缺少时间数据，无法计算跟进风险（不虚估）"}
    silent_h = max(0.0, (now - anchor).total_seconds() / 3600.0)
    score = 0 if silent_h < 1 else _cap(silent_h * 100.0 / 72.0)   # 72h=100；48h≈67
    if silent_h < 1:
        tip = "刚刚有过动作，暂无跟进风险"
    elif silent_h < 24:
        tip = f"{int(silent_h)} 小时内无新进展，风险低"
    elif silent_h < 48:
        tip = f"已 {int(silent_h)} 小时无进展，接近 48 小时风险线"
    else:
        tip = f"已超过 48 小时未推进（{int(silent_h)} 小时），客户流失风险显著上升"
    return {"state": "known", "score": score, "display": str(score), "reason": tip}


# ---------------- AI Priority 聚合 + 解释 ----------------
def seven_dims(item: dict, now: datetime.datetime = None) -> list:
    ls = lead_summary_of(item)
    dims = {
        "purchase_intent": dim_purchase_intent(ls),
        "product_clarity": dim_product_clarity(ls),
        "product_match": dim_product_match(item, ls),
        "quote_readiness": dim_quote_readiness(item),
        "customer_value": dim_customer_value(ls),
        "urgency": dim_urgency(item, ls),
        "followup_risk": dim_followup_risk(item, now),
    }
    rows = []
    for key, cn, _w in AIP_DEF:
        d = dims[key]
        rows.append({"key": key, "cn": cn, "state": d["state"],
                     "score": d["score"], "display": d["display"],
                     "reason": d["reason"]})
    return rows


def ai_priority(item: dict, now: datetime.datetime = None) -> dict:
    """计算 AI Priority 与可解释明细。

    返回：{aip, rows, level_cn, formula}；rows 每维含 net（=得分−50，nodata 为 None）。
    """
    rows = seven_dims(item, now)
    total = round(sum(r["score"] * AIP_WEIGHTS[r["key"]] for r in rows))
    total = max(0, min(100, total))
    level = "🔴 高优先（4 小时内回复）" if total >= 80 else (
        "🟠 中高优先（24 小时内回复）" if total >= 60 else (
            "🟢 常规优先（48 小时内回复）" if total >= 40 else
            "⚪ 低优先（可先归档培养）"))
    formula = " + ".join(f"{r['score']}×{AIP_WEIGHTS[r['key']]:.2f}" for r in rows)
    for r in rows:
        r["net"] = (r["score"] - 50) if r["state"] != "nodata" else None
    return {"aip": total, "rows": rows, "level_cn": level,
            "formula": formula}


# ---------------- Next Best Action ----------------
def next_best_action(item: dict, act: dict = None, fu_state: str = "",
                     now: datetime.datetime = None) -> dict:
    """把既有 workflow next_action 包装成 Phase 3 NBA（不改状态机）。

    返回：{current_stage, current_status, action_label, action_type, action_type_cn,
          reason, priority, orig_type}
    reason 分支按 act 原始 type 驱动，与 workflow.next_action 优先级顺序一致。
    """
    now = now or datetime.datetime.now()
    biz = item.get("biz") or ""
    need = item.get("need") or {}
    act = act or {}

    # Current Stage（漏斗中文 / 终态中文）
    try:
        import crm as _crm
        stg = _crm.opp_stage_of_biz(biz)
        stage_cn = _crm.OPP_CN.get(stg, stg)
    except Exception:
        stage_cn = ""
    status_cn = _biz_cn(biz)

    action_type = _ACT_TYPE_MAP.get(act.get("type") or "")
    action_label = str(act.get("label") or "")
    priority = str(act.get("priority") or "")
    blk = [field_cn(b) for b in (need.get("blockers") or [])]
    t = act.get("type")

    if biz in _TERMINAL_BIZ:
        action_type = action_type or "MARK_COMPLETE"
        if biz == "WON":
            action_label = action_label or "已成交"
            reason = "客户已确认成交，转入履约/生产跟进"
        elif biz == "LOST":
            action_label = action_label or "已丢单"
            reason = "客户已明确放弃（可推进到谈判复活）"
        else:
            action_label = action_label or "暂缓"
            reason = "询盘暂缓中，恢复后再进入销售流程"
    elif t and t.startswith("CONFIRM") or t in ("CHECK_CERTIFICATION",
                                               "CHECK_SAMPLE", "SEND_CATALOG"):
        action_type = action_type or "COLLECT_INFO"
        reason = (f"缺少 {'、'.join(blk[:3])}"
                  + (" 等" if len(blk) > 3 else "")
                  + ("；或客户仍有信息需确认" if not blk else "") +
                  "——报价要素不完整，先补齐再推进")
    elif (t == "CREATE_QUOTE" or biz == "READY_FOR_QUOTE"
          or need.get("readiness") in ("ready_for_quotation", "READY_FOR_QUOTE")):
        # 业务状态证据（已具备报价条件）优先于泛型 type 映射
        action_type = ("CREATE_QUOTE" if (biz == "READY_FOR_QUOTE"
                                          or need.get("readiness")
                                          in ("ready_for_quotation",
                                              "READY_FOR_QUOTE"))
                       else (action_type or "CREATE_QUOTE"))
        reason = "产品/规格/数量等已确认，尽快给正式报价可避免客户流失"
    elif t == "FOLLOW_UP_QUOTE":
        action_type = ("NEGOTIATE" if biz == "NEGOTIATING" else
                       action_type or "FOLLOW_UP")
        reason = ("报价已发出，需主动跟进拿客户反馈（客户可能正在对比供应商）"
                  if biz != "NEGOTIATING" else
                  "客户正在谈判/还价，按商务条款逐轮推进并守住底线")
    elif t == "FOLLOW_UP_CUSTOMER" or fu_state in ("已逾期", "今日跟进"):
        action_type = "FOLLOW_UP"
        reason = "跟进时间已到/已逾期，客户可能转向其它供应商，请立即跟进"
    elif t in ("REVIEW_REPLY", "SEND_REPLY", "ANALYZE_REPLY"):
        action_type = action_type or "REPLY"
        reason = "回复草稿已就绪，请人工审阅后发送（或按客户反馈修订）"
    elif t == "CREATE_FOLLOW_UP":
        action_type = action_type or "SCHEDULE_FOLLOW_UP"
        reason = "已回复客户但未安排下次跟进，容易断联，先设一个提醒"
    elif not action_label:
        action_label = "等待客户反馈"
        action_type = action_type or "FOLLOW_UP"
        reason = "已回复/已跟进，等待客户反馈；期间无需主动打扰"
    else:
        reason = "按当前销售阶段推进即可"

    return {"current_stage": stage_cn or status_cn, "current_status": status_cn,
            "action_label": action_label, "action_type": action_type or "",
            "action_type_cn": ACT_TYPE_CN.get(action_type, action_type or "—"),
            "reason": reason, "priority": priority,
            "orig_type": act.get("type") or ""}


def _biz_cn(biz: str) -> str:
    try:
        import workflow as _wf
        return _wf.BIZ_CN.get(biz or "", biz or "")
    except Exception:
        return biz or ""


def stage_emergency(item: dict, act: dict = None, fu_state: str = "") -> int:
    """Queue Score 的 stage 项：表达「该行现在多需要被处理」。

    规则（优先级从高到低）：
      到期/逾期跟进 95 > 待回复/待补信息 85 > 可报价 82 > 跟报价 60 >
      待安排跟进 55 > 在等客户 45 > 本轮跟进已完成 20 > 历史已处理(无待办) 10 >
      终态/暂缓 0。
    """
    biz = item.get("biz") or ""
    if biz in _TERMINAL_BIZ:
        return 0
    act = act or {}
    at = act.get("type")
    if at == "FOLLOW_UP_CUSTOMER" or fu_state in ("已逾期", "今日跟进"):
        return 95
    status = item.get("status") or ""
    if biz in ("READY_TO_REPLY", "NEEDS_INFO") and status != "已处理":
        return 85
    if biz == "READY_FOR_QUOTE" or at == "CREATE_QUOTE":
        return 82
    if biz == "QUOTED":
        return 60
    if biz == "NEGOTIATING":
        return 58
    if at == "CREATE_FOLLOW_UP":
        return 55
    wf = item.get("wf") or {}
    if wf.get("follow_up_done"):
        return 20
    if status == "已处理" and not (wf.get("follow_up_at") or ""):
        return 10            # 历史旧「已处理」记录：无我方待办，沉底
    return 45                # 已回复/报价后在等客户


# ---------------- Queue Score ----------------
def queue_score(item: dict, aip: int = None, act: dict = None,
                fu_state: str = "", now: datetime.datetime = None) -> dict:
    """综合队列分：不只按 AI Priority。

        Queue Score = round( AIP×0.50 + 跟进风险×0.15 + 紧迫度×0.15 + 阶段紧急度×0.20 )
    已成交/已丢单/暂缓 阶段紧急度 = 0（沉底，不打扰正在处理的队列）。
    返回 {qs, aip, risk, urgency, stage}。
    """
    now = now or datetime.datetime.now()
    dims = {r["key"]: r for r in seven_dims(item, now)}
    aip = aip if aip is not None else round(
        sum(dims[k]["score"] * AIP_WEIGHTS[k] for k in AIP_WEIGHTS))
    risk = dims["followup_risk"]["score"]
    urg = dims["urgency"]["score"]
    stage = stage_emergency(item, act, fu_state)
    qs = round(aip * 0.50 + risk * 0.15 + urg * 0.15 + stage * 0.20)
    return {"qs": qs, "aip": aip, "risk": risk, "urgency": urg, "stage": stage}


# ---------------- 完整入口（队列/详情共用） ----------------
def analyze(item: dict, act: dict = None, now: datetime.datetime = None) -> dict:
    """一条询盘 → AI Priority + NBA + Queue Score 完整结果。

    item 必需键：id / created / status / biz / need / wf（队列行 load_queue 已具备，
    详情页另可补 matches / lead 提高精确度）。act 为 workflow.next_action 结果。
    """
    now = now or datetime.datetime.now()
    aip_res = ai_priority(item, now)
    nba = next_best_action(item, act, fu_state="", now=now)
    qs_res = queue_score(item, aip_res["aip"], act, fu_state="", now=now)
    return {"id": item.get("id"), "aip": aip_res["aip"],
            "rows": aip_res["rows"], "level_cn": aip_res["level_cn"],
            "formula": aip_res["formula"],
            "nba": nba, "qs": qs_res["qs"],
            "qs_parts": {k: qs_res[k] for k in ("aip", "risk", "urgency", "stage")}}
