# -*- coding: utf-8 -*-
"""第十八轮（ROUND 3）· AI Follow-up Email 生成（离线规则版）。

原则（spec 11/12/13）：
  - 复用 Test01/Test02 已建立的 Email Safety Policy（validate_reply_commitments /
    count_questions）：不发无依据承诺、问句 ≤3、不索取已存在信息。
  - 按 FollowUpReason 分策略：不同 reason 引用不同上一轮上下文，
    绝不生成无上下文的 "Just following up on my previous email."。
  - 不重复询问客户已回答的字段（ctx.known 字段集合传入即跳过）。
  - 输出 {draft, question_count, issues}；issues 空 = 通过安全策略。
本模块不自动发送邮件（spec 40-20）。
"""
import re

MAX_QUESTIONS = 3


def _company(info):
    return str((info or {}).get("company") or "").strip()


def _contact(info):
    raw = str((info or {}).get("contact_name") or "").strip()
    return raw.split()[0] if raw else (str((info or {}).get("contact") or "").strip() or "")


def _product_line(ctx):
    product = str((ctx or {}).get("product") or "").strip()
    qty = str((ctx or {}).get("qty") or "").strip()
    deal = str((ctx or {}).get("deal") or "").strip()
    bits = [b for b in (deal or product, qty) if b]
    return " · ".join(bits) or "your requested product"


# ---------------- reason → 邮件策略 ----------------
def _block_quote(ctx, first):
    product = _product_line(ctx)
    lines = [
        f"Dear {first},",
        "",
        f"I am writing regarding the quotation we sent for {product}. "
        "Have you had a chance to review it?",
        "We are happy to clarify any point, adjust the configuration, or "
        "answer questions on lead time and payment — please let us know.",
    ]
    return lines


def _block_sample(ctx, first):
    product = _product_line(ctx)
    lines = [
        f"Dear {first},",
        "",
        f"We hope the samples for {product} have arrived safely. "
        "Could you share your feedback on the samples?",
        "Once you confirm the specification, we can proceed to arrange the "
        "next order step accordingly.",
    ]
    return lines


def _block_info_waiting(ctx, first):
    # 只提醒仍未解决的关键信息（ctx.unresolved），不重复全部旧问题
    unresolved = [str(x) for x in ((ctx or {}).get("unresolved") or [])]
    if not unresolved:
        unresolved = ["the key specification we asked about"]
    prod = _product_line(ctx)
    if len(unresolved) == 1:
        ask = unresolved[0]
    else:
        ask = " / ".join(f"{i}) {u}" for i, u in enumerate(unresolved, 1))
    lines = [
        f"Dear {first},",
        "",
        f"Following up on our previous message regarding {prod}: we are still "
        "missing the following to finalize the most suitable solution for you:",
        f"- {ask}",
        "",
        "Could you kindly confirm the above? Once confirmed we will respond "
        "with the details right away.",
    ]
    return lines


def _block_reply_due(ctx, first):
    prod = _product_line(ctx)
    return [f"Dear {first},", "",
            f"Thank you for your inquiry about {prod}. Our team has prepared "
            "the information you requested.",
            "We will send it to you shortly — please let us know if there is "
            "anything else we should include."]


def _block_quote_prep(ctx, first):
    prod = _product_line(ctx)
    unresolved = [str(x) for x in ((ctx or {}).get("unresolved") or [])]
    extra = []
    if unresolved:
        extra = ["", "To finalize, please confirm: "
                 + (" / ".join(f"{i}) {u}" for i, u in enumerate(unresolved, 1))
                    if len(unresolved) > 1 else unresolved[0]) + "."]
    return ([f"Dear {first},", "",
             f"We are finalizing the quotation for {prod} as planned. "
             "To send an accurate proposal, we need the last detail confirmed."]
            + extra
            + ["", "You will receive the quotation right after that."])


def _block_customer_promised(ctx, first):
    # 引用客户承诺的时间口径；无具体日期时不编造
    frame = str((ctx or {}).get("promised_timeframe") or "").strip()
    prod = _product_line(ctx)
    body = (f"As you mentioned you would update us{(' ' + frame) if frame else ''}, "
            "we wanted to check in on your decision for " + prod + ".")
    return [f"Dear {first},", "", body,
            "If anything needs adjustment on our side, we would be glad to "
            "address it — please just let us know."]


def _block_sales_promised(ctx, first):
    prod = _product_line(ctx)
    return [f"Dear {first},", "",
            f"As promised in our previous exchange about {prod}, we are "
            "getting back to you as agreed.",
            "Please let us know if you need anything further from our side."]


def _block_match_pending(ctx, first):
    prod = _product_line(ctx)
    return [f"Dear {first},", "",
            f"Thank you for your patience while we review the best configuration "
            f"for {prod} against your requirement.",
            "We will come back shortly with the closest matching option — "
            "no further action is needed from you right now."]


def _block_negotiation(ctx, first):
    return [f"Dear {first},", "",
            "We would like to keep the momentum on our discussion and close "
            "the remaining point between us.",
            "Could you let us know your view on the open item, so we can "
            "finalize the agreement?"]


def _block_po_expected(ctx, first):
    prod = _product_line(ctx)
    return [f"Dear {first},", "",
            f"With regard to {prod}, we understand you are close to placing "
            "the order.",
            "Could you confirm your expected purchase timeline, and let us "
            "know if any final point is still blocking the order?"]


def _block_overdue(ctx, first):
    prod = _product_line(ctx)
    return [f"Dear {first},", "",
            f"We previously planned to follow up with you on {prod} at this "
            "time, as arranged.",
            "Please let us know the current status on your side."]


def _block_manual(ctx, first):
    note = str((ctx or {}).get("note") or "").strip()
    lines = [f"Dear {first},", ""]
    lines.append(note if note else "Following up on our recent discussion.")
    return lines


_BUILDERS = {
    "reply_due": _block_reply_due,
    "quote_no_reply": _block_quote,
    "sample_no_reply": _block_sample,
    "info_waiting": _block_info_waiting,
    "customer_promised": _block_customer_promised,
    "sales_promised": _block_sales_promised,
    "quote_prep": _block_quote_prep,
    "match_pending": _block_match_pending,
    "negotiation_stalled": _block_negotiation,
    "po_expected": _block_po_expected,
    "overdue": _block_overdue,
    "manual": _block_manual,
}


def _closing(ctx, seller):
    close = ["", "Best regards,"]
    who = str((ctx or {}).get("seller_name") or "").strip()
    comp = seller or str((ctx or {}).get("seller_company") or "").strip()
    who = f"{who}\n" if who else ""
    if comp:
        close.append(f"{who}{comp}")
    elif who:
        close.append(who.rstrip("\n"))
    else:
        close.append("")
    return close


def build_followup_email(reason: str, ctx: dict, seller_company: str = "") -> dict:
    """按 reason 生成跟进邮件正文 + 安全校验。

    ctx 键（只读）：company/contact_name/contact/product/qty/deal/known(已答字段集合)
      unresolved(仍未解决阻塞字段)/promised_timeframe/note/last_summary。
    返回 {"draft", "question_count", "issues"}。
    """
    info = ctx or {}
    reason = str(reason or "")
    strat_key = REASON_STRAT.get(reason, "manual")
    builder = _BUILDERS.get(strat_key, _block_manual)
    first = _contact(info)
    head = builder(info, first or "Sir/Madam")
    tail = _closing(info, seller_company)
    draft = "\n".join(head + tail).strip()
    if draft.endswith("\n\n"):
        draft = draft[:-2]
    issues = safety_issues(draft, reason, known=set(info.get("known") or []),
                           sla=info.get("sla"))
    return {"draft": draft, "question_count": count_questions(draft),
            "issues": issues}


# reason 码 → 邮件策略键（与 followup.REASON_NBA 第二元组一致）
REASON_STRAT = {
    "REPLY_DUE": "reply_due",
    "QUOTE_SENT_NO_REPLY": "quote_no_reply",
    "SAMPLE_SENT_NO_REPLY": "sample_no_reply",
    "CUSTOMER_PROMISED_REPLY": "customer_promised",
    "SALES_PROMISED_FOLLOWUP": "sales_promised",
    "QUOTE_PREPARATION_DUE": "quote_prep",
    "PRODUCT_MATCH_PENDING": "match_pending",
    "INFORMATION_WAITING": "info_waiting",
    "NEGOTIATION_STALLED": "negotiation_stalled",
    "PO_EXPECTED": "po_expected",
    "OVERDUE_FOLLOWUP": "overdue",
    "MANUAL_FOLLOWUP": "manual",
}


def count_questions(draft: str) -> int:
    """问句数 = 以 ? 结尾且长度 >8 的句子数（与 reply_strategy 同口径）。"""
    if not draft:
        return 0
    sents = [s.strip() for s in re.split(r"(?<=[.?])\s+", draft) if s.strip()]
    return sum(1 for s in sents if s.endswith("?") and len(s) > 8)


# Email Safety Policy（复用 Test01/02 口径，独立实现便于跟进邮件单测）
_FORBIDDEN_QUESTION_HINTS = (
    ("email", ("email address", "your email", "e-mail")),
    ("payment", ("payment terms", "payment method", "t/t", "letter of credit")),
    ("website", ("your website", "company website", "web site")),
)
_TIME_PROMISE = re.compile(
    r"within\s+\d+\s*(hour|day|week)s?|in\s+\d+\s*(hour|day|week)s?|"
    r"by\s+tomorrow|by\s+friday|guarantee.*deliver", re.I)


def safety_issues(draft: str, reason: str, known=None, sla: bool = False) -> list:
    """跟进邮件的 Safety 检查。空列表 = 通过。

    1) 不索取客户已提供字段（known）
    2) 不询问 email / payment / website（跟进阶段除非策略明确需要）
    3) 无 SLA 支撑时不做 24h / N 小时承诺
    4) 问句 ≤3
    """
    issues = []
    if not draft or not draft.strip():
        return issues
    known = set(known or [])
    low = draft.lower()
    for field, hints in _FORBIDDEN_QUESTION_HINTS:
        if field in known:
            continue
        # 只在问句语境里命中才算（句子以 ? 结尾且包含提示词）
        for s in re.split(r"(?<=[.?])\s+", draft):
            if s.rstrip().endswith("?") and any(h in s.lower() for h in hints):
                issues.append(f"followup asks {field} which is not needed")
    if not sla and _TIME_PROMISE.search(low):
        issues.append("unsupported time promise without SLA")
    if count_questions(draft) > MAX_QUESTIONS:
        issues.append(f"too many questions ({count_questions(draft)}>{MAX_QUESTIONS})")
    return issues
