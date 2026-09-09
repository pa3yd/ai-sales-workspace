# -*- coding: utf-8 -*-
"""
模块⑤：业务洞察层 (InsightBuilder) —— Agent 业务逻辑优化新增

解决的问题：旧流水线只回答"缺什么字段"，不回答业务员真正关心的问题：
  - 现在能不能报价？（报价准备度 quotation_readiness）
  - 到底卡在哪里？（阻塞原因 blocking_reasons，说清楚为什么、影响什么）
  - 客户给了候选值但没定怎么办？（partially_confirmed，不算 missing）
  - 这单有什么事实依据的风险？（risks，禁止编造）
  - 我下一步该干什么？（next_actions，最多 5 条按业务优先级排序）

设计原则：
  - 纯离线规则，零成本零延迟，断网可用（LLM 模式同样适用）
  - 只基于询盘原文 + 提取结果 + 产品库真实数据判断，禁止编造
  - 输出全部为**新增字段**，不改动旧 Schema，向后兼容
"""

import re

# ---------- 与 workbench/gapcheck 同源的正则（保持判断口径一致） ----------
_UNCERTAIN_RE = re.compile(
    r"\b(not\s+sure|not\s+decided|unsure|undecided|haven'?t\s+decided|"
    r"didn'?t\s+decide|not\s+certain|still\s+deciding|tbd|tbc|maybe|yet)\b", re.I)
_OR_RE = re.compile(
    r"\b([\w\.\-/\s]{1,40}?)\s+or\s+([\w\.\-/\s]{1,40}?)(?=[\,\.\;\!\?]|\Z)", re.I)
_VAGUE_QTY_RE = re.compile(
    r"\b(about|around|approx\.?|approximately|or\s+so|more\s+or\s+less|\d+\s*-\s*\d+)\b", re.I)
# 第五轮补丁 02：扩展 hedge 词 —— 客户用 may/might/could/potentially/initial/trial 等
# 模糊表达数量时，不应被视为"明确数量"。
_APPROX_HEDGE_RE = re.compile(
    r"\b(may|might|could|possibly|potentially|likely|probably|estimated?|"
    r"initial\s+quantity|trial\s+order|potential\s+(?:annual|monthly)?\s*volume|"
    r"subject\s+to|expected\s+quantity|target\s+quantity|planned)\b", re.I)
_DEADLINE_RE = re.compile(
    r"\b(?:within|in|by)\s+(\d+\s+(?:days?|weeks?|months?))\b"
    r"|\b(\d+)\s*[-\s]?(days?|weeks?|months?)\b", re.I)
_QUANTITY_RE = re.compile(r"(\d[\d,]*)\s*(k|pcs|pieces|units|pairs|sets|cartons|ctns)?\b", re.I)

# 认证：兴趣（问我们有什么） vs 要求（客户明确要求）
_CERT_INTEREST_RE = re.compile(
    r"\b(what|which)\s+certificat\w*|"
    r"\bdo\s+you\s+have\s+(any\s+)?certificat\w*|"
    r"\bany\s+certificat\w*|"
    r"\bcertificat\w*\s+(do\s+you\s+have|available)\b", re.I)
_CERT_REQUIRE_RE = re.compile(
    r"\b(require[sd]?|must\s+have|must\s+be|need[sed]?)\b[^.!?]{0,40}"
    r"\b(certificat\w*|CE\b|EN\s?71|REACH|FDA|RoHS|BSCI|SGS)\b", re.I)

# 紧急表达（紧急度 ≠ 采购意向，分开判断）
_URGENT_HIGH_RE = re.compile(r"\b(urgent|asap|immediate|immediately|rush\s+order)\b", re.I)
_URGENT_MED_RE = re.compile(
    r"\b(as\s+soon\s+as\s+possible|fast\s+delivery|quick\s+delivery|time\s+is\s+tight)\b", re.I)

# 四级报价状态（第三轮优化一：评分与状态绝对一致，固定枚举）
STATUS_INSUFFICIENT = "insufficient_info"     # 信息不足：无法进行有效报价
STATUS_PRELIM = "preliminary_quote_ready"     # 可初步报价：可给参考价/区间，不能正式报价
STATUS_READY = "ready_for_quotation"          # 可正式报价：产品/规格/数量及关键商务条件已确认
STATUS_QUOTED = "quoted"                      # 已报价：正式报价已生成并发送（由人工标记，系统不自动判定）

STATUS_CN = {
    STATUS_INSUFFICIENT: "🔴 信息不足，无法有效报价",
    STATUS_PRELIM: "🟡 可初步报价",
    STATUS_READY: "✅ 可正式报价",
    STATUS_QUOTED: "🔵 已报价",
    # ---- 旧版本状态值（历史库记录兼容显示） ----
    "needs_confirmation": "🔴 信息不足，无法有效报价（旧版：需先确认关键信息）",
    "cannot_quote": "🔴 信息不足，无法有效报价（旧版：暂无法报价）",
}

STATUS_DESC = {
    STATUS_INSUFFICIENT: "产品方向或数量等 A 类必须确认项缺失，当前无法形成有效报价，先追问再谈价格。",
    STATUS_PRELIM: "可提供参考价格 / 价格区间 / 预算报价，帮助客户推进；但尚有 A 类细节或 B 类条件未定，不能正式报价。",
    STATUS_READY: "产品、规格、数量及关键商务条件已确认，可以进入正式报价。",
    STATUS_QUOTED: "正式报价已发送，转入跟进阶段。",
    # 旧版兼容
    "needs_confirmation": "产品方向或数量等 A 类必须确认项缺失，当前无法形成有效报价，先追问再谈价格。",
    "cannot_quote": "产品方向或数量等 A 类必须确认项缺失，当前无法形成有效报价，先追问再谈价格。",
}

# 旧状态值 → 新四级（历史数据兼容映射）
LEGACY_STATUS_MAP = {
    "needs_confirmation": STATUS_INSUFFICIENT,
    "cannot_quote": STATUS_INSUFFICIENT,
    "preliminary_quote_ready": STATUS_PRELIM,
    "ready_for_quotation": STATUS_READY,
}

# ---------- 第五轮第二次补丁 03：需求语义五态 ----------
# 严格区分三层语义：
#   Customer Requirement   客户已提出（原话里有，就是真实需求，绝不能标 Unknown）
#   Confirmed Requirement  已确认（客户给出的确定值：FOB、Germany……）
#   Final Commercial Confirmation  最终商业确认（最终选型落定，才能正式报价）
# 展示层统一用五态标签：
REQ_CONFIRMED = "confirmed"                  # ✅ 客户明确提出且值确定
REQ_PARTIAL = "partially_confirmed"          # 🟡 客户给出候选值 / 大概值
REQ_PENDING = "pending_confirmation"         # ⏳ 主题已提出，值待双方落定（如最终选型）
REQ_UNKNOWN = "unknown"                      # ⚪ 全文未提及
REQ_NA = "not_applicable"                    # — 不适用（前置层级未定，该层级无从谈起）

REQ_STATE_CN = {
    REQ_CONFIRMED: "✔ Confirmed · 已确认",
    REQ_PARTIAL: "◐ Partially Confirmed · 部分确认（候选/大概值）",
    REQ_PENDING: "⏳ Pending Confirmation · 待确认（客户已提出，尚未落定）",
    REQ_UNKNOWN: "⚪ Unknown · 未提供",
    REQ_NA: "— N/A · 不适用（前置层级未定）",
}


def normalize_status(status: str) -> str:
    """把任何历史状态值归一到新四级枚举上。"""
    if not status:
        return STATUS_INSUFFICIENT
    return LEGACY_STATUS_MAP.get(status, status)


# 报价准备度分数区间（第三轮一致性硬约束：分数必须落在状态对应区间内）
SCORE_BANDS = {
    STATUS_INSUFFICIENT: (0, 55),
    STATUS_PRELIM: (56, 88),
    STATUS_READY: (89, 100),
}


def _cap(x):
    return max(0, min(100, int(x)))


def _norm(text):
    return (text or "").strip()


def find_open_options(text: str) -> list:
    """找出客户自己给出但还没定的选项。

    例："Maybe 500ml or 750ml, not sure yet."  →  ["500ml or 750ml"]
    """
    out = []
    _tail = re.compile(r"(?:\s+(?:not\s+sure|not\s+yet|not\s+decided|not\s+certain|"
                       r"yet|maybe|tbd|tbc|i\s+guess|not\s+sure\s+yet))*$", re.I)
    for sent in re.split(r"[.!?\n]", text or ""):
        if " or " not in sent.lower():
            continue
        if not _UNCERTAIN_RE.search(sent):
            continue
        m = _OR_RE.search(sent)
        if not m:
            continue
        left_words = m.group(1).strip().split()[-2:]
        while left_words and left_words[0].lower() in (
                "for", "in", "of", "to", "on", "at", "a", "an", "the",
                "with", "need", "needs", "want", "like", "is", "are", "maybe"):
            left_words = left_words[1:]
        left = " ".join(left_words)
        right = _tail.sub("", m.group(2).strip())
        frag = f"{left} or {right}"
        frag = re.sub(r"\s+", " ", frag).strip(" ,.-")
        if frag and frag not in out:
            out.append(frag)
    return out


def _option_field(frag: str) -> str:
    """根据选项内容判断客户在犹豫哪个字段。"""
    if re.search(r"\d+\s*(ml|l|cl|oz|gallon)\b", frag, re.I):
        return "capacity"
    if re.search(r"\b(black|white|blue|red|green|yellow|pink|purple|grey|gray|orange|navy)\b", frag, re.I):
        return "color"
    if re.search(r"\d+\s*(pcs|pieces|units|pairs|sets)\b", frag, re.I):
        return "quantity"
    if re.search(r"\b(x?s|m|l|xl|xxl)\b", frag, re.I):
        return "size"
    return "specification"


def _solution_question(field: str, values: list) -> str:
    """方案式追问（第三轮优化五）：不是单纯索取信息，而是帮客户做决定。"""
    joined = " and ".join(values)
    if field == "quantity":
        return (f"We can quote both {joined} - would you like a comparison, "
                f"or do you already have a target quantity?")
    if field == "capacity":
        return (f"We can offer both {joined} options. Would you like us to quote "
                f"both sizes for comparison, or do you already have a preferred capacity?")
    return (f"We can cover both {joined} - shall we quote both options side by side "
            f"so you can compare, or do you already lean toward one?")


def _sales_advice(field: str, values: list) -> str:
    """给销售的建议（第三轮优化三）：partially_confirmed ≠ missing 的业务价值。"""
    joined = " / ".join(values)
    if field == "capacity":
        return (f"客户在 {joined} 之间犹豫：可同时提供两个规格的价格、MOQ 和基本参数，"
                "帮客户做决定，而不是干等客户回复。")
    if field == "quantity":
        return (f"客户数量在 {joined} 之间：可按两档数量分别报单价（量越大价越优），"
                "引导客户往上靠。")
    return f"客户在 {joined} 之间未定：可同时给出两种方案的报价与参数供对比。"


def detect_partially_confirmed(text: str, info: dict) -> list:
    """识别"客户给了候选值但没最终确认"的字段。

    关键业务规则：partially_confirmed ≠ missing / unknown。
    客户说 "Maybe 500ml or 750ml, not sure yet" 不是没给信息，
    而是给了两个候选值。

    第三轮优化三、五：
      - 每个 item 增加 customer_options（客户当前意向列表）、
        status_text（尚未最终确认）、sales_advice（给销售的方案式建议）
      - question 升级为"方案式追问"：帮客户对比两个选项做决定，
        而不是干巴巴地问 "Which one do you prefer?"
    """
    low = (text or "").lower()
    out = []

    for frag in find_open_options(text):
        field = _option_field(frag)
        values = [v.strip(" ,.") for v in frag.split(" or ") if v.strip(" ,.")]
        out.append({
            "field": field,
            "status": "partially_confirmed",
            "status_text": "尚未最终确认",
            "values": values,
            "customer_options": values,          # 客户当前意向（第三轮优化三）
            "quote": frag,
            "question": _solution_question(field, values),
            "sales_advice": _sales_advice(field, values),
        })

    # 数量含糊："about 3000 pcs" —— 有数但不精确
    # 第五轮补丁 02：扩展 hedge 词（may/might/could/potentially/trial/initial quantity 等），
    # 把所有"客户表达的不是确定采购意向"的数量都标 partially_confirmed，
    # 不应被 Quote Readiness / extractor 当成 confirmed。
    if not any(p["field"] == "quantity" for p in out):
        if ((_VAGUE_QTY_RE.search(low) or _APPROX_HEDGE_RE.search(low))
                and _QUANTITY_RE.search(low)):
            m = _QUANTITY_RE.search(low)
            vals = [m.group(0).strip()]
            # 区分"模糊量"和"多档候选量"（第五轮补丁 02）
            quote_text = m.group(0).strip()
            if _APPROX_HEDGE_RE.search(low):
                quote_text = f"approximately {quote_text} (hedge word detected: may/might/could/potential/trial/initial)"
            out.append({"field": "quantity", "status": "partially_confirmed",
                        "status_text": "尚未最终确认",
                        "values": vals, "customer_options": vals,
                        "quote": quote_text,
                        "question": "Could you confirm the quantity - is that per order?",
                        "sales_advice": "数量是大概值/可能值：初步报价可先按该量给，"
                                        "正式报价前确认准确数量。勿在首轮草稿中标记为 confirmed。"})

    # 只说了 European market，没给具体国家 → 目的地部分明确
    if not (info or {}).get("country") and re.search(r"\beurope\w*", low, re.I):
        out.append({"field": "destination", "status": "partially_confirmed",
                    "status_text": "尚未最终确认",
                    "values": ["Europe"], "customer_options": ["Europe"],
                    "question": "Which country in Europe should we deliver to?",
                    "quote": "European market",
                    "sales_advice": "先给出厂价 / FOB 价区间不受影响，"
                                    "正式报 CIF 前确认具体目的港 / 国家。"})
    return out


# ---------- 客户原文产品信号（第五轮第二次补丁 03，与 reply_strategy 同源） ----------
_OBJECT_RE_P = re.compile(
    r"\b(?:interested\s+in|looking\s+for|looking\s+at|need|needs|want|require|"
    r"would\s+like(?:\s+to\s+(?:buy|order|purchase))?|planning(?:\s+to\s+buy)?)\s+"
    r"([a-z0-9][a-z0-9\s\-/&]{2,60})", re.I)
_GENERIC_OBJECT_RE_P = re.compile(
    r"^(?:your|the|some|any|these|those|all|more|several|a|an|our)?\s*"
    r"(?:products?|items?|goods|things|stuff|models?|catalog|catalogue|brochure|range|"
    r"specifications?|spec\s?sheets?|details|information|quotation|quote|price|prices|"
    r"pricing|cost|offer|samples?|list)\b", re.I)
_MATERIAL_RE_P = re.compile(
    r"\b(neoprene|silicone|latex|pvc|tpu|eva|stainless\s+steel|glass|aluminum|"
    r"microfiber|nylon|polyester)\b", re.I)
_MODEL_VALUE_RE = re.compile(
    r"\b(?:model|ref\.?|reference)\s*[:#\s]*([A-Za-z0-9][\w\-]{1,20})", re.I)
_INCOTERM_RE_P = re.compile(r"\b(fob|cif|exw|ddp|ddu|dap|fca|cfr|cnf)\b", re.I)
_PAYMENT_RE_P = re.compile(
    r"\b(t\s?/\s?t|l\s?/\s?c|paypal|western\s?union|deposit)\b", re.I)


def customer_product_phrase(text: str) -> str:
    """客户原文里的产品品类描述（只用客户原话，绝不猜测、绝不补全）。

    例："We are interested in Stainless Steel Water Bottle, 500ml or 750ml"
        → "Stainless Steel Water Bottle"
    与 reply_strategy.customer_product_phrase 同源同口径。
    """
    for m in _OBJECT_RE_P.finditer(text or ""):
        raw = re.split(r"[,.;:\n]|\b(?:for|with|and|or|in|at|to|of|from)\b",
                       m.group(1), maxsplit=1)[0]
        raw = re.sub(r"\s+", " ", raw).strip(" ,.-")
        # 去掉引导冠词/物主代词："your Stainless Steel Water Bottle"
        #   → "Stainless Steel Water Bottle"（只留客户所述品类本身）
        raw = re.sub(r"^(?:your|our|the|a|an|some|own)\s+", "", raw,
                     flags=re.I).strip(" ,.-")
        if not raw or _GENERIC_OBJECT_RE_P.match(raw):
            continue
        if len(raw.split()) > 6:
            raw = " ".join(raw.split()[:6])
        return raw
    return ""


def build_product_hierarchy(text: str, info: dict, matches: list,
                            partially: list) -> dict:
    """产品规格五层层级：Category → Type → Model → Spec → Final Selection。

    语义口径（第五轮第二次补丁 03）：
      - 客户原话明确品类（如 "Stainless Steel Water Bottle"）→ category = confirmed
        （客户已提出且确定 —— 绝不能因"库内无匹配"而降级为 Unknown）
      - 型号只有客户明确给出（model X / ref XYZ）才 confirmed；品类未知时 model = N/A
        （层级前置未定，谈型号没有意义）
      - 最终选型：库内匹配且规格无开放选项 → confirmed（最终商业确认）；
        品类已明确但选型未落定 → pending_confirmation（待确认，不阻塞初步报价）；
        品类未知 → N/A
    """
    low = (text or "").lower()
    info = info or {}
    partially = partially or []
    has_match = any((m.get("match_score") or 0) >= 0.3 for m in (matches or []))
    phrase = customer_product_phrase(text) or _norm(info.get("product_query"))
    open_spec = any(p["field"] in ("capacity", "size", "specification")
                    for p in partially)

    category_val = (matches[0]["name"] if has_match else "") or phrase
    category_known = bool(category_val)
    material_m = _MATERIAL_RE_P.search(low)
    model_m = _MODEL_VALUE_RE.search(low)
    spec_vals = []
    for p in partially:
        if p["field"] in ("capacity", "size", "specification"):
            spec_vals += p.get("values", [])
    spec_tokens = re.findall(r"\b\d+\s*(?:ml|l|oz|cl|cm|mm|inch|g|kg)\b", low)

    category_state = REQ_CONFIRMED if category_known else REQ_UNKNOWN
    type_state = (REQ_NA if not category_known
                  else (REQ_CONFIRMED if (material_m or phrase) else REQ_UNKNOWN))
    model_state = (REQ_CONFIRMED if model_m
                   else (REQ_NA if not category_known else REQ_UNKNOWN))
    if open_spec:
        spec_state = REQ_PARTIAL
    elif spec_vals or spec_tokens or has_match:
        spec_state = REQ_CONFIRMED
    else:
        spec_state = REQ_NA if not category_known else REQ_UNKNOWN
    if not category_known:
        final_state = REQ_NA
    elif has_match and not open_spec:
        final_state = REQ_CONFIRMED
    else:
        final_state = REQ_PENDING

    return {
        "levels": [
            {"level": "category", "short": "产品类别",
             "label": "Product Category · 产品类别",
             "value": category_val or None, "state": category_state,
             "source": ("customer_information" if phrase else "product_data")
                       if category_known else None},
            {"level": "type", "short": "类型/材质",
             "label": "Product Type · 类型/材质",
             "value": (material_m.group(0).title() if material_m else None),
             "state": type_state,
             "source": "customer_information" if material_m else None},
            {"level": "model", "short": "型号", "label": "Model · 型号",
             "value": (model_m.group(1) if model_m else None),
             "state": model_state,
             "source": "customer_information" if model_m else None},
            {"level": "specification", "short": "规格",
             "label": "Specification · 规格",
             "value": (" / ".join(spec_vals[:2]) or "、".join(spec_tokens[:2])) or None,
             "state": spec_state,
             "source": ("customer_information" if (spec_vals or spec_tokens)
                        else None)},
            {"level": "final_selection", "short": "最终选型",
             "label": "Final Selection · 最终选型",
             "value": (matches[0]["name"] if (has_match and not open_spec) else None),
             "state": final_state,
             "source": "product_data" if final_state == REQ_CONFIRMED else None},
        ],
        "category_confirmed": category_known,
        "note": ("客户已明确产品类别 → 下一步是在产品库中匹配候选产品，而不是向客户"
                 "确认产品类别；型号 / 最终选型未落定前保持「待确认」，不当作已确认。"),
    }


# 需求语义清单的展示顺序（第五轮第二次补丁 03）
_SEM_LABELS = [
    ("product_category", "产品类别"),
    ("product_type", "类型/材质"),
    ("model", "型号"),
    ("specification", "规格"),
    ("quantity", "数量"),
    ("customization", "定制要求"),
    ("certification", "认证要求"),
    ("destination", "目的地"),
    ("delivery_time", "交期"),
    ("incoterm", "贸易术语"),
    ("payment_terms", "付款方式"),
    ("target_price", "目标价"),
    ("final_selection", "最终选型"),
]


def build_requirement_semantics(req: dict, partially: list, info: dict = None,
                                matches: list = None) -> list:
    """需求确认语义五态清单（第五轮第二次补丁 03）。

    每项：{field, label, state, state_cn, value, note}
    三层语义映射：
      - 客户已提出且值确定（如 FOB / Germany）→ confirmed
      - 客户给出候选/大概值（500ml or 750ml / about 3,000 pcs）→ partially_confirmed
        + note 标注「初始/大概」，不当作已确认订单量，也不降级为 Unknown
      - 主题已提出但需双方落定（最终选型）→ pending_confirmation
      - 前置层级未定（品类未知时的型号）→ not_applicable
    """
    info = info or {}
    req = req or {}
    partial_map = {p["field"]: p for p in (partially or [])}
    low_inc = _INCOTERM_RE_P.search((info.get("raw_text") or "").lower())
    low_pay = _PAYMENT_RE_P.search((info.get("raw_text") or "").lower())

    qty_val = None
    if info.get("quantity"):
        _unit = (info.get("quantity_unit") or "pcs").strip()
        # 客户给的是大概值（about 3,000 / 候选值）→ 展示带「约」，
        # 语义 = 部分确认（初始/大概），不是 Unknown，也不是已确认订单量
        qty_val = (f"约 {info['quantity']:,} {_unit}" if "quantity" in partial_map
                   else f"{info['quantity']:,} {_unit}")

    values = {
        "product_category": (partial_map.get("product_category", {}) or {}).get("value"),
        "quantity": qty_val,
        "incoterm": (low_inc.group(0).upper() if low_inc else None),
        "payment_terms": (low_pay.group(0).upper() if low_pay else None),
        "target_price": (f"USD {info['target_price']}" if info.get("target_price") else None),
    }

    out = []
    for field, label in _SEM_LABELS:
        state = req.get(field, REQ_UNKNOWN)
        note = ""
        p = partial_map.get(field)
        if p:
            note = p.get("sales_advice") or ""
        if field == "quantity" and state == REQ_PARTIAL:
            note = "初始/大概数量：可先按该量初步报价，正式报价前确认准确数量；不降级为 Unknown，也不当作已确认订单量。"
        if state == REQ_UNKNOWN:
            continue          # 未提供的字段交给「待处理/缺失检测」表达，不重复堆叠
        out.append({
            "field": field, "label": label, "state": state,
            "state_cn": REQ_STATE_CN.get(state, state),
            "value": values.get(field), "note": note,
        })
    return out


def assess_requirements(text: str, info: dict, matches: list,
                        partially: list, cert: dict = None) -> dict:
    """逐字段输出 confirmed / partially_confirmed / unknown（十一节要求的字段）。

    判定口径：客户明说 = confirmed；给了候选/大概值 = partially_confirmed；
    全文没提 = unknown。绝不能把 partially_confirmed 记成 unknown。
    """
    low = (text or "").lower()
    partial_fields = {p["field"] for p in partially}
    has_match = any((m.get("match_score") or 0) >= 0.3 for m in (matches or []))
    cert = cert or certify_split(text)
    # 第五轮第二次补丁 03：客户原话明确的产品品类（客户已提出 ≠ Unknown）
    phrase = customer_product_phrase(text) or _norm(info.get("product_query"))
    category_known = has_match or bool(phrase)
    open_spec = any(p["field"] in ("capacity", "size", "specification")
                    for p in partially)

    def st(confirmed, partial=False, na=False):
        if na:
            return REQ_NA
        return "confirmed" if confirmed else ("partially_confirmed" if partial else "unknown")

    return {
        # 品类是否已知只取决于客户原文/匹配，与购买意向无关（intent 是意向维度，
        # 不应把"想买"误标为"品类部分已知"）
        "product": st(category_known),
        "product_category": st(category_known),
        # 类型/材质：客户说清品类即类型锚点成立；原文出现材质词更明确
        "product_type": st(category_known or bool(_MATERIAL_RE_P.search(low))),
        # 型号：客户明确给出（model X / ref X）才 confirmed；
        # 品类未知时为 N/A（层级前置未定，谈型号没有意义）
        "model": (REQ_CONFIRMED if _MODEL_VALUE_RE.search(low)
                  else (REQ_NA if not category_known else REQ_UNKNOWN)),
        "specification": st(
            (has_match or bool(re.search(r"\b\d+\s*(?:ml|l|oz|cl|cm|mm|inch|g|kg)\b", low))
             or bool(_MATERIAL_RE_P.search(low)))
            and not any(p["field"] == "specification" for p in partially),
            partial=any(p["field"] in ("capacity", "specification", "size") for p in partially)),
        # 最终选型（Final Commercial Confirmation）：
        #   库内匹配且规格无开放选项 → confirmed；
        #   品类已明确但选型未落定 → pending_confirmation（待确认，不是 Unknown）；
        #   品类未知 → N/A
        "final_selection": (
            REQ_NA if not category_known
            else (REQ_CONFIRMED if (has_match and not open_spec) else REQ_PENDING)),
        "material": st(bool(re.search(r"\b(neoprene|silicone|latex|vinyl|pvc|tpu|eva)\b", low))),
        "size": st(not any(p["field"] == "size" for p in partially) and
                   bool(re.search(r"\bsize[sd]?\b", low)),
                   partial=any(p["field"] == "size" for p in partially)),
        "capacity": ("confirmed" if re.search(r"\b\d+\s*(ml|l|oz)\b(?!\s*or)", low)
                     and not any(p["field"] == "capacity" for p in partially)
                     else ("partially_confirmed" if any(p["field"] == "capacity" for p in partially)
                           else "unknown")),
        "color": st(bool(re.search(r"\b(black|white|blue|red|green|yellow|pink|grey|gray|navy)\b", low))
                    and not any(p["field"] == "color" for p in partially),
                    partial=any(p["field"] == "color" for p in partially)),
        "quantity": ("confirmed" if info.get("quantity") and not any(
                        p["field"] == "quantity" for p in partially)
                     else ("partially_confirmed" if any(p["field"] == "quantity" for p in partially)
                           else "unknown")),
        "customization": st(bool(re.search(r"\b(custom\w*|logo|oem|odm|private\s+label|print\w*|emboss\w*)\b", low))),
        "logo": st(bool(re.search(r"\b(logo|private\s+label|print\w*|emboss\w*)\b", low))),
        "packaging": st(bool(re.search(r"\b(packag\w*|poly\s?bag|blister|carton|gift\s?box|barcode|premium)\b", low))),
        "certification": ({
            CERT_REQUIRED: "confirmed",
            CERT_REQUESTED: "partially_confirmed",
            CERT_INTERESTED: "partially_confirmed",
        }.get(cert["certification_status"], "unknown")),
        "target_market": st(bool(info.get("country")),
                            partial=bool(re.search(r"\beurope\w*|us\s+market|american\s+market", low))
                            and not info.get("country")),
        "destination": st(bool(info.get("country")),
                          partial=any(p["field"] == "destination" for p in partially)),
        "incoterm": st(bool(re.search(r"\b(fob|cif|exw|ddp|ddu|dap|fca|cfr|cnf)\b", low))),
        "payment_terms": st(bool(re.search(r"\b(t\s?/\s?t|l\s?/\s?c|paypal|western\s?union|deposit|payment\s+terms?)\b", low))),
        "delivery_time": st(bool(_DEADLINE_RE.search(low) or re.search(r"\b(urgent|asap|as\s+soon\s+as\s+possible)\b", low)),
                            partial=bool(re.search(r"\bfast\s+delivery|quick\s+delivery\b", low))
                            and not _DEADLINE_RE.search(low)),
        "sample_requirement": st(bool(re.search(r"\bsample[s]?\b", low))),
        "target_price": st(bool(info.get("target_price"))),
    }


# 认证四态（优化二：required / requested / interested / unknown）
CERT_REQUIRED = "required"      # 明确要求具体认证（We require CE）→ 才可能是高优先级阻塞
CERT_REQUESTED = "requested"    # 主动问我们有哪些认证（What certs do you have）→ 建议确认，不阻塞报价
CERT_INTERESTED = "interested"  # 提到需要认证，但没说具体标准，也没主动要清单
CERT_UNKNOWN = "unknown"        # 全文没提认证

CERT_STATUS_CN = {
    CERT_REQUIRED: "🔴 required · 客户强制要求具体认证",
    CERT_REQUESTED: "🟡 requested · 客户询问我方有哪些认证",
    CERT_INTERESTED: "🟢 interested · 客户提到需要认证但未明确标准",
    CERT_UNKNOWN: "⚪ unknown · 未提及认证",
}


def certify_split(text: str) -> dict:
    """认证判定升级为四态（第二轮优化二）。

    - "What certificates do you have for Europe?" → requested（客户在了解，是建议确认项，**不是**报价阻塞项）
    - "We require CE certification."             → required（影响选品和成本，才可作为高优先级阻塞项）
    - 泛泛提 "we need certificates"               → interested
    - 全文没提                                     → unknown
    """
    low = (text or "").lower()
    requirement = bool(_CERT_REQUIRE_RE.search(low))
    # 第五轮优化：含 may / might / if needed 等不确定措辞时，客户并未把认证定为强制门槛
    # （例："We may need certificates for our retail chain."），降级为 interested，
    # 不作为报价阻塞项，也不进入首轮追问。
    hedged = False
    if requirement:
        m = _CERT_REQUIRE_RE.search(low)
        seg = low[max(0, m.start() - 80): m.end() + 80]
        hedged = bool(re.search(
            r"\b(may|might|possibly|possible|if\s+needed|preferably|would\s+like"
            r"|hopefully|eventually|in\s+the\s+future|for\s+reference)\b", seg))
        if hedged:
            requirement = False
    requested = (not requirement) and bool(_CERT_INTEREST_RE.search(low))
    interested = ((not requirement and not requested
                   and bool(re.search(r"\bcertificat\w*\b", low))) or hedged)

    status = CERT_UNKNOWN
    if requirement:
        status = CERT_REQUIRED
    elif requested:
        status = CERT_REQUESTED
    elif interested:
        status = CERT_INTERESTED

    return {
        # 四态（新）
        "certification_status": status,
        # 旧布尔字段保留，向后兼容
        "certification_interest": bool(requested or interested),
        "certification_requirement": requirement,
    }


def _detect_deadline(text: str):
    """返回 (期限字符串, 天数)。解析不了天数时天数为 None。"""
    m = _DEADLINE_RE.search(text or "")
    if not m:
        return None, None
    period = m.group(1) or f"{m.group(2)} {m.group(3)}s"
    num = re.search(r"\d+", period)
    days = None
    if num:
        n = int(num.group(0))
        days = n * 7 if re.search(r"week", period, re.I) else (
            n * 30 if re.search(r"month", period, re.I) else n)
    return period, days


# ---------- 报价准备度：十项因素 + A/B/C 确认项分级（第三轮优化一、二、八） ----------
# A = 必须确认项（未确认 → 不能"可正式报价"；产品/数量完全没着落 → 连初步报价都不行）
# B = 建议确认项（未确认 → 可初步报价，正式报价前最好确认）
# C = 可后续确认项（未确认 → 不阻塞任何报价，边谈边补）
READINESS_FACTORS = [
    # key, 中文名, 权重(合计100), 分级
    ("product",       "产品是否明确",     25, "A"),
    ("quantity",      "数量是否明确",     20, "A"),
    ("specification", "规格是否明确",     12, "A"),
    ("certification", "认证要求是否明确",  8, "B"),
    ("destination",   "目的地是否明确",    8, "B"),
    ("delivery_time", "交期要求是否明确", 10, "B"),
    ("customization", "定制要求是否明确",  4, "B"),
    ("incoterm",      "贸易术语是否明确",  3, "C"),
    ("payment",       "付款方式是否明确",  3, "C"),
    ("contact",       "联系方式是否完整",  2, "C"),
]

TIER_CN = {
    "A": "A · 必须确认项（不确认不能正式报价）",
    "B": "B · 建议确认项（可先初步报价，正式报价前确认）",
    "C": "C · 可后续确认项（边谈边补，不阻塞报价）",
}


def _factor_state(key: str, text: str, info: dict, matches: list,
                  partially: list, cert: dict, req: dict) -> tuple:
    """返回 (state, reason)。state ∈ confirmed / partially_confirmed / unknown。
    reason 为"输入事实 → 判断"的可解释链条（第三轮优化八）。"""
    low = (text or "").lower()
    info = info or {}
    has_match = any((m.get("match_score") or 0) >= 0.3 for m in (matches or []))
    partial_fields = {p["field"] for p in partially}
    product_query = _norm(info.get("product_query"))

    if key == "product":
        if has_match:
            return "confirmed", f"产品库匹配到「{matches[0]['name']}」→ 产品明确"
        if product_query:
            return "confirmed", f"客户明说产品「{product_query}」→ 产品方向明确（库内无精确匹配，需人工核对）"
        # 第五轮第二次补丁 03：客户原话明确品类 → 客户已提出 ≠ Unknown，
        # 只是"最终选型"未落定 → partially_confirmed（不阻塞初步报价）
        phrase_hit = customer_product_phrase(text)
        if phrase_hit:
            return "partially_confirmed", (f"客户明确产品品类「{phrase_hit}」→ 品类/类型已确认；"
                                            "最终选型（型号/规格落定）待确认")
        if "capacity" in partial_fields or "specification" in partial_fields:
            return "partially_confirmed", "客户给了候选规格但没说产品 → 产品部分明确"
        if _norm(info.get("intent")):
            return "partially_confirmed", "客户表达了采购意向但没说具体产品 → 产品部分明确"
        return "unknown", "客户未说明要什么产品，产品库也未匹配到相关产品 → 产品不明确"

    if key == "quantity":
        if info.get("quantity") and "quantity" not in partial_fields:
            # 多数量语义补丁：询盘里有多个数量时，逐个标注商业语义，
            # 说明它们各归各位、不构成冲突（避免 AI 摘要制造虚假数量冲突）
            sems = info.get("quantity_semantics") or []
            if len(sems) > 1:
                detail = "；".join(f"{s['role_cn']} {s['value']:,}"
                                   for s in sems[:4])
                return "confirmed", (f"客户明确当前报价数量 {info['quantity']:,} → 数量明确"
                                     f"（{detail} —— 商业语义不同，不构成数量冲突）")
            return "confirmed", f"客户明确数量 {info['quantity']:,} → 数量明确"
        if "quantity" in partial_fields:
            vals = next((p["values"] for p in partially if p["field"] == "quantity"), [])
            return "partially_confirmed", f"数量为大概值/候选值 {'/'.join(vals)} → 部分明确"
        return "unknown", "客户未给出采购数量 → 数量不明确（没有数量就没有价格）"

    if key == "specification":
        partial_specs = [p for p in partially
                         if p["field"] in ("capacity", "size", "specification")]
        if partial_specs:
            vals = next((p["values"] for p in partial_specs), [])
            return "partially_confirmed", f"客户在 {'/'.join(vals)} 之间未定 → 规格部分明确"
        if has_match:
            return "confirmed", (f"产品库已匹配到具体产品「{matches[0]['name']}」"
                                 "（含价格/MOQ/常规参数），客户未提出未被覆盖的规格变化 → 规格可按匹配 SKU 定")
        if product_query:
            return "partially_confirmed", "客户描述了产品但库内无精确匹配，具体规格需人工核对 → 部分明确"
        return "unknown", "产品与规格均未提及 → 规格不明确"

    if key == "certification":
        st = cert["certification_status"]
        if st == CERT_REQUIRED:
            return "confirmed", "客户明确提出认证要求 → 认证要求明确（正式报价前需核对我方能满足）"
        if st == CERT_REQUESTED:
            return "partially_confirmed", "客户在询问我方有哪些认证 → 认证关注但未指定标准"
        if st == CERT_INTERESTED:
            return "partially_confirmed", "客户提到需要认证但未说具体标准 → 部分明确"
        return "unknown", "全文未提及认证 → 认证要求未知（不阻塞初步报价）"

    if key == "destination":
        if info.get("country"):
            return "confirmed", f"客户明确目的地 {info['country']} → 目的地明确"
        if "destination" in partial_fields:
            return "partially_confirmed", "客户只说 Europe 未给具体国家 → 目的地部分明确"
        return "unknown", "客户未提及目的地 → 影响运费，建议确认"

    if key == "delivery_time":
        period, _ = _detect_deadline(text)
        if period:
            return "confirmed", f"客户明确交期 {period} → 交期明确"
        if _URGENT_HIGH_RE.search(low):
            return "confirmed", "客户表达 urgent/ASAP → 交期诉求明确（具体天数需确认）"
        if _URGENT_MED_RE.search(low):
            return "partially_confirmed", "客户希望尽快但未给具体交期 → 部分明确"
        return "unknown", "客户未提及交期 → 建议确认"

    if key == "customization":
        if re.search(r"\b(custom\w*|logo|oem|odm|private\s+label|print\w*|emboss\w*)\b", low):
            return "confirmed", "客户明确提出定制/logo/OEM 要求 → 定制要求明确"
        return "unknown", "客户未提及定制要求 → 建议确认（影响报价）"

    if key == "incoterm":
        if re.search(r"\b(fob|cif|exw|ddp|ddu|dap|fca|cfr|cnf)\b", low):
            return "confirmed", "客户提及贸易术语 → 明确"
        return "unknown", "客户未提及贸易术语 → 可后续确认"

    if key == "payment":
        if re.search(r"\b(t\s?/\s?t|l\s?/\s?c|paypal|western\s?union|deposit|payment\s+terms?)\b", low):
            return "confirmed", "客户提及付款方式 → 明确"
        return "unknown", "客户未提及付款方式 → 可后续确认"

    if key == "contact":
        if _norm(info.get("email")) or _norm(info.get("phone")):
            return "confirmed", "已有可用联系方式（邮箱/电话）→ 完整"
        if _norm(info.get("contact_name")):
            return "partially_confirmed", "有联系人姓名但无邮箱/电话 → 部分完整"
        return "unknown", "无任何联系方式 → 需通过原渠道回复"

    return "unknown", ""


def build_readiness(text: str, info: dict, matches: list, partially: list,
                    cert: dict) -> dict:
    """报价准备度 v3（第三轮优化一、二）。

    分数 = 十项因素加权（确认=满分，部分确认=半分，未确认=0 分），
    再按"报价状态"硬性收口：分数必须落在状态对应区间内，绝不冲突：
      信息不足   ≤ 55
      可初步报价 56 ~ 88
      可正式报价 ≥ 89
    状态判定只看确认项分级：
      A 类（产品/规格/数量）有未确认 → 绝不显示"可正式报价"；
      产品+数量完全没着落 → "信息不足"；
      只有 B/C 类未确认 → 允许初步报价。
    """
    info = info or {}
    cert = cert or certify_split(text)
    req = assess_requirements(text, info, matches, partially, cert)

    # ---- 逐因素评估（含 fact→判断 链） ----
    factors = []
    for key, label, weight, tier in READINESS_FACTORS:
        state, reason = _factor_state(key, text, info, matches, partially, cert, req)
        got = {"confirmed": weight, "partially_confirmed": weight / 2,
               "unknown": 0}[state]
        factors.append({
            "key": key, "label": label, "tier": tier,
            "state": state, "weight": weight,
            "score": round(got, 1), "reason": reason,
        })
    raw = sum(f["score"] for f in factors)

    has_match = any((m.get("match_score") or 0) >= 0.3 for m in (matches or []))
    product_query = _norm(info.get("product_query"))

    # ---- 状态判定（只依据 A/B/C 分级，与分数无关，先定状态再收口分数） ----
    a_unresolved = [f for f in factors if f["tier"] == "A" and f["state"] != "confirmed"]
    product_f = next(f for f in factors if f["key"] == "product")
    qty_f = next(f for f in factors if f["key"] == "quantity")

    prelim_hard = []      # 连初步报价都做不了
    formal_blockers = []  # 可初步报价，正式报价前需解决
    # 产品方向硬阻塞口径：状态 unknown，或仅凭一句"感兴趣"撑起来（无产品库匹配、
    # 无产品描述、无规格锚点）——都算"连初步报价都无从下手"。
    # 第五轮第二次补丁 03：客户原话明确品类（如 Stainless Steel Water Bottle）
    # 也是有效产品方向锚点 → 不作为"连初步报价都做不了"的硬阻塞。
    product_anchor_ok = (has_match or product_query
                         or customer_product_phrase(text)
                         or any(p["field"] in ("capacity", "size", "specification")
                                for p in partially))
    if product_f["state"] == "unknown" or (
            product_f["state"] == "partially_confirmed" and not product_anchor_ok):
        prelim_hard.append({
            "field": "product", "severity": "high", "phase": "preliminary",
            "impact": "product_selection",
            "reason": "客户没有说明具体要什么产品，当前产品库也未匹配到相关产品"
                      "（没有足够证据，不编造）——必须先向客户确认产品方向"
                      "（名称 / 图片 / 链接 / 型号），这一步没解决前连价格区间都给不了。",
        })
    if qty_f["state"] == "unknown":
        prelim_hard.append({
            "field": "quantity", "severity": "high", "phase": "preliminary",
            "impact": "cost",
            "reason": "客户未给出采购数量，无法核算单价档位——没有数量就没有价格，"
                      "需先确认数量才能谈报价。",
        })
    # 规格未定（A 类但可初步报价）→ 正式报价前确认
    spec_f = next(f for f in factors if f["key"] == "specification")
    if spec_f["state"] in ("partially_confirmed", "unknown") and not prelim_hard:
        vals = next((p["values"] for p in partially
                     if p["field"] in ("capacity", "size", "specification")), [])
        formal_blockers.append({
            "field": "specification", "severity": "medium", "phase": "formal",
            "impact": "product_selection",
            "reason": (f"规格在 {'/'.join(vals)} 之间尚未最终确定，"
                       if vals else "关键规格（容量/尺寸）尚未确认，")
                      + "不同规格可能对应不同 SKU 和价格；可先按候选规格各给一版初步报价，"
                        "正式报价前确认最终规格。",
        })
    # 认证明确要求（B 类，但影响合规成本）→ 正式报价前确认
    if cert["certification_status"] == CERT_REQUIRED:
        formal_blockers.append({
            "field": "certification", "severity": "medium", "phase": "formal",
            "impact": "compliance",
            "reason": "客户明确提出具体认证要求，正式报价前需确认该产品能符合对应认证"
                      "（避免报错版本 / 成本 / 订单执行）。",
        })
    # 第五轮第二次补丁 03：品类已明确但最终选型未落定（Final Commercial Confirmation）
    # → 正式报价前确认；不作为硬阻塞，更不升 P0（客户已提出品类，不该反过来问"要什么产品"）
    if not prelim_hard and (product_query or customer_product_phrase(text)) and not has_match:
        _phrase_b = product_query or customer_product_phrase(text)
        formal_blockers.append({
            "field": "final_selection", "severity": "medium", "phase": "formal",
            "impact": "product_selection",
            "reason": (f"客户已明确产品品类「{_phrase_b}」，产品库暂无对应产品——"
                       "下一步在产品库中检索该品类 / 相近品类的候选产品；正式报价前"
                       "需确认最终选型（参考型号 / 图片 / 库内替代品类）。"),
        })

    if prelim_hard:
        status = STATUS_INSUFFICIENT
    elif a_unresolved or formal_blockers:
        status = STATUS_PRELIM
    else:
        status = STATUS_READY

    # ---- 分数按状态区间硬收口（第三轮一致性核心） ----
    lo, hi = SCORE_BANDS[status]
    score = _cap(raw)
    if status == STATUS_INSUFFICIENT:
        score = min(score, hi)
    elif status == STATUS_PRELIM:
        score = _cap(max(lo, min(score, hi)))
    else:
        score = _cap(max(lo, score))

    # ---- 确认项分级清单（给界面：A/B/C 三组） ----
    confirmation_items = {"A": [], "B": [], "C": []}
    for f in factors:
        if f["state"] == "confirmed":
            continue
        entry = {"field": f["key"], "label": f["label"],
                 "state": f["state"], "reason": f["reason"]}
        if f["tier"] == "A":
            entry["advice"] = "该因素直接决定能否报价，必须先确认。"
        elif f["tier"] == "B":
            entry["advice"] = "建议在初步报价时一并确认，正式报价前落定。"
        else:
            entry["advice"] = "可后续确认，不阻塞当前报价推进。"
        confirmation_items[f["tier"]].append(entry)

    # 建议确认项（不阻塞初步报价，但销售最好在回复里带一句）
    to_confirm = []
    if cert["certification_status"] in (CERT_REQUESTED, CERT_INTERESTED):
        to_confirm.append({
            "field": "certification",
            "status": cert["certification_status"],
            "reason": ("客户在询问我们有哪些认证（requested/interested），不是强制要求，"
                       "不必作为报价阻塞项；回复时如实列出可提供的认证供客户核对即可，"
                       "不要承诺未经验证的认证。"),
        })

    # ---- 业务员一眼看懂的总结语 ----
    if status == STATUS_INSUFFICIENT:
        quote_summary = ("信息不足，现在无法有效报价："
                         + "；".join(b["reason"] for b in prelim_hard)
                         + "。先确认产品方向和数量，再谈价格。")
    elif status == STATUS_PRELIM:
        if formal_blockers:
            _clean = "；".join(b["reason"].rstrip("。") for b in formal_blockers)
            quote_summary = ("可以先给初步报价（参考价 / 价格区间）："
                             + _clean + "。正式报价前把这些确认掉。")
        else:
            quote_summary = ("可以先给初步报价 / 价格区间："
                             + "；".join(f["label"] + "未定" for f in a_unresolved)
                             + "。")
    else:
        quote_summary = "产品、规格、数量及关键商务条件已确认，可以直接进入正式报价。"
    if to_confirm:
        quote_summary += " 认证为询问/兴趣项，回复里主动列出我方认证供客户核对即可。"

    blocking = prelim_hard + formal_blockers
    return {
        "quotation_readiness_score": score,
        "quotation_readiness_status": status,
        # 兼容旧 Schema：blocking_reasons 保持原结构（phase 区分初步/正式）
        "blocking_reasons": blocking,
        "blockers_preliminary": prelim_hard,
        "blockers_formal": formal_blockers,
        # 建议确认项（不阻塞，销售回复时可顺带提及）
        "to_confirm": to_confirm,
        # 一句话总结：现在能不能报、能做到什么程度、还差什么
        "quote_summary": quote_summary,
        # 第三轮新增：十项因素明细（fact→判断→分数）+ A/B/C 确认项分级
        "factors": factors,
        "confirmation_items": confirmation_items,
        "readiness_basis": [
            f"{f['label']}（{f['tier']}类）：{f['reason']} → {f['score']}/{f['weight']}"
            for f in factors
        ],
    }


def build_risks(text: str, info: dict, matches: list, partially: list,
                cert: dict) -> list:
    """风险识别（第十六节）——只输出有事实依据的风险，禁止编造。"""
    low = (text or "").lower()
    risks = []
    period, days = _detect_deadline(text)

    # 交期风险：客户要求交期 vs 产品库常规交期（真实数据）
    if period:
        lead_times = [m.get("lead_time") for m in (matches or []) if m.get("lead_time")]
        ref = lead_times[0] if lead_times else None
        if days and days <= 21:
            ref_txt = f"（产品库同类产品常规交期：{ref}）" if ref else ""
            sev = "high" if days <= 14 else "medium"
            risks.append({
                "type": "delivery_time", "severity": sev,
                "reason": f"客户要求 {period} 内交货，明显偏短{ref_txt}，"
                          "需先与生产确认排产可行性，不要直接承诺。",
            })
        elif days:
            risks.append({
                "type": "delivery_time", "severity": "low",
                "reason": f"客户要求 {period} 内交货，需与生产核对常规交期后确认。",
            })
    elif _URGENT_HIGH_RE.search(low):
        risks.append({
            "type": "delivery_time", "severity": "medium",
            "reason": "客户表达紧急（urgent / ASAP），需尽快响应并确认实际可行交期。",
        })

    # 规格风险：候选规格未定 → SKU/成本不确定
    for p in partially:
        if p["field"] in ("capacity", "size", "specification"):
            risks.append({
                "type": "specification", "severity": "medium",
                "reason": f"客户在 {'/'.join(p['values'])} 之间未确定，"
                          "SKU 选择和成本核算存在不确定性。",
            })
            break

    # 认证风险：只问不要求 → 不要主动承诺
    if cert["certification_interest"] and not cert["certification_requirement"]:
        risks.append({
            "type": "certification", "severity": "low",
            "reason": "客户关注认证但未明确具体标准，回复时只列已有认证事实，"
                      "不要承诺未经验证的认证。",
        })

    # 目标价风险：客户目标价低于产品库参考价下限（真实数据对比）
    tp = info.get("target_price")
    if tp and matches:
        floor = min(m["price_range"][0] for m in matches if m.get("price_range"))
        if tp < floor:
            risks.append({
                "type": "target_price", "severity": "medium",
                "reason": f"客户目标价 {tp} 低于产品库参考价下限 {floor}，"
                          "需评估用料/工艺降本方案或调整预期。",
            })

    # 数量低于 MOQ（真实数据对比）
    qty = info.get("quantity")
    if isinstance(qty, int) and matches:
        top = matches[0]
        if qty < top.get("moq", 0):
            risks.append({
                "type": "moq", "severity": "low",
                "reason": f"客户数量 {qty} 低于 {top.get('name')} 的 MOQ "
                          f"{top['moq']:,}，需确认能否接受小单或建议调整数量。",
            })
    return risks


ACTION_CN = {
    "ask_for_information": "追问关键信息",
    "prepare_preliminary_quote": "准备初步报价",
    "prepare_formal_quote": "准备正式报价",
    "recommend_products": "推荐产品",
    "send_reply": "发送回复",
    "follow_up": "后续跟进",
    "manual_review": "人工复核",
    "confirm_capacity": "确认容量",
    "confirm_specification": "确认规格",
    "request_product_detail": "索取产品信息",
    "offer_certifications": "提供我方认证清单",
}

# action → 更精确的动作动词（供结构化 action 使用）
_ACTION_KEY = {
    "prepare_formal_quote": "prepare_formal_quote",
    "prepare_preliminary_quote": "prepare_preliminary_quote",
    "ask_for_information": "ask_for_information",
    "recommend_products": "recommend_products",
    "send_reply": "send_reply",
    "follow_up": "follow_up",
}


def _act(key, priority, reason, related_field=None, deadline=None):
    """生成结构化 action。priority 形如 P0/P1/P2/P3/P4。"""
    return {
        "action": key,
        "action_cn": ACTION_CN.get(key, key),
        "priority": priority,
        "reason": reason,
        "related_field": related_field,
        "deadline": deadline,
    }


def build_next_actions(readiness: dict, text: str, info: dict, matches: list,
                       partially: list, cert: dict) -> list:
    """下一步业务动作（第二轮优化十）：最多 5 条，可执行，带 related_field 和 deadline。

    每个 action 结构：{action, action_cn, priority(P0..P4), reason,
    related_field, deadline}。按业务优先级 P0 最高。
    """
    status = readiness["quotation_readiness_status"]
    prelim_hard = readiness.get("blockers_preliminary", [])
    formal = readiness.get("blockers_formal", [])
    actions = []

    # P0 · 如果连初步报价都卡住，最优先是先把产品方向 / 数量问清楚
    if prelim_hard:
        for b in prelim_hard:
            if b["field"] == "product":
                actions.append(_act(
                    "request_product_detail", "P0",
                    "客户没说清具体产品，产品库也未匹配到相关产品——先向客户索取产品名称 / "
                    "图片 / 链接 / 参考型号，才能选型核价。",
                    related_field="product",
                    deadline="before any quote"))
            elif b["field"] == "quantity":
                actions.append(_act(
                    "ask_for_information", "P0",
                    "客户未给出采购数量，无法核算单价档位——先确认数量。",
                    related_field="quantity",
                    deadline="before any quote"))

    # P1 · 按可执行状态推进
    if status == STATUS_READY:
        actions.append(_act(
            "prepare_formal_quote", "P1",
            "关键信息已齐全，可以直接进入正式报价。",
            related_field=None, deadline="immediately"))
    elif status == STATUS_PRELIM:
        actions.append(_act(
            "prepare_preliminary_quote", "P1",
            "产品和数量方向已明确，可先准备初步报价 / 价格区间。",
            related_field=None, deadline="within 24h"))
    # 候选规格（容量/尺寸）未定 → 单独一条可执行的确认动作
    # 第五轮第二次补丁 03：vals 跨 capacity/size/specification 三个字段取，
    # 修复「客户在『』之间未定」候选值显示为空的 bug
    for fkey, verb in (("capacity", "confirm_capacity"), ("size", "confirm_size"),
                       ("specification", "confirm_specification")):
        if any(b["field"] == fkey for b in formal):
            vals = next((p["values"] for p in partially
                         if p["field"] in ("capacity", "size", "specification")), [])
            vtxt = " / ".join(vals) if vals else ""
            actions.append(_act(
                verb, "P2",
                f"客户在「{vtxt}」之间未最终确定，不同规格可能对应不同 SKU 和价格，"
                "需在正式报价前确认最终规格。",
                related_field=fkey, deadline="before formal quotation"))

    # 认证 requested/interested → 行动是"提供我方认证清单"，不是追问
    if cert["certification_status"] in (CERT_REQUESTED, CERT_INTERESTED):
        actions.append(_act(
            "offer_certifications", "P2",
            "客户在询问我方有哪些认证（不是强制要求）——回复里主动列出可提供的认证清单"
            "（如 CE / REACH / FDA 等，只列已有的事实），供客户核对。",
            related_field="certification", deadline="in first reply"))
    elif cert["certification_status"] == CERT_REQUIRED:
        actions.append(_act(
            "ask_for_information", "P2",
            "客户有明确认证要求，需核对产品能否满足后再正式报价。",
            related_field="certification", deadline="before formal quotation"))

    # 未匹配到产品 → 按产品层级区分下一步（第五轮第二次补丁 03）：
    #   品类已明确 → 下一步是"查看产品库并匹配候选产品"（内部动作，P2，不升 P0，
    #                也绝不向客户反问"要什么产品类别"）；
    #   品类未知   → 才需要向客户索取产品方向（图片 / 链接 / 型号）。
    if not matches and not _norm(info.get("product_query")):
        _phrase_act = customer_product_phrase(text)
        if _phrase_act:
            actions.append(_act(
                "recommend_products", "P2",
                f"客户已明确产品品类「{_phrase_act}」→ 查看产品库并匹配候选产品"
                "（检索该品类 / 相近品类），匹配不上再请客户提供参考型号 / 图片；"
                "品类已确认，不需要向客户确认产品类别。",
                related_field="product", deadline="before preliminary quote"))
        else:
            actions.append(_act(
                "recommend_products", "P2",
                "当前产品库未找到匹配产品（如实提示）——先向客户索取产品图片 / 链接 / 型号，"
                "再人工判断产品库中是否有可替代品类，而不是编造产品报价。",
                related_field="product", deadline="before any quote"))

    # P3 · 发送回复（第一封）
    actions.append(_act(
        "send_reply", "P3",
        "结合报价准备度与结构化行动，发送第一封回复（只问真正阻塞下一步的 1~3 个问题）。",
        related_field=None, deadline="today"))

    # P4 · 交期跟进
    period, _ = _detect_deadline(text)
    if period and period.lower().find("week") >= 0:
        n = re.search(r"\d+", period)
        if n and int(n.group(0)) <= 3:
            actions.append(_act(
                "follow_up", "P4",
                f"客户要求 {period} 内交货，内部需先确认产能，回复时给出有把握的交期区间"
                "而不是直接承诺。",
                related_field="delivery", deadline=f"within {period}"))
    if _norm(info.get("email")) or _norm(info.get("phone")):
        actions.append(_act(
            "follow_up", "P4",
            "3 天内客户未回复时，用 WhatsApp / 邮件温和跟进一次。",
            related_field="contact", deadline="in 3 days"))

    # 去重：同一 (action,related_field) 只留优先级最高的一条
    seen, unique = set(), []
    prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
    for a in sorted(actions, key=lambda x: prio_rank.get(x["priority"], 9)):
        key = (a["action"], a.get("related_field"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(a)
    return unique[:5]


# ---------- Anti-Hallucination 硬规则（第三轮优化九） ----------
ANTI_HALLUCINATION_RULES = [
    "严禁编造：产品价格 / MOQ / 交期 / 库存 / 产品规格 / 认证 / 公司资质 / 运输费用 / 产品链接 / 产品型号",
    "数据库或公司知识库中不存在的信息，只能显示「未知」「未提供」或「需要人工确认」，不能猜测",
    "产品库没有相关产品时，如实说明并请客户提供图片 / 链接 / 型号，绝不虚构产品",
    "认证只陈述我方真实持有的事实，不承诺未经验证的认证",
    "订单金额没有真实单价依据时只按采购规模档位描述，不虚构金额",
]

# ---------- 四类数据来源（第三轮优化十） ----------
SOURCE_CUSTOMER = "customer_information"   # 客户原始询盘
SOURCE_COMPANY = "company_knowledge"       # 公司资料
SOURCE_PRODUCT = "product_data"            # 产品数据库
SOURCE_AI = "ai_inference"                 # AI 推理（不能覆盖前三类）

SOURCE_CN = {
    SOURCE_CUSTOMER: "客户信息（询盘原文）",
    SOURCE_COMPANY: "公司资料",
    SOURCE_PRODUCT: "产品数据库",
    SOURCE_AI: "AI 推理（仅供参考，不覆盖真实数据）",
}


def tag_data_sources(text: str, info: dict, matches: list) -> dict:
    """把报告里的关键数据按来源归类（第三轮优化十）。

    原则：AI Inference 只做推理补充，凡与前三类真实数据冲突，一律以真实数据为准。
    """
    info = info or {}
    customer = []
    if info.get("quantity"):
        # 多数量语义补丁：多数量时逐个标注商业语义（报价数量/试单/潜在订单量）
        sems = info.get("quantity_semantics") or []
        if len(sems) > 1:
            customer.append("；".join(f"{s['role_cn']} {s['value']:,}"
                                      for s in sems[:4]))
        else:
            customer.append(f"采购数量 {info['quantity']:,}")
    if info.get("country"):
        customer.append(f"目的地 {info['country']}")
    if info.get("company"):
        customer.append(f"公司 {info['company']}")
    if info.get("contact_name"):
        customer.append(f"联系人 {info['contact_name']}")
    if info.get("email"):
        customer.append(f"邮箱 {info['email']}")
    if info.get("target_price"):
        customer.append(f"目标价 {info['target_price']}")
    for p in find_open_options(text or ""):
        customer.append(f"客户原话候选「{p}」")

    product = []
    if matches:
        top = matches[0]
        product.append(f"匹配产品 {top['name']}（匹配度 {int(top['match_score']*100)}%）")
        if top.get("price_range"):
            product.append(f"参考价 USD {top['price_range'][0]:.2f}-{top['price_range'][1]:.2f}")
        if top.get("moq"):
            product.append(f"MOQ {top['moq']:,}")
        if top.get("lead_time"):
            product.append(f"常规交期 {top['lead_time']}")
    else:
        product.append("无（产品库没有足够证据找到匹配产品，相关价格/MOQ/交期一律为「未知」）")

    company = ["卖方资料（config.json SELLER：公司名 / 所在地 / 销售代表）",
               "我方认证清单：以公司实际持有为准，系统不预填、不承诺"]

    ai = ["采购意向 / 紧急度 / 各项评分 / 阻塞判断 / 下一步动作建议",
          "匹配候选与推荐理由（基于产品库关键词，需人工复核）"]

    return {
        SOURCE_CUSTOMER: customer or ["（本条询盘未提取到结构化客户信息）"],
        SOURCE_COMPANY: company,
        SOURCE_PRODUCT: product,
        SOURCE_AI: ai,
        "rule": "AI Inference 仅用于推理与展示，不能覆盖 Customer Information / "
                "Company Knowledge / Product Data 三类真实数据；冲突时以真实数据为准并提示人工确认。",
    }


def check_consistency(text: str, info: dict, matches: list, lead: dict,
                      insight: dict, draft: str, gaps: list = None) -> list:
    """系统一致性检查（第三轮优化十二）：每次分析完成后自动执行 6 项检查。

    返回 [{check, ok, detail}]；出现冲突时 ok=False，优先提示系统，而不是生成错误结果。
    """
    checks = []
    low = (draft or "").lower()
    insight = insight or {}
    qr = insight.get("quotation_readiness") or {}

    # 1. 报价准备度分数 与 报价状态 一致（分数必须落在状态区间内）
    status = normalize_status(qr.get("quotation_readiness_status", ""))
    score = qr.get("quotation_readiness_score", 0)
    lo, hi = SCORE_BANDS.get(status, (0, 100))
    ok = lo <= score <= hi
    checks.append({
        "check": "报价准备度分数与状态一致",
        "ok": ok,
        "detail": f"{score} 分 ↔ 状态「{STATUS_CN.get(status, status)}」"
                  f"（要求 {lo}~{hi} 分）" + ("" if ok else " ← 冲突！"),
    })

    # 2. 缺失信息 与 追问问题 一致（邮件不得重复问已知项，不得问 Payment/Trade/TargetPrice/Email）
    known_patterns = []
    if info.get("quantity"):
        known_patterns.append(r"(what\s+quantity|how\s+many\s+pcs|order\s+quantity\?)")
    forbidden = r"(payment\s+terms?\?|which\s+incoterm|fob\s+or\s+cif\?|target\s+price\?|your\s+email|e-?mail\s+address\?)"
    bad = re.search(forbidden, low)
    ok = not bad
    detail = "邮件未重复追问已知信息，也未涉及 Payment/Trade Terms/Target Price/Email" if ok \
        else f"邮件包含不应第一轮追问的内容：「{bad.group(0) if bad else ''}」 ← 冲突！"
    checks.append({"check": "追问问题与已知/禁止项一致", "ok": ok, "detail": detail})

    # 3. 产品规格与邮件一致：客户给的候选规格必须出现在邮件里（方案式追问）
    partial_spec = next((p for p in insight.get("partially_confirmed", [])
                         if p["field"] in ("capacity", "size", "specification")), None)
    if partial_spec:
        missing_opts = [v for v in partial_spec.get("values", [])
                        if v and v.lower() not in low]
        ok = not missing_opts
        checks.append({
            "check": "产品规格（候选未定）与邮件追问一致",
            "ok": ok,
            "detail": (f"邮件已引用客户候选「{'/'.join(partial_spec.get('values', []))}」"
                       if ok else f"邮件遗漏候选规格：{missing_opts} ← 冲突！"),
        })

    # 4. 产品匹配结果 与 邮件推荐 一致（没匹配就不许出现产品名/价格/MOQ）
    if not matches:
        # 说明：只抓"给出具体数字的价格/MOQ"这类虚构；
        #      "advise you on pricing, MOQ and lead time" 是下一步动作，不算虚构（第五轮）。
        leak = re.search(r"(moq\s*(?:is|:|=)?\s*\d|usd\s*\d|\$\s*\d)", low)
        names = [m.get("name", "").lower() for m in (insight.get("_catalog_names") or [])]
        leak = leak or any(n and n in low for n in names)
        checks.append({
            "check": "无匹配产品时邮件不推荐任何产品",
            "ok": not leak,
            "detail": "邮件未提及具体产品/价格/MOQ（未编造）" if not leak
                      else "邮件出现了价格/MOQ/产品字样，但产品库并无匹配 ← 冲突！",
        })

    # 5. AI 评分 与 评分依据 一致（各维加权之和 = 综合分）
    dims = lead.get("dims") or []
    if dims and lead.get("weights"):
        w = lead["weights"]
        calc = round(sum(d["score"] * w.get(d["key"], 0) for d in dims))
        ok = abs(calc - lead.get("overall_score", lead.get("score", 0))) <= 1
        checks.append({
            "check": "AI 评分与评分依据一致",
            "ok": ok,
            "detail": f"各维加权之和 {calc} = 综合分 {lead.get('overall_score', lead.get('score'))}"
                      if ok else f"加权之和 {calc} ≠ 综合分 {lead.get('overall_score')} ← 冲突！",
        })

    # 6. 客户信息与邮件一致（称谓不张冠李戴；两者都未知则跳过，不误报）
    company = (info.get("company") or "").lower()
    contact = (info.get("contact_name") or "").lower()
    generic_greeting = re.search(r"\bdear\s+(there|sir|madam|sirs?)\b", low)
    if not (contact or company):
        ok = True
        checks.append({
            "check": "客户信息与邮件称谓一致",
            "ok": ok,
            "detail": "客户联系人/公司均未识别到，跳过称谓校验（建议人工确认称呼）",
        })
    else:
        ok = bool((contact and contact in low) or (company and company in low)
                  or not generic_greeting and bool(re.search(r"\bdear\s+[a-z]", low)))
        checks.append({
            "check": "客户信息与邮件称谓一致",
            "ok": ok,
            "detail": f"邮件正确称呼「{info.get('contact_name') or info.get('company')}」"
                      if ok else "邮件用了泛称（Dear there/Sir），但已识别到客户姓名/公司 ← 建议改为实名称呼",
        })

    return checks


def build_insight(text: str, info: dict, matches: list, lead: dict,
                  gaps: list = None) -> dict:
    """总入口：输入流水线上游结果，输出完整业务洞察（全部为新增字段）。"""
    info = info or {}
    partially = detect_partially_confirmed(text, info)
    cert = certify_split(text)
    req_status = assess_requirements(text, info, matches, partially, cert)
    readiness = build_readiness(text, info, matches, partially, cert)
    risks = build_risks(text, info, matches, partially, cert)
    next_actions = build_next_actions(readiness, text, info, matches, partially, cert)
    data_sources = tag_data_sources(text, info, matches)

    # 产品匹配说明（第三轮优化六：没匹配到 = 没有足够证据，给原因 + 下一步建议，禁止编造）
    # 第五轮第二次补丁 03：区分"品类已明确"与"品类未知"两种口径
    product_note = ""
    if not matches:
        _phrase_note = customer_product_phrase(text)
        if _phrase_note:
            product_note = (f"客户已明确产品品类「{_phrase_note}」，产品库暂无对应产品——"
                            "下一步查看产品库并匹配候选产品（检索该品类 / 相近品类），"
                            "匹配不上再请客户提供参考型号 / 图片（不编造产品）。")
        elif not _norm(info.get("product_query")):
            product_note = ("当前产品库没有足够证据找到匹配产品（如实提示，不编造产品）。"
                            "建议先向客户索取产品图片、产品链接或参考型号，再判断产品库"
                            "是否有可替代品类。")
        else:
            product_note = ("客户对产品的描述较模糊，产品库未给出高置信匹配；"
                            "建议结合客户原话人工复核，必要时请客户补充图片 / 型号 / 规格。")

    # 第五轮第二次补丁 03：产品层级 + 需求语义五态（全部为新增字段，向后兼容）
    product_hierarchy = build_product_hierarchy(text, info, matches, partially)
    requirement_semantics = build_requirement_semantics(req_status, partially,
                                                        info, matches)

    return {
        "inquiry": req_status,                    # 需求字段三态评估
        "product_hierarchy": product_hierarchy,   # Category→Type→Model→Spec→Final Selection
        "requirement_semantics": requirement_semantics,   # 需求确认五态清单
        "partially_confirmed": partially,          # 候选未定字段（含客户意向/销售建议）
        "certification": cert,                     # 四态认证（required/requested/interested/unknown）
        "certification_status": cert["certification_status"],   # 便捷顶层字段
        "quotation_readiness": readiness,          # 准备度 v3：四级状态 + 十因素 + A/B/C 分级
        "risks": risks,                            # 事实型风险
        "next_actions": next_actions,              # 下一步动作（结构化，可执行）
        "product_matching_note": product_note,
        "data_sources": data_sources,              # 四类数据来源（第三轮优化十）
        "anti_hallucination_rules": ANTI_HALLUCINATION_RULES,   # 第三轮优化九
    }


if __name__ == "__main__":
    demo = ("Hi, We are interested in your products. Maybe 500ml or 750ml, not sure yet. "
            "Please send price for 3000 pcs. We need delivery within 3 weeks.")
    print(build_insight(demo, {"quantity": 3000, "intent": "OEM/贴牌定制"}, [], None))
