# -*- coding: utf-8 -*-
"""业务事实层（第九轮）：Value / Source / Certainty 统一三元组。

核心原则（spec 十四）：
  客户说的 ≠ 公司承诺
  客户目标价 ≠ 公司报价
  客户期望交期 ≠ 公司交期
  客户预计数量 ≠ 最终订单数量
  AI推断 ≠ 客户事实
  事实先于推断，证据先于结论。

本模块是纯函数层：只做「从当前询盘提取事实 + 打来源/可信度标签」，
不修改任何现有字段，不影响既有 Agent 架构。所有重要业务字段必须带
Source（来源）与 Certainty（可信度），禁止只保存裸值。

Source（来源五类）：
  Customer Fact  客户原话给出的事实
  Company Data   我方公司资料（config.json SELLER）
  Product Data   产品库数据
  AI Inference   AI 推断（只能做参考，不能当客户事实引用）
  Unknown        未知（客户没说、库里没有、系统没有）

Certainty（可信度七档）：
  Confirmed     双方已确认
  Explicit      客户明确说出的确定值
  Approximate   客户给的大概值（around / about / approximately）
  Preferred     客户的期望/偏好（prefer / hope / should / if possible）
  Unconfirmed   有数据支撑但尚未与客户确认（如产品库候选）
  Unknown       未提及
  Missing       缺失（需要向客户索取）
"""

import re

# ---------------- Source（来源） ----------------
SRC_CUSTOMER = "Customer Fact"
SRC_COMPANY = "Company Data"
SRC_PRODUCT = "Product Data"
SRC_AI = "AI Inference"
SRC_UNKNOWN = "Unknown"

# ---------------- Certainty（可信度） ----------------
CERT_CONFIRMED = "Confirmed"
CERT_EXPLICIT = "Explicit"
CERT_APPROXIMATE = "Approximate"
CERT_PREFERRED = "Preferred"
CERT_UNCONFIRMED = "Unconfirmed"
CERT_UNKNOWN = "Unknown"
CERT_MISSING = "Missing"

CERT_CN = {
    CERT_CONFIRMED: "已确认",
    CERT_EXPLICIT: "客户明确",
    CERT_APPROXIMATE: "约数",
    CERT_PREFERRED: "客户期望",
    CERT_UNCONFIRMED: "待客户确认",
    CERT_UNKNOWN: "未提供",
    CERT_MISSING: "缺失",
}

SRC_CN = {
    SRC_CUSTOMER: "客户事实",
    SRC_COMPANY: "公司资料",
    SRC_PRODUCT: "产品库数据",
    SRC_AI: "AI推断",
    SRC_UNKNOWN: "未知",
}

# ---------------- 报价准备状态（spec 六：统一四态） ----------------
QR_NOT_READY = "NOT_READY"
QR_PARTIAL = "PARTIALLY_READY"
QR_READY = "READY_FOR_QUOTE"
QR_QUOTED = "QUOTED"

QR_CN = {
    QR_NOT_READY: "🔴 暂不可报价",
    QR_PARTIAL: "🟡 可初步报价（参考价）",
    QR_READY: "✅ 可正式报价",
    QR_QUOTED: "🔵 已报价",
}

# insight 四级状态 → R9 统一四态
_INSIGHT_TO_QR = {
    "insufficient_info": QR_NOT_READY,
    "needs_confirmation": QR_NOT_READY,        # 旧版兼容
    "cannot_quote": QR_NOT_READY,              # 旧版兼容
    "preliminary_quote_ready": QR_PARTIAL,
    "ready_for_quotation": QR_READY,
    "quoted": QR_QUOTED,
}


def map_quote_status(insight_status: str) -> str:
    """把 insight 报价准备状态映射为统一四态（未知值一律按 NOT_READY 处理）。"""
    return _INSIGHT_TO_QR.get((insight_status or "").strip(), QR_NOT_READY)


# ---------------- 交期（客户期望 vs 公司承诺，spec 四） ----------------
# 识别客户对交期/货期的表达：
#   "Delivery should be around 30 days." / "We prefer delivery within 30 days."
#   "We hope to receive the goods within 30 days." / "delivery: 30 days"
# 注意必须先出现 delivery / lead time / shipment / receive the goods 类词，
# 再在其后 60 字符内出现数字+时间单位，避免把生产交期之外的时间当交期。
_DELIVERY_RE = re.compile(
    r"\b(?:delivery|deliver(?:y)?\s*time|lead\s*time|shipment|shipping\s*time|"
    r"receive\s+the\s+goods|goods\s+received?)\b"
    r"[^.\n]{0,60}?\b(\d+)\s*(days?|weeks?|months?)\b", re.I)
_DELIVERY_PREFER_RE = re.compile(
    r"\b(prefer\w*|hope|should|would\s+like|if\s+possible|around|about|"
    r"approximately|approx\.?|roughly|target|ideally)\b", re.I)
_DELIVERY_HARD_RE = re.compile(
    r"\b(must|require[sd]?|no\s+later\s+than|not\s+later\s+than|"
    r"at\s+the\s+latest|deadline)\b", re.I)

# ---------------- 数量可信度（spec 三） ----------------
_QTY_TOKEN_RE = re.compile(
    r"(\d[\d,]*)\s*(pcs|pieces|units|pairs|sets|cartons|ctns)\b", re.I)
_QTY_HEDGE_RE = re.compile(
    r"\b(about|around|approx\.?|approximately|more\s+or\s+less|or\s+so|"
    r"may|might|could|possibly|estimated?|roughly|up\s+to)\b", re.I)
_QTY_ASSERT_RE = re.compile(
    r"\b(we\s+need|we\s+will\s+order|we'?ll\s+order|will\s+order|we\s+confirm|"
    r"we\s+require|firm\s+order|definitely)\b", re.I)

# ---------------- 目标价 / 贸易术语（spec 二） ----------------
_PRICE_TOKEN_RE = re.compile(
    r"(?:usd|us\$|\$)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*/?\s*(?:per\s+)?(pc|pcs|piece|unit)\b",
    re.I)
_TRADE_TERM_RE = re.compile(
    r"\b(FOB|CIF|EXW|DDP|DDU|CNF|CFR|FCA|DAP)\b")
_PRICE_HEDGE_RE = re.compile(
    r"\b(around|about|approx\.?|approximately|roughly|or\s+so|up\s+to|below|under|max)\b",
    re.I)

# 客户目标价绝不能当公司报价的措辞（校验用，spec 八/十一）
_COMPANY_PRICE_WORDS_RE = re.compile(
    r"\b(our\s+(?:best\s+)?price|we\s+can\s+offer|we\s+offer|our\s+price\s+is|"
    r"unit\s+price|we\s+quote|quotation\s+price|our\s+quotation)\b", re.I)
# 未经公司数据不得做出的交期承诺（校验用，spec 八）
_COMMIT_LEADTIME_RE = re.compile(
    r"\b(we\s+can\s+deliver|we\s+will\s+deliver|we\s+can\s+ship|we\s+will\s+ship|"
    r"we\s+can\s+deliver\s+within|delivery\s+within|lead\s+time\s+(?:is|of|will\s+be)"
    r"\s*\d+|deliver(?:y|ed)?\s+within\s+\d+|shipment\s+within\s+\d+)\b", re.I)
_DAYS_NUM_RE = re.compile(r"(\d+)\s*(?:days?|weeks?|months?)", re.I)

# ---------------- 邮件事实守卫（Fact Guard，本轮新增） ----------------
# 每一条商业事实必须属于四源之一：CUSTOMER_FACT / COMPANY_FACT（含产品库）/
# SYSTEM_RULE / UNKNOWN。禁止把 UNKNOWN / AI_INFERENCE 写成 COMPANY_FACT。
# 以下承诺句式在公司知识库有明确依据前一律禁止自动生成。
_GUARD_FREE_RE = re.compile(
    r"\bfree\b[^.\n]{0,40}\b(sample[s]?|courier|freight|shipping|mold[s]?|"
    r"mould[s]?|tooling|tool\s+fee|design|artwork|certif\w*|logo)\b", re.I)
# 名词在前、免费表述在后的句式（The mold fee will be free / Certification free of charge）
_GUARD_FREE_REV_RE = re.compile(
    r"\b(sample[s]?|courier|freight|shipping|mold[s]?|mould[s]?|tooling|"
    r"tool\s+fee|design|artwork|certif\w*|logo)\b[^.\n]{0,35}"
    r"\b(free|free\s+of\s+charge|no\s+charge|at\s+no\s+cost|complimentary|waived)\b",
    re.I)
_GUARD_MOLD_RE = re.compile(
    r"\b(mold|mould|tooling)\s+(fee|charge|cost)\s+(is\s+)?(free|waived|on\s+us)\b", re.I)
_GUARD_STOCK_RE = re.compile(
    r"\b(in\s+stock|stock(s)?\s+(is\s+)?available|have\s+(the\s+)?(goods\s+)?"
    r"in\s+stock|ready\s+stock|we\s+have\s+(plenty\s+of\s+)?stock)\b", re.I)
_GUARD_CAPACITY_RE = re.compile(
    r"\b(production\s+)?capacity\b[^.\n]{0,40}\d", re.I)
_GUARD_CERT_HAVE_RE = re.compile(
    r"\bwe\s+(have|hold|got|are\s+)\s*(?:already\s+)?[\w\s]{0,30}"
    r"\b(certified|certificat\w*|CE\s+certif\w*|RoHS\s+certif\w*|FDA\s+certif\w*)\b", re.I)
_GUARD_PAYMENT_RE = re.compile(
    r"\b(payment\s+terms?\s+(are|is|will\s+be)|we\s+(only\s+)?accept|"
    r"\bT/T\b|\bL/C\b\s+at\s+sight|30%\s+deposit)\b", re.I)
_GUARD_WARRANTY_RE = re.compile(
    r"\b(\d+\s*(?:year|month|month)s?\s+warranty|warranty\s+(is|of|for|period)|"
    r"we\s+(offer|provide|give|grant)\b[^.\n]{0,30}warranty)\b", re.I)
_GUARD_SHIPPING_RE = re.compile(
    r"\b(shipping|transit)\s+(time|takes?)\b[^.\n]{0,25}\d", re.I)
_GUARD_GUARANTEE_RE = re.compile(
    r"\bwe\s+(guarantee|assure|ensure|promise)\b[^.\n]{0,50}"
    r"\b(price|quality|delivery|lead\s+time|shipment|MOQ)\b", re.I)
_GUARD_MOQ_RE = re.compile(
    r"\b(our\s+)?moq\b[^.\n]{0,25}\b(is|would\s+be|will\s+be|starts?\s+(at|from))\b"
    r"[^.\n]{0,15}\d", re.I)
_GUARD_FIXED_PRICE_RE = re.compile(
    r"\b(price\s+(is|will\s+be)\s+(fixed|locked|guaranteed)|fixed\s+price|"
    r"price\s+is\s+firm)\b", re.I)


def validate_email_fact_guard(draft: str, product=None) -> list:
    """Customer-facing Email Fact Guard（本轮核心）。

    邮件中每个商业承诺必须有 COMPANY_FACT（公司知识库/产品库/价格库/政策库）
    或 CUSTOMER_FACT / SYSTEM_RULE 依据；查不到依据一律按 UNKNOWN 处理并拦截。
    检查（缺公司数据支撑即违规）：
      免费样品/运费/模具/设计/认证 · 库存 · 产能 · 已具备认证 ·
      付款条件 · 保修 · 运输时效 · 担保句式 · MOQ 数值 · 固定价格
    返回问题列表（空 = 通过，可自动发送；非空 = ⚠️ HUMAN REVIEW REQUIRED）。
    """
    issues = []
    if not draft or not draft.strip():
        return issues
    prod = product or {}
    prod_cert = str(prod.get("certification") or prod.get("certs") or "")

    def _add(tag, m):
        issues.append(f"【Fact Guard · {tag}】邮件出现未经公司知识库支撑的承诺："
                      f"「{m.group(0).strip()}」→ 需人工复核或改为确认式表述")

    for m in _GUARD_FREE_RE.finditer(draft):
        _add("免费承诺", m)
    for m in _GUARD_FREE_REV_RE.finditer(draft):
        _add("免费承诺", m)
    for m in _GUARD_MOLD_RE.finditer(draft):
        _add("免费模具", m)
    for m in _GUARD_STOCK_RE.finditer(draft):
        _add("库存承诺", m)
    for m in _GUARD_CAPACITY_RE.finditer(draft):
        _add("产能承诺", m)
    for m in _GUARD_PAYMENT_RE.finditer(draft):
        _add("付款条件", m)
    for m in _GUARD_WARRANTY_RE.finditer(draft):
        _add("保修承诺", m)
    for m in _GUARD_SHIPPING_RE.finditer(draft):
        _add("运输时效", m)
    for m in _GUARD_GUARANTEE_RE.finditer(draft):
        _add("担保句式", m)
    for m in _GUARD_FIXED_PRICE_RE.finditer(draft):
        _add("固定价格", m)
    # MOQ：有匹配产品 MOQ 数据才允许报数值
    for m in _GUARD_MOQ_RE.finditer(draft):
        prod_moq = str(prod.get("moq") or "")
        seg = m.group(0)
        if not (prod_moq and any(n in prod_moq for n in re.findall(r"\d+", seg))):
            _add("MOQ 承诺", m)
    # 认证：产品库有该认证才允许"已具备"表述
    for m in _GUARD_CERT_HAVE_RE.finditer(draft):
        seg = m.group(0).upper()
        named = [k for k in ("CE", "ROHS", "FDA", "REACH", "UL", "SGS") if k in seg]
        if not (prod_cert and all(k in prod_cert.upper() for k in named)):
            _add("认证承诺", m)
    return list(dict.fromkeys(issues))


def _confirmed_product(matches: list):
    """已确认产品（与 main.analyze 同一口径）：规则关键词命中或 LLM 置信度 ≥0.5。"""
    top = matches[0] if matches else None
    if top and (top.get("hit_keywords")
                or (top.get("match_source") == "LLM"
                    and (top.get("match_score") or 0) >= 0.5)):
        return top
    return None


def _num(s):
    try:
        return int(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


# product_query 垃圾值过滤：extractor/insight 的产品短语在「产品未提及」的询盘里
# 可能抓到 "around 10,000 pcs" / "USD 2.80" 这类数量/价格碎片——不是产品，绝不能
# 当产品事实展示。含数字+单位、约数词+数字、货币符号的一律视为无效。
_PRODUCT_PHRASE_INVALID_RE = re.compile(
    r"(?:about|around|approx\.?|approximately)\s+[\d,]|\d[\d,]*\s*(?:pcs|pieces|"
    r"units|sets|k\b)|usd|us\$|\$|/\s*pc\b", re.I)


def _valid_product_phrase(phrase) -> bool:
    p = (phrase or "").strip()
    if not p or len(p) < 3:
        return False
    return not _PRODUCT_PHRASE_INVALID_RE.search(p)


def detect_delivery_preference(text: str):
    """从客户原话识别「客户期望交期」。

    返回 {"value": "30 days", "days": 30, "certainty": Preferred/Explicit,
          "evidence": 原话片段} 或 None（客户未提及交期）。
    规则（spec 四）：
      prefer / hope / should / would like / around / if possible → Preferred
      must / require / no later than → Explicit（硬性要求，仍是客户事实）
      平铺陈述（Delivery: 30 days）→ Explicit
    绝不生成公司承诺（"we can deliver within 30 days"）——那是公司数据的事。
    """
    low = (text or "")
    for m in _DELIVERY_RE.finditer(low):
        val, unit = m.group(1), m.group(2).lower()
        days = _num(val)
        if not days:
            continue
        seg = low[max(0, m.start() - 40): m.end() + 10]
        if _DELIVERY_HARD_RE.search(seg):
            cert = CERT_EXPLICIT
        elif _DELIVERY_PREFER_RE.search(seg):
            cert = CERT_PREFERRED
        else:
            cert = CERT_EXPLICIT
        return {"value": f"{val} {unit}", "days": days, "certainty": cert,
                "evidence": seg.strip()}
    return None


def _ctx_of(pattern, text: str, value):
    """找到与 value 对应的原文位置，返回 ±55 字符上下文（用于 hedge 判断）。"""
    for m in pattern.finditer(text or ""):
        if _num(m.group(1)) == value:
            return (text or "")[max(0, m.start() - 55): m.end() + 55]
    return text or ""


def quantity_certainty(text: str, info: dict):
    """数量可信度（spec 三）：

    around / about / approximately / may → Approximate（约 10,000 pcs）
    we need / we will order / firm order → Confirmed
    其他明确陈述 → Explicit
    """
    qty = info.get("quantity")
    if not qty:
        return CERT_MISSING, None
    ctx = _ctx_of(_QTY_TOKEN_RE, text or "", int(qty))
    if _QTY_HEDGE_RE.search(ctx):
        return CERT_APPROXIMATE, ctx
    if _QTY_ASSERT_RE.search(ctx):
        return CERT_CONFIRMED, ctx
    return CERT_EXPLICIT, ctx


def price_certainty(text: str, info: dict):
    """目标价可信度：客户原话带约数词 → Approximate，否则 Explicit。

    上下文截断到价格前的最近句子边界（句号/换行/分号），
    防止上一句数量的 around/about 泄漏进价格判断窗口。
    """
    price = info.get("target_price")
    if not price:
        return CERT_MISSING, None
    low = text or ""
    ctx = low
    for m in re.finditer(r"(?:usd|us\$|\$)\s*(\d+(?:\.\d+)?)", low, re.I):
        if abs(float(m.group(1)) - float(price)) < 1e-6:
            seg_start = max(0, m.start() - 55)
            # 截断到价格前最近的句子/换行/逗号边界（只看本句话）
            breaks = [b.end() for b in re.finditer(r"[.!?\n;:]", low[seg_start: m.start()])]
            if breaks:
                seg_start = seg_start + breaks[-1]
            ctx = low[seg_start: m.end() + 40]
            break
    if _PRICE_HEDGE_RE.search(ctx):
        return CERT_APPROXIMATE, ctx
    return CERT_EXPLICIT, ctx


def trade_term_of(text: str):
    """贸易术语（FOB/CIF/EXW…）：客户原话出现即 Customer Fact / Explicit。"""
    m = _TRADE_TERM_RE.search(text or "")
    return m.group(1).upper() if m else None


def build_fact_layer(text: str, info: dict, matches: list,
                     insight: dict = None) -> list:
    """构建统一事实层：每个关键业务字段一条
    {field, label, value, display, source, certainty, note}。

    UI 与邮件生成共用此结构；旧记录可在展示时用当前函数实时重建。
    """
    info = info or {}
    matches = matches or []
    insight = insight or {}
    text = text or ""
    facts = []

    # 1) 数量（客户事实；约数绝不写成确认量）
    q_cert, _ = quantity_certainty(text, info)
    qty = info.get("quantity")
    if qty:
        unit = info.get("quantity_unit") or "pcs"
        prefix = "约 " if q_cert == CERT_APPROXIMATE else ""
        facts.append({
            "field": "quantity", "label": "采购数量",
            "value": f"{qty:,} {unit}",
            "display": f"{prefix}{qty:,} {unit}",
            "source": SRC_CUSTOMER, "certainty": q_cert,
            "note": "客户预计数量 ≠ 最终订单数量" if q_cert != CERT_CONFIRMED else "",
        })
    else:
        facts.append({"field": "quantity", "label": "采购数量", "value": None,
                      "display": "未提供", "source": SRC_UNKNOWN,
                      "certainty": CERT_MISSING, "note": "需向客户确认数量"})

    # 2) 客户目标价（永远 ≠ 公司报价，spec 二）
    price = info.get("target_price")
    if price:
        p_cert, _ = price_certainty(text, info)
        cur = info.get("target_price_currency") or "USD"
        term = trade_term_of(text)
        prefix = "约 " if p_cert == CERT_APPROXIMATE else ""
        disp = f"{prefix}{cur} {price} / pc" + (f" {term}" if term else "")
        facts.append({
            "field": "customer_target_price", "label": "客户目标价",
            "value": f"{cur} {price}" + (f" {term}" if term else ""),
            "display": disp,
            "source": SRC_CUSTOMER, "certainty": p_cert,
            "note": "客户说的目标价，不是公司报价；未经公司价格数据确认不得对外承诺",
        })
    else:
        facts.append({"field": "customer_target_price", "label": "客户目标价",
                      "value": None, "display": "未提及",
                      "source": SRC_UNKNOWN, "certainty": CERT_UNKNOWN,
                      "note": ""})
    # 公司报价（永远单独一行，未提供就写未提供）
    _top_prod = _confirmed_product(matches)
    if _top_prod and _top_prod.get("price_range"):
        lo, hi = _top_prod["price_range"]
        facts.append({
            "field": "company_quote", "label": "公司报价",
            "value": f"USD {float(lo):.2f}-{float(hi):.2f}（产品库参考价）",
            "display": f"USD {float(lo):.2f}-{float(hi):.2f}（产品库参考价，非正式报价）",
            "source": SRC_PRODUCT, "certainty": CERT_UNCONFIRMED,
            "note": "参考区间来自产品库，正式报价需业务员确认",
        })
    else:
        facts.append({"field": "company_quote", "label": "公司报价",
                      "value": None, "display": "未提供",
                      "source": SRC_UNKNOWN, "certainty": CERT_UNKNOWN,
                      "note": "没有公司价格数据前，不得生成任何报价"})

    # 3) 贸易术语
    term = trade_term_of(text)
    facts.append({
        "field": "trade_term",
        "label": "贸易术语",
        "value": term,
        "display": term or "未提及",
        "source": SRC_CUSTOMER if term else SRC_UNKNOWN,
        "certainty": CERT_EXPLICIT if term else CERT_UNKNOWN,
        "note": "",
    })

    # 4) 客户期望交期（客户要求 ≠ 公司交期，spec 四）
    deliv = detect_delivery_preference(text)
    if deliv:
        facts.append({
            "field": "delivery", "label": "客户期望交期",
            "value": deliv["value"], "display": deliv["value"],
            "source": SRC_CUSTOMER, "certainty": deliv["certainty"],
            "note": "客户要求 ≠ 公司承诺；公司交期需按产品数据确认后才能给出",
        })
    else:
        facts.append({"field": "delivery", "label": "客户期望交期",
                      "value": None, "display": "未提及",
                      "source": SRC_UNKNOWN, "certainty": CERT_UNKNOWN,
                      "note": ""})
    # 公司交期（有产品库数据才有）
    if _top_prod and _top_prod.get("lead_time"):
        facts.append({
            "field": "company_lead_time", "label": "公司常规交期",
            "value": str(_top_prod["lead_time"]),
            "display": f"{_top_prod['lead_time']}（产品库常规交期）",
            "source": SRC_PRODUCT, "certainty": CERT_EXPLICIT,
            "note": "产品库数据；实际交期以业务员确认为准",
        })
    else:
        facts.append({"field": "company_lead_time", "label": "公司交期",
                      "value": None, "display": "未提供",
                      "source": SRC_UNKNOWN, "certainty": CERT_UNKNOWN,
                      "note": "无产品数据支撑前，邮件不得承诺任何交期"})

    # 5) 产品（产品未知 ≠ AI 猜产品，spec 五）
    hier = (insight.get("product_hierarchy") or {}).get("levels") or []
    cat = next((l for l in hier if l.get("level") == "category"), {})
    if _top_prod:
        facts.append({
            "field": "product", "label": "产品匹配",
            "value": _top_prod["name"],
            "display": f"{_top_prod['name']}（产品库匹配，待客户确认）",
            "source": SRC_PRODUCT, "certainty": CERT_UNCONFIRMED,
            "note": "库内匹配候选，客户选定后才能进入报价",
        })
    elif (cat.get("state") == "confirmed" and cat.get("value")
          and _valid_product_phrase(cat.get("value"))):
        facts.append({
            "field": "product", "label": "产品类别",
            "value": str(cat["value"]),
            "display": f"{cat['value']}（客户原话，产品库暂无匹配 NO_MATCH）",
            "source": SRC_CUSTOMER, "certainty": CERT_EXPLICIT,
            "note": "品类已知但库内无匹配：请客户提供 photo / link / reference model",
        })
    elif _valid_product_phrase(info.get("product_query")):
        facts.append({
            "field": "product", "label": "产品描述",
            "value": info["product_query"],
            "display": f"{info['product_query']}（客户原话，库内无确认匹配）",
            "source": SRC_CUSTOMER, "certainty": CERT_EXPLICIT,
            "note": "NO_MATCH：请客户提供 photo / link / reference model",
        })
    else:
        facts.append({
            "field": "product", "label": "产品",
            "value": None, "display": "未知（客户未提及）",
            "source": SRC_UNKNOWN, "certainty": CERT_MISSING,
            "note": "先问 Product Category / Product Type，不机械追问型号",
        })

    # 6) 定制 / 样品（客户事实，原话提及即 Explicit）
    low = text.lower()
    if re.search(r"\b(custom\w*|logo|oem|odm|private\s+label|packag\w*)\b", low):
        facts.append({"field": "customization", "label": "定制需求",
                      "value": "需要（客户原话提及）", "display": "需要",
                      "source": SRC_CUSTOMER, "certainty": CERT_EXPLICIT,
                      "note": ""})
    if re.search(r"\bsample[s]?\b", low):
        facts.append({"field": "sample", "label": "样品",
                      "value": "客户请求样品", "display": "已请求",
                      "source": SRC_CUSTOMER, "certainty": CERT_EXPLICIT,
                      "note": "样品是否可送、费用由公司数据决定，邮件不得自动承诺"})
    return facts


def quote_readiness_of(insight: dict) -> dict:
    """从 insight 报价准备度得到统一四态 + 中文展示（spec 六）。"""
    qr = (insight or {}).get("quotation_readiness") or {}
    raw = qr.get("quotation_readiness_status", "")
    try:
        from agent.insight import normalize_status
        raw = normalize_status(raw)
    except Exception:
        pass
    status = map_quote_status(raw)
    return {"status": status, "status_cn": QR_CN[status],
            "score": qr.get("quotation_readiness_score"),
            "raw": raw}


def build_reply_context(text: str, info: dict, matches: list,
                        insight: dict = None, product=None) -> dict:
    """构建 Reply Context（spec 七）：邮件生成的唯一事实入口。

    必须包含：confirmed_facts / approximate_facts / preferred_facts /
    customer_target_price / missing_information / unknown_information /
    product_match / quote_readiness / reply_strategy / allowed_facts。
    只能绑定当前询盘（inquiry_id 由调用方写入），禁止引用任何历史询盘。
    """
    info = info or {}
    insight = insight or {}
    facts = build_fact_layer(text, info, matches, insight)

    confirmed = [f for f in facts if f["certainty"] in
                 (CERT_CONFIRMED, CERT_EXPLICIT) and f["value"]]
    approximate = [f for f in facts if f["certainty"] == CERT_APPROXIMATE]
    preferred = [f for f in facts if f["certainty"] == CERT_PREFERRED]
    unknown = [f for f in facts if f["source"] == SRC_UNKNOWN and f["value"] is None]
    missing = [f for f in facts if f["certainty"] == CERT_MISSING]

    # 客户目标价：单独成项，绝不允许与公司报价混同
    tp = next((f for f in facts if f["field"] == "customer_target_price"
               and f["value"]), None)

    # 产品匹配：NO_MATCH = 没有确认匹配（含"未提及产品"与"库内无匹配"两种）
    try:
        from agent.reply_strategy import (detect_match_state, MATCH_MATCHED,
                                          MATCH_PARTIAL)
        ms = detect_match_state(text or "", info, product, matches)
        state = ms.get("state", "")
    except Exception:
        state = ""
    if product is not None and state == MATCH_MATCHED:
        product_match, detail = "MATCHED", "MATCHED"
    elif state == MATCH_PARTIAL:
        product_match, detail = "NO_MATCH", "PARTIAL_MATCH（候选待客户选择）"
    else:
        product_match, detail = "NO_MATCH", state or "INSUFFICIENT_INFORMATION"

    qr = quote_readiness_of(insight)

    # allowed_facts：邮件允许引用的事实（字符串清单，直接进 Prompt）
    allowed = []
    for f in confirmed:
        allowed.append(f"{f['label']}: {f['value']} ({f['source']}/{f['certainty']})")
    for f in approximate:
        allowed.append(f"{f['label']}: 约 {f['value']} ({f['source']}/Approximate"
                       f" - 用 approximately/around 表述，不得写成确认订单)")
    for f in preferred:
        allowed.append(f"{f['label']}: {f['value']} ({f['source']}/Preferred"
                       f" - 只能复述为客户期望，不得写成公司承诺)")
    if tp:
        allowed.append(f"Customer Target Price: {tp['value']} (Customer Fact - "
                       "绝不作为我方报价；不得出现 our price = 该数字)")
    allowed.append("Company quotation: NOT AVAILABLE - do not state any company price"
                   if not (product and product.get("price_range"))
                   else f"Reference price (Product Data): USD "
                        f"{product['price_range'][0]:.2f}-{product['price_range'][1]:.2f}")

    return {
        "confirmed_facts": [f"{f['label']}: {f['value']}" for f in confirmed],
        "approximate_facts": [f"{f['label']}: 约 {f['value']}" for f in approximate],
        "preferred_facts": [f"{f['label']}: {f['value']}" for f in preferred],
        "customer_target_price": ({"value": tp["value"],
                                   "source": tp["source"],
                                   "certainty": tp["certainty"]} if tp else None),
        "missing_information": [f["label"] for f in missing] or [],
        "unknown_information": [f["label"] for f in unknown] or [],
        "product_match": product_match,
        "product_match_detail": detail,
        "quote_readiness": qr["status"],
        "quote_readiness_cn": qr["status_cn"],
        "reply_strategy": ("第一轮邮件：回应客户明确提出的需求 → 确认最核心缺失信息"
                          "（P0，最多 1-3 问）→ 推进下一步；不做未经确认的商业承诺"),
        "allowed_facts": allowed,
        "facts": facts,
    }


def validate_reply_commitments(draft: str, text: str, info: dict,
                               product, matches: list = None) -> list:
    """Reply Validation（spec 八/十一）：邮件承诺必须与事实层一致。

    检查：
      1. 未经公司/产品数据支撑的交期承诺（we can deliver within X days 等）
      2. 客户目标价被当成公司报价（our price / we can offer + 目标价数字，
         且当前没有匹配产品价格数据）
      3. 未经数据支撑的 MOQ / 价格承诺（沿用 validate_reply_draft 的口径之外，
         这里专查"承诺句式"）
    返回问题列表（空 = 通过）。
    """
    issues = []
    if not draft or not draft.strip():
        return issues
    info = info or {}

    # 1) 交期承诺：邮件出现承诺句式 + 天/周数字，且该数字不来自匹配产品 lead_time
    product_lead = str((product or {}).get("lead_time") or "")
    for m in _COMMIT_LEADTIME_RE.finditer(draft):
        seg = draft[max(0, m.start() - 30): m.end() + 40]
        nums = _DAYS_NUM_RE.findall(seg)
        ok = False
        for n, _u in nums:
            if n and n in product_lead:
                ok = True
        # "delivery within 30 days" 若只是复述客户期望（前面有 noted/preferred
        # your requested 等词）不算承诺
        prefix = draft[max(0, m.start() - 60): m.start()].lower()
        if re.search(r"\b(noted|preferred|requested|your)\b", prefix) and \
                not re.search(r"\bwe\s+(can|will)\b", m.group(0).lower()):
            ok = True
        if not ok:
            issues.append(f"邮件出现未经产品/公司数据支撑的交期承诺：「{m.group(0)}」"
                          f"（客户期望交期 ≠ 公司承诺）")
            break

    # 2) 客户目标价 ≠ 公司报价
    tp = info.get("target_price")
    if tp and not (product and product.get("price_range")):
        for m in _COMPANY_PRICE_WORDS_RE.finditer(draft):
            seg = draft[m.end(): m.end() + 40]
            nums = re.findall(r"(\d+(?:\.\d+)?)", seg)
            if any(abs(float(n) - float(tp)) < 1e-6 for n in nums):
                issues.append("邮件把客户目标价当作我方报价（客户目标价 ≠ 公司报价）")
                break

    # 3) 无匹配产品时禁止 "we can offer ... price" 承诺句式（价格数字由 2 捕获，
    #    这里只拦句式本身带明确承诺的）
    if not (product and product.get("price_range")):
        if re.search(r"\bwe\s+(can|will)\s+(?:offer|quote)\b[^.\n]{0,40}"
                     r"\b(price|quotation)\b", draft, re.I):
            issues.append("无匹配产品价格数据，邮件出现 we can offer/quote price 承诺句式")
    return issues
