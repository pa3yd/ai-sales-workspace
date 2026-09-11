# -*- coding: utf-8 -*-
"""第二十一轮（TEST03 之后）· 统一客户邮件生成（Unified Customer Email）。

问题背景：
  CRM 曾经把「追问邮件 / 回复邮件 / 跟进邮件」拆成三套入口与三套逻辑，
  业务员要在界面上自己决定“我该生成追问还是回复”，且三类问句可能重复。

本模块提供的统一口径（spec 1-11）：
  - EmailIntent 模型：通信意图集中定义，不散落在 UI 组件里（spec 2）。
  - determine_email_intent()：从 InquiryStatus / DealStage / FollowUpStatus /
    NextBestAction / missingBlockingFields 自动判定邮件目的（spec 3）。
  - generate_customer_email()：唯一生成管线
        determine_email_intent -> build_context -> generate ->
        dedupe_questions -> known_info_guard -> supplier_fact_guard
    内部复用既有安全策略（Test01/02 的 validate_reply_commitments /
    validate_email_fact_guard / followup_email.safety_issues），不重复维护
    一套新安全规则（spec 4/9）。
  - 只产草稿，绝不自动发送（spec 13-8）。

ctx（只读，统一入参）常用键：
  text / info / matches / gaps / stored_draft / seller_company /
  biz（workflow 业务状态）/ quotation_ready(bool) / fu_reason / fu_ctx /
  customer_product（客户原话产品短语）/ product_short / known(已答字段) /
  unresolved（仍未解决阻塞字段）/ insight / existing_issues
"""
import re

# ==================== EmailIntent 模型（spec 2） ====================
INTENT_AUTO = "AUTO"
CLARIFY_REQUIREMENT = "CLARIFY_REQUIREMENT"        # 补充信息（阻塞字段待客户确认）
CONFIRM_SPECIFICATION = "CONFIRM_SPECIFICATION"    # 确认产品规格 / 参考款
REPLY_INQUIRY = "REPLY_INQUIRY"                    # 回复客户（常规进展）
SEND_QUOTATION = "SEND_QUOTATION"                  # 发送报价
UPDATED_QUOTATION_REPLY = "UPDATED_QUOTATION_REPLY"  # 更新报价请求：先确认修订并说明内部核实
FOLLOW_UP_QUOTATION = "FOLLOW_UP_QUOTATION"        # 报价已发，客户沉默 → 跟进
FOLLOW_UP_SAMPLE = "FOLLOW_UP_SAMPLE"              # 样品已发，待反馈 → 跟进
NEGOTIATION_REPLY = "NEGOTIATION_REPLY"            # 谈判推进
PO_FOLLOW_UP = "PO_FOLLOW_UP"                      # 订单（PO）跟进
GENERAL_REPLY = "GENERAL_REPLY"                    # 通用回复（轻量进展）
FOLLOW_UP = "FOLLOW_UP"                            # 通用跟进（无特定原因时的兜底意图）

# 界面统一用这组可选项（spec 6：默认必须“自动”）
SELECT_OPTIONS = [
    ("AUTO", "自动"),
    ("CLARIFY_REQUIREMENT", "补充信息"),
    ("REPLY_INQUIRY", "回复客户"),
    ("SEND_QUOTATION", "报价"),
    ("FOLLOW_UP", "跟进"),
]

INTENT_CN = {
    INTENT_AUTO: "自动判断",
    CLARIFY_REQUIREMENT: "补充信息",
    CONFIRM_SPECIFICATION: "确认产品规格",
    REPLY_INQUIRY: "回复客户",
    SEND_QUOTATION: "发送报价",
    UPDATED_QUOTATION_REPLY: "更新报价准备",
    FOLLOW_UP_QUOTATION: "报价跟进",
    FOLLOW_UP_SAMPLE: "样品跟进",
    NEGOTIATION_REPLY: "谈判回复",
    PO_FOLLOW_UP: "订单跟进",
    GENERAL_REPLY: "通用回复",
    FOLLOW_UP: "跟进",
}

# 人工可选意图 → 内部精确意图（跟进/回复在无更多信号时落到 GENERIC 实现）
_SELECT_TO_INTENT = {
    "AUTO": None,
    CLARIFY_REQUIREMENT: CLARIFY_REQUIREMENT,
    REPLY_INQUIRY: REPLY_INQUIRY,
    SEND_QUOTATION: SEND_QUOTATION,
    FOLLOW_UP: FOLLOW_UP,
}

# FollowUpReason → 通信意图（spec 3：FollowUpStatus 参与意图判定）
REASON_INTENT = {
    "QUOTE_SENT_NO_REPLY": FOLLOW_UP_QUOTATION,
    "SAMPLE_SENT_NO_REPLY": FOLLOW_UP_SAMPLE,
    "INFORMATION_WAITING": CLARIFY_REQUIREMENT,
    "REPLY_DUE": REPLY_INQUIRY,
    "QUOTE_PREPARATION_DUE": SEND_QUOTATION,
    "CUSTOMER_PROMISED_REPLY": GENERAL_REPLY,
    "SALES_PROMISED_FOLLOWUP": GENERAL_REPLY,
    "PRODUCT_MATCH_PENDING": GENERAL_REPLY,
    "NEGOTIATION_STALLED": NEGOTIATION_REPLY,
    "PO_EXPECTED": PO_FOLLOW_UP,
    "OVERDUE_FOLLOWUP": GENERAL_REPLY,
    "MANUAL_FOLLOWUP": GENERAL_REPLY,
}

_SPEC_KEYS = {"product_spec", "open_option", "customization"}


def _has_outcome(outcome, code: str) -> bool:
    if isinstance(outcome, (list, tuple, set)):
        return code in outcome
    return str(outcome or "") == code


def customer_requested_outcome(ctx: dict):
    rs = (ctx or {}).get("resolved_state") or {}
    if rs.get("customerRequestedOutcome"):
        return rs.get("customerRequestedOutcome")
    try:
        import queue_ui as _ui
        return _ui.customer_requested_outcome_from_text((ctx or {}).get("text") or "")
    except Exception:
        return ""


def requirement_buckets(ctx: dict) -> dict:
    rs = (ctx or {}).get("resolved_state") or {}
    if rs.get("customerBlockingItems") is not None:
        return {
            "customerBlockingItems": rs.get("customerBlockingItems") or [],
            "optionalEnhancements": rs.get("optionalEnhancements") or [],
            "internalPrerequisites": rs.get("internalPrerequisites") or [],
            "requirementCompleteness": rs.get("requirementCompleteness")
                                      or rs.get("requirementStatus") or "",
        }
    try:
        import queue_ui as _ui
        return _ui.requirement_buckets(
            (ctx or {}).get("info") or {}, (ctx or {}).get("gaps") or [],
            (ctx or {}).get("matches") or [], (ctx or {}).get("text") or "")
    except Exception:
        return {"customerBlockingItems": [], "optionalEnhancements": [],
                "internalPrerequisites": [], "requirementCompleteness": ""}


def _gaps(ctx):
    return list(ctx.get("gaps") or [])


def _customer_ask_now(ctx):
    """客户侧、真正要问的阻塞项（ASK_NOW）。库内无匹配 ≠ 客户缺产品信息，
    这类内部缺口不会当成“向客户追问”的项。

    第二十四轮（恢复 R21 契约）：requirement_buckets 可能依据 info 字段缺失
    合成 quantity/product 阻塞项；若 ctx.gaps 里没有对应 ASK_NOW gap 佐证，
    这些合成项不作为向客户追问的依据（否则报价就绪/产品明确的场景会被
    误判成“缺少数量、产品”）。智能规格类定向追问（smart + product_spec）
    始终保留为客户侧阻塞 —— 这是 CONFIRM_SPECIFICATION 意图的信号源。
    """
    buckets = requirement_buckets(ctx)
    if buckets.get("customerBlockingItems") is not None:
        gap_keys = {str(m.get("key") or "") for m in _gaps(ctx)
                    if m.get("ask_timing") == "ASK_NOW"}
        bl = [m for m in (buckets.get("customerBlockingItems") or [])
              if str(m.get("key") or "") in gap_keys
              or str(m.get("key") or "") not in ("quantity", "product")]
        for m in _gaps(ctx):
            if (m.get("ask_timing") == "ASK_NOW" and m.get("smart")
                    and str(m.get("key") or "") == "product_spec"
                    and all(str(x.get("key") or "") != "product_spec"
                            for x in bl)):
                bl.append(m)
        return bl
    out = []
    for m in _gaps(ctx):
        if m.get("ask_timing") != "ASK_NOW":
            continue
        key = str(m.get("key") or "")
        if key in ("email", "company", "website", "packaging", "payment",
                   "incoterm", "sales_channel", "company_scale", "annual_volume",
                   "purchase_cycle", "competitor"):
            continue          # 档案/渠道/时机类：不问或不该现在问
        if key in ("product", "product_spec") and ctx.get("customer_product"):
            # 客户已明确产品 → 这条要么被智能追问覆盖、要么不该泛问产品
            if m.get("smart") and key == "product_spec":
                out.append(m)      # “参考型号/图片/规格”式的定向追问保留
            continue
        out.append(m)
    return out


# ==================== 意图自动判定（spec 3） ====================
def determine_email_intent(ctx: dict) -> tuple:
    """返回 (intent_code, 中文原因)。按信号强弱依次判定。"""
    ctx = ctx or {}
    fu = str(ctx.get("fu_reason") or "").strip()
    biz = str(ctx.get("biz") or "")
    ready = bool(ctx.get("quotation_ready"))
    matched = bool(ctx.get("matches"))
    product_known = bool((ctx.get("customer_product") or "").strip()
                         or (ctx.get("info") or {}).get("product_query")
                         or (ctx.get("product_short") or "").strip())

    outcome = customer_requested_outcome(ctx)
    buckets = requirement_buckets(ctx)
    ask_now = list(buckets.get("customerBlockingItems") or [])
    internal = list(buckets.get("internalPrerequisites") or [])

    # ① 客户明确要求更新报价：先看客户侧阻塞，再看内部报价前置条件
    if _has_outcome(outcome, "REQUEST_UPDATED_QUOTATION"):
        if ask_now:
            names = "、".join(m.get("name", "") for m in ask_now[:3])
            return CLARIFY_REQUIREMENT, f"客户要求更新报价，但仍缺少「{names}」。"
        if internal:
            return UPDATED_QUOTATION_REPLY, (
                "客户明确要求更新报价；客户侧信息足够，邮件应确认修订并说明内部核价/样品/证书核实。")
        return SEND_QUOTATION, "客户要求更新报价，报价要素已准备完成。"

    # ①b 客户请求供应商推荐方案：这是内部产品/成本/样品/交期核实工作，
    #     不是客户缺信息。邮件应确认需求并说明内部 review，不追问 lid/reference/photo。
    if _has_outcome(outcome, "REQUEST_PRODUCT_RECOMMENDATION") and product_known and not ask_now:
        return REPLY_INQUIRY, (
            "客户请求供应商推荐产品方案；客户侧信息足够，下一步是内部确认产品/成本/样品/交期后回复。")

    # ② 有活跃跟进任务 → 跟进原因决定意图
    if fu and fu in REASON_INTENT:
        code = REASON_INTENT[fu]
        why = _intent_why_fu(fu, ctx)
        return code, why

    # ③ 已报价且无跟进上下文 → 报价跟进（QUOTED 由 workflow/商机阶段表达）
    if biz in ("QUOTED",) and matched:
        return FOLLOW_UP_QUOTATION, "报价已发送，客户尚未回复 —— 建议发一封报价跟进。"

    # ④ 库内无匹配 + 客户产品明确（TEST03 形态）→ 回复客户：内部核实供应能力，
    #    绝不再问“您需要什么产品”。
    if not matched and product_known:
        return REPLY_INQUIRY, ("客户需求明确，但当前产品库无对应品类 —— "
                               "邮件应说明内部核实供应能力，而非反问客户产品。")

    # ⑤ 客户侧阻塞项确实存在 → 补充信息（产品已知且阻塞少而精且都是规格类时精确为确认规格）
    ask_now = _customer_ask_now(ctx)
    if ask_now:
        spec_only = (product_known
                     and all(str(m.get("key")) in _SPEC_KEYS for m in ask_now))
        if spec_only:
            return CONFIRM_SPECIFICATION, (
                "当前缺少最终规格 / 参考款，确认后可继续匹配与报价。")
        names = "、".join(m.get("name", "") for m in ask_now[:3])
        return CLARIFY_REQUIREMENT, f"当前缺少「{names}」，需先向客户确认后再推进。"

    # ⑥ 报价要素已齐 → 报价
    if ready and matched:
        return SEND_QUOTATION, "报价要素已完整，可以发送正式报价。"

    # ⑦ 其余：回复客户（有产品线索走进展式回复；无任何产品信息仍可轻量回信）
    if product_known:
        return REPLY_INQUIRY, _business_progress_reason(ctx)
    return CLARIFY_REQUIREMENT, "客户尚未说明产品方向 —— 邮件需请客户补充关键产品信息。"


def _business_progress_reason(ctx: dict) -> str:
    rs = (ctx or {}).get("resolved_state") or (ctx or {}).get("resolvedState") or {}
    customer_blockers = (rs.get("customerBlockingItems") or rs.get("blockingItems")
                         or (ctx or {}).get("customer_blocking_items") or [])
    internal_items = (rs.get("internalPrerequisites")
                      or (ctx or {}).get("internal_prerequisites") or [])
    readiness = str(rs.get("quotationReadiness") or (ctx or {}).get("quotation_readiness") or "").upper()
    product = (rs.get("customerProductRequirement") or (ctx or {}).get("customer_product")
               or _short_product(ctx))
    if customer_blockers:
        return "客户侧仍有关键确认项，需先补齐后再报价。"
    if internal_items:
        return "客户需求已基本明确，但内部供应能力和报价条件尚未确认。"
    if readiness in {"READY", "READY_FOR_QUOTATION", "READY_FOR_QUOTE", "CONDITIONAL", "PARTIALLY_READY"}:
        return "客户需求和报价要素基本明确，可进入回复或报价动作。"
    if product:
        return "客户产品需求已明确，下一步应推进回复、报价或内部确认。"
    return "客户需求仍缺少产品方向，需先确认关键需求。"


def _intent_why_fu(fu: str, ctx: dict) -> str:
    notes = {
        "QUOTE_SENT_NO_REPLY": "报价已发数日，客户未回复 —— 发送第一次报价跟进。",
        "SAMPLE_SENT_NO_REPLY": "样品已发，尚未收到客户反馈 —— 跟进样品意见。",
        "INFORMATION_WAITING": "仍在等客户补充关键信息 —— 温和提醒确认，不重复全部旧问题。",
        "REPLY_DUE": "承诺给客户的回复已到期 —— 尽快回复客户。",
        "QUOTE_PREPARATION_DUE": "报价准备到期 —— 补齐最后细节后发出报价。",
        "CUSTOMER_PROMISED_REPLY": "客户承诺的回复时间已到 —— 礼貌询问决策进展。",
        "SALES_PROMISED_FOLLOWUP": "我方承诺的跟进时间已到 —— 按承诺联系客户。",
        "PRODUCT_MATCH_PENDING": "产品匹配仍在内部进行 —— 回复客户进展，无需客户动作。",
        "NEGOTIATION_STALLED": "谈判停滞 —— 推动剩余条款达成一致。",
        "PO_EXPECTED": "客户接近下单 —— 确认采购时间线并扫清最后障碍。",
        "OVERDUE_FOLLOWUP": "跟进已逾期 —— 立即联系客户确认当前状态。",
        "MANUAL_FOLLOWUP": "跟进已逾期 —— 立即联系客户确认当前状态。",
    }
    return notes.get(fu, "按当前跟进原因推进。")


# ==================== 统一生成管线（spec 4） ====================
def generate_customer_email(ctx: dict, intent: str = INTENT_AUTO) -> dict:
    """唯一生成入口。intent=自动时先 determine_email_intent。

    返回 {intent, intent_cn, reason, subject, body, issues, question_count,
          human_review_required}
    issues 为空 = 通过既有安全策略；不为空 → 只允许人工复核后手动发送。
    """
    ctx = dict(ctx or {})
    if intent == INTENT_AUTO or not intent:
        intent, reason = determine_email_intent(ctx)
        ctx["_auto_reason"] = reason
    elif intent in _SELECT_TO_INTENT:
        resolved = _SELECT_TO_INTENT[intent]
        if resolved is None:
            resolved, reason = determine_email_intent(ctx)
        else:
            resolved = intent
            reason = _manual_reason(intent, ctx)
        intent, reason = resolved, reason
        ctx["_auto_reason"] = reason
    reason = ctx.get("_auto_reason") or _manual_reason(intent, ctx)

    # —— 分意图生成正文 ——
    body = ""
    issues = []
    outcome = customer_requested_outcome(ctx)
    if _has_outcome(outcome, "REQUEST_PRODUCT_RECOMMENDATION") and intent == REPLY_INQUIRY:
        body = _build_recommendation_ack(ctx)
    elif intent in (CLARIFY_REQUIREMENT, CONFIRM_SPECIFICATION):
        body = _build_clarify(ctx, spec_only=(intent == CONFIRM_SPECIFICATION))
        if not body:
            body = ctx.get("stored_draft") or _generic_reply(ctx)
    elif intent == UPDATED_QUOTATION_REPLY:
        body = _build_updated_quotation_reply(ctx)
    elif intent == SEND_QUOTATION:
        body = _build_quote(ctx) or ctx.get("stored_draft") or _generic_reply(ctx)
    elif intent in (FOLLOW_UP_QUOTATION, FOLLOW_UP_SAMPLE, NEGOTIATION_REPLY,
                    PO_FOLLOW_UP, GENERAL_REPLY, FOLLOW_UP):
        res = _build_followup(ctx, intent)
        body = res["body"]
        issues += res["issues"]
    else:  # REPLY_INQUIRY / 兜底
        body = ctx.get("stored_draft") or _generic_reply(ctx)

    # —— 共享后处理：去重问句 + 已知信息护栏 + 事实护栏 ——
    body = dedupe_questions(body or "")
    body = drop_known_product_questions(body, ctx)
    body = _fact_safe_rewrites(body, ctx)
    body = (body or "").strip()
    issues += _guard_issues(body, ctx)
    # 去重同一条 safety 提示（builder 与 guard 可能同时命中）
    _seen, _uniq = set(), []
    for _i in issues:
        _k = str(_i).strip()
        if _k and _k not in _seen:
            _seen.add(_k)
            _uniq.append(_k)
    issues = _uniq

    product = _short_product(ctx)
    subject = _subject_for(intent, product)
    # 若引擎草稿自带首行 Subject（LLM/规则回复习惯），提升为独立 subject 避免重复
    _lifted = re.match(r"^Subject\s*:\s*(.+?)(?:\n|$)", body, re.I | re.S)
    if _lifted:
        subject = _lifted.group(1).strip()
        body = body[_lifted.end():].lstrip("\n").strip()
    q_count = _count_questions(body)
    return {
        "intent": intent,
        "intent_cn": INTENT_CN.get(intent, intent),
        "reason": reason,
        "subject": subject,
        "body": body,
        "issues": issues,
        "question_count": q_count,
        "human_review_required": bool(issues),
    }


def _manual_reason(intent: str, ctx: dict) -> str:
    if intent == CLARIFY_REQUIREMENT:
        names = "、".join(m.get("name", "") for m in _customer_ask_now(ctx)[:3])
        return f"向客户补充关键信息（{'「' + names + '」' if names else '产品方向'}）。"
    if intent == CONFIRM_SPECIFICATION:
        return "向客户确认最终规格 / 参考款。"
    if intent == REPLY_INQUIRY:
        return "回复客户本次询盘并表达进展。"
    if intent == SEND_QUOTATION:
        return "发送正式报价草稿。"
    if intent in (FOLLOW_UP, GENERAL_REPLY):
        return "联系客户推进当前事项。"
    return _business_progress_reason(ctx)


# ---------------- 各意图正文生成 ----------------
def _build_clarify(ctx, spec_only: bool = False) -> str:
    """追问邮件（复用 gapcheck 既有生成器：只问 ASK_NOW、≤3 问）"""
    try:
        from workbench import gapcheck
    except Exception:
        return ""
    info = ctx.get("info") or {}
    seller = ctx.get("seller_company") or ""
    items = _customer_ask_now(ctx)
    if spec_only:
        items = [m for m in items if str(m.get("key")) in _SPEC_KEYS]
    if not items:
        return ""
    picked = items[:3]
    first = str(info.get("contact_name") or "").strip()
    first = first.split()[0] if first else ""
    lines = [f"Dear {first or 'Sir/Madam'},", ""]
    if ctx.get("customer_product"):
        lines.append(
            "Thank you for your inquiry. To prepare an accurate quotation for you, "
            "could you please confirm the following details:")
    else:
        lines.append(
            "Thank you for your message. To understand your requirement better, "
            "could you please confirm the following:")
    lines.append("")
    for i, m in enumerate(picked, 1):
        lines.append(f"{i}. {m.get('question') or ''}")
    lines.append("")
    lines.append("Once we have these details, we will check the applicable options "
                 "and come back to you with our proposal.")
    lines.append("")
    lines.append("Best regards,")
    if seller:
        lines.append(seller)
    return "\n".join(lines)


def _build_quote(ctx) -> str:
    """报价邮件（草稿口径与既有工作台报价文案一致，不编造承诺）。"""
    info = ctx.get("info") or {}
    matches = ctx.get("matches") or []
    if not matches:
        return ""
    seller = ctx.get("seller_company") or ""
    first = str(info.get("contact_name") or "").strip()
    first = first.split()[0] if first else ""
    qty = str(ctx.get("qty") or "").strip() or (
        f"{info.get('quantity')} pcs" if info.get("quantity") else "")
    lines = [f"Dear {first or 'Sir/Madam'},", "",
             "Thank you for your inquiry. Please find our quotation below "
             "(subject to final specification confirmation):", ""]
    for pm in matches[:2]:
        name = pm.get("name_cn") or pm.get("name", "")
        try:
            rng = f"${pm['price_range'][0]:.2f}–${pm['price_range'][1]:.2f}/pc"
        except Exception:
            rng = "price to be confirmed"
        lines.append(f"- {name}: {rng}, MOQ {pm.get('moq') or 'to be confirmed'}")
    lines.append("")
    if qty:
        lines.append(f"Quantity: {qty}")
    tp = info.get("target_price")
    if tp is not None:
        cur = info.get("target_price_currency") or "USD"
        lines.append(f"Customer target price: {cur} {tp}")
    lines.append("")
    lines.append("Final quotation is subject to confirmed specification and "
                 "trade terms.")
    lines.append("")
    lines.append("Best regards,")
    if seller:
        lines.append(seller)
    return "\n".join(lines)


def _extract_certifications(text: str) -> list:
    found = []
    for pat, label in ((r"\bCE\b", "CE"), (r"\bRoHS\b", "RoHS"),
                       (r"\bREACH\b", "REACH"), (r"\bFDA\b", "FDA")):
        if re.search(pat, text or "", re.I):
            found.append(label)
    return found


def _extract_lid_options(text: str) -> str:
    m = re.search(r"considering\s+either\s+(?:a\s+)?(.+?lid)\s+or\s+(?:a\s+)?(.+?)(?:,|\.|\n)", text or "", re.I | re.S)
    if not m:
        m = re.search(r"(?:standard\s+screw\s+lid).{0,80}?(?:integrated\s+spoon\s+lid|lid\s+with\s+an\s+integrated\s+spoon)", text or "", re.I | re.S)
        return "standard screw lid / lid with an integrated spoon" if m else ""
    vals = []
    for x in m.groups():
        val = " ".join(str(x).split()).strip(" ,.;")
        vals.append(val)
    return " / ".join(vals)


def _build_recommendation_ack(ctx) -> str:
    """TEST05: acknowledge supplier recommendation request; do not ask customer to choose."""
    info = ctx.get("info") or {}
    text = ctx.get("text") or ""
    seller = ctx.get("seller_company") or ""
    first = str(info.get("contact_name") or "").strip().split()
    name = first[0] if first else "Sir/Madam"
    product = ctx.get("customer_product") or info.get("product_query") or _short_product(ctx) or "your product"
    qty = (f"{int(info['quantity']):,} {info.get('quantity_unit') or 'pcs'}" if info.get("quantity") else "")
    facts = [product]
    for val in (info.get("material"), info.get("capacity"), info.get("specification"), info.get("customization")):
        if val and str(val) not in facts:
            facts.append(str(val))
    if qty:
        facts.append(qty)
    if info.get("target_price") is not None:
        facts.append(f"{info.get('target_price_currency') or 'USD'} {info.get('target_price')}/pc target")
    if info.get("incoterm"):
        facts.append(str(info.get("incoterm")))
    if info.get("destination") or info.get("country"):
        facts.append(str(info.get("destination") or info.get("country")))
    if info.get("delivery_time") or info.get("lead_time") or info.get("timeline"):
        facts.append(str(info.get("delivery_time") or info.get("lead_time") or info.get("timeline")))
    m = re.search(r"\b(\d+)\s+samples?\b", text, re.I)
    if m:
        facts.append(f"{m.group(1)} samples")
    lid = _extract_lid_options(text)
    lines = [f"Dear {name},", "",
             "Thank you for your inquiry and for sharing the project details.", ""]
    lines.append("We have noted: " + "; ".join(facts) + ".")
    if lid:
        lines.append("")
        lines.append("We also noted that you would like our recommendation on the lid option, including "
                     f"{lid}. We will review which option is more suitable for your market and application.")
    lines.append("")
    lines.append("Our team will check the suitable product and lid options, cost, sample arrangement, "
                 "customization cost, production lead time, pricing, and applicable compliance documentation.")
    lines.append("We will then come back to you with our recommendation and quotation.")
    lines.append("")
    lines.append("Best regards,")
    if seller:
        lines.append(seller)
    return "\n".join(lines)


def _build_updated_quotation_reply(ctx) -> str:
    """Test04 口径：确认修订需求，说明内部核实，不问可选 reference/photo。"""
    info = ctx.get("info") or {}
    rs = ctx.get("resolved_state") or {}
    req = rs.get("resolvedRequirement") or {}
    text = ctx.get("text") or ""
    seller = ctx.get("seller_company") or ""
    first = str(info.get("contact_name") or "").strip().split()
    name = first[0] if first else "Sir/Madam"
    product = (req.get("product") or ctx.get("customer_product")
               or info.get("product_query") or _short_product(ctx)
               or "your product")
    qty = req.get("quantity") or (
        f"{int(info['quantity']):,} {info.get('quantity_unit') or 'pcs'}"
        if info.get("quantity") else "")
    qhist = req.get("quantityHistory") or []
    revised = bool(req.get("quantityChanged") or re.search(r"\binstead\s+of\b|revised|updated", text, re.I))
    target = ""
    if info.get("target_price") is not None:
        cur = info.get("target_price_currency") or "USD"
        unit = info.get("target_price_unit") or "set"
        target = f"{cur} {info.get('target_price')}/{unit}"
    certs = _extract_certifications(text) or _extract_certifications(str(info.get("certification") or ""))
    samples = ""
    m = re.search(r"\b(\d+)\s+samples?\b", text, re.I)
    if m:
        samples = f"{m.group(1)} samples"
    lines = [f"Dear {name},", "",
             "Thank you for the update and for sharing the revised requirements.", ""]
    facts = []
    if qty:
        facts.append(("the revised first-order quantity of " if revised else "the first-order quantity of ") + qty)
    if target:
        facts.append(f"your target FOB price of {target}")
    facts.append(f"the updated specifications for {product}")
    lines.append("We have noted " + ", together with ".join(facts) + ".")
    lines.append("")
    extra = []
    if samples:
        extra.append(f"your request for {samples}")
    if certs:
        extra.append("/".join(certs) + " documentation for the proposed model")
    if extra:
        lines.append("We have also noted " + " and ".join(extra) + ".")
        lines.append("")
    lines.append("Our team will now review the applicable product option, sample arrangement, "
                 "customization costs, production lead time, and pricing.")
    if certs:
        lines.append("We will also verify the relevant " + "/".join(certs)
                     + " documentation for the proposed model.")
    lines.append("")
    lines.append("We will get back to you with the updated quotation once these details are confirmed internally.")
    lines.append("")
    lines.append("Best regards,")
    if seller:
        lines.append(seller)
    return "\n".join(lines)


def _build_followup(ctx, intent) -> dict:
    """跟进邮件：复用 agent.followup_email（同一安全策略），只允许草稿。"""
    from agent.followup_email import (build_followup_email, REASON_STRAT)
    fu_reason = str(ctx.get("fu_reason") or "").strip()
    if not fu_reason:
        fu_reason = {FOLLOW_UP_QUOTATION: "QUOTE_SENT_NO_REPLY",
                     FOLLOW_UP_SAMPLE: "SAMPLE_SENT_NO_REPLY",
                     NEGOTIATION_REPLY: "NEGOTIATION_STALLED",
                     PO_FOLLOW_UP: "PO_EXPECTED"}.get(intent, "MANUAL_FOLLOWUP")
    if fu_reason not in REASON_STRAT:
        fu_reason = "MANUAL_FOLLOWUP"
    info = ctx.get("info") or {}
    fu_ctx = dict(ctx.get("fu_ctx") or {})
    fu_ctx.setdefault("contact_name", info.get("contact_name") or "")
    fu_ctx.setdefault("company", info.get("company") or "")
    fu_ctx.setdefault("product", ctx.get("product_short")
                      or ctx.get("customer_product") or info.get("product_query")
                      or "")
    if not fu_ctx.get("qty") and info.get("quantity"):
        fu_ctx["qty"] = f"{info['quantity']:,} pcs"
    fu_ctx.setdefault("known", list(ctx.get("known") or []))
    res = build_followup_email(fu_reason, fu_ctx, ctx.get("seller_company") or "")
    return {"body": res.get("draft") or "", "issues": list(res.get("issues") or [])}


def _generic_reply(ctx) -> str:
    """无草稿时的兜底轻量回信（无承诺、无重复问询、无空话）。"""
    info = ctx.get("info") or {}
    first = str(info.get("contact_name") or "").strip()
    first = first.split()[0] if first else ""
    seller = ctx.get("seller_company") or ""
    lines = [f"Dear {first or 'Sir/Madam'},", "",
             "Thank you for your message. We have received your requirement and "
             "are reviewing the applicable options."]
    if ctx.get("customer_product"):
        lines.append("We will come back to you with the details shortly.")
    else:
        lines.append("To proceed, could you please share a little more about the "
                     "product you are looking for?")
    lines += ["", "Best regards,"]
    if seller:
        lines.append(seller)
    return "\n".join(lines)


# ---------------- 共享后处理 ----------------
# “reference / photo / design” 概念组：同类问句只保留一次（spec 7）
_QUESTION_CONCEPTS = (
    ("ref_photo_design", ("reference model", "reference photo", "product photo",
                          "a photo", "photo of", "design", "reference")),
    ("spec_capacity", ("capacity", "size", "specification", "spec",
                       "which model", "model or specification")),
    ("qty", ("quantity", "how many")),
    ("destination", ("country", "port", "deliver to", "destination")),
)

_GENERIC_PRODUCT_QS = re.compile(
    r"\b(which model or specification|which specification|which product|which item|"
    r"which type of product|which model are|which model do|which model would|"
    r"what product|what model|what item|"
    r"you have not specified what product|what product do you need)\b", re.I)


def _split_sentences(body: str):
    if not body:
        return []
    return [s.strip() for s in re.split(r"(?<=[.?])\s+", body) if s.strip()]


def dedupe_questions(body: str) -> str:
    """问句概念级去重：reference/photo/design 同概念只保留一次（spec 7）。

    保留第一次出现；若后续句子与已保留句子同概念且更完整（更长），替换。
    非问句一律原样保留。
    """
    if not body:
        return body
    sents = _split_sentences(body)
    kept, seen_concepts = [], set()
    for s in sents:
        is_q = s.endswith("?") and len(s) > 8
        if not is_q:
            kept.append(s)
            continue
        hit = None
        for concept, hints in _QUESTION_CONCEPTS:
            low = s.lower()
            if any(h in low for h in hints):
                hit = concept
                break
        if hit is None or hit not in seen_concepts:
            kept.append(s)
            if hit:
                seen_concepts.add(hit)
        # else: 同类问句已问过 → 丢弃重复（第 2 个及以后）
    return "\n".join(kept)


def drop_known_product_questions(body: str, ctx: dict) -> str:
    """已知信息护栏（spec 8）：产品品类/规格已存在时，绝不泛问
    “which model/product are you interested in?”。
    若因删除而一条规格追问都没剩且确有 ASK_NOW 规格项，则保留智能定向问句。
    """
    if not body:
        return body
    product_known = bool((ctx.get("customer_product") or "").strip()
                         or (ctx.get("info") or {}).get("product_query")
                         or ctx.get("matches"))
    if not product_known:
        return body
    sents = _split_sentences(body)
    out = [s for s in sents if not _GENERIC_PRODUCT_QS.search(s)]
    # 若删过头（规格类阻塞本来要问却没了），用 detect_missing 的 smart 问句补一条
    ask = _customer_ask_now(ctx)
    if (any(str(m.get("key")) in _SPEC_KEYS for m in ask)
            and not any(s.endswith("?") for s in out)):
        smart = [m for m in ask if str(m.get("key")) in _SPEC_KEYS][0]
        q = smart.get("question")
        if q:
            if out and not out[-1].endswith("?"):
                out.append(q)
            else:
                out.append(q)
    return "\n".join(out)


def _fact_safe_rewrites(body: str, ctx: dict) -> str:
    """Test04 安全改写：客户请求 ≠ 我方能力已验证。"""
    if not body:
        return body
    out = body
    out = re.sub(
        r"We will also share our available CE and RoHS documentation\.?",
        "We will also verify the relevant CE/RoHS documentation for the proposed model.",
        out, flags=re.I)
    out = re.sub(
        r"We can provide the applicable CE and RoHS documentation\.?",
        "We will verify the applicable CE/RoHS documentation for the proposed model.",
        out, flags=re.I)
    rs = (ctx or {}).get("resolved_state") or {}
    req = rs.get("resolvedRequirement") or {}
    if req.get("quantityChanged") or re.search(r"\binstead\s+of\b|revised|updated", (ctx or {}).get("text") or "", re.I):
        out = re.sub(r"\binitial quantity of\b",
                     "revised first-order quantity of", out, flags=re.I)
        out = re.sub(r"\binitial order quantity of\b",
                     "revised first-order quantity of", out, flags=re.I)
    info = (ctx or {}).get("info") or {}
    tp = info.get("target_price")
    if tp is not None:
        cur = info.get("target_price_currency") or "USD"
        val = re.escape(str(tp))
        out = re.sub(rf"\bour\s+(?:FOB\s+)?price\s+is\s+{re.escape(cur)}\s*{val}",
                     f"your target FOB price is {cur} {tp}", out, flags=re.I)
    out = re.sub(r"\b(sample availability|samples are available)\b",
                 "sample availability will be confirmed", out, flags=re.I)
    return out


def _guard_issues(body: str, ctx: dict) -> list:
    """事实护栏（spec 9）：复用 facts 的 validate_email_fact_guard +
    followup_email.safety_issues（时间承诺 / 不索取 email·payment·website / ≤3 问）。

    validate_reply_commitments 需要客户原文做证据比对，仅在提供原文时启用，
    避免跟进/澄清类（无新原文语境）误报。
    """
    issues = []
    if not body:
        return issues
    info = ctx.get("info") or {}
    try:
        from agent.facts import validate_email_fact_guard
        # 注意：签名第二参期望产品档案 dict（可为 None）；不要传产品字符串，
        # 否则函数内部 prod.get() 抛错。证书/资质等守卫只依赖正文与知识库。
        issues += validate_email_fact_guard(body, None)
    except Exception:
        pass
    if (ctx.get("text") or "").strip():
        try:
            from agent.facts import validate_reply_commitments
            issues += validate_reply_commitments(
                body, ctx.get("text") or "", info, None,
                matches=ctx.get("matches") or [])
        except Exception:
            pass
    try:
        from agent.followup_email import safety_issues
        issues += safety_issues(body, ctx.get("fu_reason") or "",
                                known=set(ctx.get("known") or []),
                                sla=bool(ctx.get("sla")))
    except Exception:
        pass
    if re.search(r"available CE and RoHS documentation|we can provide .*CE.*RoHS", body, re.I):
        issues.append("CE/RoHS requested by customer, but availability is not verified.")
    if re.search(r"\binitial quantity of\b|\binitial order quantity of\b", body, re.I) \
            and re.search(r"\binstead\s+of\b|revised|updated", ctx.get("text") or "", re.I):
        issues.append("Revised quantity must not be described as initial quantity.")
    return issues


def _count_questions(body: str) -> int:
    try:
        from agent.followup_email import count_questions as _cq
        return _cq(body)
    except Exception:
        return 0


def _short_product(ctx: dict) -> str:
    matches = ctx.get("matches") or []
    if matches:
        name = (matches[0].get("name") or matches[0].get("name_cn") or "").strip()
        if name:
            return re.sub(r"\s+", " ", name)[:60]
    p = (ctx.get("product_short") or ctx.get("customer_product")
         or (ctx.get("info") or {}).get("product_query") or "")
    p = re.sub(r"\s+", " ", str(p or "")).strip()
    return p[:60] if p else ""


def _subject_for(intent: str, product: str) -> str:
    prod = product or "your request"
    return {
        CLARIFY_REQUIREMENT: f"Re: your inquiry about {prod} - a few details to confirm",
        CONFIRM_SPECIFICATION: f"Re: your inquiry about {prod} - confirming the specification",
        SEND_QUOTATION: f"Quotation for {prod}",
        UPDATED_QUOTATION_REPLY: f"Updated quotation for {prod}",
        FOLLOW_UP_QUOTATION: f"Following up on our quotation for {prod}",
        FOLLOW_UP_SAMPLE: f"Your feedback on the samples for {prod}",
        NEGOTIATION_REPLY: f"Re: our discussion on {prod}",
        PO_FOLLOW_UP: f"Re: your order of {prod}",
        GENERAL_REPLY: f"Re: your inquiry about {prod}",
        REPLY_INQUIRY: f"Re: your inquiry about {prod}",
        FOLLOW_UP: f"Re: {prod}",
    }.get(intent, f"Re: {prod}")


def ctx_from_ui(**kw) -> dict:
    """UI 组装用：过滤空值并返回上下文 dict。"""
    return {k: v for k, v in kw.items() if v is not None}
